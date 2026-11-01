"""Register ``headwaters_do`` and ``headwaters_ask`` (HDW-204, HDW-206, v3.4 B1).

These are the model tools. Each call is planned first (a dry run: no model is called), then:

- it asks the user (MCP elicitation) when the expected spend is over ``budget.confirm_above_usd``
  or the call asks for a wider scope than ``default_scope``. A host that cannot be asked gets a
  ``confirmation_required`` result instead and ``force: true`` stands in only for that host;
- it runs in the background (``job_id``) when it is expected to take longer than
  ``mcp.async_threshold_s``, or when ``async`` is true;
- otherwise it runs now and sends progress notifications while it works.

No ``from __future__ import annotations`` here on purpose: the approval resolvers are local
functions, and the SDK evaluates the tools' annotations to find them, so they must not be strings.
"""

from typing import Annotated, Any

from mcp import types
from mcp.server import MCPServer
from mcp.server.mcpserver import Context, ElicitationResult, Resolve
from pydantic import AliasChoices, Field

from headwaters.core.rules import Scope
from headwaters.mcp.tools import descriptions, responders
from headwaters.mcp.tools.context import ToolContext
from headwaters.mcp.tools.frontdoor import meta_of, tool_annotations
from headwaters.mcp.tools.guarded import Agree, Gate, execute, verdict
from headwaters.mcp.tools.schemas import AskResult, DoResult, Want
from headwaters.mcp.tools.schemas_more import ThinkResult

__all__ = ["ASYNC_HELP", "FORCE_HELP", "register_run"]

FORCE_HELP = (
    "Set true ONLY after the user agreed to the spend or the wider scope this call was refused "
    "for. Ignored when the host can ask the user itself."
)
ASYNC_HELP = "Run in the background and return a job_id (poll headwaters_job_status)."


def register_run(server: MCPServer, tc: ToolContext, gate: Gate) -> None:
    """Add ``headwaters_do`` and ``headwaters_ask`` to ``server``."""

    async def approve_do(
        ctx: Context,
        task: str,
        paths: list[str] | None,
        images: list[str] | None,
        scope: Scope | None,
        want: Want,
        force: bool,
    ) -> Any:
        return await gate.approve(
            ctx,
            responders.DO,
            force=force,
            task=task,
            want=want,
            paths=paths or [],
            images=images or [],
            scope=scope,
            meta=meta_of(ctx),
            verify_always=True,
        )

    @server.tool(
        name="headwaters_do",
        title="Do a coding task",
        description=descriptions.DO,
        annotations=tool_annotations("Do a coding task", read_only=False, open_world=True),
    )
    async def headwaters_do(
        task: Annotated[str, Field(min_length=1, description="What to do, in plain words.")],
        ctx: Context,
        approval: Annotated[ElicitationResult[Agree], Resolve(approve_do)],
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
        force: Annotated[bool, Field(description=FORCE_HELP)] = False,
        run_async: Annotated[
            bool,
            Field(validation_alias=AliasChoices("async", "run_async"), description=ASYNC_HELP),
        ] = False,
    ) -> Annotated[types.CallToolResult, DoResult]:
        plan = await gate.plan_for(
            ctx,
            responders.DO,
            task=task,
            want=want,
            paths=paths or [],
            images=images or [],
            scope=scope,
            meta=meta_of(ctx),
            verify_always=True,
        )
        return await execute(tc, responders.DO, plan, verdict(approval), ctx, run_async=run_async)

    def think_request(
        ctx: Context, question: str, paths: list[str] | None, scope: Scope | None
    ) -> dict[str, Any]:
        return {
            "task": question,
            "want": "answer",
            "paths": paths or [],
            "images": [],
            "scope": scope,
            "meta": meta_of(ctx),
            "verify_always": False,
            "task_hint": "reasoning",
            "reasoning": True,
        }

    async def approve_think(
        ctx: Context, question: str, paths: list[str] | None, scope: Scope | None, force: bool
    ) -> Any:
        request = think_request(ctx, question, paths, scope)
        return await gate.approve(ctx, responders.THINK, force=force, **request)

    @server.tool(
        name="headwaters_think",
        title="Think hard about a question",
        description=descriptions.THINK,
        annotations=tool_annotations(
            "Think hard about a question", read_only=True, open_world=True
        ),
    )
    async def headwaters_think(
        question: Annotated[str, Field(min_length=1, description="The question.")],
        ctx: Context,
        approval: Annotated[ElicitationResult[Agree], Resolve(approve_think)],
        paths: Annotated[
            list[str] | None, Field(description="Files to read, relative to the project.")
        ] = None,
        scope: Annotated[
            Scope | None, Field(description="Narrow the models: auto, free, local or cloud.")
        ] = None,
        force: Annotated[bool, Field(description=FORCE_HELP)] = False,
        run_async: Annotated[
            bool,
            Field(validation_alias=AliasChoices("async", "run_async"), description=ASYNC_HELP),
        ] = False,
    ) -> Annotated[types.CallToolResult, ThinkResult]:
        request = think_request(ctx, question, paths, scope)
        plan = await gate.plan_for(ctx, responders.THINK, **request)
        answer = verdict(approval)
        return await execute(tc, responders.THINK, plan, answer, ctx, run_async=run_async)

    async def approve_ask(
        ctx: Context,
        question: str,
        paths: list[str] | None,
        scope: Scope | None,
        force: bool,
    ) -> Any:
        return await gate.approve(
            ctx,
            responders.ASK,
            force=force,
            task=question,
            want="answer",
            paths=paths or [],
            images=[],
            scope=scope,
            meta=meta_of(ctx),
            verify_always=False,
        )

    @server.tool(
        name="headwaters_ask",
        title="Ask about code",
        description=descriptions.ASK,
        annotations=tool_annotations("Ask about code", read_only=True, open_world=True),
    )
    async def headwaters_ask(
        question: Annotated[str, Field(min_length=1, description="The question.")],
        ctx: Context,
        approval: Annotated[ElicitationResult[Agree], Resolve(approve_ask)],
        paths: Annotated[
            list[str] | None, Field(description="Files to read, relative to the project.")
        ] = None,
        scope: Annotated[
            Scope | None, Field(description="Narrow the models: auto, free, local or cloud.")
        ] = None,
        force: Annotated[bool, Field(description=FORCE_HELP)] = False,
        run_async: Annotated[
            bool,
            Field(validation_alias=AliasChoices("async", "run_async"), description=ASYNC_HELP),
        ] = False,
    ) -> Annotated[types.CallToolResult, AskResult]:
        plan = await gate.plan_for(
            ctx,
            responders.ASK,
            task=question,
            want="answer",
            paths=paths or [],
            images=[],
            scope=scope,
            meta=meta_of(ctx),
            verify_always=False,
        )
        return await execute(tc, responders.ASK, plan, verdict(approval), ctx, run_async=run_async)
