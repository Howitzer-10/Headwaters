"""Policy engine B: the decision ladder (HDW-114, v3 section 4.5, v3.3 section B4).

``decide(req, ctx)`` picks a model inside the effective scope. Ladder (first = decision,
the rest = fallbacks):

    0. pin, then force overrides (the hard filter still applies, so locks always win)
    1. hard filter (requirements, availability, hardware fit, scope)
    2. cheap rung      best of {local, free cloud} with score >= floor (local wins ties)
    3. optimistic rung within margin of the floor AND verifiable (code/JSON); only when an
                       ``EscalationPlugin`` is registered
    4. paid rung       the user's paid primary, then the OpenRouter auto router
    5. best effort     cheap models below the floor (``below_floor``, verification mandatory),
                       then the OpenRouter free router if enabled and complexity allows
    6. ``NoRouteError`` with reset ETAs and suggestions

``primary_first`` moves the paid rung to the front. In local scope nothing leaves the machine:
escalation is the larger local model (it ranks higher), then the best local model below the
floor (best effort, verification mandatory, ``pull_model``/``split_task`` suggestions), and an
error only when no local model passes the hard filter (D10, HDW-123). Premium (Fusion) is
only chosen by an explicit force. Battery or a busy GPU only add a ``headwaters/cloud``
suggestion. Pure functions; never touches request text.
"""

from __future__ import annotations

from headwaters.core.policy.filters import Candidate, Rejection, hard_filter
from headwaters.core.policy.ladder import build_ladder, no_route, suggestions_for
from headwaters.core.policy.scoring import floor_for, rank_candidates
from headwaters.core.policy.types import (
    FORCE_PREMIUM,
    Entry,
    PolicyContext,
    PolicyRequest,
    RouteDecision,
    Rung,
)


def _override(
    kept: list[Candidate], rejected: tuple[Rejection, ...], req: PolicyRequest, ctx: PolicyContext
) -> tuple[Candidate, Rung, str] | None:
    """Pin beats force. An override must still pass the hard filter."""
    if req.pin:
        match = next((c for c in kept if c.id == req.pin), None)
        if match is None:
            why = next((r.reason for r in rejected if r.model_id == req.pin), "unknown_model")
            msg = f"Pinned model {req.pin!r} cannot serve this request ({why})."
            raise no_route("pin_rejected", msg, rejected, ctx.candidates, req)
        return match, "pin", f"pinned {match.id}"
    if req.force == FORCE_PREMIUM:
        premium = next((c for c in kept if c.role == "premium"), None)
        if premium is None:
            msg = "Premium is not available in this scope."
            raise no_route("premium_unavailable", msg, rejected, ctx.candidates, req)
        return premium, "force", "forced premium"
    return None


def _decision(
    entry: Entry, rest: list[Entry], req: PolicyRequest, ctx: PolicyContext, reasons: list[str]
) -> RouteDecision:
    cand, b, rung = entry
    below = not b.meets_floor
    tail = f", below floor by {b.below_floor_by:.1f}" if below else ""
    reasons.append(f"{rung}: {cand.id} score {b.total:.1f}, floor {b.floor:.0f}{tail}")
    return RouteDecision(
        model=cand.id,
        tier=cand.tier,
        score=b.total,
        floor=b.floor,
        below_floor=below,
        scope=req.scope,
        reasons=tuple(reasons),
        fallbacks=tuple(e[0].id for e in rest),
        suggestions=suggestions_for(cand, req, ctx, below_floor=below),
        rung=rung,
        verify_required=below or rung == "optimistic",
        breakdown=b,
    )


def decide(req: PolicyRequest, ctx: PolicyContext) -> RouteDecision:
    """Run the ladder. Raises ``NoRouteError`` when nothing can serve the request."""
    reqs = req.requirements
    result = hard_filter(ctx.candidates, reqs.hard, req.scope, ctx.rules, ctx.hw)
    kept, rejected = list(result.kept), result.rejected
    floor = floor_for(reqs.soft, ctx.rules)
    reasons = [
        f"scope tiers: {', '.join(sorted(req.scope.tiers))}",
        *req.scope.reasons,
        f"floor {floor.value:.0f} for {reqs.soft.task}",
    ]
    forced = _override(kept, rejected, req, ctx)
    if forced is not None:
        cand, rung, label = forced
        args = (reqs.soft, reqs.est_tokens, req.scope.strategy, ctx.rules, ctx.hw)
        breakdown = rank_candidates([cand], *args)[0][1]
        reasons.append(label)
        rest = build_ladder([k for k in kept if k.id != cand.id and k.role != "premium"], req, ctx)
        return _decision((cand, breakdown, rung), rest, req, ctx, reasons)

    ladder = build_ladder([k for k in kept if k.role != "premium"], req, ctx)
    if ladder:
        return _decision(ladder[0], ladder[1:], req, ctx, reasons)
    # Local scope serves below-floor local models (HDW-123), so an empty ladder in a local-only
    # state means no local model passed the hard filter: ``no_candidates``.
    has_normal = any(k.role == "normal" for k in kept)
    code = "no_route" if has_normal else "no_candidates"
    msg = "No model can serve this request in the current scope."
    raise no_route(code, msg, rejected, ctx.candidates, req)
