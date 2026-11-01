"""The streamable-HTTP MCP endpoint of the shared daemon (HDW-201, D8).

All MCP SDK use stays in this package. The result is a plain ASGI callable plus the lifespan the
host server must run around it, so the web framework never has to be imported here: the HTTP
server (``headwaters.gateway.server``) mounts the app and owns authentication.

The front-door tools are registered here when the daemon passes a ``ToolContext``:
``headwaters_do``, ``_ask``, ``_check`` (HDW-204), ``_think``, ``_status``, ``_scope``,
``_handoff`` and ``_more`` (HDW-205), the ``headwaters://result/<id>`` resource, and eight
advanced tools that ``tools/list`` shows only to a host that called ``headwaters_more``. The
model tools (``do``, ``ask``, ``think``) plan, ask the user and run in the background (HDW-206).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, MutableMapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from typing import Any

from mcp.server import MCPServer

from headwaters.mcp.disclosure import Disclosure
from headwaters.mcp.tools import descriptions
from headwaters.mcp.tools.context import ToolContext
from headwaters.mcp.tools.frontdoor import register_frontdoor
from headwaters.mcp.tools.frontdoor_advanced import register_advanced
from headwaters.mcp.tools.frontdoor_more import register_more
from headwaters.mcp.tools.frontdoor_prompts import register_prompts
from headwaters.mcp.tools.frontdoor_run import register_run
from headwaters.mcp.tools.guarded import Gate

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
        disclosure = Disclosure(tools.enabled_hosts, descriptions.ADVANCED)
        summaries = {name: text.split("\n", 1)[0] for name, text in descriptions.ADVANCED.items()}
        register_frontdoor(mcp, tools)
        register_run(mcp, tools, Gate(tools))
        register_more(mcp, tools, disclosure, summaries)
        register_advanced(mcp, tools)
        register_prompts(mcp)
        mcp.middleware.append(disclosure)
    app = mcp.streamable_http_app(streamable_http_path=MCP_PATH, host="127.0.0.1")
    return McpEndpoint(app=app, lifespan=mcp.session_manager.run)
