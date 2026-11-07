"""Targeted edits to ``rules.yaml`` that keep the user's comments and layout (HDW-118).

Top-level scalar settings (``default_scope``, ``private_lock``, ``strategy``) are changed through
:func:`set_setting`; HDW-508 added :func:`set_nested` for the two nested settings the report needs
(``features.<feature id>`` and ``report.endpoint``) and nothing else — the pairs in
:data:`EDITABLE_NESTED` are the whole allow-list, so a typo cannot rewrite another setting. Both
replace one line, validate the result against ``Rules`` before anything is written, and swap the
file in atomically so the hot-reload watcher never sees a half-written file. Line endings of the
file are kept.
"""

from __future__ import annotations

import contextlib
import json
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
from headwaters.core.usage_report import REPORT_FLAG

EDITABLE_KEYS: frozenset[str] = frozenset({"default_scope", "private_lock", "strategy"})

# HDW-508: the only nested settings the CLI may write. The report's switch is the
# ``features:`` entry core names in ``core.usage_report.REPORT_FLAG``, and its collector is
# ``report.endpoint``; nothing else is editable here, so a typo cannot rewrite another feature.
EDITABLE_NESTED: frozenset[tuple[str, str]] = frozenset(
    {("features", REPORT_FLAG), ("report", "endpoint")}
)
_SAFE_VALUE = re.compile(r"^[A-Za-z0-9_.-]+$")
_MISSING = object()


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


_BLANK_OR_NULL = frozenset({"{}", "~", "null", "Null", "NULL"})


def _render_nested(value: bool | str | None) -> str:
    """A bool as YAML, a safe word bare, anything else quoted (a URL must round-trip exactly)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    return value if _SAFE_VALUE.match(value) else json.dumps(value)


def _ensure_block(lines: list[str], section: str, target: Path) -> tuple[int, int]:
    """``(header index, end index)`` of ``section``'s block, appending the header when missing."""
    header = re.compile(rf"^{re.escape(section)}[ \t]*:[ \t]*(?P<rest>[^\n]*)$")
    for index, line in enumerate(lines):
        found = header.match(line)
        if found is None:
            continue
        rest = found.group("rest").strip()
        if rest.startswith("#"):
            rest = ""
        if rest and rest not in _BLANK_OR_NULL:
            raise ConfigError(
                target, [f"cannot edit '{section}' automatically; change it in the file"]
            )
        end = index + 1
        while end < len(lines) and (not lines[end].strip() or lines[end][0].isspace()):
            end += 1
        if rest in _BLANK_OR_NULL:  # ``features: {}`` or ``report: null`` becomes a block
            lines[index] = f"{section}:"
        return index, end
    while lines and not lines[-1].strip():
        lines.pop()
    lines.append(f"{section}:")
    return len(lines) - 1, len(lines)


def _validate_nested(
    text: str, section: str, key: str, value: bool | str | None, target: Path
) -> None:
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
    block = data.get(section)
    if not isinstance(block, dict) or block.get(key) != value:
        raise ConfigError(
            target, [f"'{section}.{key}' is set more than once; change it in the file"]
        )


def set_nested(
    section: str, key: str, value: bool | str | None, path: Path | None = None
) -> tuple[Any, Any]:
    """Set one nested setting in ``rules.yaml``, keeping the rest of the file as it was written.

    Only the pairs in :data:`EDITABLE_NESTED` may be touched. The result is validated against
    ``Rules`` before it is swapped in, exactly like :func:`set_setting`, so an edit that would
    invalidate the document leaves the file alone. Returns ``(previous, new)``.
    """
    if (section, key) not in EDITABLE_NESTED:
        raise ValueError(f"{section}.{key} cannot be edited by the CLI")
    target = path or rules_path()
    ensure_rules_file(target)
    current = load_rules(target)
    if section == "features":
        previous: Any = current.features.get(key)
    else:
        previous = getattr(getattr(current, section, None), key, _MISSING)
    raw = paths.long_safe(target).read_bytes().decode("utf-8")
    crlf = "\r\n" in raw
    text = raw.replace("\r\n", "\n")
    lines = text.split("\n")
    header, end = _ensure_block(lines, section, target)
    rendered = _render_nested(value)
    entry = re.compile(
        rf"^(?P<head>(?P<indent>[ \t]+){re.escape(key)}[ \t]*:[ \t]*)"
        r"(?:\"[^\"\n]*\"|'[^'\n]*'|[^\s#]*)"
        r"(?P<tail>[ \t]*(?:#.*)?)$"
    )
    for index in range(header + 1, end):
        found = entry.match(lines[index])
        if found is not None:
            lines[index] = found.group("head") + rendered + found.group("tail")
            break
    else:
        indent = "  "
        insert_at = end
        for index in range(end - 1, header, -1):
            if lines[index].strip():
                insert_at = index + 1
                line = lines[index]
                indent = line[: len(line) - len(line.lstrip())]
                break
        lines.insert(insert_at, f"{indent}{key}: {rendered}")
    edited = "\n".join(lines)
    _validate_nested(edited, section, key, value, target)
    if edited != text:
        _write_atomic(target, edited.replace("\n", "\r\n") if crlf else edited)
    return previous, value
