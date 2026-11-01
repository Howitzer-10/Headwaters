"""Workspace and project rule for one tool call (HDW-204, D9).

The daemon is shared by several IDEs, so it cannot know which folder a call belongs to. The shim
sends its working directory in the request ``_meta`` (``headwaters/cwd``); a host that has roots may
add ``headwaters/roots``. ``resolve_roots`` (HDW-203) picks the boundary: a configured root, which
host roots may only narrow, else the shim's directory. With nothing at all there is no workspace and
a call that names files is refused: the daemon's own directory is never a fallback.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from headwaters.core.project_config import ProjectConfig, load_project_config
from headwaters.core.rules import Rules
from headwaters.core.rules_io import ConfigError
from headwaters.core.workspace_files import FileEntry
from headwaters.core.workspace_paths import WorkspaceError
from headwaters.core.workspace_roots import WorkspaceRoots
from headwaters.core.workspace_select import resolve_roots
from headwaters.mcp.meta import META_CWD, META_ROOTS
from headwaters.mcp.tools.context import ToolFailure
from headwaters.mcp.tools.schemas import FileNote

__all__ = [
    "Workspace",
    "note_for",
    "open_workspace",
    "require_roots",
]


@dataclass(frozen=True)
class Workspace:
    roots: WorkspaceRoots | None
    project: ProjectConfig

    @property
    def repo(self) -> Path | None:
        """The folder ``git apply --check`` runs in."""
        return self.roots.roots[0] if self.roots is not None else None


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    return tuple(v for v in value if isinstance(v, str) and v.strip())


def open_workspace(
    meta: Mapping[str, Any] | None, rules: Rules, configured: Sequence[str]
) -> Workspace:
    """The workspace of this call and its ``.headwaters.yaml`` (which can only tighten)."""
    meta = meta or {}
    cwd = _text(meta.get(META_CWD))
    host = _strings(meta.get(META_ROOTS))
    if not (configured or host or cwd):
        return Workspace(None, ProjectConfig())
    try:
        roots = resolve_roots(configured, host, cwd, use_host_roots=rules.mcp.roots == "auto")
    except WorkspaceError as exc:
        raise ToolFailure(
            "bad_workspace",
            str(exc),
            "Open the project folder in your host, or set the workspace root in Headwaters.",
        ) from exc
    if roots.source == "cwd" and cwd is None:  # the daemon's own directory is never a boundary
        return Workspace(None, ProjectConfig())
    try:
        project, _found = load_project_config(roots.roots[0])
    except ConfigError as exc:
        raise ToolFailure(
            "bad_project_config", str(exc), "Fix .headwaters.yaml in the project folder."
        ) from exc
    return Workspace(roots, project)


def note_for(entry: FileEntry) -> FileNote:
    return FileNote(path=entry.display or entry.requested, status=entry.status, tokens=entry.tokens)


def require_roots(workspace: Workspace, what: str) -> WorkspaceRoots:
    if workspace.roots is None:
        raise ToolFailure(
            "no_workspace",
            f"{what} need a workspace folder, and the host did not give one.",
            "Start the host in the project folder, or set the workspace root in Headwaters.",
        )
    return workspace.roots
