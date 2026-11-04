"""``headwaters_scope``: set the default scope, the Private lock and the strategy (HDW-205).

Tightening applies at once. Loosening (a wider scope, or the Private lock off) needs the user:
the tool asks through elicitation when the host supports it, and otherwise returns a
"confirmation required" result with the command the USER runs; nothing changes until then
(D9, "scope loosening stays user-only"). A call that mixes both applies nothing until the
loosening is confirmed. The Private lock and a project rule still win over any default scope.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from mcp.server.mcpserver import Context, Elicit
from pydantic import BaseModel, Field

from headwaters.core.project_config import ProjectConfig
from headwaters.core.rules import Rules, Scope, Strategy
from headwaters.core.rules_io import ConfigError
from headwaters.core.scope import SCOPE_TIERS
from headwaters.mcp.threads import run_blocking
from headwaters.mcp.tools.context import ToolContext, ToolFailure
from headwaters.mcp.tools.schemas import ErrorInfo
from headwaters.mcp.tools.schemas_more import ScopeResult
from headwaters.mcp.tools.status import scope_info

__all__ = [
    "Change",
    "Confirm",
    "apply_scope",
    "loosens_scope",
    "make_resolver",
    "plan_change",
    "user_command",
]

NO_ASK = "none"  # nothing loosens: no question needed
NO_CHANNEL = "no_channel"  # something loosens and the host cannot be asked


class Confirm(BaseModel):
    confirm: bool = Field(description="Tick to allow Headwaters to loosen this setting.")


class Change(BaseModel):
    """What a call would change, split into the safe part and the part that needs the user."""

    tighten: dict[str, bool | str] = Field(default_factory=dict)
    loosen: dict[str, bool | str] = Field(default_factory=dict)
    same: list[str] = Field(default_factory=list)


def loosens_scope(current: Scope, new: Scope) -> bool:
    """True when ``new`` allows a tier that ``current`` does not."""
    return not SCOPE_TIERS[new] <= SCOPE_TIERS[current]


def plan_change(
    rules: Rules, scope: Scope | None, private_lock: bool | None, strategy: Strategy | None
) -> Change:
    change = Change()
    if scope is not None:
        if scope == rules.default_scope:
            change.same.append("default_scope")
        elif loosens_scope(rules.default_scope, scope):
            change.loosen["default_scope"] = scope
        else:
            change.tighten["default_scope"] = scope
    if private_lock is not None:
        if private_lock == rules.private_lock:
            change.same.append("private_lock")
        elif private_lock:
            change.tighten["private_lock"] = True
        else:
            change.loosen["private_lock"] = False
    if strategy is not None:  # a ranking choice inside the scope, not a lock
        if strategy == rules.strategy:
            change.same.append("strategy")
        else:
            change.tighten["strategy"] = strategy
    return change


def user_command(loosen: dict[str, bool | str]) -> str:
    """The commands the user runs themselves; the CLI is the user (``headwaters scope``)."""
    parts: list[str] = []
    if "default_scope" in loosen:
        parts.append(f"headwaters scope {loosen['default_scope']}")
    if "private_lock" in loosen:
        parts.append("headwaters private off")
    return " ; ".join(parts)


def _question(loosen: dict[str, bool | str]) -> str:
    what = []
    if "default_scope" in loosen:
        what.append(f"allow more model tiers (scope {loosen['default_scope']})")
    if "private_lock" in loosen:
        what.append("turn the Private lock off (cloud models may then see your prompts)")
    return "A tool asks Headwaters to " + " and ".join(what) + ". Allow it?"


def make_resolver(tc: ToolContext) -> Callable[..., Awaitable[Any]]:
    """The resolver of ``headwaters_scope``. ``scope``, ``private_lock`` and ``strategy`` are the
    tool's own arguments (the SDK passes them by name); ``tc`` is closed over."""

    async def ask_to_loosen(
        ctx: Context,
        scope: Scope | None = None,
        private_lock: bool | None = None,
        strategy: Strategy | None = None,
    ) -> Any:
        """Ask the user only when something loosens and the host can be asked."""
        change = plan_change(tc.rules(), scope, private_lock, strategy)
        if not change.loosen:
            return NO_ASK
        caps = ctx.client_capabilities
        if caps is None or caps.elicitation is None:
            return NO_CHANNEL
        return Elicit(_question(change.loosen), Confirm)

    return ask_to_loosen


def _invalid(exc: ConfigError) -> ToolFailure:
    return ToolFailure(
        "invalid_rules",
        str(exc),
        "Fix the problems listed in rules.yaml (the user's file), then try again.",
    )


async def apply_scope(
    tc: ToolContext,
    project: ProjectConfig | None,
    *,
    scope: Scope | None,
    private_lock: bool | None,
    strategy: Strategy | None,
    approved: bool,
    asked: bool,
) -> ScopeResult:
    """Apply what is allowed. ``approved``: the user said yes; ``asked``: the user was asked."""
    change = plan_change(tc.rules(), scope, private_lock, strategy)
    notes: list[str] = []
    if scope is None and private_lock is None and strategy is None:
        notes.append("Nothing was asked to change; this is the current state.")
    if change.loosen and not approved:
        return _refusal(tc, project, change, asked)
    todo = {**change.tighten, **change.loosen}

    def write() -> None:
        for key, value in todo.items():
            tc.edit_rules(key, value)

    try:
        await run_blocking(write)
    except ConfigError as exc:
        raise _invalid(exc) from exc
    if todo:
        await run_blocking(tc.reload_rules)
    notes.extend(f"{key} is already as asked." for key in change.same)
    info = scope_info(tc.engine(), tc.rules(), project)
    if "default_scope" in todo and info.effective != todo["default_scope"]:
        notes.append("A lock or project rule is narrowing this; the tighter setting always wins.")
    if info.tier_order and info.tier_order != "interleave":
        # HDW-516: the fallback order is a rules.yaml setting this tool cannot change, so say
        # so instead of letting a host believe the scope call covered it.
        notes.append(
            f"Fallback order is {info.tier_order}: it comes from router.tier_order and "
            "router.tier_direction in rules.yaml, which only the user edits."
        )
    return ScopeResult(ok=True, changed=sorted(todo), scope=info, notes=notes)


def _refusal(
    tc: ToolContext, project: ProjectConfig | None, change: Change, asked: bool
) -> ScopeResult:
    command = user_command(change.loosen)
    why = (
        "The user declined, so nothing was changed."
        if asked
        else "This host cannot ask the user, so nothing was changed."
    )
    return ScopeResult(
        ok=False,
        confirmation_required=True,
        user_command=command,
        scope=scope_info(tc.engine(), tc.rules(), project),
        notes=[why, "Loosening a lock is the user's decision: tell them to run the command."],
        error=ErrorInfo(
            code="confirmation_required",
            message="Loosening the scope or the Private lock needs the user's confirmation.",
            fix=f"Ask the user to run: {command}",
        ),
    )
