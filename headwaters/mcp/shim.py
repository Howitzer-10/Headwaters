"""The stdio MCP shim (``headwaters-mcp``, HDW-202).

A host (an IDE, an agent) starts this process and talks MCP over stdin/stdout. The shim holds no
router and no model: every ``tools/list`` and ``tools/call`` is forwarded to the one shared
daemon (HDW-201), so three IDEs open at once share one router and one warm GPU model. If the
daemon is not running the shim starts it (``headwaters.mcp.link``).

Stdout discipline: nothing but JSON-RPC may reach stdout. ``guard_stdout`` swaps ``sys.stdout``
for a guard that diverts stray ``print`` calls to stderr, and the protocol writer is the only
thing bound to the real stdout. Logs go to stderr and ``mcp-shim.log``, never to stdout.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from typing import Any, cast

import anyio
from mcp import MCPError, types
from mcp.server import Server, ServerRequestContext
from mcp.server.stdio import stdio_server

import headwaters
from headwaters.core import paths
from headwaters.core.logging import configure_logging
from headwaters.core.stdio import configure_utf8, guard_stdout
from headwaters.mcp.link import DaemonLink, DaemonUnavailable

__all__ = ["build_shim_server", "main", "serve_stdio"]

SERVER_NAME = "headwaters"
SHIM_LOG = "mcp-shim.log"
INTERNAL_ERROR = -32603


def _unavailable_text(exc: DaemonUnavailable) -> str:
    return (
        f"Headwaters could not reach its background daemon: {exc} "
        "Run `headwaters start`, then try again."
    )


def build_shim_server(link: DaemonLink) -> Server[Any]:
    """The low-level MCP server whose handlers forward to the daemon through ``link``."""

    async def on_list_tools(
        ctx: ServerRequestContext[Any], params: types.PaginatedRequestParams | None
    ) -> types.ListToolsResult:
        cursor = params.cursor if params is not None else None
        try:
            listed = await link.call(lambda c: c.list_tools(cursor=cursor), retry=True)
        except DaemonUnavailable as exc:
            raise MCPError(INTERNAL_ERROR, _unavailable_text(exc)) from exc
        return types.ListToolsResult(tools=listed.tools, next_cursor=listed.next_cursor)

    async def on_call_tool(
        ctx: ServerRequestContext[Any], params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        try:
            return await link.call(lambda c: c.call_tool(params.name, params.arguments))
        except DaemonUnavailable as exc:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=_unavailable_text(exc))],
                is_error=True,
            )

    return Server(
        SERVER_NAME,
        version=headwaters.__version__,
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )


class _ProtocolOut:
    """File-like sink for the SDK's stdout writer: every write goes through the guard's writer."""

    def __init__(self, emit: Callable[[str], None]) -> None:
        self._emit = emit

    def write(self, text: str) -> int:
        self._emit(text)
        return len(text)

    def flush(self) -> None:
        return None


async def serve_stdio(
    server: Server[Any],
    emit: Callable[[str], None],
    *,
    stdin: Any = None,
) -> None:
    """Serve ``server`` over stdin and the guarded stdout until the host closes stdin.

    ``stdin`` is for tests (an ``anyio`` file); by default the process's own stdin is used.
    """
    stdout = anyio.wrap_file(cast(Any, _ProtocolOut(emit)))
    async with stdio_server(stdin=stdin, stdout=stdout) as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


async def _amain(emit: Callable[[str], None]) -> None:
    link = DaemonLink()
    server = build_shim_server(link)
    prewarm = asyncio.create_task(link.prewarm())
    try:
        await serve_stdio(server, emit)
    finally:
        prewarm.cancel()
        with contextlib.suppress(BaseException):
            await prewarm
        await link.close()


def main() -> int:
    """Entry point of ``headwaters-mcp`` and ``python -m headwaters.mcp``."""
    configure_utf8()
    paths.get_paths().ensure_dirs()
    configure_logging(log_file=paths.log_dir() / SHIM_LOG)
    with guard_stdout(strict=False) as emit, contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_amain(emit))
    return 0
