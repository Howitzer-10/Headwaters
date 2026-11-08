"""Simple files-by-path packing (HDW-203).

``pack_files`` takes paths in the caller's order, validates each against the workspace roots,
skips ignored, missing, binary, oversized and directory paths (reporting why, never content),
and fills a token budget: each file goes in whole while it fits; the first file that does not fit
is cut at a line boundary and every later file is reported as ``budget``. This stays the core
packer until the (★) context engine replaces it through ``ContextPlugin``.

Sizes come first (HDW-601, FU-203-7): a ``stat`` decides ``too_large``, and a file that cannot
fit the remaining budget is read as a bounded prefix instead of up to a megabyte. Since a UTF-8
character is at most 4 bytes, four bytes of file per character the budget allows always covers
enough text to decide, so the prefix is never too short and the file is not read twice.
``total_tokens`` of a file that was not read whole is a size-based upper bound, not an exact count.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field

from headwaters.core.workspace_files import (
    DEFAULT_CHARS_PER_TOKEN,
    DEFAULT_MAX_FILE_BYTES,
    TRUNCATION_MARK,
    FileEntry,
    FileStatus,
    TokenCounter,
    estimate_tokens,
    estimate_tokens_for_bytes,
    probe_file,
    read_text,
    truncate_to_tokens,
)
from headwaters.core.workspace_ignore import IgnoreMatcher
from headwaters.core.workspace_paths import WorkspaceError
from headwaters.core.workspace_roots import WorkspaceRoots

#: Bytes to read past a 4-byte-per-character estimate so the cut can always place its marker.
_PREFIX_SLACK_BYTES = 4 * (len(TRUNCATION_MARK) + 256)


@dataclass(frozen=True)
class PackResult:
    entries: tuple[FileEntry, ...]
    budget_tokens: int
    used_tokens: int
    files: tuple[FileEntry, ...] = field(init=False)

    def __post_init__(self) -> None:
        kept = tuple(e for e in self.entries if e.status in ("included", "truncated"))
        object.__setattr__(self, "files", kept)

    def render(self) -> str:
        """The packed files as one text block (``### path`` headers), in request order."""
        return "\n".join(f"### {e.display}\n{e.text}" for e in self.files)


def _error_status(exc: WorkspaceError) -> FileStatus:
    return "outside" if exc.code == "outside_workspace" else "invalid"


def pack_files(
    roots: WorkspaceRoots,
    requested: Sequence[str],
    budget_tokens: int,
    *,
    default_ignore: Sequence[str] = (),
    token_counter: TokenCounter | None = None,
    chars_per_token: float = DEFAULT_CHARS_PER_TOKEN,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
) -> PackResult:
    """Pack ``requested`` files in order into ``budget_tokens`` (see the module docstring)."""

    def count(text: str) -> int:
        return token_counter(text) if token_counter else estimate_tokens(text, chars_per_token)

    matchers: dict[str, IgnoreMatcher] = {}
    entries: list[FileEntry] = []
    seen: set[str] = set()
    used = 0
    full = False
    for item in requested:
        try:
            resolved = roots.resolve(item)
        except WorkspaceError as exc:
            entries.append(FileEntry(item, _error_status(exc), detail=exc.code))
            continue
        if resolved.display in seen:
            continue
        seen.add(resolved.display)
        matcher = matchers.setdefault(
            str(resolved.root), IgnoreMatcher(resolved.root, default_ignore)
        )
        if matcher.is_ignored(resolved.rel):
            entries.append(FileEntry(item, "ignored", resolved.display))
            continue
        size, problem = probe_file(resolved)
        if problem is not None:
            entries.append(FileEntry(item, problem, resolved.display))
            continue
        if size > max_file_bytes:
            entries.append(FileEntry(item, "too_large", resolved.display))
            continue
        estimated = estimate_tokens_for_bytes(size, chars_per_token)
        if full:
            entries.append(FileEntry(item, "budget", resolved.display, 0, estimated))
            continue
        room_chars = (budget_tokens - used) * chars_per_token
        read_limit: int | None = None
        if token_counter is None and size > room_chars:
            read_limit = math.ceil(room_chars * 4) + _PREFIX_SLACK_BYTES
        text, problem = read_text(resolved, max_file_bytes, read_limit=read_limit)
        if problem is not None:
            entries.append(FileEntry(item, problem, resolved.display))
            continue
        complete = read_limit is None or read_limit >= size
        total = count(text)
        if complete and total <= budget_tokens - used:
            entries.append(FileEntry(item, "included", resolved.display, total, total, text))
            used += total
            continue
        cut = "" if full else truncate_to_tokens(text, budget_tokens - used, count)
        full = True
        if cut:
            tokens = count(cut)
            entries.append(
                FileEntry(
                    item,
                    "truncated",
                    resolved.display,
                    tokens,
                    total if complete else estimated,
                    cut,
                )
            )
            used += tokens
        else:
            entries.append(FileEntry(item, "budget", resolved.display, 0, estimated))
    return PackResult(tuple(entries), budget_tokens, used)
