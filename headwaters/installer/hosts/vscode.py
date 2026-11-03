"""VS Code and Copilot Chat: MCP entry, BYOK custom endpoint, prompt files and rules (HDW-403).

Three files carry the setup: ``.vscode/mcp.json`` (the MCP server), ``.vscode/settings.json`` (the
setting that shows custom-endpoint models) and the user's ``chatLanguageModels.json`` (the endpoint
itself, with the picker names the gateway serves). The token is asked for once through an input
variable, so it never lands in a file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from headwaters.core.daemon_info import token_path
from headwaters.installer import commands, jsonc, merge, plan, templates, text
from headwaters.installer.base import HostAdapter
from headwaters.installer.context import Context
from headwaters.installer.names import MCP_SERVER_NAME, PROVIDER_DISPLAY_NAME
from headwaters.installer.plan import FileChange

__all__ = ["VsCode"]

MCP_FILE = Path(".vscode") / "mcp.json"
SETTINGS_FILE = Path(".vscode") / "settings.json"
PROMPT_DIR = Path(".github") / "prompts"
RULES_FILE = Path(".github") / "copilot-instructions.md"
RULES_SOURCE = ".github/copilot-instructions.md"
BYOK_SETTING = "chat.agentHost.byokModels.enabled"
BYOK_PARTS = ("Code", "User", "chatLanguageModels.json")

ENTERPRISE_NOTE = (
    'Business or Enterprise: an admin has to allow "Bring Your Own Language Model Key in VS Code" '
    "before a custom endpoint appears; the setting alone is not enough."
)


class VsCode(HostAdapter):
    id = "vscode"
    title = "VS Code (Copilot Chat)"
    scope = "project"
    template_names = ("vscode_mcp", "vscode_settings", "vscode_byok")

    # -- detection ---------------------------------------------------------------------------
    def detect(self, ctx: Context) -> bool:
        return (ctx.project / ".vscode").is_dir() or (ctx.project / ".github").is_dir()

    def config_paths(self, ctx: Context) -> list[Path]:
        return [
            ctx.project / MCP_FILE,
            ctx.project / SETTINGS_FILE,
            self.byok_path(ctx),
            ctx.project / RULES_FILE,
        ]

    @staticmethod
    def byok_path(ctx: Context) -> Path:
        return ctx.appdata.joinpath(*BYOK_PARTS)

    # -- plan --------------------------------------------------------------------------------
    def plan(self, ctx: Context) -> list[FileChange]:
        return [
            self._mcp(ctx),
            self._settings(ctx),
            self._byok(ctx),
            self._rules(ctx),
            *self._prompts(ctx),
        ]

    def _mcp(self, ctx: Context) -> FileChange:
        path = ctx.project / MCP_FILE
        entry = templates.load("vscode_mcp", ctx)["servers"][MCP_SERVER_NAME]
        after, note = merge.merge_object(path, lambda data: _put_server(data, "servers", entry))
        return plan.change(path, after, note)

    def _settings(self, ctx: Context) -> FileChange:
        path = ctx.project / SETTINGS_FILE
        wanted = templates.load("vscode_settings", ctx)
        after, note = merge.merge_object(path, lambda data: data.update(wanted))
        return plan.change(path, after, note)

    def _byok(self, ctx: Context) -> FileChange:
        path = self.byok_path(ctx)
        provider = templates.load("vscode_byok", ctx)[0]

        def update(items: Any) -> None:
            for index, existing in enumerate(items):
                if isinstance(existing, dict) and existing.get("name") == PROVIDER_DISPLAY_NAME:
                    items[index] = provider
                    return
            items.append(provider)

        after, note = merge.merge_list(path, update)
        return plan.change(path, after, note)

    def _rules(self, ctx: Context) -> FileChange:
        path = ctx.project / RULES_FILE
        block = commands.rules_text(RULES_SOURCE)
        if block is None:
            return merge.keep(path, "integrations/rules is not part of this install; skipped")
        return plan.change(path, text.upsert(plan.read_text(path), block))

    def _prompts(self, ctx: Context) -> list[FileChange]:
        return [
            plan.change(
                ctx.project / PROMPT_DIR / f"{command.name}.prompt.md",
                commands.render(command, commands.INPUT_STYLE),
            )
            for command in commands.commands()
        ]

    # -- what the user still does ------------------------------------------------------------
    def notes(self, ctx: Context) -> list[str]:
        return [
            f"VS Code asks once for the gateway token: paste the daemon's own token from "
            f'{token_path()} into the "{PROVIDER_DISPLAY_NAME}" prompt, or set the profile input.',
            ENTERPRISE_NOTE,
            "Reload the window after the first install so the MCP server and the models appear.",
            "Prompt files land in .github/prompts: type /headwaters-<name> in Copilot Chat.",
        ]

    # -- is it still right? ------------------------------------------------------------------
    def problems(self, ctx: Context) -> list[str]:
        found: list[str] = []
        mcp = ctx.project / MCP_FILE
        if mcp.is_file():
            try:
                data: Any = jsonc.load_file(mcp, {})
            except jsonc.JsoncError as exc:
                found.append(f"{mcp}: {exc}")
            else:
                servers = data.get("servers") if isinstance(data, dict) else None
                entry = servers.get(MCP_SERVER_NAME) if isinstance(servers, dict) else None
                if not isinstance(entry, dict):
                    found.append(
                        f"{mcp} has no `{MCP_SERVER_NAME}` server; run "
                        "`headwaters install --host vscode`"
                    )
                elif entry.get("command") != ctx.launch.command:
                    found.append(
                        f"{mcp}: `{MCP_SERVER_NAME}` runs {entry.get('command')!r}, not the "
                        f"current launcher {ctx.launch.command!r}; re-run the install"
                    )
        settings = ctx.project / SETTINGS_FILE
        if settings.is_file():
            try:
                values = jsonc.load_file(settings, {})
            except jsonc.JsoncError as exc:
                found.append(f"{settings}: {exc}")
            else:
                if not (isinstance(values, dict) and values.get(BYOK_SETTING) is True):
                    found.append(
                        f"{settings} does not set `{BYOK_SETTING}`; custom endpoint models "
                        "stay hidden"
                    )
        byok = self.byok_path(ctx)
        if byok.is_file():
            try:
                items = jsonc.load_file(byok, [])
            except jsonc.JsoncError as exc:
                found.append(f"{byok}: {exc}")
            else:
                names = (
                    {item.get("name") for item in items or [] if isinstance(item, dict)}
                    if isinstance(items, list)
                    else set()
                )
                if PROVIDER_DISPLAY_NAME not in names:
                    found.append(
                        f"{byok} has no `{PROVIDER_DISPLAY_NAME}` provider; VS Code cannot reach "
                        "the gateway"
                    )
        return found


def _put_server(data: dict[str, Any], key: str, entry: dict[str, Any]) -> None:
    servers = data.get(key)
    if not isinstance(servers, dict):
        servers = {}
    servers[MCP_SERVER_NAME] = entry
    data[key] = servers
