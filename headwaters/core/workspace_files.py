"""Read workspace files and cut text to a token budget (HDW-203).

Token counts use a counter when given, else characters / ``chars_per_token_code`` from the
catalog's analyzer hints (the code ratio for every file: it counts slightly high, which is safe
for a budget). Nothing here logs or stores file contents. Packing is ``workspace_pack``.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from headwaters.core.paths import long_safe
from headwaters.core.workspace_roots import ResolvedPath

TokenCounter = Callable[[str], int]
FileStatus = Literal[
    "included", "truncated", "budget", "ignored", "outside", "invalid", "missing",
    "binary", "too_large", "directory", "unreadable",
]  # fmt: skip

DEFAULT_MAX_FILE_BYTES = 1024 * 1024
DEFAULT_CHARS_PER_TOKEN = 3.5
TRUNCATION_MARK = "\n[... truncated by Headwaters to fit the token budget ...]\n"


def estimate_tokens(text: str, chars_per_token: float = DEFAULT_CHARS_PER_TOKEN) -> int:
    return math.ceil(len(text) / chars_per_token) if text else 0


def estimate_tokens_for_bytes(size: int, chars_per_token: float = DEFAULT_CHARS_PER_TOKEN) -> int:
    """Token estimate for a file of ``size`` bytes without reading it.

    Characters never outnumber bytes, so this is an upper bound on ``estimate_tokens`` for the
    file's text - which is what a budget check needs.
    """
    return math.ceil(size / chars_per_token) if size > 0 else 0


def probe_file(resolved: ResolvedPath) -> tuple[int, FileStatus | None]:
    """``(size_bytes, problem)`` from one ``stat``, with no read of the file's contents."""
    target = long_safe(resolved.path)
    try:
        if target.is_dir():
            return 0, "directory"
        if not target.is_file():
            return 0, "missing"
        return target.stat().st_size, None
    except FileNotFoundError:
        return 0, "missing"
    except OSError:
        return 0, "unreadable"


@dataclass(frozen=True)
class FileEntry:
    """One requested path and what happened to it. ``text`` is empty unless it was included."""

    requested: str
    status: FileStatus
    display: str = ""
    tokens: int = 0
    total_tokens: int = 0
    text: str = ""
    detail: str = ""


def read_text(
    resolved: ResolvedPath,
    max_bytes: int,
    *,
    read_limit: int | None = None,
) -> tuple[str, FileStatus | None]:
    """``(text, problem)``: ``problem`` is a status when the file cannot be used.

    ``read_limit`` caps how many bytes are read even when the file is smaller than ``max_bytes``;
    a caller uses it to look at a prefix of a file that cannot fit its budget (HDW-601, FU-203-7).
    """
    target = long_safe(resolved.path)
    limit = max_bytes if read_limit is None else min(read_limit, max_bytes)
    try:
        if target.is_dir():
            return "", "directory"
        if not target.is_file():
            return "", "missing"
        with target.open("rb") as handle:
            data = handle.read(limit + 1)
    except FileNotFoundError:
        return "", "missing"
    except OSError:
        return "", "unreadable"
    if len(data) > max_bytes:
        return "", "too_large"
    if b"\x00" in data:
        return "", "binary"
    return data.decode("utf-8-sig", errors="replace").replace("\r\n", "\n"), None


def truncate_to_tokens(text: str, max_tokens: int, count: TokenCounter) -> str:
    """The longest line-aligned prefix of ``text`` plus a marker within ``max_tokens``.

    Returns ``""`` when not even one line and the marker fit.
    """
    room = max_tokens - count(TRUNCATION_MARK)
    if room <= 0:
        return ""
    low, high = 0, len(text)
    while low < high:  # binary search on characters
        mid = (low + high + 1) // 2
        if count(text[:mid]) <= room:
            low = mid
        else:
            high = mid - 1
    cut = text[:low]
    newline = cut.rfind("\n")
    if newline >= 0:
        cut = cut[: newline + 1]
    elif low < len(text):
        return ""  # a single long line that does not fit: do not cut it mid-line
    body = cut.rstrip("\n")
    return body + TRUNCATION_MARK if body else ""
