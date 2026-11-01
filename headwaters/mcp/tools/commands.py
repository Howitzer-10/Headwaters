"""The slash commands of Headwaters (HDW-207): one source for MCP prompts and command files.

A host that lists MCP prompts shows these as ``/headwaters-local`` and so on. A host that reads a
command folder gets the same text as a ``.md`` file (``integrations/commands/``, rendered by
``render_command_file`` and checked against the files by a test, so the two cannot drift).

The texts only tell the host model which Headwaters tool to call. Loosening a scope or the Private
lock stays the user's decision (D9): the scope prompts say so, and ``headwaters_scope`` enforces it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

__all__ = ["COMMANDS", "CommandSpec", "render_command_file", "render_prompt"]

ARGUMENTS_TOKEN: Final = "$ARGUMENTS"  # what command-folder hosts replace with the user's text

_SCOPE_NOTE = (
    "Tightening applies at once. If the result says `confirmation_required`, the setting would "
    "loosen a lock: tell the user the command in `user_command` and let them run it. Never run "
    "it yourself. "
)


@dataclass(frozen=True)
class CommandSpec:
    slug: str  # the command and prompt name, without the slash
    title: str
    description: str
    body: str  # ``{goal}`` is filled in only when ``takes_goal``
    takes_goal: bool = False


COMMANDS: Final[tuple[CommandSpec, ...]] = (
    CommandSpec(
        "headwaters-local",
        "Headwaters: local only",
        "Keep every request on local models (nothing leaves this machine).",
        'Call `headwaters_scope` with {"scope": "local"}. ' + _SCOPE_NOTE + "Then say in one "
        "line which scope is now in force. Use `headwaters_do` for implementation work.",
    ),
    CommandSpec(
        "headwaters-free",
        "Headwaters: free models",
        "Allow free cloud and local models only; never a paid key.",
        'Call `headwaters_scope` with {"scope": "free"}. ' + _SCOPE_NOTE + "Then say in one "
        "line which scope is now in force. Use `headwaters_do` for implementation work.",
    ),
    CommandSpec(
        "headwaters-auto",
        "Headwaters: automatic",
        "Let Headwaters pick the best allowed model (your keys, then free, then local).",
        'Call `headwaters_scope` with {"scope": "auto"}. ' + _SCOPE_NOTE + "A Private lock or "
        "a project rule can still narrow it. Then say in one line which scope is now in force.",
    ),
    CommandSpec(
        "headwaters-handoff",
        "Headwaters: hand off",
        "Save a short packet so another model or host can carry on.",
        "Call `headwaters_handoff` so another model or host can carry on. {goal} Pass the files "
        "you were working on in `paths` (names only, never contents) and the last error, if any. "
        "Then give the user the `packet_id`.",
        takes_goal=True,
    ),
    CommandSpec(
        "headwaters-status",
        "Headwaters: status",
        "Show the scope, locks, walls, GPU and savings.",
        "Call `headwaters_status` and show the user the scope and locks in force, any walls with "
        "their reset times, and the warm local model, in a few lines.",
    ),
)


def render_prompt(spec: CommandSpec, goal: str = "") -> str:
    """The prompt text for ``spec``. ``goal`` is the user's own words (handoff only)."""
    if not spec.takes_goal:
        return spec.body
    said = goal.strip()
    line = f"The goal: {said}" if said else "Work out the goal from this conversation."
    return spec.body.replace("{goal}", line)


def render_command_file(spec: CommandSpec) -> str:
    """The ``.md`` command file of ``spec``: front matter, then the prompt text."""
    body = render_prompt(spec, ARGUMENTS_TOKEN) if spec.takes_goal else spec.body
    return f"---\ndescription: {spec.description}\n---\n\n{body}\n"
