"""Ladder construction and error/suggestion helpers (HDW-114). See ``decide`` for the rungs."""

from __future__ import annotations

from collections.abc import Sequence

from headwaters.core.policy.errors import NoRouteError, Suggestion
from headwaters.core.policy.filters import Candidate, Rejection
from headwaters.core.policy.scoring import ScoreBreakdown, rank_candidates
from headwaters.core.policy.types import Entry, PolicyContext, PolicyRequest, Rung
from headwaters.core.rules import TierDirection
from headwaters.core.scope import TierName, tier_walk

_CHEAP_TIERS: frozenset[TierName] = frozenset({"local", "free_cloud"})


def _entries(
    ranked: list[tuple[Candidate, ScoreBreakdown]], rung: Rung, only_ok: bool | None = None
) -> list[Entry]:
    return [(c, b, rung) for c, b in ranked if only_ok is None or b.meets_floor == only_ok]


def _pull_text(base: str, hint: str | None) -> str:
    """``base`` that fits your GPU, naming the catalog model when the router knows one."""
    return f"{base} that fits your GPU: {hint}." if hint else f"{base} that fits your GPU."


def is_local_only(req: PolicyRequest, ctx: PolicyContext) -> bool:
    """Local scope, a lock, or battery ``local_only``: nothing may leave the machine."""
    return req.scope.local_only or (ctx.hw is not None and ctx.hw.battery_policy == "local_only")


def suggestions_for(
    chosen: Candidate, req: PolicyRequest, ctx: PolicyContext, *, below_floor: bool = False
) -> tuple[Suggestion, ...]:
    """Hints that ride on a decision; they never change the choice or leave local.

    A local-only pick below the floor (HDW-123, D10) keeps the old error's advice: split the
    task, pull a larger model, and (no lock) use ``headwaters/cloud``. Battery or a busy GPU
    suggest ``headwaters/cloud`` for a local pick unless a lock forbids the hint.
    """
    tips: list[Suggestion] = []
    if below_floor and chosen.is_local and is_local_only(req, ctx):
        # The hint names nothing when every recommended model is already installed (HDW-512):
        # the generic advice survives, the dishonest "pull <the model you just picked>" does not.
        tips.append(
            Suggestion("pull_model", _pull_text("Pull a larger local model", ctx.pull_hint))
        )
        tips.append(Suggestion("split_task", "Split the task into smaller steps."))
        if req.scope.local_only and not req.scope.active_locks:
            tips.append(Suggestion("use_cloud", "Use headwaters/cloud to allow cloud models."))
    hw = ctx.hw
    if hw is None or not chosen.is_local or req.scope.active_locks:  # any lock forbids the hint
        return tuple(tips)
    if hw.battery_policy == "prefer_cloud":
        why = "on battery"
    elif hw.gpu_busy.busy:
        why = "the GPU is busy"
    else:
        return tuple(tips)
    text = f"Running locally while {why}; pick headwaters/cloud to use cloud models instead."
    if not any(t.code == "use_cloud" for t in tips):
        tips.append(Suggestion("use_cloud", text))
    return tuple(tips)


def no_route(
    code: str,
    message: str,
    rejected: tuple[Rejection, ...],
    cands: Sequence[Candidate],
    req: PolicyRequest,
    pull_hint: str | None = None,
) -> NoRouteError:
    etas = {
        c.id: c.availability.eta
        for c in cands
        if c.availability.eta is not None and not c.availability.usable
    }
    codes = {r.reason for r in rejected}
    tips: list[Suggestion] = []
    if etas:
        tips.append(Suggestion("wait", f"Wait about {int(min(etas.values()))}s for a reset."))
    if "ctx_too_small" in codes:
        tips.append(Suggestion("split_task", "Split the task or send less context."))
    if "ctx_unknown" in codes:
        tips.append(
            Suggestion(
                "set_context", "Set the model's context size in rules.yaml (models.overrides)."
            )
        )
    wants_tools = bool(req.requirements.hard.tools)
    if any(x.startswith("no_fit_") for x in codes) and "local" in req.scope.tiers:
        # A tool request carries JSON schemas, which take context a smaller model has not got:
        # say so instead of pretending a bigger pull is always the answer (HDW-512).
        base = (
            "Pull a smaller local model that fits your GPU with tool schemas"
            if wants_tools
            else "Pull a local model"
        )
        tips.append(Suggestion("pull_model", _pull_text(base, pull_hint)))
    elif "no_vision" in codes and "local" in req.scope.tiers and pull_hint:
        tips.append(Suggestion("pull_model", _pull_text("Pull a local vision model", pull_hint)))
    elif "no_tools" in codes and "local" in req.scope.tiers and pull_hint:
        tips.append(
            Suggestion(
                "pull_model", _pull_text("Pull a local model that supports tool calls", pull_hint)
            )
        )
    if codes & {"no_vision", "no_tools", "no_json"}:
        tips.append(Suggestion("add_capable_model", "No model supports what this needs."))
    if req.scope.local_only and not req.scope.active_locks:
        tips.append(Suggestion("use_cloud", "Use headwaters/cloud to allow cloud models."))
    return NoRouteError(code, message, etas=etas, suggestions=tuple(tips), rejected=rejected)


def interleave_tiers(entries: list[Entry], tried: Sequence[TierName] = ()) -> list[Entry]:
    """Round-robin the ladder by tier: one attempt per tier before any tier takes a second.

    HDW-513 (D17). ``entries`` is already ordered by rung and, inside a rung, by score, so a
    tier's first appearance keeps the policy's own precedence: the head of the ladder is still
    the best candidate the previous rules chose. Only the second and later candidates of a tier
    wait for every other available tier to take a turn.

    ``tried`` is the tiers already attempted in this request, in order. The rotation starts just
    after the last one, so a re-decision after a tier failed does not hand that tier the next
    attempt while an untried tier is still available. Because the router removes failed models
    and re-decides from the remaining pool, this is what guarantees the interleaving across
    attempts rather than only inside one ladder. Deterministic: the same pool and the same
    ``tried`` always produce the same order.
    """
    queues: dict[TierName, list[Entry]] = {}
    order: list[TierName] = []
    for entry in entries:
        tier = entry[0].tier
        if tier not in queues:
            queues[tier] = []
            order.append(tier)
        queues[tier].append(entry)
    if len(order) < 2:
        return entries
    if tried and tried[-1] in queues:
        start = order.index(tried[-1]) + 1
        order = order[start:] + order[:start]
    out: list[Entry] = []
    while any(queues[tier] for tier in order):
        for tier in order:
            if queues[tier]:
                out.append(queues[tier].pop(0))
    return out


def descend_tiers(entries: list[Entry], direction: TierDirection = "cloud_first") -> list[Entry]:
    """Exhaust one tier before the next, walking the tiers in ``direction`` order (HDW-516).

    ``entries`` is already ordered by rung and, inside a rung, by score, and the sort is stable,
    so inside a tier the policy's own precedence is kept. A tier outside ``TIER_WALK`` (an id a
    newer catalog grew) sorts last, also stably, instead of being dropped.
    """
    walk = tier_walk(direction)
    rank = {tier: at for at, tier in enumerate(walk)}
    return sorted(entries, key=lambda entry: rank.get(entry[0].tier, len(walk)))


def build_ladder(kept: list[Candidate], req: PolicyRequest, ctx: PolicyContext) -> list[Entry]:
    """Ordered candidates: first is the decision, the rest are fallbacks (no duplicates)."""
    rules, reqs = ctx.rules, req.requirements
    soft, strategy = reqs.soft, req.scope.strategy

    def ranked(items: list[Candidate]) -> list[tuple[Candidate, ScoreBreakdown]]:
        return rank_candidates(items, soft, reqs.est_tokens, strategy, rules, ctx.hw)

    normal = [c for c in kept if c.role == "normal"]
    cheap = ranked([c for c in normal if c.tier in _CHEAP_TIERS])
    primaries = ranked([c for c in normal if c.tier == "paid" and c.is_primary])
    auto = ranked([c for c in kept if c.role == "openrouter_auto"])
    free_router = ranked([c for c in kept if c.role == "openrouter_free"])

    paid = _entries(primaries, "paid") + _entries(auto, "paid")
    cheap_ok = _entries(cheap, "cheap", True)
    cheap_low = _entries(cheap, "best_effort", False)
    verifiable = soft.task in rules.floors.verifiable_tasks or reqs.hard.json

    ladder: list[Entry] = []
    if strategy == "primary_first":
        # F13 (HDW-132): every usable primary leads, even below the floor (the decision then
        # carries ``below_floor`` and mandatory verification); openrouter/auto only if it passes.
        ladder += _entries(primaries, "paid")
        ladder += _entries(auto, "paid", True)
    ladder += cheap_ok
    if not cheap_ok and ctx.escalation is not None and verifiable and cheap_low:
        near = cheap_low[0]
        if near[1].below_floor_by <= rules.floors.optimistic_margin:
            ladder.append((near[0], near[1], "optimistic"))
    ladder += paid
    # Below-floor local entries come last. In local-only states they are the only below-floor
    # entries in scope, so local scope serves the best local model, flagged (D10, HDW-123).
    ladder += cheap_low
    if not is_local_only(req, ctx):
        cap = rules.tiers.openrouter_free_router
        if cap.enabled and soft.complexity <= cap.max_complexity:
            ladder += _entries(free_router, "free_router")
    seen: set[str] = set()
    unique: list[Entry] = []
    for entry in ladder:
        if entry[0].id not in seen:
            seen.add(entry[0].id)
            unique.append(entry)
    if req.tier_order == "descend":
        return descend_tiers(unique, req.tier_direction)
    return interleave_tiers(unique, req.tier_attempts)
