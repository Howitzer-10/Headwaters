"""The shim's connection to the shared daemon (HDW-202).

Finds the daemon (starting it when it is not running), opens one MCP client to its loopback
endpoint with the per-user token, and re-opens it after a transport failure. No prompt or output
text is logged here.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import TypeVar

import httpx2
from mcp import Client, MCPError
from mcp.client.session import ElicitationFnT
from mcp.client.streamable_http import streamable_http_client

from headwaters.core.daemon_client import DaemonClient, find_running
from headwaters.core.daemon_control import ControlResult, start_daemon
from headwaters.core.daemon_info import read_token
from headwaters.core.logging import get_logger
from headwaters.mcp.threads import run_blocking

__all__ = [
    "DaemonLink",
    "DaemonUnavailable",
    "ensure_daemon",
    "open_daemon_client",
]

CONNECT_TIMEOUT_S = 30.0
READ_TIMEOUT_S = 600.0  # a cold local model can take minutes to answer

T = TypeVar("T")
Ensure = Callable[[], Awaitable[DaemonClient]]
Opener = Callable[[DaemonClient, ElicitationFnT | None], AbstractAsyncContextManager[Client]]

_log = get_logger("mcp.link")


class DaemonUnavailable(Exception):
    """The shared daemon could not be reached or started. The message is safe to show."""


async def ensure_daemon(
    *,
    find: Callable[[], DaemonClient | None] = find_running,
    start: Callable[[], ControlResult] = start_daemon,
) -> DaemonClient:
    """A client for the live daemon; start the daemon first if it is not running."""
    client = await run_blocking(find)
    if client is not None:
        return client
    _log.info("daemon not running, starting it")
    result = await run_blocking(start)
    if not result.ok:
        raise DaemonUnavailable(result.message)
    client = await run_blocking(find)
    if client is None:
        raise DaemonUnavailable("The daemon started but does not answer.")
    return client


@asynccontextmanager
async def open_daemon_client(
    daemon: DaemonClient, elicitation: ElicitationFnT | None = None
) -> AsyncIterator[Client]:
    """An MCP client connected to the daemon's loopback endpoint with the per-user token.

    ``elicitation`` makes the client declare the elicitation capability (HDW-146); leave it out when
    the host cannot be asked, so the daemon takes its ``force`` text fallback (D9).
    """
    token = read_token()
    if not token:
        raise DaemonUnavailable("No daemon token is available.")
    http = httpx2.AsyncClient(
        headers={"Authorization": f"Bearer {token}"},
        trust_env=False,  # loopback only: never through a proxy
        timeout=httpx2.Timeout(CONNECT_TIMEOUT_S, read=READ_TIMEOUT_S),
    )
    transport = streamable_http_client(daemon.info.mcp_url, http_client=http)
    async with http, Client(transport, elicitation_callback=elicitation) as c:
        yield c


class DaemonLink:
    """One lazily opened, shared connection from this shim to the daemon.

    The connection is owned by a dedicated task (anyio cancel scopes must be entered and left by
    the same task, and requests arrive on different ones). A transport failure drops it and the
    next request reconnects, starting the daemon again if it died.
    """

    def __init__(
        self,
        ensure: Ensure = ensure_daemon,
        opener: Opener = open_daemon_client,
        elicitation: ElicitationFnT | None = None,
    ) -> None:
        self._ensure = ensure
        self._opener = opener
        # HDW-146: answers the daemon's questions through the shim's host. The daemon client
        # declares the elicitation capability only on a connection opened for a host that can be
        # asked (``call(..., ask=True)``); every other connection declares none, so the daemon
        # takes its ``force`` text fallback (D9).
        self._elicitation = elicitation
        self._asking = False
        self._lock = asyncio.Lock()
        self._client: Client | None = None
        self._owner: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    async def _own(
        self,
        daemon: DaemonClient,
        ready: asyncio.Future[Client],
        stop: asyncio.Event,
        elicitation: ElicitationFnT | None,
    ) -> None:
        try:
            async with self._opener(daemon, elicitation) as client:
                if not ready.done():
                    ready.set_result(client)
                await stop.wait()
        except BaseException as exc:
            if not ready.done():
                ready.set_exception(exc)
            else:
                _log.warning("daemon connection ended", extra={"error": type(exc).__name__})
            if isinstance(exc, asyncio.CancelledError):
                raise

    async def _connect(self, ask: bool = False) -> Client:
        """The shared connection. ``ask`` needs one that declares elicitation: a connection opened
        without it is replaced once (never the other way round, so a call that does not ask reuses
        an asking connection)."""
        want = ask and self._elicitation is not None
        async with self._lock:
            if self._client is not None and (self._asking or not want):
                return self._client
            if self._client is not None:  # opened before the host's capability was known
                owner, self._owner, self._client = self._owner, None, None
                self._stop.set()
                if owner is not None:
                    with contextlib.suppress(BaseException):
                        await asyncio.wait_for(owner, timeout=5)
            daemon = await self._ensure()
            loop = asyncio.get_running_loop()
            ready: asyncio.Future[Client] = loop.create_future()
            self._stop = asyncio.Event()
            callback = self._elicitation if want else None
            self._asking = want
            self._owner = asyncio.create_task(self._own(daemon, ready, self._stop, callback))
            try:
                self._client = await ready
            except DaemonUnavailable:
                raise
            except Exception as exc:
                raise DaemonUnavailable(
                    f"Could not connect to the daemon ({type(exc).__name__})."
                ) from exc
            return self._client

    async def _drop(self) -> None:
        async with self._lock:
            owner, self._owner, self._client = self._owner, None, None
            self._stop.set()
        if owner is not None:
            with contextlib.suppress(BaseException):
                await asyncio.wait_for(owner, timeout=5)

    async def close(self) -> None:
        await self._drop()

    async def prewarm(self) -> None:
        """Start and connect to the daemon in the background so the first call is fast."""
        with contextlib.suppress(DaemonUnavailable):
            await self._connect()

    async def call(
        self, op: Callable[[Client], Awaitable[T]], *, retry: bool = False, ask: bool = False
    ) -> T:
        """Run ``op`` on the daemon connection.

        ``MCPError`` (the daemon answered with an error) is passed on untouched. Any other
        failure drops the connection. With ``retry`` the call is repeated once on a fresh
        connection; never for a tool call, which may already have run. ``ask`` says the shim's host
        can be asked (HDW-146), so the connection declares elicitation. A host that cancels the call
        cancels ``op`` here, which cancels the request on the daemon; the connection stays open.
        """
        attempts = 2 if retry else 1
        for attempt in range(attempts):
            client = await self._connect(ask)
            try:
                return await op(client)
            except MCPError:
                raise
            except Exception as exc:
                await self._drop()
                if attempt + 1 == attempts:
                    raise DaemonUnavailable(
                        f"The daemon stopped answering ({type(exc).__name__})."
                    ) from exc
        raise AssertionError("unreachable")  # pragma: no cover
