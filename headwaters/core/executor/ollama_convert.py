"""Ollama native-API conversion helpers (HDW-115): request payload and stream-line parsing."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from headwaters.core.executor.errors import ExecutorError
from headwaters.core.executor.types import BackendRequest, Delta, Message, ToolCallDelta, Usage

_DATA_URL_MARK = ";base64,"


def _text_and_images(content: object) -> tuple[str, list[str]]:
    """OpenAI content (string or parts) -> (text, base64 images). Remote image URLs are dropped:
    fetching them would be network egress that the executor does not control."""
    if content is None:
        return "", []
    if isinstance(content, str):
        return content, []
    texts: list[str] = []
    images: list[str] = []
    if isinstance(content, Sequence):
        for part in content:
            if not isinstance(part, Mapping):
                continue
            if part.get("type") == "text":
                texts.append(str(part.get("text", "")))
            elif part.get("type") == "image_url":
                url = part.get("image_url")
                raw = url.get("url", "") if isinstance(url, Mapping) else str(url or "")
                if raw.startswith("data:") and _DATA_URL_MARK in raw:
                    images.append(raw.split(_DATA_URL_MARK, 1)[1])
    return "".join(texts), images


def _arguments_object(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def to_ollama_messages(messages: Sequence[Message]) -> list[dict[str, Any]]:
    """Convert OpenAI-style chat messages to Ollama's native shape."""
    names: dict[str, str] = {}
    out: list[dict[str, Any]] = []
    for message in messages:
        role = str(message.get("role", "user"))
        text, images = _text_and_images(message.get("content"))
        item: dict[str, Any] = {"role": role, "content": text}
        if images:
            item["images"] = images
        calls = message.get("tool_calls")
        if role == "assistant" and isinstance(calls, Sequence) and calls:
            converted: list[dict[str, Any]] = []
            for call in calls:
                if not isinstance(call, Mapping):
                    continue
                function = call.get("function")
                fn = function if isinstance(function, Mapping) else {}
                name = str(fn.get("name", ""))
                if call.get("id"):
                    names[str(call["id"])] = name
                arguments = _arguments_object(fn.get("arguments"))
                converted.append({"function": {"name": name, "arguments": arguments}})
            item["tool_calls"] = converted
        if role == "tool":
            tool_name = message.get("name") or names.get(str(message.get("tool_call_id", "")))
            if tool_name:
                item["tool_name"] = str(tool_name)
        out.append(item)
    return out


def build_payload(request: BackendRequest) -> dict[str, Any]:
    target = request.target
    if target.num_ctx is None:
        raise ExecutorError(
            "missing_num_ctx",
            "Local call needs a computed num_ctx; refusing to fall back to the server default.",
            model=target.model_id,
        )
    options: dict[str, Any] = {"num_ctx": target.num_ctx}
    if request.options.temperature is not None:
        options["temperature"] = request.options.temperature
    if request.options.max_tokens is not None:
        options["num_predict"] = request.options.max_tokens
    payload: dict[str, Any] = {
        "model": target.route,
        "messages": to_ollama_messages(request.messages),
        "stream": True,
        "options": options,
        "keep_alive": request.keep_alive,
    }
    if request.tools:
        payload["tools"] = [dict(t) for t in request.tools]
    if request.options.json_schema is not None:
        payload["format"] = dict(request.options.json_schema)
    if request.options.reasoning is not None:
        payload["think"] = request.options.reasoning
    return payload


def _opt_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def parse_line(data: Mapping[str, Any], seen_calls: int) -> tuple[Delta, int]:
    """One NDJSON object -> (``Delta``, number of tool calls seen so far)."""
    raw_message = data.get("message")
    message: Mapping[str, Any] = raw_message if isinstance(raw_message, Mapping) else {}
    calls: list[ToolCallDelta] = []
    raw_calls = message.get("tool_calls")
    if isinstance(raw_calls, Sequence):
        for call in raw_calls:
            if not isinstance(call, Mapping):
                continue
            function = call.get("function")
            fn = function if isinstance(function, Mapping) else {}
            calls.append(
                ToolCallDelta(
                    index=seen_calls,
                    id=str(call.get("id") or f"call_{seen_calls}"),
                    name=str(fn.get("name", "")),
                    arguments=json.dumps(_arguments_object(fn.get("arguments"))),
                )
            )
            seen_calls += 1
    finish: str | None = None
    usage: Usage | None = None
    if data.get("done"):
        finish = "tool_calls" if seen_calls else str(data.get("done_reason") or "stop")
        usage = Usage(
            prompt_tokens=_opt_int(data.get("prompt_eval_count")),
            completion_tokens=_opt_int(data.get("eval_count")),
        )
    delta = Delta(
        content=str(message.get("content") or ""),
        reasoning=str(message.get("thinking") or ""),
        tool_calls=tuple(calls),
        finish_reason=finish,
        usage=usage,
    )
    return delta, seen_calls
