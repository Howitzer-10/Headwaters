"""Request preparation for the router (HDW-117): messages, options, secrets, candidates.

Pure helpers. Nothing here stores or logs request text.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace

from headwaters.core.analyzer._extract import collect
from headwaters.core.analyzer.requirements import AnalyzerInput
from headwaters.core.executor.types import ExecOptions, Message
from headwaters.core.hardware.residency import ResidencyConfig, ResidencyInputs
from headwaters.core.policy.filters import Candidate
from headwaters.core.router_session import text_of
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
    top = text_of(request.system)
    if top and not any(m.get("role") == "system" for m in messages):
        messages.insert(0, {"role": "system", "content": top})
    return messages


def executor_options(request: RouteRequest) -> ExecOptions:
    if request.options is not None:
        return request.options
    return ExecOptions(
        json_schema=schema_of(request.response_format), max_tokens=request.max_output_tokens
    )


def scan_texts(request: RouteRequest) -> list[str]:
    """Every string leaf the secret scan must see (HDW-120).

    System blocks, content parts, OpenAI ``tool_calls[].function.arguments``, Anthropic
    ``tool_use.input`` and nested ``tool_result`` content: agent loops carry file contents and
    shell output exactly there.
    """
    return [t for t in collect(request.messages, request.system).scan_text if t]


def with_warm_preference(
    candidates: Sequence[Candidate], warm_model: str | None, config: ResidencyConfig | None = None
) -> tuple[Candidate, ...]:
    """Give the session's previous model the warm bonus (the policy turns it into a score term).

    Models Ollama already holds keep their own residency data; only a candidate without a warm
    flag gets the session preference.
    """
    if warm_model is None:
        return tuple(candidates)
    bonus = (config or ResidencyConfig()).warm_bonus
    out: list[Candidate] = []
    for c in candidates:
        if c.id == warm_model and not (c.residency is not None and c.residency.warm):
            c = replace(c, residency=ResidencyInputs(True, bonus, (), 0.0))
        out.append(c)
    return tuple(out)


def local_usable(candidates: Sequence[Candidate]) -> bool:
    """True when at least one local model could run right now (fits and not walled)."""
    return any(
        c.is_local and c.fit is not None and c.fit.usable and c.availability.usable
        for c in candidates
    )


def only_local(candidates: Sequence[Candidate]) -> tuple[Candidate, ...]:
    """Airplane mode: nothing may leave the machine."""
    return tuple(c for c in candidates if c.is_local)
