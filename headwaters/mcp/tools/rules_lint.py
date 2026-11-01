"""Lint check for the rules packs in ``integrations/rules/`` (HDW-207).

A rules file is read by a host model on every turn, so it must stay short, name only tools that
exist, teach the four required habits, and not tell the model to do and not do the same thing.
Wording checks only; nothing here is a model, a limit or a pattern the router uses.
"""

from __future__ import annotations

import re
from collections.abc import Collection
from typing import Final

__all__ = ["MAX_CHARS", "MAX_LINES", "lint_rules"]

MAX_LINES: Final = 40  # first-draft bounds; a host reads the whole file every turn
MAX_CHARS: Final = 2_500

_TOOL = re.compile(r"`(headwaters_[a-z_]+)`")
_NEGATIVE = re.compile(r"\b(?:never|do not|don't|must not|avoid)\b", re.IGNORECASE)
_POSITIVE = re.compile(r"\b(?:always|call|use|prefer|run)\b", re.IGNORECASE)

# Each required habit and a phrase that shows the file teaches it.
_REQUIRED: Final = {
    "call headwaters_do for implementation work": "`headwaters_do`",
    "honor relay": "`relay`",
    "apply diffs exactly": "exactly",
    "prefer delegating cheap subtasks": "delegating",
}


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?:])\s+|\n", text)
    return [p.strip() for p in parts if p.strip()]


def lint_rules(text: str, known_tools: Collection[str]) -> list[str]:
    """Problems found in one rules file (an empty list means it passes)."""
    problems: list[str] = []
    if len(text.splitlines()) > MAX_LINES:
        problems.append(f"too long: more than {MAX_LINES} lines")
    if len(text) > MAX_CHARS:
        problems.append(f"too long: more than {MAX_CHARS} characters")
    for tool in sorted(set(_TOOL.findall(text)) - set(known_tools)):
        problems.append(f"names an unknown tool: {tool}")
    problems.extend(
        f"does not say how to: {habit}" for habit, marker in _REQUIRED.items() if marker not in text
    )
    forbidden: set[str] = set()
    wanted: set[str] = set()
    for sentence in _sentences(text):
        tools = set(_TOOL.findall(sentence))
        if _NEGATIVE.search(sentence):
            forbidden |= tools
        elif _POSITIVE.search(sentence):
            wanted |= tools
    problems.extend(
        f"contradiction: {tool} is both required and forbidden"
        for tool in sorted(forbidden & wanted)
    )
    return problems
