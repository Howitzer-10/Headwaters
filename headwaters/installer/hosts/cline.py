"""Cline: the MCP settings file, a rules file and the command workflows (HDW-403).

Cline keeps MCP servers in its own settings (``~/.cline/data/settings/cline_mcp_settings.json``,
or under ``CLINE_DATA_DIR``), not in the project, so this adapter writes one file in the user's
profile and the rest into the project. Rules go to ``.clinerules/`` and commands to
``.cline/workflows/``; Cline does not substitute ``$ARGUMENTS``, so the handoff command says the
goal in words instead (FU-207-1).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from headwaters.installer import commands, jsonc, merge, plan, templates
from headwaters.installer.base import HostAdapter
from headwaters.installer.context import Context
from headwaters.installer.names import MCP_SERVER_NAME
from headwaters.installer.plan import FileChange

__all__ = ["Cline"]

RULES_FILE = Path(".clinerules") / "headwaters.md"
RULES_SOURCE = ".clinerules"
WORKFLOW_DIR = Path(".cline") / "workflows"
SETTINGS_PARTS = ("settings", "cline_mcp_settings.json")


def cline_dir(ctx: Context) -> Path:
    """``~/.cline``, or whatever ``CLINE_DIR`` names (Cline's own override)."""
    raw = os.environ.get("CLINE_DIR", "").strip()
    return Path(raw) if raw else ctx.home / ".cline"


def settings_path(ctx: Context) -> Path:
    """The MCP settings file; ``CLINE_DATA_DIR`` replaces ``~/.cline/data``."""
    raw = os.environ.get("CLINE_DATA_DIR", "").strip()
    data = Path(raw) if raw else cline_dir(ctx) / "data"
    return data.joinpath(*SETTINGS_PARTS)


class Cline(HostAdapter):
    id = "cline"
    title = "Cline (VS Code extension, Desktop and CLI)"
    scope = "user"

    def detect(self, ctx: Context) -> bool:
        return cline_dir(ctx).is_dir() or (ctx.project / ".clinerules").is_dir()

    def config_paths(self, ctx: Context) -> list[Path]:
        return [settings_path(ctx), ctx.project / RULES_FILE]

    def plan(self, ctx: Context) -> list[FileChange]:
        return [self._mcp(ctx), self._rules(ctx), *self._workflows(ctx)]

    def _mcp(self, ctx: Context) -> FileChange:
        path = settings_path(ctx)
        entry = templates.load("cline_mcp", ctx)["mcpServers"][MCP_SERVER_NAME]

        def update(data: Any) -> None:
            servers = data.get("mcpServers")
            if not isinstance(servers, dict):
                servers = {}
            servers[MCP_SERVER_NAME] = entry
            data["mcpServers"] = servers

        after, note = merge.merge_object(path, update)
        return plan.change(path, after, note)

    def _rules(self, ctx: Context) -> FileChange:
        path = ctx.project / RULES_FILE
        block = commands.rules_text(RULES_SOURCE)
        if block is None:
            return merge.keep(path, "integrations/rules is not part of this install; skipped")
        return plan.change(path, block)

    def _workflows(self, ctx: Context) -> list[FileChange]:
        return [
            plan.change(
                ctx.project / WORKFLOW_DIR / f"{command.name}.md",
                commands.render(command, commands.LITERAL_STYLE),
            )
            for command in commands.commands()
        ]

    def notes(self, ctx: Context) -> list[str]:
        return [
            f"MCP servers live in your Cline settings ({settings_path(ctx)}); restart the "
            "extension or the CLI task after the install so it reloads them.",
            "The workflows appear in the slash-command list: type / and pick headwaters-<name>.",
            "Cline reads .clinerules/ and AGENTS.md: both are kept, so a project that already has "
            "one is unchanged.",
        ]

    def problems(self, ctx: Context) -> list[str]:
        found: list[str] = []
        path = settings_path(ctx)
        if path.is_file():
            try:
                data: Any = jsonc.load_file(path, {})
            except jsonc.JsoncError as exc:
                found.append(f"{path}: {exc}")
            else:
                servers = data.get("mcpServers") if isinstance(data, dict) else None
                entry = servers.get(MCP_SERVER_NAME) if isinstance(servers, dict) else None
                if not isinstance(entry, dict):
                    found.append(
                        f"{path} has no `{MCP_SERVER_NAME}` server; run "
                        "`headwaters install --host cline`"
                    )
                elif entry.get("command") != ctx.launch.command:
                    found.append(
                        f"{path}: `{MCP_SERVER_NAME}` runs {entry.get('command')!r}, not the "
                        f"current launcher {ctx.launch.command!r}; re-run the install"
                    )
        rules = ctx.project / RULES_FILE
        if not rules.is_file():
            found.append(f"{rules} is missing, so Cline gets no Headwaters rules")
        return found
