"""Short text renderings of the second wave of tools (HDW-205).

Hosts that ignore ``structuredContent`` read only this, so each must stand alone. A refusal that
needs the user (scope loosening) puts the command to run on a line of its own. No prompt text, no
file contents, no reasoning.
"""

from __future__ import annotations

from collections.abc import Sequence

from headwaters.mcp.tools.render import render_error
from headwaters.mcp.tools.schemas_more import (
    GpuInfoOut,
    HandoffResult,
    LocalModelsResult,
    MoreResult,
    QuotaResult,
    RecommendResult,
    ScopeInfo,
    ScopeResult,
    StatusResult,
    SwitchHostResult,
    WallInfo,
)

__all__ = [
    "render_handoff",
    "render_local_models",
    "render_more",
    "render_quota",
    "render_recommend",
    "render_scope",
    "render_status",
    "render_switch",
]


def _lines(items: Sequence[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def _scope_line(s: ScopeInfo) -> str:
    if s.error is not None:
        return f"Scope: none can be served ({s.error.message})"
    tiers = ", ".join(s.tiers)
    line = (
        f"Scope: {s.effective} (tiers: {tiers}); default {s.default_scope}, strategy {s.strategy}"
    )
    return line + (f"; locks: {', '.join(s.locks)}" if s.locks else "")


def _wall_line(w: WallInfo) -> str:
    eta = "until it is lifted" if w.eta_s is None else f"for about {int(w.eta_s)} s"
    return f"{w.subject}: {w.kind} {eta}"


def _gpu_line(g: GpuInfoOut | None) -> str:
    if g is None:
        return "GPU: none detected"
    return f"GPU: {g.name}, {g.total_gb} GB ({g.budget_gb} GB for models), {g.profile}"


def render_status(r: StatusResult) -> str:
    if r.error is not None:
        return render_error(r.error)
    parts = [_scope_line(r.scope)]
    parts.append(
        "Walls:\n" + _lines([_wall_line(w) for w in r.walls]) if r.walls else "Walls: none"
    )
    parts.append(_gpu_line(r.gpu))
    if r.warm_models:
        parts.append("Warm: " + ", ".join(m.model for m in r.warm_models))
    if r.pinned_model:
        parts.append(f"Pinned model: {r.pinned_model}")
    if r.savings:
        parts.append("Savings: " + ", ".join(f"{k} {v}" for k, v in r.savings.items()))
    parts.extend(f"Note: {n}" for n in r.notes)
    return "\n".join(parts)


def render_scope(r: ScopeResult) -> str:
    if r.confirmation_required:
        lines = [
            "Not changed: loosening needs the user's confirmation.",
            f"The USER can run this: {r.user_command}",
            "Do not run it yourself.",
        ]
        return "\n".join(lines)
    if r.error is not None:
        return render_error(r.error)
    head = f"Changed: {', '.join(r.changed)}." if r.changed else "Nothing changed."
    return "\n".join([head, _scope_line(r.scope), *r.notes])


def render_handoff(r: HandoffResult) -> str:
    if r.error is not None or r.packet is None:
        return render_error(r.error) if r.error else "No packet."
    p = r.packet
    lines = [f"Handoff packet {r.packet_id} saved.", f"Goal: {p.goal}"]
    if p.files:
        lines.append("Files:\n" + _lines([f"{f['path']} ({f['sha256'][:12]})" for f in p.files]))
    if p.last_error:
        lines.append(f"Last error: {p.last_error}")
    if p.recent_turns:
        lines.append("Recent turns:\n" + _lines(p.recent_turns))
    lines.extend(f"Note: {n}" for n in r.notes)
    return "\n".join(lines)


def render_more(r: MoreResult) -> str:
    lines = ["Advanced tools:"] + [f"- {t.name}: {t.summary}" for t in r.tools]
    return "\n".join([*lines, *r.notes])


def render_recommend(r: RecommendResult) -> str:
    if r.error is not None:
        return render_error(r.error, [s.text for s in r.suggestions])
    lines = [f"Best model: {r.model} ({r.tier}, scope {r.scope})"]
    if r.below_floor:
        lines.append("No model in scope reaches the quality floor; this is the best available.")
    if r.reasons:
        lines.append("Why:\n" + _lines(r.reasons))
    if r.alternatives:
        lines.append("Then: " + ", ".join(r.alternatives))
    if r.switch:
        lines.append(
            "To switch (you run these):\n" + _lines([f"{s.host}: {s.command}" for s in r.switch])
        )
    lines.extend(f"Note: {n}" for n in r.notes)
    return "\n".join(lines)


def render_switch(r: SwitchHostResult) -> str:
    if r.error is not None:
        return render_error(r.error)
    if not r.switch:
        return r.reason or "No switch needed."
    lines = [f"Switch recommended ({r.severity}): {r.reason}"]
    if r.recommended is not None:
        lines.append(render_recommend(r.recommended))
    lines.extend(f"Note: {n}" for n in r.notes)
    return "\n".join(lines)


def render_local_models(r: LocalModelsResult) -> str:
    lines = [_gpu_line(r.gpu)]
    for m in r.models:
        speed = f", ~{m.expected_tps} tok/s" if m.expected_tps else ""
        warm = ", warm" if m.warm else ""
        lines.append(f"- {m.model}: {m.fit}, context {m.num_ctx or '-'}{speed}{warm}")
    lines.extend(f"Note: {n}" for n in r.notes)
    return "\n".join(lines)


def render_quota(r: QuotaResult) -> str:
    lines = ["Walls:\n" + _lines([_wall_line(w) for w in r.walls]) if r.walls else "Walls: none"]
    for m in r.models:
        lines.append(
            f"- {m.model}: {m.status}, {m.rpm_used}/{m.rpm_limit or '-'} per minute, "
            f"{m.rpd_used}/{m.rpd_limit or '-'} per day"
        )
    lines.extend(f"Note: {n}" for n in r.notes)
    return "\n".join(lines)
