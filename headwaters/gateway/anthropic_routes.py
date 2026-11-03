"""The Anthropic-compatible face of the gateway (HDW-303): ``POST /v1/messages``.

Claude Code and the Anthropic SDKs speak the Messages API, so the daemon offers that face beside
the OpenAI one, on the same guarded loopback port: pointing ``ANTHROPIC_BASE_URL`` at the gateway
and using the daemon token as the API key is the whole setup (see ``README.md``).

The translation is hand-written. D7 chose the ``litellm`` SDK *inside* the cloud executor, not
LiteLLM's proxy: the router owns failover, verification and the routing headers, and giving
another translator that job would put a second policy engine in the path (D7, D14).

Translated: ``system``, text/image/``tool_use``/``tool_result`` content blocks and tool
definitions (``input_schema``) on the way in; text and ``tool_use`` blocks plus the stop reason on
the way out; and the Messages stream (``message_start`` ... ``message_stop``), with an ``error``
event for a failure after the first token.

Not translatable yet, because the core request carries no such fields: ``stop_sequences``,
``top_p``, ``top_k``, ``tool_choice`` and ``metadata`` (FU-303-2). ``input_tokens`` is only known
once the provider reports usage, so ``message_start`` says 0 and the real count arrives on
``message_delta``.

``POST /v1/messages/count_tokens`` is served (HDW-408) and is local arithmetic: the same body
parsing as ``/v1/messages``, no route, no model choice and no provider call, so it answers even
when no model can serve anything. The number is ``Analyzer.estimate_tokens``'s estimate rather
than a provider-exact count, which is the right answer for an advisory budget figure.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Callable, Coroutine, Mapping
from typing import Any, cast

from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse

from headwaters.core.analyzer import Analyzer
from headwaters.core.analyzer._extract import collect, dumps
from headwaters.core.executor.types import ExecOptions, ExecResult, ToolCall
from headwaters.core.logging import get_logger
from headwaters.core.router_types import RouteEvent, RouteRequest, RouteResult
from headwaters.core.rules import Rules
from headwaters.core.scope import ScopeError
from headwaters.gateway.aliases import requested
from headwaters.gateway.shared import (
    SERVING_ERRORS,
    RouterLike,
    error_details,
    meta_headers,
)

__all__ = ["COUNT_TOKENS_PATH", "MESSAGES_PATH", "count_tokens", "messages"]

MESSAGES_PATH = "/v1/messages"
COUNT_TOKENS_PATH = "/v1/messages/count_tokens"
MESSAGE_OBJECT = "message"
EVENT_STREAM = "text/event-stream"
NOT_READY_STATUS = 529
# Reasoning is never replayed (D12): these blocks are accepted and dropped, not rejected.
REASONING_BLOCKS = ("thinking", "redacted_thinking")
ERROR_TYPES = {
    400: "invalid_request_error",
    401: "authentication_error",
    403: "permission_error",
    404: "not_found_error",
    413: "request_too_large",
    429: "rate_limit_error",
    500: "api_error",
    529: "overloaded_error",
}
STOP_REASONS = {
    "stop": "end_turn",
    "end_turn": "end_turn",
    "stop_sequence": "stop_sequence",
    "length": "max_tokens",
    "max_tokens": "max_tokens",
    "tool_calls": "tool_use",
    "function_call": "tool_use",
    "tool_use": "tool_use",
    "content_filter": "refusal",
    "refusal": "refusal",
}
_log = get_logger("gateway")


class MessageError(ValueError):
    """A request the Messages API allows but this face cannot map onto a route yet."""


async def messages(
    request: Request,
    router: Callable[[], RouterLike | None],
    rules: Callable[[], Rules],
) -> Response:
    """One ``POST /v1/messages``: validate, route through the live router, translate."""
    body = await _json_body(request)
    if isinstance(body, Response):
        return body
    model = body.get("model")
    if not isinstance(model, str) or not model.strip():
        return _error(400, "model: Field required")
    live_rules = rules()
    try:
        wanted = _request_for(body, model.strip(), request.headers, live_rules)
    except ScopeError as exc:
        return _failure(exc)
    except MessageError as exc:
        return _error(400, str(exc))
    live = router()
    if live is None:
        return _error(NOT_READY_STATUS, "The daemon is still starting; retry.")
    try:
        if wanted.stream:
            events = cast("AsyncIterator[RouteEvent]", live.handle(wanted))
            return await _start_stream(events, live_rules)
        result = await cast("Coroutine[Any, Any, RouteResult]", live.handle(wanted))
    except SERVING_ERRORS as exc:
        return _failure(exc)
    except Exception:  # a broken engine must not hand the client a traceback
        _log.exception("gateway messages failed")
        return _error(500, "The gateway could not serve the request.")
    return JSONResponse(
        _message(result),
        headers=meta_headers(result.decision, live_rules, result=result),
    )


# The default hints are the ones a catalog with no overrides gives, so counting needs no catalog.
_COUNTER = Analyzer()


async def count_tokens(request: Request) -> Response:
    """One ``POST /v1/messages/count_tokens``: parse like the messages face, then count.

    Nothing here routes, picks a model or calls a provider, so it answers even while the daemon
    has no model to serve with. The body is read by the same helpers as ``/v1/messages``, so a
    body that route accepts is counted the same way and one it refuses is refused here too.
    """
    body = await _json_body(request)
    if isinstance(body, Response):
        return body
    try:
        messages = _messages(body)
        tools = _tools(body.get("tools"))
    except MessageError as exc:
        return _error(400, str(exc))
    return JSONResponse({"input_tokens": _count(messages, tools)})


def _count(messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None) -> int:
    """The analyzer's own rule: prose at 4 chars/token, code and tools at 3.5, images per hint."""
    collected = collect(messages, None)
    tools_chars = len(dumps(tools)) if tools else 0
    return _COUNTER.estimate_tokens(
        collected.all_text, collected.code_extra_chars + tools_chars, collected.image_count
    )


def _request_for(
    body: Mapping[str, Any],
    model: str,
    headers: Mapping[str, str],
    live_rules: Rules,
) -> RouteRequest:
    # Both credentials are the daemon's own secret: neither is a routing hint or part of the
    # session key, so neither ever reaches the router.
    seen = {
        key.lower(): value
        for key, value in headers.items()
        if key.lower() not in ("authorization", "x-api-key")
    }
    # HDW-304: the same alias table, picker syntax and ``x-headwaters-pin`` as the OpenAI face.
    asked = requested(model, seen, live_rules)
    parsed = asked.parsed
    max_tokens = _int_or_none(body.get("max_tokens"))
    temperature = _float_or_none(body.get("temperature"))
    options: ExecOptions | None = None
    if temperature is not None:
        # The one sampling option the core carries; the Messages API requires ``max_tokens``.
        options = ExecOptions(max_tokens=max_tokens, temperature=temperature)
    return RouteRequest(
        messages=_messages(body),
        surface="gateway",
        tools=_tools(body.get("tools")),
        headers=seen,
        max_output_tokens=max_tokens,
        stream=body.get("stream") is True,
        scope=parsed.scope if parsed is not None else None,
        strategy=parsed.strategy if parsed is not None else None,
        verify=parsed.verify if parsed is not None else None,
        # A model the picker does not know and the alias table does not map
        # (``claude-sonnet-4-5`` from Claude Code) is a request for that exact model: it passes
        # through, with the hard filter and walls still applying. ``x-headwaters-pin: true``
        # stops the failover when the call fails.
        pin=asked.pin,
        pin_strict=asked.strict,
        session_id=seen.get("x-session-id") or None,
        options=options,
    )


# ------------------------------------------------------------------------------- request side


def _messages(body: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The whole conversation as the OpenAI-shaped messages the router already serves."""
    source = body.get("messages")
    if not isinstance(source, list) or not source:
        raise MessageError("messages: a non-empty list of messages is required")
    out: list[dict[str, Any]] = []
    system = _system_text(body.get("system"))
    if system:
        out.append({"role": "system", "content": system})
    for raw in source:
        if not isinstance(raw, Mapping):
            raise MessageError("messages: every message must be an object")
        role = raw.get("role")
        if role not in ("user", "assistant"):
            raise MessageError(f"messages: role {role!r} is not 'user' or 'assistant'")
        content = raw.get("content")
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue
        if not isinstance(content, list):
            raise MessageError("messages: content must be a string or a list of blocks")
        if role == "assistant":
            out.append(_assistant(content))
        else:
            out.extend(_user(content))
    return out


def _system_text(system: Any) -> str:
    if system is None:
        return ""
    if isinstance(system, str):
        return system
    if not isinstance(system, list):
        raise MessageError("system: must be a string or a list of text blocks")
    parts: list[str] = []
    for block in system:
        if not isinstance(block, Mapping):
            raise MessageError("system: every block must be an object")
        if block.get("type") != "text":
            raise MessageError("system: only text blocks are supported")
        parts.append(str(block.get("text") or ""))
    return "".join(parts)


def _assistant(blocks: list[Any]) -> dict[str, Any]:
    text: list[str] = []
    calls: list[dict[str, Any]] = []
    for block in blocks:
        if not isinstance(block, Mapping):
            raise MessageError("messages: every content block must be an object")
        kind = block.get("type")
        if kind == "text":
            text.append(str(block.get("text") or ""))
        elif kind == "tool_use":
            call_id, name = block.get("id"), block.get("name")
            if not isinstance(call_id, str) or not call_id or not isinstance(name, str) or not name:
                raise MessageError("tool_use blocks need a string 'id' and 'name'")
            calls.append(
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(block.get("input") or {}),
                    },
                }
            )
        elif kind in REASONING_BLOCKS:
            continue
        else:
            raise MessageError(f"assistant content block {kind!r} is not supported")
    message: dict[str, Any] = {"role": "assistant", "content": "".join(text)}
    if calls:
        message["tool_calls"] = calls
    return message


def _user(blocks: list[Any]) -> list[dict[str, Any]]:
    """One Anthropic user turn becomes the ``tool`` messages, then one user message."""
    tool_messages: list[dict[str, Any]] = []
    parts: list[dict[str, Any]] = []
    for block in blocks:
        if not isinstance(block, Mapping):
            raise MessageError("messages: every content block must be an object")
        kind = block.get("type")
        if kind == "tool_result":
            call_id = block.get("tool_use_id")
            if not isinstance(call_id, str) or not call_id:
                raise MessageError("tool_result blocks need a string 'tool_use_id'")
            text, images = _result_content(block.get("content"))
            tool_messages.append({"role": "tool", "tool_call_id": call_id, "content": text})
            parts.extend(images)
        elif kind == "text":
            parts.append({"type": "text", "text": str(block.get("text") or "")})
        elif kind == "image":
            parts.append(_image_part(block))
        else:
            raise MessageError(f"user content block {kind!r} is not supported")
    content = _content_of(parts)
    if content != "" or not tool_messages:
        tool_messages.append({"role": "user", "content": content})
    return tool_messages


def _result_content(content: Any) -> tuple[str, list[dict[str, Any]]]:
    if content is None:
        return "", []
    if isinstance(content, str):
        return content, []
    if not isinstance(content, list):
        raise MessageError("tool_result content must be a string or a list of blocks")
    texts: list[str] = []
    images: list[dict[str, Any]] = []
    for block in content:
        if not isinstance(block, Mapping):
            raise MessageError("tool_result content: every block must be an object")
        kind = block.get("type")
        if kind == "text":
            texts.append(str(block.get("text") or ""))
        elif kind == "image":
            images.append(_image_part(block))
        elif kind in REASONING_BLOCKS:
            continue
        else:
            raise MessageError(f"tool_result block {kind!r} is not supported")
    return "".join(texts), images


def _content_of(parts: list[dict[str, Any]]) -> Any:
    """A plain string when the turn is text only, else the OpenAI part list."""
    if not parts:
        return ""
    if all(part["type"] == "text" for part in parts):
        return "".join(part["text"] for part in parts)
    return parts


def _image_part(block: Mapping[str, Any]) -> dict[str, Any]:
    source = block.get("source")
    if not isinstance(source, Mapping):
        raise MessageError("image blocks need a 'source'")
    kind = source.get("type")
    if kind == "base64":
        media = source.get("media_type") or "image/png"
        return {
            "type": "image_url",
            "image_url": {"url": f"data:{media};base64,{source.get('data') or ''}"},
        }
    if kind == "url":
        return {"type": "image_url", "image_url": {"url": str(source.get("url") or "")}}
    raise MessageError(f"image source {kind!r} is not supported")


def _tools(raw: Any) -> list[dict[str, Any]] | None:
    if not isinstance(raw, list) or not raw:
        return None
    tools: list[dict[str, Any]] = []
    for tool in raw:
        if not isinstance(tool, Mapping):
            raise MessageError("tools: every tool must be an object")
        name = tool.get("name")
        if not isinstance(name, str) or not name:
            raise MessageError("tools: every tool needs a string 'name'")
        function: dict[str, Any] = {
            "name": name,
            "parameters": tool.get("input_schema") or {"type": "object", "properties": {}},
        }
        if tool.get("description"):
            function["description"] = tool["description"]
        tools.append({"type": "function", "function": function})
    return tools


# ------------------------------------------------------------------------------ response side


def _message(result: RouteResult) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    if result.result.content or not result.result.tool_calls:
        content.append({"type": "text", "text": result.result.content or ""})
    for call in result.result.tool_calls:
        content.append(_tool_block(call))
    return {
        "id": _message_id(),
        "type": MESSAGE_OBJECT,
        "role": "assistant",
        "model": result.decision.model,
        "content": content,
        "stop_reason": _stop_reason(result.result),
        "stop_sequence": None,
        "usage": _usage(result.result),
    }


def _tool_block(call: ToolCall) -> dict[str, Any]:
    return {"type": "tool_use", "id": call.id, "name": call.name, "input": _input_of(call)}


def _input_of(call: ToolCall) -> dict[str, Any]:
    try:
        parsed = json.loads(call.arguments) if call.arguments else {}
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _stop_reason(result: ExecResult | None) -> str:
    if result is None:
        return "end_turn"
    if result.finish_reason:
        return STOP_REASONS.get(result.finish_reason, "end_turn")
    return "tool_use" if result.tool_calls else "end_turn"


def _usage(result: ExecResult | None) -> dict[str, int]:
    return {
        "input_tokens": 0 if result is None else result.usage.prompt_tokens or 0,
        "output_tokens": 0 if result is None else result.usage.completion_tokens or 0,
    }


def _message_id() -> str:
    return f"msg_{uuid.uuid4().hex[:24]}"


# -------------------------------------------------------------------------------- streaming


async def _start_stream(events: AsyncIterator[RouteEvent], rules: Rules) -> Response:
    """Start the SSE answer; a refusal before the first token is still a JSON error."""
    try:
        first = await anext(events)
    except SERVING_ERRORS as exc:
        return _failure(exc)
    except StopAsyncIteration:
        return _error(500, "The gateway produced no answer.")
    headers = meta_headers(first.decision, rules, pending=True)
    headers["Cache-Control"] = "no-cache"
    return StreamingResponse(_stream(first, events), media_type=EVENT_STREAM, headers=headers)


async def _stream(first: RouteEvent, events: AsyncIterator[RouteEvent]) -> AsyncIterator[str]:
    """The Messages event sequence: one text block, one block per tool call, then the stop."""
    decision = first.decision
    model = decision.model if decision is not None else ""
    yield _frame("message_start", {"type": "message_start", "message": _started(model)})
    index, text_open = 0, False
    result, failure, done = None, None, None
    async for event in _chain(first, events):
        if event.kind == "route":
            if event.decision is not None:
                decision = event.decision
        elif event.kind == "stream":
            item = event.event
            if item is None:
                continue
            if item.kind == "content" and item.text:
                if not text_open:
                    yield _frame(
                        "content_block_start",
                        _block_start(index, {"type": "text", "text": ""}),
                    )
                    text_open = True
                yield _frame("content_block_delta", _text_delta(index, item.text))
            elif item.kind == "tool_call" and item.tool_call is not None:
                if text_open:
                    yield _frame("content_block_stop", _block_stop(index))
                    index, text_open = index + 1, False
                yield _frame(
                    "content_block_start", _block_start(index, _tool_block(item.tool_call))
                )
                yield _frame(
                    "content_block_delta",
                    _json_delta(index, item.tool_call.arguments or "{}"),
                )
                yield _frame("content_block_stop", _block_stop(index))
                index += 1
            elif item.kind == "done" and item.result is not None:
                done = item.result
        else:
            result, failure = event.result, event.error
    if failure is not None:
        # The Messages stream has a terminal error event, and no message_stop after it.
        status, _code, message, advice = error_details(failure)
        yield _frame("error", _error_body(status, message, advice))
        return
    if text_open:
        yield _frame("content_block_stop", _block_stop(index))
    elif index == 0:
        # An empty answer still needs one block, or the client accumulates a block-less message.
        yield _frame("content_block_start", _block_start(index, {"type": "text", "text": ""}))
        yield _frame("content_block_stop", _block_stop(index))
    answer = result.result if result is not None else done
    yield _frame("message_delta", _delta(answer))
    yield _frame("message_stop", {"type": "message_stop"})


def _started(model: str) -> dict[str, Any]:
    return {
        "id": _message_id(),
        "type": MESSAGE_OBJECT,
        "role": "assistant",
        "model": model,
        "content": [],
        "stop_reason": None,
        "stop_sequence": None,
        "usage": {"input_tokens": 0, "output_tokens": 0},
    }


def _delta(answer: ExecResult | None) -> dict[str, Any]:
    return {
        "type": "message_delta",
        "delta": {"stop_reason": _stop_reason(answer), "stop_sequence": None},
        "usage": _usage(answer),
    }


def _block_start(index: int, block: Mapping[str, Any]) -> dict[str, Any]:
    return {"type": "content_block_start", "index": index, "content_block": dict(block)}


def _block_stop(index: int) -> dict[str, Any]:
    return {"type": "content_block_stop", "index": index}


def _text_delta(index: int, text: str) -> dict[str, Any]:
    return {
        "type": "content_block_delta",
        "index": index,
        "delta": {"type": "text_delta", "text": text},
    }


def _json_delta(index: int, partial: str) -> dict[str, Any]:
    return {
        "type": "content_block_delta",
        "index": index,
        "delta": {"type": "input_json_delta", "partial_json": partial},
    }


def _frame(event: str, payload: Mapping[str, Any]) -> str:
    """An Anthropic stream event is named: the SDK skips a frame with no ``event:`` line."""
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


async def _chain(first: RouteEvent, rest: AsyncIterator[RouteEvent]) -> AsyncIterator[RouteEvent]:
    yield first
    async for event in rest:
        yield event


# ------------------------------------------------------------------------------------ errors


def _error_body(
    status: int, message: str, advice: str | None = None, kind: str | None = None
) -> dict[str, Any]:
    text = message if not advice else f"{message} {advice}"
    return {
        "type": "error",
        "error": {"type": kind or ERROR_TYPES.get(status, "api_error"), "message": text},
    }


def _error(
    status: int, message: str, advice: str | None = None, kind: str | None = None
) -> Response:
    return JSONResponse(_error_body(status, message, advice, kind), status_code=status)


def _failure(exc: Exception) -> Response:
    """A router refusal in the Anthropic envelope, keeping the advice as the message tail."""
    status, _code, message, advice = error_details(exc)
    return _error(status, message, advice)


async def _json_body(request: Request) -> dict[str, Any] | Response:
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        return _error(400, "The request body is not valid JSON.")
    if not isinstance(body, dict):
        return _error(400, "The request body must be a JSON object.")
    return body


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return int(value)


def _float_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)
