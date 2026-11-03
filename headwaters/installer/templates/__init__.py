"""Config blocks the installer merges into host files (HDW-403).

Each template is real JSON with a few marker strings in place of the values that change per
machine. The marker is replaced in the *parsed* structure, never in the raw text, so a Windows path
full of backslashes cannot break the JSON.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from headwaters.installer.context import Context
from headwaters.installer.names import (
    MCP_SERVER_NAME,
    OPENCODE_PROVIDER_ID,
    TOKEN_ENV_VAR,
)

__all__ = ["load", "values"]

_DIR = Path(__file__).resolve().parent

COMMAND = "@command"
ARGS = "@args"
ARGV = "@argv"
BASE_URL = "@base_url"
CHAT_URL = "@chat_url"
SERVER = "@server"
PROVIDER = "@provider"
TOKEN_ENV = "@token_env"


def values(ctx: Context) -> dict[str, Any]:
    """Everything a template marker can stand for, on this machine."""
    return {
        COMMAND: ctx.launch.command,
        ARGS: list(ctx.launch.args),
        ARGV: [ctx.launch.command, *ctx.launch.args],
        BASE_URL: ctx.base_url,
        CHAT_URL: ctx.chat_url,
        SERVER: MCP_SERVER_NAME,
        PROVIDER: OPENCODE_PROVIDER_ID,
        TOKEN_ENV: TOKEN_ENV_VAR,
    }


def _fill(node: Any, mapping: dict[str, Any]) -> Any:
    if isinstance(node, dict):
        return {
            (mapping[key] if isinstance(key, str) and key in mapping else key): _fill(
                value, mapping
            )
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [_fill(item, mapping) for item in node]
    if isinstance(node, str):
        if node in mapping:
            return mapping[node]
        for marker, value in mapping.items():
            if marker in node and isinstance(value, str):
                node = node.replace(marker, value)
        return node
    return node


def load(name: str, ctx: Context) -> Any:
    """Parse ``<name>.json`` and fill its markers for ``ctx``."""
    raw = json.loads((_DIR / f"{name}.json").read_text(encoding="utf-8"))
    return _fill(raw, values(ctx))
