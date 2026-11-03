"""Reading a host config, merging keys into it, and keeping what was already there (HDW-403).

Every merge parses the file as JSONC, changes only the keys Headwaters owns, and writes plain JSON
back. When the original had comments, the caller notes it in the diff, because the rewrite drops
them - the backup keeps the original.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from headwaters.installer import jsonc
from headwaters.installer.plan import FileChange, read_text

__all__ = [
    "COMMENTS_NOTE",
    "keep",
    "list_at",
    "merge_list",
    "merge_object",
    "object_at",
]

COMMENTS_NOTE = "comments are dropped by the rewrite; the original is backed up"


def object_at(path: Path, value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise jsonc.JsoncError(f"{path}: expected a JSON object at the top level")
    return value


def list_at(path: Path, value: Any) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise jsonc.JsoncError(f"{path}: expected a JSON array at the top level")
    return value


def _note(before: str | None) -> str:
    if before and before.strip() and jsonc.needs_lenient_parse(before):
        return COMMENTS_NOTE
    return ""


def _parse(path: Path, before: str | None) -> Any:
    if before is None or not before.strip():
        return None
    return jsonc.loads(before)


def merge_object(path: Path, update: Callable[[dict[str, Any]], None]) -> tuple[str, str]:
    """``(text, note)`` for the file with ``update`` applied to its top-level object."""
    before = read_text(path)
    data: dict[str, Any] = object_at(path, _parse(path, before))
    update(data)
    return jsonc.dumps(data), _note(before)


def merge_list(path: Path, update: Callable[[list[Any]], None]) -> tuple[str, str]:
    """``(text, note)`` for a file whose top level is an array (VS Code's model list)."""
    before = read_text(path)
    items: list[Any] = list_at(path, _parse(path, before))
    update(items)
    return jsonc.dumps(items), _note(before)


def keep(path: Path, note: str = "") -> FileChange:
    """A file this run deliberately leaves alone, listed so the user sees it was considered."""
    before = read_text(path) or ""
    return FileChange(path=path, before=before, after=before, note=note)
