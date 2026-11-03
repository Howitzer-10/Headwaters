"""What an install would change, and how the change is shown (HDW-403).

The installer never writes without showing this first: a ``FileChange`` carries the text before and
after, so the diff the user approves is exactly the diff that gets written.
"""

from __future__ import annotations

import difflib
from dataclasses import dataclass
from pathlib import Path

__all__ = ["CREATE", "UNCHANGED", "UPDATE", "FileChange", "change", "read_text", "render"]

CREATE = "create"
UPDATE = "update"
UNCHANGED = "unchanged"


@dataclass(frozen=True)
class FileChange:
    """One file the installer would (or did) write."""

    path: Path
    before: str | None
    after: str
    note: str = ""

    @property
    def kind(self) -> str:
        if self.before is None:
            return CREATE
        return UNCHANGED if self.before == self.after else UPDATE

    @property
    def changed(self) -> bool:
        return self.before != self.after


def read_text(path: Path) -> str | None:
    """The file's text, or ``None`` when it does not exist (so a new file is not "empty")."""
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8-sig")


def change(path: Path, after: str, note: str = "") -> FileChange:
    return FileChange(path=path, before=read_text(path), after=after, note=note)


def render(changes: list[FileChange], *, context: int = 3) -> str:
    """A unified diff per file, with unchanged files listed so the user sees the whole set."""
    lines: list[str] = []
    for item in changes:
        if not item.changed:
            lines.append(f"  unchanged  {item.path}")
            continue
        lines.append(f"  {item.kind:<9}  {item.path}")
        if item.note:
            lines.append(f"             {item.note}")
        before = (item.before or "").splitlines(keepends=True)
        after = item.after.splitlines(keepends=True)
        diff = difflib.unified_diff(before, after, n=context, lineterm="")
        for line in diff:
            lines.append(f"    {line}")
    return "\n".join(lines)
