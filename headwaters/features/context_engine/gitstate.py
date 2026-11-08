"""Git-aware invalidation for the symbol index (HDW-601).

Two questions, both answered by ``git`` when it is installed and by ``None`` when it is not:

* what commit is checked out (``head_commit``) - the index records it after every full build, so
  the next start can tell "same tree" from "the tree moved under me";
* which paths differ between two commits (``changed_between``) - a checkout, pull, rebase or
  branch switch then refreshes only those files instead of re-stat'ing the workspace.

Paths come back relative to the workspace root, not the repository top, because a root may be a
subdirectory of the repository (D9 allows narrowing). A repository with no commits (an unborn
``HEAD``) yields ``None``: there is nothing to compare, and a stat-based pass is still correct.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

GIT_TIMEOUT_S = 10.0


def _git(root: Path, *args: str) -> str | None:
    try:
        done = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def head_commit(root: Path) -> str | None:
    """The commit checked out at ``root``, or ``None`` outside a repository or before a commit."""
    out = _git(root, "rev-parse", "--verify", "HEAD")
    if not out:
        return None
    value = out.strip()
    return value or None


def _root_prefix(root: Path) -> tuple[str, ...] | None:
    top = _git(root, "rev-parse", "--show-toplevel")
    if not top:
        return None
    try:
        return root.resolve().relative_to(Path(top.strip()).resolve()).parts
    except (OSError, ValueError):
        return None


def changed_between(root: Path, old: str, new: str) -> tuple[list[str], list[str]] | None:
    """``(changed, removed)`` root-relative paths between two commits, or ``None``.

    ``None`` means git could not answer (not a repository, an unknown commit, git missing). A
    caller then falls back to the stat-based scan, which is always correct, just slower.
    """
    if old == new:
        return [], []
    prefix = _root_prefix(root)
    if prefix is None:
        return None
    out = _git(root, "diff", "--name-status", "-z", "--no-renames", old, new)
    if out is None:
        return None
    fields = [field for field in out.split("\0") if field]
    changed: list[str] = []
    removed: list[str] = []
    for status, path in zip(fields[0::2], fields[1::2], strict=False):
        parts = Path(path).parts
        if parts[: len(prefix)] != prefix:
            continue
        rel = "/".join(parts[len(prefix) :])
        if not rel:
            continue
        if status.startswith("D"):
            removed.append(rel)
        elif status.startswith(("A", "M", "T")):
            changed.append(rel)
    return changed, removed
