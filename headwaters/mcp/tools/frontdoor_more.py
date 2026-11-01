"""Register the second wave of front-door tools (HDW-205, v3.4 B1).

``headwaters_status``, ``headwaters_scope``, ``headwaters_handoff`` and ``headwaters_more``
(``headwaters_think`` moved to ``frontdoor_run`` in HDW-206 with the other model tools). The eight
advanced tools are in ``frontdoor_advanced`` and appear in ``tools/list`` only for a host that
called ``headwaters_more`` (progressive disclosure).

No ``from __future__ import annotations`` here on purpose: the scope tool's resolver is a local
variable, and the SDK evaluates the tool's annotations to find it, so they must not be strings.
"""

from typing import Annotated

from mcp import types
from mcp.server import MCPServer
from mcp.server.mcpserver import AcceptedElicitation, Context, ElicitationResult, Resolve
from pydantic import Field

from headwaters.core.rules import Scope, Strategy
from headwaters.mcp.disclosure import Disclosure, host_id
from headwaters.mcp.threads import run_blocking
from headwaters.mcp.tools import descriptions
from headwaters.mcp.tools.context import ToolContext
from headwaters.mcp.tools.failures import Failure
from headwaters.mcp.tools.flow import current_engine
from headwaters.mcp.tools.frontdoor import classify, meta_of, tool_annotations
from headwaters.mcp.tools.handoff import build_handoff
from headwaters.mcp.tools.render_more import (
    render_handoff,
    render_more,
    render_scope,
    render_status,
)
from headwaters.mcp.tools.results import envelope
from headwaters.mcp.tools.schemas import ErrorInfo
from headwaters.mcp.tools.schemas_more import (
    AdvancedToolInfo,
    HandoffResult,
    MoreResult,
    ScopeResult,
    StatusResult,
)
from headwaters.mcp.tools.scope_tool import Confirm, apply_scope, make_resolver
from headwaters.mcp.tools.status import build_status
from headwaters.mcp.tools.workspace import open_workspace

__all__ = ["register_more"]


def _error(failure: Failure) -> ErrorInfo:
    return failure.error


def register_more(
    server: MCPServer, tc: ToolContext, disclosure: Disclosure, advanced: dict[str, str]
) -> None:
    """Add the four tools. ``advanced`` maps each advanced tool name to its summary."""

    @server.tool(
        name="headwaters_status",
        title="Show Headwaters status",
        description=descriptions.STATUS,
        annotations=tool_annotations("Show Headwaters status", read_only=True, open_world=False),
    )
    async def headwaters_status(ctx: Context) -> Annotated[types.CallToolResult, StatusResult]:
        try:
            project = open_workspace(meta_of(ctx), tc.rules(), tc.configured_roots()).project
            result = await run_blocking(lambda: build_status(tc, project))
        except Exception as exc:
            error = _error(classify(exc, "status"))
            failed = StatusResult(ok=False, error=error)
            return envelope(failed, render_status(failed), is_error=True)
        return envelope(result, render_status(result))

    ask_user = make_resolver(tc)

    @server.tool(
        name="headwaters_scope",
        title="Set scope, lock and strategy",
        description=descriptions.SCOPE,
        annotations=types.ToolAnnotations(
            title="Set scope, lock and strategy",
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    async def headwaters_scope(
        ctx: Context,
        approval: Annotated[ElicitationResult[Confirm], Resolve(ask_user)],
        scope: Annotated[
            Scope | None, Field(description="auto, free, local or cloud. Omit to leave as is.")
        ] = None,
        private_lock: Annotated[
            bool | None, Field(description="True = local models only. Omit to leave as is.")
        ] = None,
        strategy: Annotated[
            Strategy | None, Field(description="cost_saver, quality_first or primary_first.")
        ] = None,
    ) -> Annotated[types.CallToolResult, ScopeResult]:
        # The resolver returns a plain marker string when no question was put to the user (nothing
        # loosens, or the host cannot be asked); the SDK wraps it as an "accepted" outcome whose
        # data is that string. Only a ``Confirm`` is the user's own answer.
        answer = approval.data if isinstance(approval, AcceptedElicitation) else None
        asked = isinstance(answer, Confirm) or not isinstance(approval, AcceptedElicitation)
        approved = isinstance(answer, Confirm) and answer.confirm
        try:
            project = open_workspace(meta_of(ctx), tc.rules(), tc.configured_roots()).project
            result = await apply_scope(
                tc,
                project,
                scope=scope,
                private_lock=private_lock,
                strategy=strategy,
                approved=approved,
                asked=asked,
            )
        except Exception as exc:
            failed = ScopeResult(ok=False, error=_error(classify(exc, "scope")))
            return envelope(failed, render_scope(failed), is_error=True)
        return envelope(result, render_scope(result), is_error=not result.ok)

    @server.tool(
        name="headwaters_handoff",
        title="Save a handoff packet",
        description=descriptions.HANDOFF,
        annotations=types.ToolAnnotations(
            title="Save a handoff packet",
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=False,
        ),
    )
    async def headwaters_handoff(
        ctx: Context,
        goal: Annotated[str, Field(min_length=1, description="What the work is for.")],
        paths: Annotated[
            list[str] | None, Field(description="Files involved, relative to the project.")
        ] = None,
        last_error: Annotated[str | None, Field(description="The last error seen.")] = None,
        recent_turns: Annotated[
            list[str] | None, Field(description="The last few turns, oldest first.")
        ] = None,
    ) -> Annotated[types.CallToolResult, HandoffResult]:
        try:
            engine = current_engine(tc)
            workspace = open_workspace(meta_of(ctx), tc.rules(), tc.configured_roots())
            result = await run_blocking(
                lambda: build_handoff(
                    tc,
                    engine,
                    workspace,
                    goal=goal,
                    paths=paths or [],
                    last_error=last_error or "",
                    recent_turns=recent_turns or [],
                    session=host_id(meta_of(ctx)),
                    scope=tc.rules().default_scope,
                    model=None,
                )
            )
        except Exception as exc:
            failed = HandoffResult(ok=False, error=_error(classify(exc, "handoff")))
            return envelope(failed, render_handoff(failed), is_error=True)
        return envelope(result, render_handoff(result))

    @server.tool(
        name="headwaters_more",
        title="Show the advanced tools",
        description=descriptions.MORE,
        annotations=types.ToolAnnotations(
            title="Show the advanced tools",
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    async def headwaters_more(ctx: Context) -> Annotated[types.CallToolResult, MoreResult]:
        host = host_id(meta_of(ctx))
        changed = disclosure.enable(host)
        if changed:
            await ctx.notify_tools_changed()
        notes = [
            "They are now in tools/list. If your host does not refresh, ask it to reload tools."
        ]
        result = MoreResult(
            tools=[AdvancedToolInfo(name=n, summary=s) for n, s in sorted(advanced.items())],
            enabled=True,
            notes=notes,
        )
        return envelope(result, render_more(result))
