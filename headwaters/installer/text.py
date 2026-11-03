"""A marked block inside a rules file the user also owns (HDW-403).

``AGENTS.md`` and ``.github/copilot-instructions.md`` are shared files. Headwaters adds one marked
section, rewrites only that section on a re-install, and removes exactly that section on uninstall.
"""

from __future__ import annotations

__all__ = ["BEGIN", "END", "remove", "upsert"]

BEGIN = (
    "<!-- headwaters:begin - `headwaters install` wrote this; `headwaters uninstall` removes it -->"
)
END = "<!-- headwaters:end -->"


def _marked(block: str) -> str:
    return f"{BEGIN}\n{block.strip()}\n{END}\n"


def upsert(text: str | None, block: str) -> str:
    """Add the block, or replace the block already there, keeping the rest of the file."""
    marked = _marked(block)
    if text is None or not text.strip():
        return marked
    start = text.find(BEGIN)
    if start >= 0:
        end = text.find(END, start)
        if end >= 0:
            return text[:start] + marked + text[end + len(END) :].lstrip("\n")
    return text.rstrip("\n") + "\n\n" + marked


def remove(text: str) -> str:
    """The file without the block. Empty when the block was all there was."""
    start = text.find(BEGIN)
    if start < 0:
        return text
    end = text.find(END, start)
    if end < 0:
        return text
    head = text[:start].rstrip("\n")
    tail = text[end + len(END) :].lstrip("\n")
    parts = [part for part in (head, tail) if part]
    return "\n\n".join(parts) + "\n" if parts else ""
