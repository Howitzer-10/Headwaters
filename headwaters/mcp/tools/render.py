"""The short text rendering that rides next to every structured result (HDW-204, v3.4 B1).

Hosts that ignore ``structuredContent`` read only this, so it must stand alone: a warning line
first when the pick is below the quality floor (FU-123-2, like ``headwaters pick``), then the
answer or the diffs, then one status line. No file contents and no prompt text.
"""

from __future__ import annotations

from collections.abc import Sequence

from headwaters.core.advice import BELOW_FLOOR_WARNING
from headwaters.mcp.tools.schemas import AskResult, CheckResult, DoResult, ErrorInfo

__all__ = ["render_ask", "render_check", "render_do", "render_error"]


def _status_line(r: DoResult | AskResult) -> str:
    where = f"{r.model} ({r.tier}, scope {r.scope})"
    checks = {"pass": "verified", "fail": "FAILED verification", "unchecked": "not verified"}
    line = f"[{where}; {checks[r.verify_status]}; relay: {r.relay}]"
    return f"{line} Full detail: {r.result_uri}" if r.result_uri else line


def _lines(items: Sequence[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def _body(r: DoResult | AskResult) -> list[str]:
    out: list[str] = []
    if r.below_floor:
        out.append(BELOW_FLOOR_WARNING)
    if r.answer:
        out.append(r.answer)
    if isinstance(r, DoResult):
        out.extend(f"```diff\n{d.rstrip()}\n```" for d in r.diffs)
        if r.commands:
            out.append("Commands:\n" + _lines(r.commands))
    if r.verify_reasons:
        out.append("Problems found:\n" + _lines(r.verify_reasons))
    if r.suggestions:
        out.append("Try:\n" + _lines([s.text for s in r.suggestions]))
    if r.notes:
        out.append("Notes:\n" + _lines([n for n in r.notes if n != BELOW_FLOOR_WARNING]))
    return out


def render_do(r: DoResult) -> str:
    return "\n\n".join([*_body(r), _status_line(r)])


def render_ask(r: AskResult) -> str:
    return "\n\n".join([*_body(r), _status_line(r)])


def render_error(error: ErrorInfo, suggestions: Sequence[str] = ()) -> str:
    lines = [f"Headwaters could not do this ({error.code}): {error.message}"]
    if error.fix:
        lines.append(f"Fix: {error.fix}")
    lines.extend(f"Try: {s}" for s in suggestions)
    return "\n".join(lines)


def render_check(r: CheckResult) -> str:
    if r.error is not None:
        return render_error(r.error)
    verdict = {
        "pass": "Verified: every check that ran passed.",
        "fail": "FAILED verification.",
        "unchecked": "Not verified: no check could run (that is not a pass).",
    }[r.verify_status]
    parts = [verdict]
    if r.verify_reasons:
        parts.append("Problems found:\n" + _lines(r.verify_reasons))
    parts.extend(f"Note: {n}" for n in r.notes)
    parts.append(f"[relay: {r.relay}]")
    return "\n".join(parts)
