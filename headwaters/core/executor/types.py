"""Executor data types and the backend protocol (HDW-115)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from headwaters.core.executor.errors import ExecutorError
from headwaters.core.scope import TierName

Message = Mapping[str, Any]  # OpenAI-style chat message
ToolSpec = Mapping[str, Any]  # OpenAI-style tool: {"type": "function", "function": {...}}


@dataclass(frozen=True)
class ExecTarget:
    """What to call for one decision. ``route`` is a litellm model string (cloud) or an Ollama
    model name (local). ``num_ctx`` is required for local calls. ``cold`` is True/None when the
    local model is not known to be loaded (None = unknown, treated as cold)."""

    model_id: str
    provider: str
    route: str
    num_ctx: int | None = None
    cold: bool | None = None
    api_base: str | None = None


@dataclass(frozen=True)
class ExecOptions:
    """Per-call options. ``reasoning``: None = provider default, False = off, True or an
    effort string (minimal/low/medium/high/xhigh) = on. ``json_schema`` asks for structured
    output (Ollama ``format``, or an OpenAI ``response_format``)."""

    reasoning: bool | str | None = None
    json_schema: Mapping[str, Any] | None = None
    max_tokens: int | None = None
    temperature: float | None = None


@dataclass(frozen=True)
class ExecutorConfig:
    """Timeouts in seconds. The first-token limit is longer for a cold local model, because the
    model must be loaded (and the prompt processed) before anything streams."""

    cloud_first_token_s: float = 30.0
    local_warm_first_token_s: float = 60.0
    local_cold_first_token_s: float = 240.0
    stream_idle_s: float = 60.0  # max silence between tokens once streaming
    connect_s: float = 5.0


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str  # JSON text


@dataclass(frozen=True)
class ToolCallDelta:
    index: int
    id: str | None = None
    name: str | None = None
    arguments: str = ""  # a fragment; fragments with the same index are concatenated


@dataclass(frozen=True)
class Delta:
    """One normalised piece of a provider stream."""

    content: str = ""
    reasoning: str = ""
    tool_calls: tuple[ToolCallDelta, ...] = ()
    finish_reason: str | None = None
    usage: Usage | None = None


@dataclass(frozen=True)
class BackendRequest:
    target: ExecTarget
    messages: Sequence[Message]
    tools: Sequence[ToolSpec] | None
    options: ExecOptions
    keep_alive: str
    headers: Mapping[str, str] = field(default_factory=dict)
    api_key: str | None = None


class Backend(Protocol):
    """Streams normalised deltas. Raises ``ProviderError`` (or ``ExecutorError``) on failure."""

    def stream(self, request: BackendRequest) -> AsyncIterator[Delta]: ...


StreamKind = Literal["first_token", "reasoning", "content", "tool_call", "done", "error"]


@dataclass(frozen=True)
class ExecResult:
    """The whole answer. ``reasoning`` is kept apart from ``content``."""

    model: str
    tier: TierName
    content: str
    reasoning: str
    tool_calls: tuple[ToolCall, ...]
    finish_reason: str
    usage: Usage
    first_token_s: float | None


@dataclass(frozen=True)
class StreamEvent:
    """``first_token`` fires once, before the first reasoning/content/tool output. Callers may
    fail over only until they have seen it; after it, failures arrive as an ``error`` event."""

    kind: StreamKind
    text: str = ""
    tool_call: ToolCall | None = None
    first_token_s: float | None = None
    result: ExecResult | None = None
    error: ExecutorError | None = None
