"""Absolute launch commands for host MCP configs (HDW-202, v3.1 C5).

IDE configs must never use a bare ``python``: they get the absolute path of the
``headwaters-mcp`` launcher next to the running interpreter, or the full ``python.exe -m
headwaters.mcp`` form when no launcher exists. This module only builds paths; it never imports
the MCP SDK.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "MODULE",
    "SCRIPT_NAME",
    "McpLaunch",
    "MissingLauncher",
    "config_snippet",
    "launch_options",
    "primary",
    "require_existing",
]

SCRIPT_NAME = "headwaters-mcp"
MODULE = "headwaters.mcp"


class MissingLauncher(RuntimeError):
    """No absolute launcher on this machine, so a host config would name a command that fails."""


@dataclass(frozen=True)
class McpLaunch:
    kind: str  # "script" or "module"
    command: str
    args: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "command": self.command, "args": list(self.args)}


def _script_candidates(python: Path) -> list[Path]:
    suffix = ".exe" if sys.platform == "win32" else ""
    return [python.parent / f"{SCRIPT_NAME}{suffix}"]


def launch_options(executable: str | None = None) -> list[McpLaunch]:
    """Launchers in order of preference: the script when it exists, then ``python -m``."""
    python = Path(executable or sys.executable).resolve()
    options = [
        McpLaunch("script", str(script))
        for script in _script_candidates(python)
        if script.is_file()
    ]
    options.append(McpLaunch("module", str(python), ("-m", MODULE)))
    return options


def primary(executable: str | None = None) -> McpLaunch:
    return launch_options(executable)[0]


def require_existing(executable: str | None = None) -> McpLaunch:
    """The first launcher whose command really exists on disk (FU-202-5).

    ``launch_options`` prefers ``headwaters-mcp`` next to the interpreter, but a pipx or
    ``uv tool`` install puts that script in its own venv and a frozen build may have none at all.
    Writing a host config from a name that does not resolve leaves the host with a server that
    never starts, so the installer checks before it writes anything.
    """
    options = launch_options(executable)
    for option in options:
        if Path(option.command).is_file():
            return option
    tried = ", ".join(option.command for option in options)
    raise MissingLauncher(
        f"no MCP launcher exists on this machine (tried {tried}); install Headwaters with "
        "`uv tool install` or `pipx install` so the command resolves, then run this again"
    )


def config_snippet(launch: McpLaunch, name: str = "headwaters") -> dict[str, Any]:
    """The ``mcpServers`` entry hosts expect (command, args)."""
    return {"mcpServers": {name: {"command": launch.command, "args": list(launch.args)}}}
