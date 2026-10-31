"""Pull text, images and tool-call bytes out of OpenAI/Anthropic style messages (HDW-106)."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

IMAGE_PART_TYPES = frozenset({"image_url", "image", "input_image"})
TEXT_PART_TYPES = frozenset({"text", "input_text", "output_text"})
JSON_FORMAT_TYPES = frozenset({"json_object", "json_schema", "json"})
MAX_DEPTH = 6
MAX_LEAF_DEPTH = 32


def dumps(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        return ""


class Collected:
    """What one pass over the messages found."""

    __slots__ = (
        "all_text",
        "code_extra_chars",
        "image_count",
        "last_user_text",
        "scan_text",
        "system_text",
        "user_side_text",
    )

    def __init__(self) -> None:
        self.all_text: list[str] = []
        # Every string leaf of the system blocks and of every message (content parts, tool-call
        # arguments, tool_use input, nested tool_result content). For the secret scan (HDW-120).
        self.scan_text: list[str] = []
        self.system_text: list[str] = []
        self.user_side_text: list[str] = []  # everything except system prompts (for languages)
        self.last_user_text = ""
        self.code_extra_chars = 0  # tool-call arguments: JSON/code-like, counted at code rate
        self.image_count = 0


def _leaves(value: Any, out: list[str], depth: int = 0) -> None:
    """Append every string leaf of ``value``; image parts are skipped (base64 payloads).

    Deeper than ``MAX_LEAF_DEPTH`` the rest is serialized and kept whole, so nesting can never
    hide a string from the secret scan.
    """
    if value is None or isinstance(value, bool | int | float | bytes | bytearray):
        return
    if isinstance(value, str):
        if value:
            out.append(value)
        return
    if depth > MAX_LEAF_DEPTH:
        text = dumps(value)
        if text:
            out.append(text)
        return
    if isinstance(value, Mapping):
        if value.get("type") in IMAGE_PART_TYPES:
            return
        for item in value.values():
            _leaves(item, out, depth + 1)
        return
    if isinstance(value, Iterable):
        for item in value:
            _leaves(item, out, depth + 1)


def _scan_content(content: Any, texts: list[str], out: Collected, depth: int) -> None:
    """Append text parts to ``texts``; count images; count tool-use input as code bytes."""
    if depth > MAX_DEPTH or content is None:
        return
    if isinstance(content, str):
        texts.append(content)
        return
    if isinstance(content, Mapping):
        content = [content]
    if not isinstance(content, Iterable):
        return
    for part in content:
        if isinstance(part, str):
            texts.append(part)
            continue
        if not isinstance(part, Mapping):
            continue
        kind = part.get("type")
        if kind in IMAGE_PART_TYPES:
            out.image_count += 1
        elif kind == "tool_result":
            nested: list[str] = []
            _scan_content(part.get("content"), nested, out, depth + 1)
            out.all_text.extend(nested)
            out.user_side_text.extend(nested)
        elif kind == "tool_use":
            out.code_extra_chars += len(dumps(part.get("input")))
        else:
            text = part.get("text")
            if isinstance(text, str) and (kind is None or kind in TEXT_PART_TYPES):
                texts.append(text)


def collect(
    messages: Sequence[Mapping[str, Any]],
    system: str | Sequence[Mapping[str, Any]] | None,
) -> Collected:
    out = Collected()
    if system is not None:
        texts: list[str] = []
        _scan_content(system, texts, out, 0)
        _leaves(system, out.scan_text)
        out.system_text.extend(texts)
        out.all_text.extend(texts)
    for message in messages:
        role = str(message.get("role", "user"))
        _leaves([v for k, v in message.items() if k != "role"], out.scan_text)
        texts = []
        _scan_content(message.get("content"), texts, out, 0)
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, Sequence) and not isinstance(tool_calls, str):
            for call in tool_calls:
                function = call.get("function") if isinstance(call, Mapping) else None
                if isinstance(function, Mapping):
                    arguments = function.get("arguments")
                    out.code_extra_chars += len(
                        arguments if isinstance(arguments, str) else dumps(arguments)
                    )
        out.all_text.extend(texts)
        if role in ("system", "developer"):
            out.system_text.extend(texts)
            continue
        out.user_side_text.extend(texts)
        if role == "user":
            joined = "\n".join(texts).strip()
            if joined:
                out.last_user_text = joined
    return out


def header(headers: Mapping[str, str] | None, name: str) -> str:
    if not headers:
        return ""
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value
    return ""


def wants_json(response_format: Mapping[str, Any] | str | None) -> bool:
    if response_format is None:
        return False
    if isinstance(response_format, str):
        return response_format.lower() in JSON_FORMAT_TYPES
    kind = response_format.get("type")
    if isinstance(kind, str) and kind.lower() in JSON_FORMAT_TYPES:
        return True
    return any(response_format.get(key) for key in ("json_schema", "schema"))
