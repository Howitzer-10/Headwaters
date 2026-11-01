"""Register ``headwaters_check`` and the result resource; shared helpers of the tools.

``headwaters_check`` declares an ``outputSchema`` (from the result model), returns structured
content plus a short text rendering, and carries annotations. ``headwaters_do`` and
``headwaters_ask`` moved to ``frontdoor_run`` in HDW-206 (planning, approval, background jobs and
progress); the other tools are in ``frontdoor_more`` and ``frontdoor_advanced`` (HDW-205).
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from mcp import types
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ResourceError
from pydantic import Field

from headwaters.core.logging import get_logger
from headwaters.mcp.tools import descriptions
from headwaters.mcp.tools.check import run_check
from headwaters.mcp.tools.context import ToolContext
from headwaters.mcp.tools.failures import Failure, failure_info
from headwaters.mcp.tools.schemas import CheckResult, ErrorInfo
from headwaters.mcp.tools.store import RESULT_URI_PREFIX

__all__ = ["classify", "meta_of", "register_frontdoor", "tool_annotations"]

_log = get_logger("mcp.tools")

_INTERNAL = Failure(
    ErrorInfo(
        code="internal_error",
        message="Headwaters hit an unexpected problem.",
        fix="Try again; if it keeps happening run `headwaters doctor`.",
    )
)


def meta_of(ctx: Context) -> dict[str, Any] | None:
    meta = ctx.request_context.meta
    return dict(meta) if meta else None


def classify(exc: Exception, tool: str) -> Failure:
    known = failure_info(exc)
    if known is not None:
        return known
    _log.warning("tool failed", extra={"tool": tool, "error": type(exc).__name__})
    return _INTERNAL


def tool_annotations(title: str, *, read_only: bool, open_world: bool) -> types.ToolAnnotations:
    return types.ToolAnnotations(
        title=title,
        read_only_hint=read_only,
        destructive_hint=False,
        idempotent_hint=read_only,  # headwaters_do is not idempotent; the other two are
        open_world_hint=open_world,
    )


def register_frontdoor(server: MCPServer, tc: ToolContext) -> None:
    """Add ``headwaters_check`` and the ``headwaters://result/<id>`` resource to ``server``."""

    @server.tool(
        name="headwaters_check",
        title="Check a diff or code",
        description=descriptions.CHECK,
        annotations=tool_annotations("Check a diff or code", read_only=True, open_world=False),
    )
    async def headwaters_check(
        ctx: Context,
        diff: Annotated[str | None, Field(description="A unified diff to check.")] = None,
        code: Annotated[str | None, Field(description="Code to check.")] = None,
        language: Annotated[
            str | None, Field(description="Language of `code`, such as python.")
        ] = None,
    ) -> Annotated[types.CallToolResult, CheckResult]:
        return await run_check(tc, diff=diff, code=code, language=language, meta=meta_of(ctx))

    @server.resource(
        RESULT_URI_PREFIX + "{result_id}",
        name="headwaters_result",
        title="Full result of a Headwaters tool call",
        description="The whole answer and routing detail behind a result_uri.",
        mime_type="application/json",
    )
    def headwaters_result(result_id: str) -> str:
        detail = tc.store.get(result_id)
        if detail is None:
            raise ResourceError("Unknown or expired result. Run the tool again.")
        return json.dumps(detail, ensure_ascii=False, indent=2)
