"""Register the front-door tools of the shared daemon (HDW-204, v3.4 B1).

``headwaters_do``, ``headwaters_ask`` and ``headwaters_check``. Each declares an ``outputSchema``
(from the result model), returns structured content plus a short text rendering, and carries
annotations. HDW-205 adds the other five tools in the same way.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Annotated, Any

from mcp import types
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.server.mcpserver.exceptions import ResourceError
from pydantic import AliasChoices, Field

from headwaters.core.logging import get_logger
from headwaters.core.rules import Scope
from headwaters.mcp.tools import descriptions
from headwaters.mcp.tools.check import run_check
from headwaters.mcp.tools.context import ToolContext
from headwaters.mcp.tools.failures import Failure, failure_info
from headwaters.mcp.tools.flow import run_model_tool
from headwaters.mcp.tools.results import ask_envelope, ask_error, do_envelope, do_error
from headwaters.mcp.tools.schemas import AskResult, CheckResult, DoResult, ErrorInfo, Want
from headwaters.mcp.tools.store import RESULT_URI_PREFIX

__all__ = ["ASYNC_NOTE", "register_frontdoor"]

_log = get_logger("mcp.tools")

ASYNC_NOTE = "async was requested: background jobs arrive later, so this ran now and waited."
_INTERNAL = Failure(
    ErrorInfo(
        code="internal_error",
        message="Headwaters hit an unexpected problem.",
        fix="Try again; if it keeps happening run `headwaters doctor`.",
    )
)


def _meta(ctx: Context) -> dict[str, Any] | None:
    meta = ctx.request_context.meta
    return dict(meta) if meta else None


def _classify(exc: Exception, tool: str) -> Failure:
    known = failure_info(exc)
    if known is not None:
        return known
    _log.warning("tool failed", extra={"tool": tool, "error": type(exc).__name__})
    return _INTERNAL


def _annotations(title: str, *, read_only: bool, open_world: bool) -> types.ToolAnnotations:
    return types.ToolAnnotations(
        title=title,
        read_only_hint=read_only,
        destructive_hint=False,
        idempotent_hint=read_only,  # headwaters_do is not idempotent; the other two are
        open_world_hint=open_world,
    )


def register_frontdoor(server: MCPServer, tc: ToolContext) -> None:
    """Add the three tools and the ``headwaters://result/<id>`` resource to ``server``."""

    @server.tool(
        name="headwaters_do",
        title="Do a coding task",
        description=descriptions.DO,
        annotations=_annotations("Do a coding task", read_only=False, open_world=True),
    )
    async def headwaters_do(
        task: Annotated[str, Field(min_length=1, description="What to do, in plain words.")],
        ctx: Context,
        paths: Annotated[
            list[str] | None, Field(description="Files to work on, relative to the project.")
        ] = None,
        images: Annotated[
            list[str] | None, Field(description="Image files (PNG, JPEG, GIF, WebP) to look at.")
        ] = None,
        scope: Annotated[
            Scope | None, Field(description="Narrow the models: auto, free, local or cloud.")
        ] = None,
        want: Annotated[Want, Field(description="answer (default), diff or plan.")] = "answer",
        run_async: Annotated[
            bool,
            Field(
                validation_alias=AliasChoices("async", "run_async"),
                description="Run in the background (not available yet; runs now).",
            ),
        ] = False,
    ) -> Annotated[types.CallToolResult, DoResult]:
        try:
            outcome = await run_model_tool(
                tc,
                "do",
                task=task,
                want=want,
                paths=paths or [],
                images=images or [],
                scope=scope,
                meta=_meta(ctx),
                verify_always=True,
            )
        except Exception as exc:
            return do_error(_classify(exc, "do"))
        if run_async:
            outcome = replace(outcome, notes=(*outcome.notes, ASYNC_NOTE))
        return do_envelope(outcome)

    @server.tool(
        name="headwaters_ask",
        title="Ask about code",
        description=descriptions.ASK,
        annotations=_annotations("Ask about code", read_only=True, open_world=True),
    )
    async def headwaters_ask(
        question: Annotated[str, Field(min_length=1, description="The question.")],
        ctx: Context,
        paths: Annotated[
            list[str] | None, Field(description="Files to read, relative to the project.")
        ] = None,
        scope: Annotated[
            Scope | None, Field(description="Narrow the models: auto, free, local or cloud.")
        ] = None,
    ) -> Annotated[types.CallToolResult, AskResult]:
        try:
            outcome = await run_model_tool(
                tc,
                "ask",
                task=question,
                want="answer",
                paths=paths or [],
                images=[],
                scope=scope,
                meta=_meta(ctx),
                verify_always=False,
            )
        except Exception as exc:
            return ask_error(_classify(exc, "ask"))
        return ask_envelope(outcome)

    @server.tool(
        name="headwaters_check",
        title="Check a diff or code",
        description=descriptions.CHECK,
        annotations=_annotations("Check a diff or code", read_only=True, open_world=False),
    )
    async def headwaters_check(
        ctx: Context,
        diff: Annotated[str | None, Field(description="A unified diff to check.")] = None,
        code: Annotated[str | None, Field(description="Code to check.")] = None,
        language: Annotated[
            str | None, Field(description="Language of `code`, such as python.")
        ] = None,
    ) -> Annotated[types.CallToolResult, CheckResult]:
        return await run_check(tc, diff=diff, code=code, language=language, meta=_meta(ctx))

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
