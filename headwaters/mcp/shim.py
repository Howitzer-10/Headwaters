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
import os
import secrets
from collections.abc import Callable
from typing import Any, cast

import anyio
from mcp import MCPError, types
from mcp.server import NotificationOptions, Server, ServerRequestContext
from mcp.server.stdio import stdio_server
from mcp.server.subscriptions import InMemorySubscriptionBus, ListenHandler
from mcp.shared.subscriptions import ToolsListChanged

import headwaters
from headwaters.core import paths
from headwaters.core.logging import configure_logging
from headwaters.core.stdio import configure_utf8, guard_stdout
from headwaters.mcp.link import DaemonLink, DaemonUnavailable
from headwaters.mcp.meta import META_CLIENT, META_CWD, MORE_TOOL

__all__ = ["build_shim_server", "main", "serve_stdio"]

SERVER_NAME = "headwaters"
SHIM_LOG = "mcp-shim.log"
INTERNAL_ERROR = -32603


def _unavailable_text(exc: DaemonUnavailable) -> str:
    return (
        f"Headwaters could not reach its background daemon: {exc} "
        "Run `headwaters start`, then try again."
    )


def workdir() -> str:
    """The shim's working directory: a host starts the shim in the project folder."""
    return os.getcwd()


async def _announce_new_tools(ctx: ServerRequestContext[Any], bus: InMemorySubscriptionBus) -> None:
    """Tell this shim's host that ``tools/list`` changed.

    A host on the 2026-07-28 protocol hears it on a ``subscriptions/listen`` stream (the bus). An
    older host gets the classic ``notifications/tools/list_changed``; on a modern connection the
    SDK drops that one by design, so sending both is safe.
    """
    await bus.publish(ToolsListChanged())
    with contextlib.suppress(Exception):  # a host without a standalone channel just re-lists
        await ctx.session.send_tool_list_changed()


def build_shim_server(link: DaemonLink) -> Server[Any]:
    """The low-level MCP server whose handlers forward to the daemon through ``link``.

    Tool calls carry ``headwaters/cwd`` in their ``_meta`` (the workspace boundary when nothing is
    configured, D9). Resources are forwarded too, so a ``headwaters://result/<id>`` link works.
    """

    # One id per shim process: the daemon tells its hosts apart by it (progressive disclosure,
    # HDW-205). ``headwaters_more`` makes this shim publish a list change to its own host.
    client_id = secrets.token_urlsafe(9)
    bus = InMemorySubscriptionBus()

    async def on_list_tools(
        ctx: ServerRequestContext[Any], params: types.PaginatedRequestParams | None
    ) -> types.ListToolsResult:
        cursor = params.cursor if params is not None else None
        meta: Any = {META_CLIENT: client_id}
        try:
            listed = await link.call(
                lambda c: c.list_tools(cursor=cursor, meta=meta, cache_mode="bypass"), retry=True
            )
        except DaemonUnavailable as exc:
            raise MCPError(INTERNAL_ERROR, _unavailable_text(exc)) from exc
        return types.ListToolsResult(tools=listed.tools, next_cursor=listed.next_cursor)

    async def on_call_tool(
        ctx: ServerRequestContext[Any], params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        # The daemon is shared, so it cannot know this host's project folder: say it (D9).
        meta: Any = {META_CWD: workdir(), META_CLIENT: client_id}
        try:
            result = await link.call(
                lambda c: c.call_tool(params.name, params.arguments, meta=meta)
            )
            if params.name == MORE_TOOL and not result.is_error:
                await _announce_new_tools(ctx, bus)
            return result
        except DaemonUnavailable as exc:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=_unavailable_text(exc))],
                is_error=True,
            )

    async def on_list_resources(
        ctx: ServerRequestContext[Any], params: types.PaginatedRequestParams | None
    ) -> types.ListResourcesResult:
        cursor = params.cursor if params is not None else None
        try:
            listed = await link.call(lambda c: c.list_resources(cursor=cursor), retry=True)
        except DaemonUnavailable as exc:
            raise MCPError(INTERNAL_ERROR, _unavailable_text(exc)) from exc
        return types.ListResourcesResult(resources=listed.resources, next_cursor=listed.next_cursor)

    async def on_list_resource_templates(
        ctx: ServerRequestContext[Any], params: types.PaginatedRequestParams | None
    ) -> types.ListResourceTemplatesResult:
        cursor = params.cursor if params is not None else None
        try:
            listed = await link.call(lambda c: c.list_resource_templates(cursor=cursor), retry=True)
        except DaemonUnavailable as exc:
            raise MCPError(INTERNAL_ERROR, _unavailable_text(exc)) from exc
        return types.ListResourceTemplatesResult(
            resource_templates=listed.resource_templates, next_cursor=listed.next_cursor
        )

    async def on_read_resource(
        ctx: ServerRequestContext[Any], params: types.ReadResourceRequestParams
    ) -> types.ReadResourceResult:
        try:
            read = await link.call(lambda c: c.read_resource(params.uri), retry=True)
        except DaemonUnavailable as exc:
            raise MCPError(INTERNAL_ERROR, _unavailable_text(exc)) from exc
        return types.ReadResourceResult(contents=read.contents)

    return Server(
        SERVER_NAME,
        version=headwaters.__version__,
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
        on_list_resources=on_list_resources,
        on_list_resource_templates=on_list_resource_templates,
        on_read_resource=on_read_resource,
        on_subscriptions_listen=ListenHandler(bus),
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
        options = server.create_initialization_options(NotificationOptions(tools_changed=True))
        await server.run(read_stream, write_stream, options)


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
