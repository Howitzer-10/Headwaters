"""Packed files and images for one tool call (HDW-204, HDW-203).

Both functions block (file reads); callers run them off the event loop. Neither returns file
contents to the host: ``FileNote`` carries a path, a status and a token count only. Remote image
URLs are never fetched (that would be egress the router does not control).
"""

from __future__ import annotations

import base64
from collections.abc import Sequence

from headwaters.core.catalog.schema import Catalog
from headwaters.core.paths import long_safe
from headwaters.core.workspace_ignore import IgnoreMatcher
from headwaters.core.workspace_pack import PackResult, pack_files
from headwaters.core.workspace_paths import WorkspaceError
from headwaters.mcp.tools.context import ToolContext, ToolFailure, ToolSettings
from headwaters.mcp.tools.schemas import FileNote
from headwaters.mcp.tools.workspace import Workspace, require_roots

__all__ = ["load_images", "pack_paths"]

_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)


def pack_paths(
    tc: ToolContext, catalog: Catalog, workspace: Workspace, paths: Sequence[str]
) -> PackResult:
    roots = require_roots(workspace, "Paths")
    if len(paths) > tc.settings.max_paths:
        raise ToolFailure(
            "too_many_paths",
            f"At most {tc.settings.max_paths} paths per call; got {len(paths)}.",
            "Name fewer files, or work on one folder at a time.",
        )
    return pack_files(
        roots,
        paths,
        tc.settings.pack_budget_tokens,
        default_ignore=catalog.default_ignore_patterns,
        chars_per_token=catalog.analyzer_hints.chars_per_token_code,
    )


def _sniff(data: bytes) -> str | None:
    for magic, mime in _MAGIC:
        if data.startswith(magic):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def load_images(
    workspace: Workspace, catalog: Catalog, requested: Sequence[str], settings: ToolSettings
) -> tuple[list[str], list[FileNote]]:
    """Data URLs for image files inside the workspace, and a note for every requested image."""
    roots = require_roots(workspace, "Images")
    if len(requested) > settings.max_images:
        raise ToolFailure(
            "too_many_images",
            f"At most {settings.max_images} images per call; got {len(requested)}.",
            "Send fewer images.",
        )
    matchers: dict[str, IgnoreMatcher] = {}
    urls: list[str] = []
    notes: list[FileNote] = []
    for item in requested:
        try:
            resolved = roots.resolve(item)
        except WorkspaceError as exc:
            status = "outside" if exc.code == "outside_workspace" else "invalid"
            notes.append(FileNote(path=item, status=status))
            continue
        matcher = matchers.setdefault(
            str(resolved.root), IgnoreMatcher(resolved.root, catalog.default_ignore_patterns)
        )
        if matcher.is_ignored(resolved.rel):
            notes.append(FileNote(path=resolved.display, status="ignored"))
            continue
        try:
            with long_safe(resolved.path).open("rb") as handle:
                data = handle.read(settings.max_image_bytes + 1)
        except FileNotFoundError:
            notes.append(FileNote(path=resolved.display, status="missing"))
            continue
        except OSError:
            notes.append(FileNote(path=resolved.display, status="unreadable"))
            continue
        if len(data) > settings.max_image_bytes:
            notes.append(FileNote(path=resolved.display, status="too_large"))
            continue
        mime = _sniff(data)
        if mime is None:
            notes.append(FileNote(path=resolved.display, status="not_an_image"))
            continue
        urls.append(f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}")
        notes.append(FileNote(path=resolved.display, status="included"))
    return urls, notes
