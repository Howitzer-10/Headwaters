"""Line-ending normalization and Markdown fence splitting for verification (HDW-116)."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_DIFF_START = re.compile(r"(?m)^(?:diff --git |--- \S.*\n\+\+\+ \S)")


@dataclass(frozen=True)
class Block:
    info: str  # raw info string after the opening fence
    language: str | None  # resolved by the caller; None when unknown
    body: str
    start_line: int  # 1-based line of the opening fence
    closed: bool


def normalize_eol(text: str) -> str:
    """CRLF and lone CR become LF."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def looks_like_diff(text: str) -> bool:
    return _DIFF_START.search(text) is not None


def split_fences(text: str, resolve: Callable[[str], str | None]) -> list[Block]:
    """Split LF text into fenced blocks. A block still open at the end is ``closed=False``."""
    blocks: list[Block] = []
    opened: tuple[str, str, int] | None = None
    body: list[str] = []
    for index, line in enumerate(text.split("\n"), start=1):
        match = _FENCE.match(line)
        if opened is None:
            if match is None:
                continue
            marker, info = match.group(1), match.group(2).strip()
            if marker[0] == "`" and "`" in info:  # inline code such as ```x``` on one line
                continue
            opened, body = (marker, info, index), []
            continue
        marker, info, start = opened
        closes = (
            match is not None
            and match.group(1)[0] == marker[0]
            and len(match.group(1)) >= len(marker)
            and not match.group(2).strip()
        )
        if closes:
            blocks.append(Block(info, resolve(info), "\n".join(body), start, True))
            opened = None
        else:
            body.append(line)
    if opened is not None:
        blocks.append(Block(opened[1], resolve(opened[1]), "\n".join(body), opened[2], False))
    return blocks
