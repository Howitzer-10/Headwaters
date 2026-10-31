"""Choose the workspace roots (HDW-203, D9, v3 5.2.2).

* ``configured`` roots are the boundary; they must be absolute existing directories.
* ``host_roots`` (paths or ``file://`` URIs sent by an MCP host) inside a configured root replace
  it (narrowing). Host roots outside it are dropped and reported, never added: roots may only
  narrow the boundary.
* With nothing configured, valid host roots are used; otherwise the host's ``cwd`` is the root.
* ``use_host_roots=False`` (``mcp.roots: off``) ignores host roots.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path, PureWindowsPath
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

from headwaters.core.paths import long_safe
from headwaters.core.workspace_paths import WorkspaceError, default_windows, is_within
from headwaters.core.workspace_roots import WorkspaceRoots


def uri_to_path(uri: str, *, windows: bool | None = None) -> Path:
    """Convert a ``file://`` root URI to a path (UNC for a non-local host on Windows)."""
    win = default_windows() if windows is None else windows
    parsed = urlparse(uri)
    if parsed.scheme.lower() != "file":
        raise WorkspaceError("bad_root", f"only file:// roots are supported: {uri!r}")
    netloc = parsed.netloc
    if netloc and netloc.lower() != "localhost":
        if not win:
            raise WorkspaceError("bad_root", f"remote file URI refused: {uri!r}")
        return Path("//" + netloc + unquote(parsed.path))
    return Path(url2pathname(parsed.path))


def _is_absolute(path: Path, windows: bool) -> bool:
    return path.is_absolute() or (windows and PureWindowsPath(str(path)).is_absolute())


def _real_dir(value: Path) -> Path | None:
    real = Path(os.path.realpath(value))
    return real if long_safe(real).is_dir() else None


def _dedupe(paths: Sequence[Path], windows: bool) -> tuple[Path, ...]:
    kept: list[Path] = []
    for path in paths:
        same = any(
            is_within(str(path), str(k), windows=windows)
            and is_within(str(k), str(path), windows=windows)
            for k in kept
        )
        if not same:
            kept.append(path)
    return tuple(kept)


def _configured_roots(configured: Sequence[str | Path], windows: bool) -> list[Path]:
    base: list[Path] = []
    for item in configured:
        path = Path(item).expanduser()
        if not _is_absolute(path, windows):
            raise WorkspaceError("bad_root", f"configured root must be absolute: {str(item)!r}")
        real = _real_dir(path)
        if real is None:
            raise WorkspaceError("bad_root", f"configured root is not a directory: {str(item)!r}")
        base.append(real)
    return base


def _host_root(item: str, base: Sequence[Path], windows: bool) -> Path | None:
    """The real directory for one host root, or None when invalid or when it would widen."""
    try:
        if item.lower().startswith("file:"):
            path = uri_to_path(item, windows=windows)
        else:
            path = Path(item)
        real = _real_dir(path) if _is_absolute(path, windows) else None
    except (WorkspaceError, OSError, ValueError):
        return None
    if real is None:
        return None
    if base and not any(is_within(str(real), str(b), windows=windows) for b in base):
        return None
    return real


def resolve_roots(
    configured: Sequence[str | Path] = (),
    host_roots: Sequence[str] = (),
    cwd: str | Path | None = None,
    *,
    use_host_roots: bool = True,
    windows: bool | None = None,
) -> WorkspaceRoots:
    """Pick the roots: narrowed host roots, else configured roots, else ``cwd``."""
    win = default_windows() if windows is None else windows
    base = _configured_roots(configured, win)
    valid: list[Path] = []
    dropped: list[str] = []
    for item in host_roots if use_host_roots else ():
        real = _host_root(item, base, win)
        if real is None:
            dropped.append(item)
        else:
            valid.append(real)
    if valid:
        return WorkspaceRoots(_dedupe(valid, win), "host", tuple(dropped), windows=win)
    if base:
        return WorkspaceRoots(_dedupe(base, win), "configured", tuple(dropped), windows=win)
    fallback = _real_dir(Path(cwd) if cwd is not None else Path.cwd())
    if fallback is None:
        raise WorkspaceError("no_roots", "the host working directory is not a directory")
    return WorkspaceRoots((fallback,), "cwd", tuple(dropped), windows=win)
