"""Claude Desktop: the MCP server entry, and nothing else (HDW-403).

Claude Desktop has no project config, no command folder and no rules file: its whole integration
is one ``mcpServers`` entry in ``%APPDATA%\\Claude\\claude_desktop_config.json``, with an absolute
command, and a restart.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from headwaters.installer import jsonc, merge, plan, templates
from headwaters.installer.base import HostAdapter
from headwaters.installer.context import Context
from headwaters.installer.names import MCP_SERVER_NAME
from headwaters.installer.plan import FileChange

__all__ = ["ClaudeDesktop", "config_path"]

PARTS = ("Claude", "claude_desktop_config.json")


def config_path(ctx: Context) -> Path:
    return ctx.appdata.joinpath(*PARTS)


class ClaudeDesktop(HostAdapter):
    id = "claude-desktop"
    title = "Claude Desktop"
    scope = "user"
    has_commands = False

    def detect(self, ctx: Context) -> bool:
        return config_path(ctx).parent.is_dir() or config_path(ctx).is_file()

    def config_paths(self, ctx: Context) -> list[Path]:
        return [config_path(ctx)]

    def plan(self, ctx: Context) -> list[FileChange]:
        path = config_path(ctx)
        entry = templates.load("claude_desktop", ctx)["mcpServers"][MCP_SERVER_NAME]

        def update(data: dict[str, Any]) -> None:
            servers = data.get("mcpServers")
            if not isinstance(servers, dict):
                servers = {}
            servers[MCP_SERVER_NAME] = entry
            data["mcpServers"] = servers

        after, note = merge.merge_object(path, update)
        return [plan.change(path, after, note)]

    def notes(self, ctx: Context) -> list[str]:
        return [
            f"Restart Claude Desktop after the install: it reads {config_path(ctx)} only at start.",
            "Claude Desktop gets the Headwaters MCP tools; it has no command files or rules, so "
            "the model switch commands come from `headwaters_recommend_model` instead.",
        ]

    def problems(self, ctx: Context) -> list[str]:
        path = config_path(ctx)
        if not path.is_file():
            return [f"{path} is missing; run `headwaters install --host claude-desktop`"]
        try:
            data: Any = jsonc.load_file(path, {})
        except jsonc.JsoncError as exc:
            return [f"{path}: {exc}"]
        servers = data.get("mcpServers") if isinstance(data, dict) else None
        entry = servers.get(MCP_SERVER_NAME) if isinstance(servers, dict) else None
        if not isinstance(entry, dict):
            return [f"{path} has no `{MCP_SERVER_NAME}` server; the tools are not loaded"]
        if entry.get("command") != ctx.launch.command:
            return [
                f"{path}: `{MCP_SERVER_NAME}` runs {entry.get('command')!r}, not the current "
                f"launcher {ctx.launch.command!r}; re-run the install"
            ]
        return []
