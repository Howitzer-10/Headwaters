"""The daemon's HTTP server: internal JSON API + the MCP endpoint, on loopback only (HDW-201).

``headwaters.gateway`` owns the web framework (D7: the gateway and the daemon share one process,
and the MCP SDK's HTTP app is itself Starlette). The MCP endpoint is passed in as a plain ASGI
app so this package never imports the MCP SDK.

Every request is checked in one place (``GuardedApp``):
- the ``Host`` header must be a loopback name (stops DNS rebinding) and any ``Origin`` must be too;
- everything except ``GET /health`` needs ``Authorization: Bearer <per-user token>``.
"""

from __future__ import annotations

import asyncio
import hmac
import socket
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any
from urllib.parse import urlsplit

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.types import ASGIApp, Receive, Scope, Send

from headwaters.core.catalog.http import is_loopback_host

__all__ = ["GuardedApp", "HttpServer", "build_app", "is_loopback_authority"]

HEALTH_PATH = "/health"
GRACE_S = 5

JsonProvider = Callable[[], Mapping[str, Any]]


def is_loopback_authority(value: str) -> bool:
    """True for ``host``, ``host:port`` or ``[::1]:port`` when the host is loopback."""
    if not value:
        return False
    try:
        parts = urlsplit("//" + value)
        host = parts.hostname
        parts.port  # noqa: B018 - raises ValueError for a malformed or out-of-range port
    except ValueError:
        return False
    return is_loopback_host(host)


def _origin_ok(origin: str) -> bool:
    try:
        return is_loopback_host(urlsplit(origin).hostname)
    except ValueError:
        return False


class GuardedApp:
    """ASGI wrapper: loopback ``Host``/``Origin`` and bearer-token checks before anything runs."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        self._app = app
        self._token = token.encode("utf-8")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self._app(scope, receive, send)  # lifespan
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        refusal = self._refusal(scope, headers)
        if refusal is None:
            await self._app(scope, receive, send)
        elif scope["type"] == "http":
            status, code = refusal
            response = JSONResponse({"ok": False, "error": {"code": code}}, status_code=status)
            if status == 401:
                response.headers["WWW-Authenticate"] = "Bearer"
            await response(scope, receive, send)
        else:
            await send({"type": "websocket.close", "code": 1008})

    def _refusal(self, scope: Scope, headers: Mapping[str, str]) -> tuple[int, str] | None:
        if not is_loopback_authority(headers.get("host", "")):
            return 421, "bad_host"
        origin = headers.get("origin")
        if origin is not None and not _origin_ok(origin):
            return 403, "bad_origin"
        if scope["type"] == "http" and scope["method"] == "GET" and scope["path"] == HEALTH_PATH:
            return None
        supplied = headers.get("authorization", "")
        scheme, _, value = supplied.partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(
            value.strip().encode("utf-8"), self._token
        ):
            return 401, "unauthorized"
        return None


def build_app(
    *,
    token: str,
    health: JsonProvider,
    status: JsonProvider,
    on_shutdown: Callable[[], None],
    on_reload: JsonProvider | None = None,
    mcp_app: ASGIApp | None = None,
    mcp_lifespan: Callable[[], AbstractAsyncContextManager[None]] | None = None,
) -> ASGIApp:
    """The daemon's ASGI app. ``health`` is public; ``status``, reload and shutdown need the
    token. ``on_reload`` re-reads rules.yaml now and returns the live switches (HDW-148)."""

    async def get_health(request: Request) -> JSONResponse:
        return JSONResponse({"ok": True, **health()})

    async def get_status(request: Request) -> JSONResponse:
        return JSONResponse({"ok": True, **status()})

    async def post_shutdown(request: Request) -> JSONResponse:
        # Answer first, then stop: the server drains this response during its graceful exit.
        asyncio.get_running_loop().call_soon(on_shutdown)
        return JSONResponse({"ok": True, "stopping": True})

    async def post_reload(request: Request) -> JSONResponse:
        if on_reload is None:
            return JSONResponse({"ok": False, "error": {"code": "not_supported"}}, status_code=501)
        # The reload parses the file and may rebuild the engine: keep it off the event loop.
        try:
            reply = await asyncio.to_thread(on_reload)
        except Exception:
            return JSONResponse({"ok": False, "error": {"code": "reload_failed"}}, status_code=500)
        return JSONResponse({"ok": True, **reply})

    routes: list[Route | Mount] = [
        Route(HEALTH_PATH, get_health, methods=["GET"]),
        Route("/v1/status", get_status, methods=["GET"]),
        Route("/v1/reload", post_reload, methods=["POST"]),
        Route("/v1/shutdown", post_shutdown, methods=["POST"]),
    ]
    if mcp_app is not None:
        routes.append(Mount("/", app=mcp_app))

    @asynccontextmanager
    async def lifespan(_app: Starlette) -> AsyncIterator[None]:
        if mcp_lifespan is None:
            yield
            return
        async with mcp_lifespan():
            yield

    return GuardedApp(Starlette(routes=routes, lifespan=lifespan), token)


class HttpServer:
    """``uvicorn`` serving ``app`` on an already-bound loopback socket."""

    def __init__(self, app: ASGIApp, sock: socket.socket) -> None:
        self._sock = sock
        config = uvicorn.Config(
            app,
            log_config=None,  # uvicorn's default access log writes to stdout
            access_log=False,
            log_level="warning",
            lifespan="on",
            timeout_graceful_shutdown=GRACE_S,
            server_header=False,
        )
        self._server = uvicorn.Server(config)

    @property
    def started(self) -> bool:
        return bool(self._server.started)

    def request_shutdown(self) -> None:
        self._server.should_exit = True

    async def serve(self) -> None:
        await self._server.serve(sockets=[self._sock])
