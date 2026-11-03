"""JSON with comments and trailing commas, for host config files (HDW-403).

VS Code's ``settings.json`` and OpenCode's ``opencode.jsonc`` are JSONC. Merging a key into them
needs a reader that tolerates comments; the writer emits plain JSON, so a file that had comments
comes back without them. That is why every write is backed up first and the diff is shown.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

__all__ = ["JsoncError", "dumps", "load_file", "loads", "needs_lenient_parse"]


class JsoncError(ValueError):
    """The text is not JSON even after comments and trailing commas are removed."""


def _scan(text: str) -> tuple[str, bool]:
    """Drop ``//`` and ``/* */`` comments outside strings; say whether any were found."""
    out: list[str] = []
    found = False
    index = 0
    length = len(text)
    in_string = False
    while index < length:
        char = text[index]
        if in_string:
            out.append(char)
            if char == "\\" and index + 1 < length:
                out.append(text[index + 1])
                index += 2
                continue
            if char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            out.append(char)
            index += 1
            continue
        if text.startswith("//", index):
            found = True
            end = text.find("\n", index)
            index = length if end < 0 else end
            continue
        if text.startswith("/*", index):
            found = True
            end = text.find("*/", index + 2)
            index = length if end < 0 else end + 2
            continue
        out.append(char)
        index += 1
    return "".join(out), found


def _strip_commas(text: str) -> tuple[str, bool]:
    """Drop commas that are followed only by whitespace and a closing bracket."""
    out: list[str] = []
    found = False
    index = 0
    length = len(text)
    in_string = False
    while index < length:
        char = text[index]
        if in_string:
            out.append(char)
            if char == "\\" and index + 1 < length:
                out.append(text[index + 1])
                index += 2
                continue
            if char == '"':
                in_string = False
            index += 1
            continue
        if char == '"':
            in_string = True
            out.append(char)
            index += 1
            continue
        if char == ",":
            probe = index + 1
            while probe < length and text[probe] in " \t\r\n":
                probe += 1
            if probe < length and text[probe] in "}]":
                found = True
                index += 1
                continue
        out.append(char)
        index += 1
    return "".join(out), found


def loads(text: str) -> Any:
    """Parse JSONC. Raises :class:`JsoncError` when even the lenient form does not parse."""
    cleaned, _ = _scan(text)
    cleaned, _ = _strip_commas(cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise JsoncError(str(exc)) from exc


def needs_lenient_parse(text: str) -> bool:
    """True when strict JSON rejects the text (so writing it back loses comments)."""
    try:
        json.loads(text)
    except json.JSONDecodeError:
        return True
    return False


def load_file(path: Path, default: Any = None) -> Any:
    """Parse a config file, or return ``default`` when it is absent or empty."""
    if not path.is_file():
        return default
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise JsoncError(f"{path}: {exc}") from exc
    if not text.strip():
        return default
    return loads(text)


def dumps(value: Any) -> str:
    """Serialise a config value the way a host's own tooling does: two spaces, trailing newline."""
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"
