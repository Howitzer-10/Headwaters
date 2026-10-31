"""Ignore rules for files-by-path (HDW-203, v3.4 D).

A file is ignored when ANY of these says so:

1. the catalog's ``default_ignore_patterns`` (secret and env files). They are matched
   case-insensitively and are separate from the user's files, so a ``!.env`` line in a
   ``.gitignore`` or ``.headwatersignore`` can never re-include a secret file;
2. ``.gitignore`` and ``.headwatersignore`` files, from the workspace root down to the file's
   directory. Each applies to paths below its own directory; deeper files override shallower
   ones and ``.headwatersignore`` overrides ``.gitignore`` in the same directory. A path under an
   ignored directory stays ignored (git cannot re-include it either).

Not read: ``.git/info/exclude``, the global git ignore file and ignore files above the root
(follow-up). Ignore files larger than ``MAX_IGNORE_BYTES`` are skipped.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from pathspec import GitIgnoreSpec

from headwaters.core.paths import long_safe

IGNORE_FILE_NAMES = (".gitignore", ".headwatersignore")
MAX_IGNORE_BYTES = 256 * 1024


def _load_spec(directory: Path) -> list[GitIgnoreSpec]:
    specs: list[GitIgnoreSpec] = []
    for name in IGNORE_FILE_NAMES:
        target = long_safe(directory / name)
        try:
            if not target.is_file() or target.stat().st_size > MAX_IGNORE_BYTES:
                continue
            text = target.read_bytes().decode("utf-8", errors="replace")
        except OSError:
            continue
        specs.append(GitIgnoreSpec.from_lines(text.splitlines()))
    return specs


class IgnoreMatcher:
    """Decides whether a path below ``root`` is ignored. One instance per request."""

    def __init__(self, root: Path, default_patterns: Sequence[str] = ()) -> None:
        self.root = root
        self._defaults = GitIgnoreSpec.from_lines([p.lower() for p in default_patterns])
        self._cache: dict[str, list[GitIgnoreSpec]] = {}

    def _specs_for(self, rel_dir: str) -> list[GitIgnoreSpec]:
        if rel_dir not in self._cache:
            directory = self.root if rel_dir == "" else self.root / rel_dir
            self._cache[rel_dir] = _load_spec(directory)
        return self._cache[rel_dir]

    def _user_ignored(self, parts: Sequence[str], *, is_dir: bool) -> bool:
        """Last matching user rule wins, root first and deepest last."""
        verdict = False
        for depth in range(len(parts)):
            rel_dir = "/".join(parts[:depth])
            below = "/".join(parts[depth:]) + ("/" if is_dir else "")
            for spec in self._specs_for(rel_dir):
                result = spec.check_file(below)
                if result.include is not None:
                    verdict = result.include
        return verdict

    def is_ignored(self, rel: str, *, is_dir: bool = False) -> bool:
        """``rel`` uses ``/`` separators and is relative to the root (``.`` is never ignored)."""
        parts = [p for p in rel.split("/") if p not in ("", ".")]
        if not parts:
            return False
        for end in range(1, len(parts) + 1):
            last = end == len(parts)
            as_dir = is_dir if last else True
            prefix = "/".join(parts[:end]) + ("/" if as_dir else "")
            if self._defaults.match_file(prefix.lower()):
                return True
            if self._user_ignored(parts[:end], is_dir=as_dir):
                return True
        return False
