"""The daemon's HTTP server: internal JSON API + the MCP endpoint, on loopback only (HDW-201).

``headwaters.gateway`` owns the web framework (D7: the gateway and the daemon share one process,
and the MCP SDK's HTTP app is itself Starlette). The MCP endpoint is passed in as a plain ASGI
app so this package never imports the MCP SDK.

Every request is checked in one place (``GuardedApp``):
- the ``Host`` header must be a loopback name (stops DNS rebinding) and any ``Origin`` must be too;
- everything except ``GET /health`` needs ``Authorization: Bearer <per-user token>``;
- the Anthropic face (``POST /v1/messages``) accepts ``x-api-key`` with the same token instead.
"""

from __future__ import annotations

import asyncio
import hmac
import secrets
import socket
from collections.abc import AsyncIterator, Awaitable, Callable, Collection, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any, Protocol
from urllib.parse import parse_qs, quote, urlsplit

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.types import ASGIApp, Receive, Scope, Send

from headwaters.core.catalog.http import is_loopback_host
from headwaters.core.keys import KeyStoreError
from headwaters.core.wizard import WIZARD_PAGE, WIZARD_PATHS

__all__ = [
    "KEYS_PATH",
    "SCOPE_PATH",
    "WIZARD_PAGE_PATH",
    "GuardedApp",
    "HttpServer",
    "KeysActions",
    "LoopbackOnlyApp",
    "WizardActions",
    "build_app",
    "build_status_page_app",
    "is_dashboard_authority",
    "is_dashboard_origin",
    "is_loopback_authority",
]

HEALTH_PATH = "/health"
# The tray's two endpoints (HDW-504). Both are internal-API routes: the bearer token is required
# exactly as for ``/v1/status``, and the daemon passes the work in as callables.
SCOPE_PATH = "/v1/scope"
KEYS_PATH = "/v1/keys"
STATUS_PAGE_PATHS = ("/", "/index.html")
# The dashboard's four writes (HDW-503): path -> the backend call it makes. Nothing else is a
# write, and a JSON body is never accepted: a cross-site page can post a form, not read one.
KEYS_PATHS: dict[str, str] = {
    "/keys/set": "save",
    "/keys/remove": "remove",
    "/keys/test": "test",
    "/keys/test-all": "test_all",
}
FORM_MEDIA_TYPE = "application/x-www-form-urlencoded"
MAX_FORM_BYTES = 8 * 1024
KEYS_SECTION = "keys"
# The wizard (HDW-505) is one GET plus the nine writes ``core.wizard`` enumerates. The table is
# imported rather than repeated: the ★ package renders forms that post to it, so there is one list
# with two readers and no drift. A path outside it is a 404, never a proxy for arbitrary work.
WIZARD_PAGE_PATH = WIZARD_PAGE
MAX_FLASH = 8
PAGE_FAILED = (
    "<!doctype html><title>Headwaters status</title>"
    "<h1>Headwaters status</h1><p>The page could not be built; the daemon log has the error.</p>"
)
GRACE_S = 5

JsonProvider = Callable[[], Mapping[str, Any]]
# ``POST /v1/scope``: writes one editable setting, then reads the value the daemon really has
# (HDW-504). Returns the reply body; ``ok`` is false with a ``reason`` when the daemon refused.
ScopeWriter = Callable[[str, object], Mapping[str, Any]]


class KeysActions(Protocol):
    """What the dashboard needs from the keys backend: exactly four calls, no other writes."""

    def save(self, alias: str, provider: str, secret: str) -> str:
        """Store a key; returns the provider whose section the page comes back to."""
        ...

    def remove(self, alias: str) -> str:
        """Remove a stored key; returns the provider whose section the page comes back to."""
        ...

    def test(self, alias: str) -> str:
        """Test one key; returns the provider whose section the page comes back to."""
        ...

    def test_all(self) -> str:
        """Test every saved key; returns "" - no one provider section is the target."""
        ...


class WizardActions(Protocol):
    """What the dashboard needs for the wizard: one page, and one enumerated action at a time.

    ``page`` renders (it writes nothing, so a reload is always safe) and ``act`` performs exactly
    one write. ``act`` never receives a value the person was not shown: the form carries what the
    page displayed - a fingerprint, a choice, a catalog model name - and the backend re-plans and
    refuses a mismatch. The sentences an action returns are shown once, on the page.
    """

    def page(
        self,
        *,
        csrf: str = "",
        step: int | None = None,
        error: str | None = None,
        notice: str | None = None,
    ) -> str:
        """The whole wizard HTML for one step, or a page saying why there is none."""
        ...

    def act(self, action: str, form: Mapping[str, str]) -> Mapping[str, object]:
        """Run one enumerated action; ``{"ok", "step", "notice", "error"}`` comes back."""
        ...


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


def is_dashboard_authority(value: str, port: int) -> bool:
    """True when ``Host`` names *this* dashboard: loopback, and on this dashboard's own port.

    The read path answers ``421 bad_host`` for a name that is not loopback (HDW-404). A write is
    stricter: a local service on another port, or a browser that was pointed at a different port
    on this host, is not this dashboard, so the port must be the one the daemon really bound
    (``DaemonInfo.ports.dashboard``) rather than the configured default.
    """
    if not value or not is_loopback_authority(value):
        return False
    try:
        return urlsplit("//" + value).port == port
    except ValueError:
        return False


def is_dashboard_origin(value: str, port: int) -> bool:
    """True when ``Origin`` (or ``Referer``) is this dashboard's own origin, port included."""
    try:
        parts = urlsplit(value)
    except ValueError:
        return False
    return parts.scheme == "http" and is_loopback_host(parts.hostname) and parts.port == port


def _read_refusal(headers: Mapping[str, str]) -> tuple[int, str] | None:
    """The read path's guard: a foreign ``Host`` is ``421``, a foreign ``Origin`` is ``403``."""
    if not is_loopback_authority(headers.get("host", "")):
        return 421, "bad_host"
    origin = headers.get("origin")
    if origin is not None and not _origin_ok(origin):
        return 403, "bad_origin"
    return None


def _refused(status: int, code: str) -> JSONResponse:
    return JSONResponse({"ok": False, "error": {"code": code}}, status_code=status)


async def _form_body(request: Request) -> bytes | None:
    """The whole form body, or ``None`` when a key form cannot possibly be that large."""
    declared = request.headers.get("content-length", "").strip()
    if declared.isdigit() and int(declared) > MAX_FORM_BYTES:
        return None
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_FORM_BYTES:
            return None
    return bytes(body)


def _first(form: Mapping[str, list[str]], name: str) -> str:
    return form.get(name, [""])[0]


def _credentials(headers: Mapping[str, str]) -> list[str]:
    """The token a client presented: ``Authorization: Bearer``, ``x-api-key`` or both."""
    found: list[str] = []
    scheme, _, value = headers.get("authorization", "").partition(" ")
    if scheme.lower() == "bearer":
        found.append(value)
    api_key = headers.get("x-api-key")
    if api_key is not None:
        found.append(api_key)
    return found


class GuardedApp:
    """ASGI wrapper: loopback ``Host``/``Origin``, then the token, before anything runs.

    The token arrives as ``Authorization: Bearer`` (MCP, OpenAI clients) or as ``x-api-key``,
    which is the only credential an Anthropic client sends (HDW-303).
    """

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
        if any(self._matches(value) for value in _credentials(headers)):
            return None
        return 401, "unauthorized"

    def _matches(self, value: str) -> bool:
        return hmac.compare_digest(value.strip().encode("utf-8"), self._token)


class LoopbackOnlyApp:
    """ASGI wrapper for the dashboard: loopback ``Host``/``Origin`` on the read paths.

    The page holds no secret and needs no token, but a request whose ``Host`` is not loopback is
    ``421 bad_host``, which is what stops a page on another machine from pointing a browser at
    ``127.0.0.1`` here (DNS rebinding).

    The key writes (HDW-503) are exempt: they answer ``403``, in their own words, from the route
    that also checks the port, the origin and the CSRF token. ``write_paths`` is empty unless the
    app was built with a keys backend.
    """

    def __init__(self, app: ASGIApp, *, write_paths: Collection[str] = ()) -> None:
        self._app = app
        self._write_paths = frozenset(write_paths)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self._app(scope, receive, send)  # lifespan
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        refusal = self._refusal(headers, scope.get("path", ""))
        if refusal is None:
            await self._app(scope, receive, send)
        elif scope["type"] == "http":
            status, code = refusal
            response = JSONResponse({"ok": False, "error": {"code": code}}, status_code=status)
            await response(scope, receive, send)
        else:
            await send({"type": "websocket.close", "code": 1008})

    def _refusal(self, headers: Mapping[str, str], path: str = "") -> tuple[int, str] | None:
        if path in self._write_paths:
            return None  # the write route refuses with 403, so this wrapper must not pre-empt it
        return _read_refusal(headers)


class _KeysRoute:
    """One ``POST /keys/<action>``: host and port, origin, form, CSRF token, then one write.

    Every refusal is a ``403`` with a code and nothing written: a foreign ``Host`` (the wrong port
    included), a foreign or unparseable ``Origin``, a missing or wrong token. The body must be a
    small form: anything else is ``415`` or ``413`` before it is read. A refusal the person can act
    on (an alias that does not exist) comes back as the page with a sentence, at ``400``.
    """

    def __init__(
        self,
        action: str,
        keys: KeysActions,
        csrf: str,
        port: int,
        render: Callable[[str | None, str | None], str],
    ) -> None:
        self._action = action
        self._keys = keys
        self._csrf = csrf
        self._port = port
        self._render = render

    async def handle(self, request: Request) -> Response:
        headers = request.headers
        if not is_dashboard_authority(headers.get("host", ""), self._port):
            return _refused(403, "bad_host")
        origin = headers.get("origin") or headers.get("referer")
        if origin is not None and not is_dashboard_origin(origin, self._port):
            return _refused(403, "bad_origin")
        media = headers.get("content-type", "").split(";")[0].strip().lower()
        if media != FORM_MEDIA_TYPE:
            return _refused(415, "unsupported_media_type")
        body = await _form_body(request)
        if body is None:
            return _refused(413, "body_too_large")
        try:
            form = parse_qs(body.decode("utf-8"), keep_blank_values=True)
        except UnicodeDecodeError:
            return _refused(400, "bad_form")
        if not self._token_ok(_first(form, "csrf")):
            return _refused(403, "bad_csrf")
        try:
            provider = self._run(form)
        except KeyStoreError as exc:
            return await self._again(400, str(exc))
        except Exception:
            return await self._again(
                500, "the change could not be completed; the daemon log has the error"
            )
        target = f"/?section={KEYS_SECTION}"
        if provider:
            target += f"#keys-{quote(provider, safe='')}"
        return RedirectResponse(target, status_code=303)

    def _token_ok(self, given: str) -> bool:
        if not self._csrf:
            return False  # no token configured means no write is allowed, never "any token"
        return hmac.compare_digest(given.encode("utf-8"), self._csrf.encode("utf-8"))

    def _run(self, form: Mapping[str, list[str]]) -> str:
        if self._action == "save":
            alias = _first(form, "alias")
            return self._keys.save(alias, _first(form, "provider"), _first(form, "secret"))
        if self._action == "remove":
            return self._keys.remove(_first(form, "alias"))
        if self._action == "test_all":
            return self._keys.test_all()
        return self._keys.test(_first(form, "alias"))

    async def _again(self, status: int, message: str) -> HTMLResponse:
        body = await asyncio.to_thread(self._render, KEYS_SECTION, message)
        return HTMLResponse(body, status_code=status, headers={"Cache-Control": "no-store"})


class _WizardRoute:
    """``GET /wizard`` and one ``POST /wizard/<action>`` per enumerated action (HDW-505).

    The guards are the key writes' guards, applied to the widest write surface in the product:
    ``Host`` must be loopback *on this dashboard's port*, any ``Origin``/``Referer`` must be
    loopback, the body must be a small urlencoded form, and the CSRF token is compared in constant
    time. D22 stands and is not hidden: that stops a page on another origin, not another program
    running as the same user.

    A POST never puts its sentence in a URL. It stores the one line under a single-use token and
    redirects to ``GET /wizard?token=...``, so a refusal is shown once and a page's text is never
    reflected into a query string or a redirect target.
    """

    def __init__(self, *, wizard: WizardActions, csrf: str, port: int) -> None:
        self._wizard = wizard
        self._csrf = csrf
        self._port = port
        self._flash: dict[str, tuple[str, str]] = {}

    def handler(self, action: str) -> Callable[[Request], Awaitable[Response]]:
        """A Starlette handler for one action: the path decides the action, not the form."""

        async def handle(request: Request) -> Response:
            return await self._handle(request, action)

        return handle

    async def page(self, request: Request) -> Response:
        notice, error = self._recall(request.query_params.get("token", ""))
        try:
            body = await asyncio.to_thread(
                self._wizard.page,
                csrf=self._csrf,
                step=_as_step(request.query_params.get("step")),
                error=error,
                notice=notice,
            )
        except Exception:
            return HTMLResponse(PAGE_FAILED, status_code=500)
        return HTMLResponse(body, headers={"Cache-Control": "no-store"})

    async def _handle(self, request: Request, action: str) -> Response:
        headers = request.headers
        if not is_dashboard_authority(headers.get("host", ""), self._port):
            return _refused(403, "bad_host")
        origin = headers.get("origin") or headers.get("referer")
        if origin is not None and not is_dashboard_origin(origin, self._port):
            return _refused(403, "bad_origin")
        media = headers.get("content-type", "").split(";")[0].strip().lower()
        if media != FORM_MEDIA_TYPE:
            return _refused(415, "unsupported_media_type")
        body = await _form_body(request)
        if body is None:
            return _refused(413, "body_too_large")
        try:
            form = parse_qs(body.decode("utf-8"), keep_blank_values=True)
        except UnicodeDecodeError:
            return _refused(400, "bad_form")
        if not self._csrf or not hmac.compare_digest(
            _first(form, "csrf").encode("utf-8"), self._csrf.encode("utf-8")
        ):
            return _refused(403, "bad_csrf")
        try:
            outcome = await asyncio.to_thread(
                self._wizard.act, action, {key: values[0] for key, values in form.items()}
            )
        except Exception:
            return _refused(500, "action_failed")
        token = self._remember(_one_line(outcome.get("notice")), _one_line(outcome.get("error")))
        target = f"{WIZARD_PAGE_PATH}?token={quote(token, safe='')}"
        step = outcome.get("step")
        if isinstance(step, int) and not isinstance(step, bool) and step > 0:
            target += f"&step={step}"
        return RedirectResponse(target, status_code=303)

    def _remember(self, notice: str, error: str) -> str:
        token = secrets.token_hex(8)
        self._flash[token] = (notice, error)
        while len(self._flash) > MAX_FLASH:
            self._flash.pop(next(iter(self._flash)))
        return token

    def _recall(self, token: str) -> tuple[str | None, str | None]:
        notice, error = self._flash.pop(token, ("", "")) if token else ("", "")
        return (notice or None, error or None)


def _one_line(value: object) -> str:
    return "" if value is None else " ".join(str(value).split())[:500]


def _as_step(value: str | None) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def build_status_page_app(
    *,
    render: Callable[[str | None, str | None], str],
    keys: KeysActions | None = None,
    csrf: str = "",
    port: int | None = None,
    wizard: WizardActions | None = None,
) -> ASGIApp:
    """The dashboard: the page on ``GET /`` plus its four key writes on ``POST /keys/*`` (HDW-503).

    ``render(section, error)`` returns the whole HTML body: ``section`` is ``"keys"`` when the
    request asked for that section first (``?section=keys``) and ``error`` is a sentence to show
    when a write was refused. It runs off the event loop: it reads rules and the shared SQLite
    state. The writes are registered only when a backend *and* this dashboard's port are given,
    because the guard needs the port the socket really bound.

    ``wizard`` adds the setup wizard (HDW-505): ``GET /wizard`` and the nine enumerated writes in
    ``core.wizard.WIZARD_PATHS``. It is served from this same app rather than a listener of its
    own (D14: the daemon is the composition root), so it shares the one CSRF token, the one origin
    check and the one port guard. The consequence is stated in its report and on its own page: while
    the daemon is down there is no wizard, so it cannot be the surface that starts the daemon.
    """

    async def get_page(request: Request) -> HTMLResponse:
        try:
            body = await asyncio.to_thread(render, request.query_params.get("section"), None)
        except Exception:
            return HTMLResponse(PAGE_FAILED, status_code=500)
        return HTMLResponse(body, headers={"Cache-Control": "no-store"})

    routes: list[Route] = [Route(path, get_page, methods=["GET"]) for path in STATUS_PAGE_PATHS]
    write_paths: list[str] = []
    if keys is not None and port is not None:
        write_paths += list(KEYS_PATHS)
        routes += [
            Route(path, _KeysRoute(action, keys, csrf, port, render).handle, methods=["POST"])
            for path, action in KEYS_PATHS.items()
        ]
    if wizard is not None and port is not None:
        wizard_route = _WizardRoute(wizard=wizard, csrf=csrf, port=port)
        write_paths += list(WIZARD_PATHS)
        routes.append(Route(WIZARD_PAGE_PATH, wizard_route.page, methods=["GET"]))
        routes += [
            Route(path, wizard_route.handler(action), methods=["POST"])
            for path, action in WIZARD_PATHS.items()
        ]
    return LoopbackOnlyApp(Starlette(routes=routes), write_paths=write_paths)


def build_app(
    *,
    token: str,
    health: JsonProvider,
    status: JsonProvider,
    on_shutdown: Callable[[], None],
    on_reload: JsonProvider | None = None,
    on_scope: ScopeWriter | None = None,
    on_keys: JsonProvider | None = None,
    mcp_app: ASGIApp | None = None,
    mcp_lifespan: Callable[[], AbstractAsyncContextManager[None]] | None = None,
) -> ASGIApp:
    """The daemon's ASGI app. ``health`` is public; ``status``, reload and shutdown need the
    token. ``on_reload`` re-reads rules.yaml now and returns the live switches (HDW-148).
    ``on_scope`` writes one editable setting and reports the value in force, and ``on_keys``
    lists the providers with where each key came from, for the tray (HDW-504); both are refused
    with 501 when the daemon was built without them."""

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

    async def post_scope(request: Request) -> JSONResponse:
        if on_scope is None:
            return JSONResponse({"ok": False, "error": {"code": "not_supported"}}, status_code=501)
        try:
            body = await request.json()
        except Exception:
            body = None
        key = body.get("key") if isinstance(body, dict) else None
        value = body.get("value") if isinstance(body, dict) else None
        if not isinstance(key, str) or not isinstance(value, str | bool):
            return JSONResponse(
                {
                    "ok": False,
                    "error": {
                        "code": "invalid_request",
                        "message": 'send {"key": "default_scope"|"private_lock", "value": ...}',
                    },
                },
                status_code=400,
            )
        # The write parses and rewrites rules.yaml and may rebuild the engine: off the loop.
        try:
            reply = await asyncio.to_thread(on_scope, key, value)
        except ValueError as exc:  # not a setting a surface may edit
            return JSONResponse({"ok": False, "key": key, "reason": str(exc)}, status_code=400)
        except Exception:
            return JSONResponse(
                {"ok": False, "key": key, "reason": "the daemon could not write rules.yaml"},
                status_code=500,
            )
        return JSONResponse(dict(reply), status_code=200 if reply.get("ok") else 409)

    async def get_keys(request: Request) -> JSONResponse:
        if on_keys is None:
            return JSONResponse({"ok": False, "error": {"code": "not_supported"}}, status_code=501)
        # The alias index lives in the credential store: a blocking read, so off the loop.
        try:
            reply = await asyncio.to_thread(on_keys)
        except Exception:
            return JSONResponse({"ok": False, "error": {"code": "keys_failed"}}, status_code=500)
        return JSONResponse({"ok": True, **reply})

    routes: list[Route | Mount] = [
        Route(HEALTH_PATH, get_health, methods=["GET"]),
        Route("/v1/status", get_status, methods=["GET"]),
        Route("/v1/reload", post_reload, methods=["POST"]),
        Route(SCOPE_PATH, post_scope, methods=["POST"]),
        Route(KEYS_PATH, get_keys, methods=["GET"]),
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
