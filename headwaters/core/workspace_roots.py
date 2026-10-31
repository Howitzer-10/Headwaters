"""Workspace roots for files-by-path (HDW-203, D9).

``WorkspaceRoots.resolve`` checks a path string lexically (``workspace_paths.check_path_text``),
resolves it with ``os.path.realpath`` (symlinks and junctions are followed) and requires the real
location to lie inside one root. Errors carry the path the caller gave and a code, never file
contents. Choosing the roots (configured, host, cwd) is ``workspace_select.resolve_roots``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path, PureWindowsPath
from typing import Literal

from headwaters.core.workspace_paths import (
    WorkspaceError,
    check_path_text,
    default_windows,
    is_within,
    relative_posix,
)

RootSource = Literal["configured", "host", "cwd"]


@dataclass(frozen=True)
class ResolvedPath:
    """A validated path: the real location, the root that contains it and the relative name."""

    path: Path
    root: Path
    rel: str  # POSIX separators, "." for the root itself
    display: str  # what to show a host (root-qualified when several roots exist)


@dataclass(frozen=True)
class WorkspaceRoots:
    roots: tuple[Path, ...]
    source: RootSource
    dropped_host_roots: tuple[str, ...] = ()
    windows: bool = field(default_factory=default_windows)

    def __post_init__(self) -> None:
        if not self.roots:
            raise WorkspaceError("no_roots", "no workspace root is available")

    def resolve(self, text: str) -> ResolvedPath:
        """Validate ``text`` (absolute, or relative to the first root) against every root."""
        check_path_text(text, windows=self.windows)
        windows_path = PureWindowsPath(text)
        if self.windows and windows_path.root and not windows_path.drive:
            raise WorkspaceError("drive_relative", f"rooted path without a drive: {text!r}")
        absolute = os.path.isabs(text) or (self.windows and windows_path.is_absolute())
        candidate = text if absolute else os.path.join(str(self.roots[0]), text)
        real = os.path.realpath(candidate)
        for root in self.roots:
            if is_within(real, str(root), windows=self.windows):
                rel = relative_posix(real, str(root), windows=self.windows)
                display = rel if len(self.roots) == 1 else f"{root.name}:{rel}"
                return ResolvedPath(Path(real), root, rel, display)
        raise WorkspaceError("outside_workspace", f"{text!r} is outside the workspace")
