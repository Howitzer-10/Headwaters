"""Ignore rules for files-by-path (HDW-203, v3.4 D; git sources HDW-601 / FU-203-4).

A file is ignored when ANY of these says so:

1. the catalog's ``default_ignore_patterns`` (secret and env files). They are matched
   case-insensitively and are separate from the user's files, so a ``!.env`` line in a
   ``.gitignore`` or ``.headwatersignore`` can never re-include a secret file;
2. when ``root`` sits inside a git working tree, git's other exclude sources, weakest first:
   ``core.excludesFile`` (default ``$XDG_CONFIG_HOME/git/ignore``), then
   ``$GIT_DIR/info/exclude``, then ``.gitignore`` and ``.headwatersignore`` in the directories
   from the working-tree top down to ``root``'s parent. ``$GIT_DIR`` is resolved through
   ``commondir``, so a linked worktree reads the shared ``info/exclude``;
3. ``.gitignore`` and ``.headwatersignore`` files, from the workspace root down to the file's
   directory. Each applies to paths below its own directory; deeper files override shallower
   ones and ``.headwatersignore`` overrides ``.gitignore`` in the same directory. A path under an
   ignored directory stays ignored (git cannot re-include it either).

Sources 2 and 3 follow git's precedence: the last matching rule wins, and the order above runs
from weakest to strongest. ``use_git_excludes=False`` turns source 2 off, which is what a caller
wants when it must not read anything outside the workspace. Ignore files larger than
``MAX_IGNORE_BYTES`` are skipped.
"""

from __future__ import annotations

import functools
import os
import subprocess
from collections.abc import Sequence
from pathlib import Path

from pathspec import GitIgnoreSpec

from headwaters.core.paths import long_safe

IGNORE_FILE_NAMES = (".gitignore", ".headwatersignore")
MAX_IGNORE_BYTES = 256 * 1024
GIT_CONFIG_TIMEOUT_S = 2.0

#: ``(root-relative path of the rule file's directory, its rules)``, in evaluation order.
ExtraSpec = tuple[tuple[str, ...], GitIgnoreSpec]


def _read_spec(target: Path) -> GitIgnoreSpec | None:
    try:
        if not target.is_file() or target.stat().st_size > MAX_IGNORE_BYTES:
            return None
        text = target.read_bytes().decode("utf-8", errors="replace")
    except OSError:
        return None
    return GitIgnoreSpec.from_lines(text.splitlines())


def _load_spec(directory: Path) -> list[GitIgnoreSpec]:
    specs: list[GitIgnoreSpec] = []
    for name in IGNORE_FILE_NAMES:
        spec = _read_spec(long_safe(directory / name))
        if spec is not None:
            specs.append(spec)
    return specs


def _work_tree_top(root: Path) -> Path | None:
    """The nearest directory at or above ``root`` that holds a ``.git`` entry, or ``None``."""
    for directory in (root, *root.parents):
        entry = long_safe(directory / ".git")
        try:
            if entry.is_dir() or entry.is_file():
                return directory
        except OSError:
            continue
    return None


def _git_dir(top: Path) -> Path | None:
    """The git directory for ``top``, following a ``.git`` file used by linked worktrees."""
    entry = long_safe(top / ".git")
    try:
        if entry.is_dir():
            return entry
        text = entry.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        key, _, value = line.partition(":")
        if key.strip() == "gitdir" and value.strip():
            target = Path(value.strip())
            return target if target.is_absolute() else top / target
    return None


def _common_git_dir(git_dir: Path) -> Path:
    """``$GIT_DIR/commondir`` points at the shared directory of a linked worktree."""
    marker = long_safe(git_dir / "commondir")
    try:
        if marker.is_file():
            target = Path(marker.read_text(encoding="utf-8").strip())
            shared = target if target.is_absolute() else git_dir / target
            if shared.is_dir():
                return shared
    except OSError:
        pass
    return git_dir


def _git_config_get(key: str, cwd: str) -> str | None:
    try:
        done = subprocess.run(
            ["git", "config", "--get", key],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=GIT_CONFIG_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = done.stdout.strip()
    return value if done.returncode == 0 and value else None


@functools.lru_cache(maxsize=32)
def _cached_git_config(env_key: tuple[str, str], cwd: str) -> str | None:
    """Cached per (relevant environment, directory) so a test can point it at a temp home."""
    return _git_config_get("core.excludesfile", cwd)


def _global_exclude_file(cwd: Path) -> Path | None:
    """``core.excludesFile``, else git's default ``$XDG_CONFIG_HOME/git/ignore``."""
    env_key = (os.environ.get("XDG_CONFIG_HOME", ""), os.environ.get("GIT_CONFIG_GLOBAL", ""))
    configured = _cached_git_config(env_key, str(cwd))
    if configured:
        candidate = Path(configured).expanduser()
    else:
        xdg = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
        candidate = Path(xdg) / "git" / "ignore"
    try:
        return candidate if candidate.is_file() else None
    except OSError:
        return None


def _prefix(root: Path, base: Path) -> tuple[str, ...]:
    """``root`` relative to ``base`` as parts; empty when they are the same directory."""
    try:
        return root.relative_to(base).parts
    except ValueError:
        return ()


def _ancestors(top: Path, root: Path) -> list[Path]:
    """Directories from ``top`` down to ``root``'s parent, outermost first (``root`` excluded)."""
    directories: list[Path] = []
    current = root.parent
    while True:
        directories.append(current)
        if current == top or current.parent == current:
            break
        current = current.parent
    directories.reverse()
    return directories


def _git_extras(root: Path) -> tuple[ExtraSpec, ...]:
    top = _work_tree_top(root)
    if top is None:
        return ()
    extras: list[ExtraSpec] = []
    top_prefix = _prefix(root, top)
    global_file = _global_exclude_file(top)
    if global_file is not None:
        spec = _read_spec(global_file)
        if spec is not None:
            extras.append((top_prefix, spec))
    git_dir = _git_dir(top)
    if git_dir is not None:
        spec = _read_spec(long_safe(_common_git_dir(git_dir) / "info" / "exclude"))
        if spec is not None:
            extras.append((top_prefix, spec))
    if top != root:
        for directory in _ancestors(top, root):
            for spec in _load_spec(directory):
                extras.append((_prefix(root, directory), spec))
    return tuple(extras)


class IgnoreMatcher:
    """Decides whether a path below ``root`` is ignored. One instance per request."""

    def __init__(
        self,
        root: Path,
        default_patterns: Sequence[str] = (),
        *,
        use_git_excludes: bool = True,
    ) -> None:
        self.root = root
        self._defaults = GitIgnoreSpec.from_lines([p.lower() for p in default_patterns])
        self._extras: tuple[ExtraSpec, ...] = _git_extras(root) if use_git_excludes else ()
        self._cache: dict[str, list[GitIgnoreSpec]] = {}

    def _specs_for(self, rel_dir: str) -> list[GitIgnoreSpec]:
        if rel_dir not in self._cache:
            directory = self.root if rel_dir == "" else self.root / rel_dir
            self._cache[rel_dir] = _load_spec(directory)
        return self._cache[rel_dir]

    def _user_ignored(self, parts: Sequence[str], *, is_dir: bool) -> bool:
        """Last matching user rule wins: git's extras first, then root to deepest."""
        verdict = False
        suffix = "/" if is_dir else ""
        for prefix, spec in self._extras:
            result = spec.check_file("/".join((*prefix, *parts)) + suffix)
            if result.include is not None:
                verdict = result.include
        for depth in range(len(parts)):
            rel_dir = "/".join(parts[:depth])
            below = "/".join(parts[depth:]) + suffix
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
