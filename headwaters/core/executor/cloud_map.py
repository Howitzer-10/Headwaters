"""litellm <-> executor mapping helpers (HDW-115): headers, reasoning params, errors, chunks.

Pure functions over duck-typed litellm objects, so they are tested without importing litellm.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from headwaters.core.executor.errors import ProviderError
from headwaters.core.executor.types import Delta, ToolCallDelta, Usage
from headwaters.core.rules import AttributionRules

OPENROUTER = "openrouter"
DEFAULT_EFFORT = "medium"  # used when reasoning is simply switched on (True)
_MAX_ERROR_CHARS = 4096
_TIMEOUT_NAMES = frozenset({"Timeout", "APITimeoutError", "ReadTimeout"})
_CONNECTION_NAMES = frozenset({"APIConnectionError", "ServiceUnavailableError"})


def attribution_headers(attribution: AttributionRules) -> dict[str, str]:
    """OpenRouter app attribution (A-OR-10). Opt-in: nothing is sent unless ``referer`` is set,
    because Headwaters promises no telemetry. ``X-Title`` is still accepted by OpenRouter."""
    if not attribution.referer:
        return {}
    return {"HTTP-Referer": attribution.referer, "X-OpenRouter-Title": attribution.title}


def reasoning_kwargs(provider: str, reasoning: bool | str | None) -> dict[str, Any]:
    """The provider's reasoning parameter (A-OR-7): OpenRouter's unified ``reasoning`` object
    through ``extra_body``; everything else through litellm's ``reasoning_effort``."""
    if reasoning is None:
        return {}
    if provider == OPENROUTER:
        if reasoning is False:
            return {"extra_body": {"reasoning": {"enabled": False}}}
        effort = DEFAULT_EFFORT if reasoning is True else reasoning
        return {"extra_body": {"reasoning": {"effort": effort}}}
    if reasoning is False:
        return {}
    return {"reasoning_effort": DEFAULT_EFFORT if reasoning is True else reasoning}


def _header_map(exc: BaseException) -> Mapping[str, str]:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return {}
    try:
        return {str(k): str(v) for k, v in headers.items()}
    except (AttributeError, TypeError):
        return {}


def map_exception(exc: BaseException) -> ProviderError:
    """litellm exception -> ``ProviderError`` (status, headers and body for the wall classifier)."""
    name = type(exc).__name__
    status = getattr(exc, "status_code", None)
    kind = "http"
    if not isinstance(status, int) or isinstance(status, bool):
        if name in _TIMEOUT_NAMES:
            status, kind = 408, "timeout"
        elif name in _CONNECTION_NAMES:
            status, kind = 0, "connection"
        else:
            status = 500
    body = str(getattr(exc, "message", None) or exc)[:_MAX_ERROR_CHARS]
    return ProviderError(status, _header_map(exc), body, kind=kind)


def _usage(value: object) -> Usage | None:
    if value is None:
        return None
    prompt = getattr(value, "prompt_tokens", None)
    completion = getattr(value, "completion_tokens", None)
    return Usage(
        prompt_tokens=prompt if isinstance(prompt, int) else None,
        completion_tokens=completion if isinstance(completion, int) else None,
    )


def delta_from_chunk(chunk: object) -> Delta:
    choices = getattr(chunk, "choices", None) or []
    usage = _usage(getattr(chunk, "usage", None))
    if not choices:
        return Delta(usage=usage)
    choice = choices[0]
    finish = getattr(choice, "finish_reason", None)
    if finish == "error":  # OpenRouter reports a mid-stream failure this way (A-OR-4)
        raise ProviderError(500, None, str(getattr(chunk, "error", "") or ""), kind="stream_error")
    delta = getattr(choice, "delta", None)
    content = getattr(delta, "content", None) or ""
    reasoning = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None) or ""
    calls: list[ToolCallDelta] = []
    for position, call in enumerate(getattr(delta, "tool_calls", None) or []):
        function = getattr(call, "function", None)
        index = getattr(call, "index", None)
        calls.append(
            ToolCallDelta(
                index=index if isinstance(index, int) else position,
                id=getattr(call, "id", None),
                name=getattr(function, "name", None),
                arguments=getattr(function, "arguments", None) or "",
            )
        )
    return Delta(
        content=str(content),
        reasoning=str(reasoning),
        tool_calls=tuple(calls),
        finish_reason=finish if isinstance(finish, str) else None,
        usage=usage,
    )
