"""Register the nine advanced tools (HDW-205, v3.4 B1; ``headwaters_job_cancel`` is HDW-146).

They are registered like any tool, but ``Disclosure`` keeps them out of ``tools/list`` until the
host calls ``headwaters_more``. Each is local: no model is called and nothing leaves the machine.
None can widen a scope or a lock; ``pin_model`` only picks among models the scope allows.
"""

from __future__ import annotations

from typing import Annotated

from mcp import types
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from pydantic import BaseModel, Field

from headwaters.core.rules import Scope
from headwaters.mcp.threads import run_blocking
from headwaters.mcp.tools import advanced, descriptions, recommend
from headwaters.mcp.tools.context import ToolContext
from headwaters.mcp.tools.flow import current_engine
from headwaters.mcp.tools.frontdoor import classify, meta_of, tool_annotations
from headwaters.mcp.tools.render import render_error
from headwaters.mcp.tools.render_more import (
    render_local_models,
    render_quota,
    render_recommend,
    render_switch,
)
from headwaters.mcp.tools.results import envelope
from headwaters.mcp.tools.schemas import ErrorInfo
from headwaters.mcp.tools.schemas_more import (
    FeedbackResult,
    JobResult,
    LocalModelsResult,
    PinResult,
    QuotaResult,
    RecommendResult,
    SwitchHostResult,
)

__all__ = ["ADVANCED_NAMES", "register_advanced"]

ADVANCED_NAMES = tuple(descriptions.ADVANCED)


def _fail(model: type[BaseModel], tool: str, exc: Exception) -> types.CallToolResult:
    error: ErrorInfo = classify(exc, tool).error
    return envelope(
        model.model_validate({"ok": False, "error": error}), render_error(error), is_error=True
    )


def _plain(result: BaseModel, ok: bool, notes: list[str]) -> types.CallToolResult:
    text = "\n".join(notes) if notes else ("Done." if ok else "Not done.")
    return envelope(result, text, is_error=not ok)


def register_advanced(server: MCPServer, tc: ToolContext) -> None:
    def ann(title: str, *, read_only: bool) -> types.ToolAnnotations:
        return tool_annotations(title, read_only=read_only, open_world=False)

    @server.tool(
        name="headwaters_recommend_model",
        title="Recommend a model",
        description=descriptions.ADVANCED["headwaters_recommend_model"],
        annotations=ann("Recommend a model", read_only=True),
    )
    async def headwaters_recommend_model(
        ctx: Context,
        task: Annotated[str, Field(min_length=1, description="A short summary of the task.")],
        scope: Annotated[Scope | None, Field(description="auto, free, local or cloud.")] = None,
        needs_vision: Annotated[bool, Field(description="The task includes an image.")] = False,
        needs_tools: Annotated[bool, Field(description="The host sends tool calls.")] = False,
        paths: Annotated[
            list[str] | None,
            Field(description="Project files the task will use; read only to size the request."),
        ] = None,
    ) -> Annotated[types.CallToolResult, RecommendResult]:
        try:
            engine = current_engine(tc)
            result = await recommend.recommend(
                engine,
                tc,
                task,
                scope,
                vision=needs_vision,
                tools=needs_tools,
                paths=paths or (),
                meta=meta_of(ctx),
            )
        except Exception as exc:
            return _fail(RecommendResult, "recommend_model", exc)
        return envelope(result, render_recommend(result), is_error=not result.ok)

    @server.tool(
        name="headwaters_should_switch_host",
        title="Should the host switch model?",
        description=descriptions.ADVANCED["headwaters_should_switch_host"],
        annotations=ann("Should the host switch model?", read_only=True),
    )
    async def headwaters_should_switch_host(
        current_model: Annotated[str, Field(min_length=1, description="The host's model now.")],
        task: Annotated[str | None, Field(description="A short summary of the work.")] = None,
        scope: Annotated[Scope | None, Field(description="auto, free, local or cloud.")] = None,
    ) -> Annotated[types.CallToolResult, SwitchHostResult]:
        try:
            engine = current_engine(tc)
            result = await recommend.should_switch(engine, tc, current_model, task, scope)
        except Exception as exc:
            return _fail(SwitchHostResult, "should_switch_host", exc)
        return envelope(result, render_switch(result), is_error=not result.ok)

    @server.tool(
        name="headwaters_list_local_models",
        title="List local models",
        description=descriptions.ADVANCED["headwaters_list_local_models"],
        annotations=ann("List local models", read_only=True),
    )
    async def headwaters_list_local_models() -> Annotated[types.CallToolResult, LocalModelsResult]:
        try:
            result = await run_blocking(lambda: advanced.list_local(tc, tc.engine()))
        except Exception as exc:
            return _fail(LocalModelsResult, "list_local_models", exc)
        return envelope(result, render_local_models(result))

    @server.tool(
        name="headwaters_quota_status",
        title="Show quotas and walls",
        description=descriptions.ADVANCED["headwaters_quota_status"],
        annotations=ann("Show quotas and walls", read_only=True),
    )
    async def headwaters_quota_status() -> Annotated[types.CallToolResult, QuotaResult]:
        try:
            result = await run_blocking(lambda: advanced.quota_status(tc, tc.engine()))
        except Exception as exc:
            return _fail(QuotaResult, "quota_status", exc)
        return envelope(result, render_quota(result))

    @server.tool(
        name="headwaters_job_status",
        title="Job status",
        description=descriptions.ADVANCED["headwaters_job_status"],
        annotations=ann("Job status", read_only=True),
    )
    async def headwaters_job_status(
        job_id: Annotated[str, Field(min_length=1, description="The job id.")],
    ) -> Annotated[types.CallToolResult, JobResult]:
        try:
            result = advanced.job_status(tc, job_id)
        except Exception as exc:
            return _fail(JobResult, "job_status", exc)
        return _job_envelope(result)

    @server.tool(
        name="headwaters_job_result",
        title="Job result",
        description=descriptions.ADVANCED["headwaters_job_result"],
        annotations=ann("Job result", read_only=True),
    )
    async def headwaters_job_result(
        job_id: Annotated[str, Field(min_length=1, description="The job id.")],
    ) -> Annotated[types.CallToolResult, JobResult]:
        try:
            result = advanced.job_result(tc, job_id)
        except Exception as exc:
            return _fail(JobResult, "job_result", exc)
        return _job_envelope(result)

    @server.tool(
        name="headwaters_job_cancel",
        title="Cancel a job",
        description=descriptions.ADVANCED["headwaters_job_cancel"],
        annotations=types.ToolAnnotations(
            title="Cancel a job",
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    async def headwaters_job_cancel(
        job_id: Annotated[str, Field(min_length=1, description="The job id.")],
    ) -> Annotated[types.CallToolResult, JobResult]:
        try:
            result = await advanced.cancel_job(tc, job_id)
        except Exception as exc:
            return _fail(JobResult, "job_cancel", exc)
        return _job_envelope(result)

    @server.tool(
        name="headwaters_pin_model",
        title="Pin a model",
        description=descriptions.ADVANCED["headwaters_pin_model"],
        annotations=types.ToolAnnotations(
            title="Pin a model",
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    async def headwaters_pin_model(
        model: Annotated[str | None, Field(description="Model id. Omit to clear the pin.")] = None,
    ) -> Annotated[types.CallToolResult, PinResult]:
        try:
            result = await run_blocking(lambda: advanced.pin_model(tc, model))
        except Exception as exc:
            return _fail(PinResult, "pin_model", exc)
        text = result.error.message if result.error else "; ".join(result.notes)
        return _plain(result, result.ok, [text])

    @server.tool(
        name="headwaters_feedback",
        title="Give feedback on an answer",
        description=descriptions.ADVANCED["headwaters_feedback"],
        annotations=types.ToolAnnotations(
            title="Give feedback on an answer",
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    async def headwaters_feedback(
        decision_id: Annotated[int, Field(ge=1, description="From an earlier result.")],
        rating: Annotated[str, Field(description="up or down.")],
    ) -> Annotated[types.CallToolResult, FeedbackResult]:
        try:
            result = await run_blocking(lambda: advanced.give_feedback(tc, decision_id, rating))
        except Exception as exc:
            return _fail(FeedbackResult, "feedback", exc)
        text = result.error.message if result.error else "Thanks, recorded."
        return _plain(result, result.ok, [text])


def _job_envelope(result: JobResult) -> types.CallToolResult:
    if result.error is not None:
        return envelope(result, render_error(result.error), is_error=True)
    line = f"Job {result.job_id}: {result.status}"
    if result.progress is not None:
        line += f" ({int(result.progress * 100)}%)"
    if result.message:
        line += f" - {result.message}"
    lines = [line, *result.notes]
    kept = result.result or {}
    if kept.get("text"):  # the answer the foreground call would have shown
        lines = [str(kept["text"]), line]
    return envelope(result, "\n".join(lines))
