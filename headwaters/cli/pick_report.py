"""Rendering of a ``RoutePlan`` for ``headwaters pick``: a JSON payload and Rich tables.

Only decision metadata is shown (model ids, tiers, scores, reason codes). The prompt is never
echoed back.
"""

from typing import Any

from headwaters.cli.output import fmt_eta, make_console, table, yes_no
from headwaters.core.advice import BELOW_FLOOR_WARNING
from headwaters.core.policy.filters import Candidate
from headwaters.core.router_plan import RoutePlan
from headwaters.core.rules import tier_order_label

_MAX_SKIPPED_SHOWN = 12


def availability(c: Candidate | None) -> dict[str, Any]:
    if c is None:
        return {"status": "unknown", "eta_s": None}
    return {"status": c.availability.status, "eta_s": c.availability.eta}


def plan_payload(plan: RoutePlan) -> dict[str, Any]:
    d, s, r = plan.decision, plan.scope, plan.requirements
    return {
        "ok": True,
        "model": d.model,
        "tier": d.tier,
        "rung": d.rung,
        "score": round(d.score, 2),
        "floor": round(d.floor, 2),
        "below_floor": d.below_floor,
        "verify_required": d.verify_required,
        "scope": {
            "scope": s.scope,
            "tiers": sorted(s.tiers),
            "strategy": s.strategy,
            "verify": s.verify,
            "locks": list(s.locks),
            "active_locks": list(s.active_locks),
            "tier_order": d.tier_order,
            "tier_direction": d.tier_direction,
        },
        "requirements": {
            "task": r.soft.task,
            "complexity": r.soft.complexity,
            "risk": r.soft.risk,
            "agentic": r.soft.agentic,
            "est_tokens": r.est_tokens,
            "vision": r.hard.vision,
            "tools": r.hard.tools,
            "json": r.hard.json,
            "min_ctx": r.hard.min_ctx,
        },
        "reasons": list(d.reasons),
        "fallbacks": [
            {"model": m, "tier": c.tier if c else None, **availability(c)}
            for m in d.fallbacks
            for c in [plan.candidate(m)]
        ],
        "suggestions": [{"code": x.code, "text": x.text} for x in d.suggestions],
        "rejected": [{"model": x.model_id, "reason": x.reason} for x in plan.rejected],
    }


def _request_line(plan: RoutePlan) -> str:
    r = plan.requirements
    needs = [
        name
        for name, on in (("vision", r.hard.vision), ("tools", r.hard.tools), ("json", r.hard.json))
        if on
    ]
    line = (
        f"{r.soft.task}, complexity {r.soft.complexity}, risk {r.soft.risk}, ~{r.est_tokens} tokens"
    )
    return line + (f", needs {', '.join(needs)}" if needs else "")


def print_plan(plan: RoutePlan) -> None:
    console = make_console()
    d, s = plan.decision, plan.scope

    head = table("Pick", "Item", "Value", no_wrap=["Item"])
    head.add_row("Model", d.model)
    head.add_row("Tier", d.tier)
    head.add_row("Scope", f"{s.scope} (tiers: {', '.join(sorted(s.tiers))})")
    head.add_row("Strategy", s.strategy)
    head.add_row("Tier order", tier_order_label(d.tier_order, d.tier_direction))
    # FU-128-3: every lock in force, so Private Mode plus the local scope never reads "none".
    narrowing = f"  (narrowed by: {', '.join(s.locks)})" if s.locks else ""
    head.add_row("Locks", (", ".join(s.active_locks) or "none") + narrowing)
    head.add_row("Rung", d.rung)
    head.add_row("Score / floor", f"{d.score:.1f} / {d.floor:.0f}")
    head.add_row("Below floor", yes_no(d.below_floor))
    head.add_row("Verification", "required" if d.verify_required else s.verify)
    head.add_row("Request", _request_line(plan))
    console.print(head)

    if d.below_floor:
        console.print("\n" + BELOW_FLOOR_WARNING)

    console.print("\nWhy:")
    for reason in d.reasons:
        console.print(f"  - {reason}")
    for tip in d.suggestions:
        console.print(f"  * {tip.text}")

    if d.fallbacks:
        fallbacks = table("Fallbacks, in order", "#", "Model", "Tier", "Availability")
        for index, model in enumerate(d.fallbacks, start=1):
            c = plan.candidate(model)
            state = availability(c)
            eta = f" (reset in {fmt_eta(state['eta_s'])})" if state["eta_s"] is not None else ""
            fallbacks.add_row(str(index), model, c.tier if c else "-", f"{state['status']}{eta}")
        console.print()
        console.print(fallbacks)
    else:
        console.print("\nFallbacks: none. If this model fails, the request fails.")

    if plan.rejected:
        skipped = table("Not eligible", "Model", "Reason", no_wrap=["Model"])
        for item in plan.rejected[:_MAX_SKIPPED_SHOWN]:
            skipped.add_row(item.model_id, item.reason)
        console.print()
        console.print(skipped)
        extra = len(plan.rejected) - _MAX_SKIPPED_SHOWN
        if extra > 0:
            console.print(f"... and {extra} more (use --json for the full list)")
