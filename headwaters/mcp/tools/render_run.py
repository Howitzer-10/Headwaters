"""Short text renderings of the results HDW-206 adds: a question for the user, a started job.

Hosts that ignore ``structuredContent`` read only this, so each stands alone. No prompt text, no
file contents.
"""

from __future__ import annotations

from headwaters.mcp.tools.schemas import ConfirmInfo

__all__ = ["render_confirmation", "render_declined", "render_job_started"]


def _usd(amount: float | None) -> str:
    return f"${(amount or 0.0):.3f}"


def render_confirmation(info: ConfirmInfo) -> str:
    """The call did not run: the user has to say yes first."""
    lines = ["Headwaters did NOT run this call yet: it needs the user's OK first."]
    if "spend" in info.reasons:
        lines.append(
            f"- Spend: about {_usd(info.estimated_cost_usd)} on {info.model} ({info.tier}), above "
            f"the user's limit of {_usd(info.confirm_above_usd)} (first model only)."
        )
    if "scope_loosening" in info.reasons:
        lines.append(
            f"- Scope: the call asks for '{info.requested_scope}', wider than the user's default "
            f"'{info.default_scope}'."
        )
    lines.append(
        "Ask the user. Only if they agree, repeat the same call with force: true. "
        "Never set force yourself without asking."
    )
    return "\n".join(lines)


def render_declined() -> str:
    return "The user said no, so nothing ran and nothing was spent."


def render_job_started(job_id: str, eta_s: float | None) -> str:
    """The work runs in the background; there is no answer yet."""
    when = f" It should take about {int(eta_s + 0.5)} s." if eta_s is not None else ""
    return (
        f"Started in the background as job {job_id}.{when} "
        "There is no answer yet: poll headwaters_job_status with this job_id, then read "
        "headwaters_job_result. If those tools are not listed, call headwaters_more first."
    )
