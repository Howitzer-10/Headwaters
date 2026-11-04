"""Targeted edits to ``rules.yaml`` that keep the user's comments and layout (HDW-118).

Only top-level scalar settings can be changed here (``default_scope``, ``private_lock``,
``strategy``); the CLI uses it for ``headwaters scope`` and ``headwaters private``, and the MCP
``headwaters_scope`` tool for all three (HDW-205). The edit replaces one line, is
validated against ``Rules`` before anything is written, and is swapped in atomically so the
hot-reload watcher never sees a half-written file. Line endings of the file are kept.
"""

from __future__ import annotations

import contextlib
import os
import re
import tempfile
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from headwaters.core import paths
from headwaters.core.rules import Rules
from headwaters.core.rules_io import (
    ConfigError,
    ensure_rules_file,
    format_validation_error,
    load_rules,
    rules_path,
)

EDITABLE_KEYS: frozenset[str] = frozenset({"default_scope", "private_lock", "strategy"})
_SAFE_VALUE = re.compile(r"^[A-Za-z0-9_.-]+$")


def _render(value: bool | str) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if not _SAFE_VALUE.match(value):
        raise ValueError(f"unsupported value: {value!r}")
    return value


def _replace_line(text: str, key: str, rendered: str, target: Path) -> str:
    """Replace the value on the ``key:`` line (keeping any trailing comment), or append it."""
    name = re.escape(key)
    line = re.compile(
        rf"^(?P<head>{name}[ \t]*:[ \t]*)(?:\"[^\"\n]*\"|'[^'\n]*'|[^\s#]+)"
        rf"(?P<tail>[ \t]*(?:#.*)?)$",
        re.MULTILINE,
    )
    if line.search(text):
        return line.sub(lambda m: m.group("head") + rendered + m.group("tail"), text, count=1)
    if re.search(rf"^{name}[ \t]*:", text, re.MULTILINE):
        raise ConfigError(target, [f"cannot edit '{key}' automatically; change it in the file"])
    separator = "" if not text or text.endswith("\n") else "\n"
    body = text.rstrip("\n")
    if body and body.splitlines()[-1].lstrip().startswith("#"):
        separator += "\n"
    return f"{text}{separator}{key}: {rendered}\n"


def _validate(text: str, key: str, value: bool | str, target: Path) -> None:
    try:
        data: Any = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(target, [f"the edited file is not valid YAML: {exc}"]) from exc
    if not isinstance(data, dict):
        raise ConfigError(target, ["top level must be a mapping of settings"])
    try:
        Rules.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(target, format_validation_error(exc)) from exc
    if data.get(key) != value:
        raise ConfigError(target, [f"'{key}' is set more than once; change it in the file"])


def _write_atomic(target: Path, text: str) -> None:
    safe = paths.long_safe(target)
    fd, tmp = tempfile.mkstemp(dir=str(safe.parent), prefix=".rules-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(text.encode("utf-8"))
        os.replace(tmp, safe)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def set_setting(key: str, value: bool | str, path: Path | None = None) -> tuple[Any, Any]:
    """Set a top-level setting in ``rules.yaml`` (created with defaults on first use).

    Returns ``(previous, new)``. Raises ``ConfigError`` when the file is invalid or the result
    would be; in that case the file is left exactly as it was.
    """
    if key not in EDITABLE_KEYS:
        raise ValueError(f"{key!r} cannot be edited by the CLI")
    target = path or rules_path()
    ensure_rules_file(target)
    previous = getattr(load_rules(target), key)
    raw = paths.long_safe(target).read_bytes().decode("utf-8")
    crlf = "\r\n" in raw
    text = raw.replace("\r\n", "\n")
    edited = _replace_line(text, key, _render(value), target)
    _validate(edited, key, value, target)
    if edited != text:
        _write_atomic(target, edited.replace("\n", "\r\n") if crlf else edited)
    return previous, value
