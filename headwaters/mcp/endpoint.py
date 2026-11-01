"""The streamable-HTTP MCP endpoint of the shared daemon (HDW-201, D8).

All MCP SDK use stays in this package. The result is a plain ASGI callable plus the lifespan the
host server must run around it, so the web framework never has to be imported here: the HTTP
server (``headwaters.gateway.server``) mounts the app and owns authentication.

The front-door tools are registered here when the daemon passes a ``ToolContext`` (HDW-204):
``headwaters_do``, ``headwaters_ask``, ``headwaters_check`` and the ``headwaters://result/<id>``
resource. HDW-205 adds the other five.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, MutableMapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any

from mcp.server import MCPServer

from headwaters.mcp.tools.context import ToolContext
from headwaters.mcp.tools.frontdoor import register_frontdoor

__all__ = ["MCP_PATH", "ASGIApp", "McpEndpoint", "build_endpoint"]

MCP_PATH = "/mcp"
SERVER_NAME = "headwaters"

Scope = MutableMapping[str, Any]
ASGIApp = Callable[
    [
        Scope,
        Callable[[], Awaitable[MutableMapping[str, Any]]],
        Callable[[MutableMapping[str, Any]], Awaitable[None]],
    ],
    Awaitable[None],
]


@dataclass(frozen=True)
class McpEndpoint:
    app: ASGIApp
    lifespan: Callable[[], AbstractAsyncContextManager[None]]
    path: str = MCP_PATH


def build_endpoint(
    version: str,
    *,
    server: MCPServer | None = None,
    tools: ToolContext | None = None,
) -> McpEndpoint:
    """Build the MCP server and its ASGI app (served at ``MCP_PATH``).

    DNS-rebinding protection is on for loopback hosts (the SDK default for ``127.0.0.1``).
    ``tools`` registers the front-door tools; without it the server has none (tests).
    """
    mcp = server or MCPServer(name=SERVER_NAME, version=version)
    if tools is not None:
        register_frontdoor(mcp, tools)
    app = mcp.streamable_http_app(streamable_http_path=MCP_PATH, host="127.0.0.1")
    return McpEndpoint(app=app, lifespan=mcp.session_manager.run)
