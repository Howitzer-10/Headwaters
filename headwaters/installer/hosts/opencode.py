"""OpenCode: the provider, the MCP entry, the command files and AGENTS.md (HDW-403).

OpenCode wants a provider for the gateway - ``provider.headwaters`` with an OpenAI-compatible npm
package and ``options.baseURL`` - and an MCP entry that is a *local* server whose ``command`` is an
array. The provider id is the one ``recommend.py`` suggests (FU-205-3), so
``opencode --model headwaters/local`` is exactly the provider this install writes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from headwaters.installer import commands, jsonc, merge, plan, templates, text
from headwaters.installer.base import HostAdapter
from headwaters.installer.context import Context
from headwaters.installer.names import MCP_SERVER_NAME, OPENCODE_PROVIDER_ID, TOKEN_ENV_VAR
from headwaters.installer.plan import FileChange

__all__ = ["OpenCode", "config_path"]

CONFIG_NAMES = ("opencode.jsonc", "opencode.json")
COMMANDS_DIR = Path(".opencode") / "commands"
RULES_FILE = Path("AGENTS.md")
RULES_SOURCE = "AGENTS.md"


def config_path(ctx: Context) -> Path:
    """The config OpenCode already uses, or the file a fresh install should create."""
    for name in CONFIG_NAMES:
        candidate = ctx.project / name
        if candidate.is_file():
            return candidate
    return ctx.project / "opencode.json"


class OpenCode(HostAdapter):
    id = "opencode"
    title = "OpenCode (terminal)"
    scope = "project"

    def detect(self, ctx: Context) -> bool:
        return any((ctx.project / name).is_file() for name in CONFIG_NAMES)

    def config_paths(self, ctx: Context) -> list[Path]:
        return [config_path(ctx), ctx.project / RULES_FILE, ctx.project / COMMANDS_DIR]

    def plan(self, ctx: Context) -> list[FileChange]:
        return [self._config(ctx), self._rules(ctx), *self._commands(ctx)]

    def _config(self, ctx: Context) -> FileChange:
        path = config_path(ctx)
        wanted = templates.load("opencode", ctx)

        def update(data: dict[str, Any]) -> None:
            data.setdefault("$schema", wanted["$schema"])
            for section in ("provider", "mcp"):
                block = data.get(section)
                if not isinstance(block, dict):
                    block = {}
                block.update(wanted[section])
                data[section] = block

        after, note = merge.merge_object(path, update)
        return plan.change(path, after, note)

    def _rules(self, ctx: Context) -> FileChange:
        path = ctx.project / RULES_FILE
        block = commands.rules_text(RULES_SOURCE)
        if block is None:
            return merge.keep(path, "integrations/rules is not part of this install; skipped")
        return plan.change(path, text.upsert(plan.read_text(path), block))

    def _commands(self, ctx: Context) -> list[FileChange]:
        return [
            plan.change(
                ctx.project / COMMANDS_DIR / f"{command.name}.md",
                commands.render(command, commands.DOLLAR_STYLE),
            )
            for command in commands.commands()
        ]

    def notes(self, ctx: Context) -> list[str]:
        return [
            f"OpenCode reads the gateway token from {TOKEN_ENV_VAR} when it starts the provider: "
            "set it to the daemon token, or run `opencode auth login` and paste the token there.",
            f"Models appear as {OPENCODE_PROVIDER_ID}/auto, /free and /local - the ids the picker "
            "names, so `--model` and /models agree.",
            "Commands live in .opencode/commands and take arguments with $ARGUMENTS.",
        ]

    def problems(self, ctx: Context) -> list[str]:
        found: list[str] = []
        path = config_path(ctx)
        if path.is_file():
            try:
                data: Any = jsonc.load_file(path, {})
            except jsonc.JsoncError as exc:
                found.append(f"{path}: {exc}")
            else:
                providers = data.get("provider") if isinstance(data, dict) else None
                provider = (
                    providers.get(OPENCODE_PROVIDER_ID) if isinstance(providers, dict) else None
                )
                if not isinstance(provider, dict):
                    found.append(
                        f"{path} has no `provider.{OPENCODE_PROVIDER_ID}`; run "
                        "`headwaters install --host opencode`"
                    )
                else:
                    options = provider.get("options")
                    base = options.get("baseURL") if isinstance(options, dict) else None
                    if base != ctx.base_url:
                        found.append(
                            f"{path}: provider.{OPENCODE_PROVIDER_ID}.options.baseURL is {base!r}, "
                            f"not {ctx.base_url!r}; re-run the install"
                        )
                servers = data.get("mcp") if isinstance(data, dict) else None
                entry = servers.get(MCP_SERVER_NAME) if isinstance(servers, dict) else None
                if not isinstance(entry, dict):
                    found.append(
                        f"{path} has no `mcp.{MCP_SERVER_NAME}` server; the Headwaters tools are "
                        "unreachable from OpenCode"
                    )
                elif entry.get("command") != [ctx.launch.command, *ctx.launch.args]:
                    found.append(
                        f"{path}: mcp.{MCP_SERVER_NAME}.command is not the current launcher; "
                        "re-run the install"
                    )
        return found
