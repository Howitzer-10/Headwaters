"""Register the slash-command prompts (HDW-207).

``/headwaters-local``, ``-free``, ``-auto``, ``-handoff`` and ``-status`` are plain MCP prompts: the
host shows them in its command list and sends the text to its model, which then calls the tool.
Nothing here changes a setting; ``headwaters_scope`` does that, with its own confirmation rules.
"""

from __future__ import annotations

from collections.abc import Callable

from mcp.server import MCPServer

from headwaters.mcp.tools.commands import COMMANDS, CommandSpec, render_prompt

__all__ = ["register_prompts"]


def _plain(spec: CommandSpec) -> Callable[[], str]:
    def prompt() -> str:
        return render_prompt(spec)

    return prompt


def _with_goal(spec: CommandSpec) -> Callable[[str], str]:
    def prompt(goal: str = "") -> str:
        """``goal``: what the work is about (optional)."""
        return render_prompt(spec, goal)

    return prompt


def register_prompts(server: MCPServer) -> None:
    for spec in COMMANDS:
        make = _with_goal if spec.takes_goal else _plain
        server.prompt(name=spec.slug, title=spec.title, description=spec.description)(make(spec))
