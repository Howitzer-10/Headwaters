"""The names every host config uses, in one place (HDW-403).

``headwaters/mcp/tools/recommend.py`` imports ``OPENCODE_PROVIDER_ID`` so the switch command it
suggests names the provider this installer actually writes (FU-205-3) instead of the docs' example
id, which a user's OpenCode may not have.
"""

from __future__ import annotations

__all__ = [
    "MCP_SERVER_NAME",
    "MODEL_KEYS",
    "OPENCODE_PROVIDER_ID",
    "PROVIDER_DISPLAY_NAME",
    "TOKEN_ENV_VAR",
]

MCP_SERVER_NAME = "headwaters"  # the key under mcpServers / servers / mcp
OPENCODE_PROVIDER_ID = "headwaters"  # provider.<id> in opencode.json, so headwaters/<model>
PROVIDER_DISPLAY_NAME = "Headwaters"
MODEL_KEYS = ("auto", "free", "local")  # headwaters/auto ... the gateway's picker names
TOKEN_ENV_VAR = "HEADWATERS_TOKEN"  # where a host reads the daemon token when it has no store
