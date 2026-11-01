"""Pull unified diffs and shell commands out of a model answer (HDW-204).

A host should not have to parse Markdown to apply a change, so the tools hand back ``diffs[]`` and
``commands[]`` as plain lists and keep the prose apart. Only closed fences count: a diff cut off
mid-way is left in the prose (the verifier reports it as a truncated fence) and never offered as
something to apply. Fence aliases come from the catalog (``analyzer_hints.fence_aliases``).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

__all__ = ["DIFF_OMITTED", "Extracted", "Segment", "extract", "split_segments"]

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_DIFF_LANGUAGES = frozenset({"diff", "patch"})
_SHELL_LANGUAGES = frozenset(
    {"shell", "powershell", "pwsh", "bat", "cmd", "console", "sh", "bash", "zsh", "ps1"}
)
DIFF_OMITTED = "[diff omitted here: it is in `diffs`]"


@dataclass(frozen=True)
class Segment:
    """A run of prose (``info`` is None) or one fenced block."""

    info: str | None
    body: str
    closed: bool = True
    fence: str = "```"

    @property
    def is_code(self) -> bool:
        return self.info is not None

    def render(self) -> str:
        if self.info is None:
            return self.body
        text = f"{self.fence}{self.info}\n{self.body}"
        return f"{text}\n{self.fence}" if self.closed else text


@dataclass(frozen=True)
class Extracted:
    diffs: tuple[str, ...]
    commands: tuple[str, ...]
    prose: str  # the answer with each extracted diff replaced by a marker


def _normalize(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def split_segments(text: str) -> list[Segment]:
    """Split LF-normalized ``text`` into prose runs and fenced blocks."""
    segments: list[Segment] = []
    prose: list[str] = []
    opened: tuple[str, str] | None = None
    body: list[str] = []
    for line in _normalize(text).split("\n"):
        match = _FENCE.match(line)
        if opened is None:
            inline = match is not None and match.group(1)[0] == "`" and "`" in match.group(2)
            if match is None or inline:
                prose.append(line)
                continue
            if prose:
                segments.append(Segment(None, "\n".join(prose)))
                prose = []
            opened, body = (match.group(1), match.group(2).strip()), []
            continue
        marker, info = opened
        closes = (
            match is not None
            and match.group(1)[0] == marker[0]
            and len(match.group(1)) >= len(marker)
            and not match.group(2).strip()
        )
        if closes:
            segments.append(Segment(info, "\n".join(body), True, marker))
            opened = None
        else:
            body.append(line)
    if opened is not None:
        segments.append(Segment(opened[1], "\n".join(body), False, opened[0]))
    elif prose:
        segments.append(Segment(None, "\n".join(prose)))
    return segments


def _language(info: str, aliases: Mapping[str, str]) -> str:
    words = info.split()
    token = words[0].strip("{}.").lower() if words else ""
    return aliases.get(token, token)


def _with_newline(text: str) -> str:
    return text if text.endswith("\n") else text + "\n"


def _unfenced_diff_start(lines: list[str]) -> int | None:
    for index, line in enumerate(lines):
        if line.startswith("diff --git "):
            return index
        if (
            line.startswith("--- ")
            and index + 1 < len(lines)
            and lines[index + 1].startswith("+++ ")
        ):
            return index
    return None


def extract(text: str, aliases: Mapping[str, str] | None = None) -> Extracted:
    """Diffs (fenced, else one bare diff at the end of the prose), commands and the prose."""
    lowered = {k.lower(): v.lower() for k, v in (aliases or {}).items()}
    segments = split_segments(text)
    diffs: list[str] = []
    commands: list[str] = []
    rebuilt: list[str] = []
    for segment in segments:
        language = _language(segment.info, lowered) if segment.info is not None else ""
        usable = segment.is_code and segment.closed and bool(segment.body.strip())
        if usable and language in _DIFF_LANGUAGES:
            diffs.append(_with_newline(segment.body))
            rebuilt.append(DIFF_OMITTED)
            continue
        if usable and language in _SHELL_LANGUAGES:
            commands.append(segment.body.strip("\n"))
        rebuilt.append(segment.render())
    if not diffs:
        lines = _normalize(text).split("\n")
        start = _unfenced_diff_start(lines)
        if start is not None and not any(s.is_code and not s.closed for s in segments):
            diffs.append(_with_newline("\n".join(lines[start:])))
            rebuilt = ["\n".join(lines[:start]), DIFF_OMITTED]
    prose = "\n".join(part for part in rebuilt if part.strip()).strip()
    return Extracted(tuple(diffs), tuple(commands), prose)
