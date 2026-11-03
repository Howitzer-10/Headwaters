"""The command files and the rules pack shipped in ``integrations/`` (HDW-403).

Each host spells "run this with an argument" differently, so the same source file is rendered per
host: OpenCode substitutes ``$ARGUMENTS`` itself, a VS Code prompt file asks for it with an input
variable, and Cline has no substitution at all (FU-207-1).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "DOLLAR_STYLE",
    "INPUT_STYLE",
    "LITERAL_STYLE",
    "CommandFile",
    "commands",
    "integrations_dir",
    "render",
    "rules_text",
]

DOLLAR_STYLE = "dollar"  # the host substitutes $ARGUMENTS
INPUT_STYLE = "input"  # the host asks with an input variable
LITERAL_STYLE = "literal"  # the host has no substitution; say it in words

_LITERAL = "the goal stated after this command"


@dataclass(frozen=True)
class CommandFile:
    name: str
    description: str
    body: str


def integrations_dir() -> Path | None:
    """The repo's ``integrations/`` folder, or ``None`` in an installed wheel (not shipped)."""
    candidate = Path(__file__).resolve().parents[2] / "integrations"
    return candidate if candidate.is_dir() else None


def _split_front_matter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---"):
        return {}, text.strip() + "\n"
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text.strip() + "\n"
    meta: dict[str, str] = {}
    for line in parts[1].splitlines():
        if ":" in line:
            key, value = line.split(":", 1)
            meta[key.strip()] = value.strip()
    return meta, parts[2].strip() + "\n"


def commands() -> list[CommandFile]:
    root = integrations_dir()
    if root is None:
        return []
    out: list[CommandFile] = []
    for path in sorted((root / "commands").glob("*.md")):
        meta, body = _split_front_matter(path.read_text(encoding="utf-8-sig"))
        out.append(CommandFile(path.stem, meta.get("description", ""), body))
    return out


def rules_text(relative: str) -> str | None:
    """One of the rules files, named relative to ``integrations/rules/``."""
    root = integrations_dir()
    if root is None:
        return None
    path = root / "rules" / relative
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8-sig").strip() + "\n"


def render(command: CommandFile, style: str) -> str:
    """The command as a markdown file for one host."""
    if style == DOLLAR_STYLE:
        return f"---\ndescription: {command.description}\n---\n\n{command.body}"
    if style == INPUT_STYLE:
        body = command.body.replace("$ARGUMENTS", "${input:goal}")
        head = f"---\nname: {command.name}\ndescription: {command.description}\n"
        if "${input:goal}" in body:
            head += 'argument-hint: "One line: the goal the next model should carry on."\n'
        return f"{head}---\n\n{body}"
    return (
        f"---\ndescription: {command.description}\n---\n\n"
        f"{command.body.replace('$ARGUMENTS', _LITERAL)}"
    )
