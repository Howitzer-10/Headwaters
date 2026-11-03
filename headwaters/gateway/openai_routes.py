"""The OpenAI-compatible face of the gateway (HDW-301): ``/v1/models`` and chat completions.

The daemon serves this app on its own bound loopback port (``daemon.gateway_sock``), and every
request goes through ``Router.handle``, so an IDE client gets the same scope, locks, walls and
verification as the MCP tools. Only this package imports the web framework (D7, D14); the router
and the rules arrive as plain callables, so each request reads the live ones (HDW-148).

Errors use the OpenAI envelope and an OpenAI client's own exception text, so a refusal with advice
(the FU-135-3 remote image case) is readable in the editor that made the call.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator, Callable, Coroutine, Mapping, Sequence
from typing import Any, Protocol, cast

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route
from starlette.types import ASGIApp

from headwaters.core.advice import REMOTE_IMAGE_ADVICE
from headwaters.core.executor.errors import ExecutorError
from headwaters.core.executor.types import ExecOptions, ExecResult, ToolCall
from headwaters.core.logging import get_logger
from headwaters.core.policy.errors import NoRouteError, Suggestion
from headwaters.core.policy.types import RouteDecision
from headwaters.core.router_types import RouteEvent, RouteFailed, RouteRequest, RouteResult
from headwaters.core.router_verify import schema_of
from headwaters.core.rules import Rules
from headwaters.core.scope import ParsedModel, ScopeError, parse_model_name
from headwaters.gateway.models import card, find, listed
from headwaters.gateway.server import HEALTH_PATH, GuardedApp

__all__ = ["CHAT_PATH", "MODELS_PATH", "RouterLike", "build_gateway_app"]

MODELS_PATH = "/v1/models"
CHAT_PATH = "/v1/chat/completions"
COMPLETION_OBJECT = "chat.completion"
CHUNK_OBJECT = "chat.completion.chunk"
IMAGE_URL_NOT_LOCAL = "image_url_not_local"
_log = get_logger("gateway")

# What ``Router.handle`` can raise before or instead of an answer: a policy refusal, an upstream
# failure or an executor error. A streaming call raises these at the first event, not on the call.
SERVING_ERRORS: tuple[type[Exception], ...] = (
    ScopeError,
    NoRouteError,
    RouteFailed,
    ExecutorError,
)


class RouterLike(Protocol):
    """The one method this app needs of ``core.router.Router``."""

    def handle(
        self, request: RouteRequest
    ) -> Coroutine[Any, Any, RouteResult] | AsyncIterator[RouteEvent]: ...


def build_gateway_app(
    *,
    token: str,
    router: Callable[[], RouterLike | None],
    rules: Callable[[], Rules],
) -> ASGIApp:
    """The OpenAI-compatible app, guarded exactly like the daemon's API (loopback + token)."""

    async def get_health(_request: Request) -> Response:
        return JSONResponse({"ok": True, "gateway": "openai"})

    async def get_models(_request: Request) -> Response:
        show_extras = rules().picker.show_extras
        return JSONResponse(
            {"object": "list", "data": [card(model) for model in listed(show_extras)]}
        )

    async def get_model(request: Request) -> Response:
        wanted = request.path_params["model_id"]
        found = find(wanted, rules().picker.show_extras)
        if found is None:
            return error_response(404, "model_not_found", f"There is no model {wanted!r}.")
        return JSONResponse(card(found))

    async def post_chat(request: Request) -> Response:
        return await _chat(request, router)

    routes = [
        Route(HEALTH_PATH, get_health, methods=["GET"]),
        Route(MODELS_PATH, get_models, methods=["GET"]),
        Route(f"{MODELS_PATH}/{{model_id:path}}", get_model, methods=["GET"]),
        Route(CHAT_PATH, post_chat, methods=["POST"]),
    ]
    return GuardedApp(Starlette(routes=routes), token)


async def _chat(request: Request, router: Callable[[], RouterLike | None]) -> Response:
    """One ``POST /v1/chat/completions``: validate, route through the live router, translate."""
    body = await _json_body(request)
    if isinstance(body, Response):
        return body
    model = body.get("model")
    if not isinstance(model, str) or not model.strip():
        return error_response(400, "missing_model", "The request needs a 'model'.")
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages:
        return error_response(
            400, "missing_messages", "The request needs a non-empty 'messages' list."
        )
    if any(not isinstance(message, Mapping) for message in messages):
        return error_response(400, "invalid_messages", "Every message must be a JSON object.")
    try:
        parsed = parse_model_name(model.strip())
    except ScopeError as exc:
        return failure_response(exc)
    live = router()
    if live is None:
        return error_response(503, "gateway_not_ready", "The daemon is still starting; retry.")
    wanted = _request_for(body, model.strip(), messages, parsed, request.headers)
    try:
        if wanted.stream:
            events = cast("AsyncIterator[RouteEvent]", live.handle(wanted))
            return await _stream(events, body)
        result = await cast("Coroutine[Any, Any, RouteResult]", live.handle(wanted))
    except SERVING_ERRORS as exc:
        return failure_response(exc)
    except Exception:  # a broken engine must not hand the IDE a traceback
        _log.exception("gateway chat failed")
        return error_response(500, "internal_error", "The gateway could not serve the request.")
    return JSONResponse(_completion(result), headers=_meta_headers(result.decision))


def _request_for(
    body: Mapping[str, Any],
    model: str,
    messages: Sequence[Mapping[str, Any]],
    parsed: ParsedModel | None,
    headers: Mapping[str, str],
) -> RouteRequest:
    # The bearer token is the daemon's own secret: it is neither a routing hint nor part of the
    # session key, so it never reaches the router.
    seen = {key.lower(): value for key, value in headers.items() if key.lower() != "authorization"}
    response_format = body.get("response_format")
    max_tokens = _int_or_none(body.get("max_completion_tokens") or body.get("max_tokens"))
    temperature = _float_or_none(body.get("temperature"))
    options: ExecOptions | None = None
    if temperature is not None:
        # Temperature is the one option the OpenAI body adds to the core mapping. Build the whole
        # set, so the JSON schema from ``response_format`` is not dropped with it.
        options = ExecOptions(
            json_schema=schema_of(response_format),
            max_tokens=max_tokens,
            temperature=temperature,
        )
    return RouteRequest(
        messages=messages,
        surface="gateway",
        tools=body.get("tools") or None,
        response_format=response_format,
        headers=seen,
        max_output_tokens=max_tokens,
        stream=body.get("stream") is True,
        scope=parsed.scope if parsed is not None else None,
        strategy=parsed.strategy if parsed is not None else None,
        verify=parsed.verify if parsed is not None else None,
        # A model name the picker does not know (``gpt-4o`` from an IDE's own list) is a request
        # for that model: it is pinned, and the hard filter and walls still apply. HDW-302 adds
        # the alias table (and the ``x-headwaters-pin`` header).
        pin=None if parsed is not None else model,
        session_id=seen.get("x-session-id") or None,
        options=options,
    )


async def _stream(events: AsyncIterator[RouteEvent], body: Mapping[str, Any]) -> Response:
    """Start the SSE answer; a refusal before the first token is still a JSON error."""
    try:
        first = await anext(events)
    except SERVING_ERRORS as exc:
        return failure_response(exc)
    except StopAsyncIteration:
        return error_response(502, "empty_stream", "The gateway produced no answer.")
    headers = _meta_headers(first.decision)
    headers["Cache-Control"] = "no-cache"
    chunks = _chunks(first, events, include_usage=_wants_usage(body))
    return StreamingResponse(chunks, media_type="text/event-stream", headers=headers)


async def _chunks(
    first: RouteEvent, events: AsyncIterator[RouteEvent], *, include_usage: bool
) -> AsyncIterator[str]:
    completion_id, created = _completion_id(), int(time.time())
    model = first.decision.model if first.decision is not None else ""
    yield _sse(_chunk(completion_id, created, model, {"role": "assistant", "content": ""}))
    tool_index, result, failure = 0, None, None
    async for event in _chain(first, events):
        if event.kind == "route":
            model = event.decision.model if event.decision is not None else model
        elif event.kind == "stream":
            item = event.event
            if item is None:
                continue
            if item.kind == "content" and item.text:
                yield _sse(_chunk(completion_id, created, model, {"content": item.text}))
            elif item.kind == "tool_call" and item.tool_call is not None:
                delta = {"tool_calls": [_tool_delta(tool_index, item.tool_call)]}
                yield _sse(_chunk(completion_id, created, model, delta))
                tool_index += 1
        else:
            result, failure = event.result, event.error
    if failure is not None:
        # OpenAI has no mid-stream error frame; the client sees an error frame and [DONE].
        status, code, message, advice = _details(failure)
        yield _sse({"error": _error_body(status, code, message, advice)})
    else:
        done = _chunk(completion_id, created, model, None)
        done["choices"][0]["finish_reason"] = _finish_reason(result.result if result else None)
        yield _sse(done)
        if include_usage and result is not None:
            yield _sse(
                {
                    **_envelope(completion_id, created, model, CHUNK_OBJECT),
                    "choices": [],
                    "usage": _usage(result.result),
                }
            )
    yield "data: [DONE]\n\n"


def _completion(result: RouteResult) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": result.result.content or ""}
    if result.result.tool_calls:
        message["tool_calls"] = [_tool_json(call) for call in result.result.tool_calls]
    return {
        **_envelope(_completion_id(), int(time.time()), result.decision.model, COMPLETION_OBJECT),
        "choices": [
            {"index": 0, "message": message, "finish_reason": _finish_reason(result.result)}
        ],
        "usage": _usage(result.result),
    }


# ---------------------------------------------------------------------------- response plumbing


def _envelope(completion_id: str, created: int, model: str, kind: str) -> dict[str, Any]:
    return {"id": completion_id, "object": kind, "created": created, "model": model}


def _chunk(
    completion_id: str, created: int, model: str, delta: Mapping[str, Any] | None
) -> dict[str, Any]:
    return {
        **_envelope(completion_id, created, model, CHUNK_OBJECT),
        "choices": [{"index": 0, "delta": dict(delta or {}), "finish_reason": None}],
    }


def _sse(payload: Mapping[str, Any]) -> str:
    return f"data: {json.dumps(payload)}\n\n"


async def _chain(first: RouteEvent, rest: AsyncIterator[RouteEvent]) -> AsyncIterator[RouteEvent]:
    yield first
    async for event in rest:
        yield event


def _meta_headers(decision: RouteDecision | None) -> dict[str, str]:
    """The routing headers (v3.3 §5.1); absent when nothing was decided."""
    if decision is None:
        return {}
    return {
        "x-headwaters-model": decision.model,
        "x-headwaters-tier": decision.tier,
        "x-headwaters-reason": decision.rung,
    }


def _usage(result: ExecResult) -> dict[str, int]:
    prompt = result.usage.prompt_tokens or 0
    completion = result.usage.completion_tokens or 0
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }


def _finish_reason(result: ExecResult | None) -> str:
    if result is None:
        return "stop"
    if result.finish_reason:
        return result.finish_reason
    return "tool_calls" if result.tool_calls else "stop"


def _tool_json(call: ToolCall) -> dict[str, Any]:
    return {
        "id": call.id,
        "type": "function",
        "function": {"name": call.name, "arguments": call.arguments},
    }


def _tool_delta(index: int, call: ToolCall) -> dict[str, Any]:
    return {"index": index, **_tool_json(call)}


def _completion_id() -> str:
    return f"chatcmpl-{uuid.uuid4().hex[:24]}"


def _wants_usage(body: Mapping[str, Any]) -> bool:
    options = body.get("stream_options")
    return isinstance(options, Mapping) and options.get("include_usage") is True


async def _json_body(request: Request) -> dict[str, Any] | Response:
    try:
        body = await request.json()
    except (ValueError, UnicodeDecodeError):
        return error_response(400, "invalid_json", "The request body is not valid JSON.")
    if not isinstance(body, dict):
        return error_response(400, "invalid_body", "The request body must be a JSON object.")
    return body


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return int(value)


def _float_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


# ------------------------------------------------------------------------------------ errors


def error_response(
    status: int, code: str, message: str, *, advice: str | None = None
) -> JSONResponse:
    return JSONResponse({"error": _error_body(status, code, message, advice)}, status_code=status)


def _error_body(status: int, code: str, message: str, advice: str | None) -> dict[str, Any]:
    return {
        "message": message if not advice else f"{message} {advice}",
        "type": _error_type(status),
        "code": code,
        "param": None,
    }


def _error_type(status: int) -> str:
    if status == 400:
        return "invalid_request_error"
    if status == 401:
        return "authentication_error"
    if status == 404:
        return "not_found_error"
    if status == 503:
        return "service_unavailable"
    return "api_error"


def failure_response(exc: Exception) -> JSONResponse:
    status, code, message, advice = _details(exc)
    return error_response(status, code, message, advice=advice)


def _details(exc: Exception) -> tuple[int, str, str, str | None]:
    """Status, code, message and advice for anything ``Router.handle`` can refuse with."""
    if isinstance(exc, ScopeError):
        return 400, exc.code, exc.message, exc.fix_hint
    if isinstance(exc, NoRouteError):
        reasons = [rejection.reason for rejection in exc.rejected]
        message = _with_suggestions(exc.message, exc.suggestions)
        return 400, exc.code, message, _image_advice(reasons)
    if isinstance(exc, RouteFailed):
        reasons = [rejection.reason for rejection in exc.no_route.rejected] if exc.no_route else []
        if exc.last_error.get("code") == IMAGE_URL_NOT_LOCAL:
            reasons.append(IMAGE_URL_NOT_LOCAL)
        return 502, exc.code, exc.message, _image_advice(reasons)
    if isinstance(exc, ExecutorError):
        advice = REMOTE_IMAGE_ADVICE if exc.code == IMAGE_URL_NOT_LOCAL else None
        return 502, exc.code, exc.message, advice
    return 500, "internal_error", "The gateway could not serve the request.", None


def _with_suggestions(message: str, suggestions: Sequence[Suggestion]) -> str:
    texts = [suggestion.text for suggestion in suggestions if suggestion.text]
    return message if not texts else f"{message} Next: {' '.join(texts)}"


def _image_advice(reasons: Sequence[str]) -> str | None:
    """FU-135-3: a refusal caused by a remote image URL says to send a ``data:`` URL instead."""
    return REMOTE_IMAGE_ADVICE if IMAGE_URL_NOT_LOCAL in reasons else None
