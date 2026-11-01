"""Request preparation for the router (HDW-117): messages, options, secrets, candidates.

Pure helpers. Nothing here stores or logs request text.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from headwaters.core.analyzer._extract import collect
from headwaters.core.analyzer.requirements import KNOWN_TASKS, AnalyzerInput, Requirements
from headwaters.core.executor.types import ExecOptions, ExecResult, Message, StreamEvent
from headwaters.core.policy.filters import Candidate
from headwaters.core.router_session import content_text
from headwaters.core.router_types import RouteRequest
from headwaters.core.router_verify import schema_of


def analyzer_input(request: RouteRequest) -> AnalyzerInput:
    return AnalyzerInput(
        messages=request.messages,
        system=request.system,
        tools=request.tools,
        response_format=request.response_format,
        file_paths=request.file_paths,
        headers=request.headers,
        max_output_tokens=request.max_output_tokens,
    )


def executor_messages(request: RouteRequest) -> list[Message]:
    """Messages for the executor. An Anthropic top-level ``system`` becomes a system message."""
    messages: list[Message] = list(request.messages)
    top = content_text(request.system)
    if top and not any(m.get("role") == "system" for m in messages):
        messages.insert(0, {"role": "system", "content": top})
    return messages


def effective_reasoning(request: RouteRequest) -> bool | str | None:
    """``request.reasoning``, else the reasoning of an explicit ``options`` object (HDW-140)."""
    if request.reasoning is not None:
        return request.reasoning
    return request.options.reasoning if request.options is not None else None


def executor_options(request: RouteRequest) -> ExecOptions:
    if request.options is not None:
        if request.reasoning is not None and request.options.reasoning is None:
            return replace(request.options, reasoning=request.reasoning)
        return request.options
    return ExecOptions(
        reasoning=request.reasoning,
        json_schema=schema_of(request.response_format),
        max_tokens=request.max_output_tokens,
    )


def apply_request_hints(reqs: Requirements, request: RouteRequest) -> Requirements:
    """Caller hints on top of the analysis (HDW-140, D12).

    ``task_hint`` forces ``soft.task`` and nothing else (the hard filter is untouched, so the
    tool does not depend on keyword hits). Reasoning switched on (True or an effort string) adds
    the hard requirement that the model can reason (``ModelRow.reasoning_ok``).
    """
    hint = request.task_hint
    if hint is not None and hint not in KNOWN_TASKS:
        raise ValueError(f"unknown task_hint {hint!r}; expected one of {', '.join(KNOWN_TASKS)}")
    wants = bool(effective_reasoning(request))
    if hint is None and not wants:
        return reqs
    soft = reqs.soft if hint is None else replace(reqs.soft, task=hint)
    hard = replace(reqs.hard, reasoning=True) if wants else reqs.hard
    return replace(reqs, soft=soft, hard=hard)


def without_reasoning(result: ExecResult) -> ExecResult:
    """The reasoning rule (D12): reasoning text is never returned, logged or stored."""
    return replace(result, reasoning="") if result.reasoning else result


def clean_event(event: StreamEvent) -> StreamEvent | None:
    """A stream event as it may leave the router: no reasoning text; ``None`` drops the event."""
    if event.kind == "reasoning":
        return None
    if event.kind == "done" and event.result is not None and event.result.reasoning:
        return replace(event, result=without_reasoning(event.result))
    return event


def scan_texts(request: RouteRequest) -> list[str]:
    """Every string leaf the secret scan must see (HDW-120).

    System blocks, content parts, OpenAI ``tool_calls[].function.arguments``, Anthropic
    ``tool_use.input`` and nested ``tool_result`` content: agent loops carry file contents and
    shell output exactly there.
    """
    return [t for t in collect(request.messages, request.system).scan_text if t]


def with_session_model(
    candidates: Sequence[Candidate], session_model: str | None
) -> tuple[Candidate, ...]:
    """Mark the session's last successful model (D11, F15).

    Only the ``is_session`` flag changes: residency (warm bonus, eviction penalty) stays what
    the hardware says, and a cloud model never gets a VRAM bonus. The policy turns the flag
    into the small ``session_bonus`` term and keeps the model first while it meets the floor.
    """
    if session_model is None:
        return tuple(candidates)
    return tuple(replace(c, is_session=True) if c.id == session_model else c for c in candidates)


def local_usable(candidates: Sequence[Candidate]) -> bool:
    """True when at least one local model could run right now (fits and not walled)."""
    return any(
        c.is_local and c.fit is not None and c.fit.usable and c.availability.usable
        for c in candidates
    )


def only_local(candidates: Sequence[Candidate]) -> tuple[Candidate, ...]:
    """Airplane mode: nothing may leave the machine."""
    return tuple(c for c in candidates if c.is_local)
