"""Ladder construction and error/suggestion helpers (HDW-114). See ``decide`` for the rungs."""

from __future__ import annotations

from collections.abc import Sequence

from headwaters.core.policy.errors import NoRouteError, Suggestion
from headwaters.core.policy.filters import Candidate, Rejection
from headwaters.core.policy.scoring import ScoreBreakdown, rank_candidates
from headwaters.core.policy.types import Entry, PolicyContext, PolicyRequest, Rung
from headwaters.core.scope import TierName

_CHEAP_TIERS: frozenset[TierName] = frozenset({"local", "free_cloud"})
_VERIFIABLE_TASKS = frozenset({"code"})  # plus any request that asks for JSON


def _entries(
    ranked: list[tuple[Candidate, ScoreBreakdown]], rung: Rung, only_ok: bool | None = None
) -> list[Entry]:
    return [(c, b, rung) for c, b in ranked if only_ok is None or b.meets_floor == only_ok]


def suggestions_for(
    chosen: Candidate, req: PolicyRequest, ctx: PolicyContext
) -> tuple[Suggestion, ...]:
    """Battery / busy GPU: suggest ``headwaters/cloud``; never leave local automatically."""
    hw = ctx.hw
    if hw is None or not chosen.is_local or req.scope.locks:  # a lock forbids the hint
        return ()
    if hw.battery_policy == "prefer_cloud":
        why = "on battery"
    elif hw.gpu_busy.busy:
        why = "the GPU is busy"
    else:
        return ()
    text = f"Running locally while {why}; pick headwaters/cloud to use cloud models instead."
    return (Suggestion("use_cloud", text),)


def no_route(
    code: str,
    message: str,
    rejected: tuple[Rejection, ...],
    cands: Sequence[Candidate],
    req: PolicyRequest,
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
    if any(x.startswith("no_fit_") for x in codes) and "local" in req.scope.tiers:
        tips.append(Suggestion("pull_model", "Pull a local model that fits your GPU."))
    if codes & {"no_vision", "no_tools", "no_json"}:
        tips.append(Suggestion("add_capable_model", "No model supports what this needs."))
    if code == "local_below_floor":
        tips.append(Suggestion("split_task", "Split the task into smaller steps."))
        tips.append(Suggestion("pull_model", "Pull a larger local model that fits your GPU."))
    if req.scope.local_only and not req.scope.locks:
        tips.append(Suggestion("use_cloud", "Use headwaters/cloud to allow cloud models."))
    return NoRouteError(code, message, etas=etas, suggestions=tuple(tips), rejected=rejected)


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
    verifiable = soft.task in _VERIFIABLE_TASKS or reqs.hard.json

    ladder: list[Entry] = []
    if strategy == "primary_first":
        ladder += [e for e in paid if e[1].meets_floor]
    ladder += cheap_ok
    if not cheap_ok and ctx.escalation is not None and verifiable and cheap_low:
        near = cheap_low[0]
        if near[1].below_floor_by <= rules.floors.optimistic_margin:
            ladder.append((near[0], near[1], "optimistic"))
    ladder += paid
    battery_local = ctx.hw is not None and ctx.hw.battery_policy == "local_only"
    if not (req.scope.local_only or battery_local):  # local never goes below the floor: errors
        ladder += cheap_low
        cap = rules.tiers.openrouter_free_router
        if cap.enabled and soft.complexity <= cap.max_complexity:
            ladder += _entries(free_router, "free_router")
    seen: set[str] = set()
    unique: list[Entry] = []
    for entry in ladder:
        if entry[0].id not in seen:
            seen.add(entry[0].id)
            unique.append(entry)
    return unique
