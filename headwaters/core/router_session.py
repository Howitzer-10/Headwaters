"""Session keying and text helpers for the router (HDW-117).

A session key is ``id:<x-session-id>`` when the host sends the header (or the caller passes an
explicit id), else ``h:`` plus a hash of the system prompt and the first user message. The hash
is one-way and only the key is stored: prompt text never reaches the database or the logs.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any

SESSION_HEADER = "x-session-id"
_MAX_ID_CHARS = 128
_HASH_CHARS = 32


def text_of(content: object) -> str:
    """Plain text of a message ``content``: a string, a text part or a list of parts."""
    if content is None or isinstance(content, bytes | bytearray):
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, Mapping):
        for key in ("text", "content"):
            value = content.get(key)
            if value is not None:
                return text_of(value)
        return ""
    if isinstance(content, Sequence):
        return "\n".join(t for t in (text_of(part) for part in content) if t)
    return ""


def system_text(
    system: str | Sequence[Mapping[str, Any]] | None, messages: Sequence[Mapping[str, Any]] = ()
) -> str:
    """The system prompt: the Anthropic top-level ``system`` or the first system message."""
    top = text_of(system)
    if top:
        return top
    for message in messages:
        if message.get("role") == "system":
            return text_of(message.get("content"))
    return ""


def header_value(headers: Mapping[str, str] | None, name: str) -> str | None:
    """Case-insensitive header lookup."""
    if not headers:
        return None
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value
    return None


def session_key(
    *,
    messages: Sequence[Mapping[str, Any]],
    system: str | Sequence[Mapping[str, Any]] | None = None,
    headers: Mapping[str, str] | None = None,
    explicit: str | None = None,
) -> str:
    """``x-session-id`` (or ``explicit``) wins; else hash(system prompt + first user message)."""
    given = (explicit or header_value(headers, SESSION_HEADER) or "").strip()
    if given:
        return "id:" + given[:_MAX_ID_CHARS]
    first_user = next(
        (text_of(m.get("content")) for m in messages if m.get("role") == "user"),
        "",
    )
    raw = system_text(system, messages) + "\x00" + first_user
    digest = hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()
    return "h:" + digest[:_HASH_CHARS]
