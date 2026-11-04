"""The daemon: owns the router, catalog refresh and keep-warm (HDW-201)."""

from __future__ import annotations

import asyncio
import contextlib
import os
import secrets
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import headwaters
from headwaters.control.keys import DashboardKeys
from headwaters.control.status_page import build_page, render_html
from headwaters.core import netports, rules_edit
from headwaters.core.catalog import discovery, updater
from headwaters.core.catalog.http import UrllibHttp
from headwaters.core.daemon_info import DaemonInfo, DaemonPorts, write_info
from headwaters.core.engine import Engine, build_engine
from headwaters.core.features import FeatureRegistry, discover_features
from headwaters.core.keepwarm import KeepWarm, KeepWarmResult, describe
from headwaters.core.key_poll import KeyPollResult, poll_paid_walls
from headwaters.core.keys import NOT_SET, KeyStore, provider_items
from headwaters.core.logging import get_logger
from headwaters.core.project_config import ProjectConfig, load_project_config
from headwaters.core.quotas import QuotaTracker
from headwaters.core.router import Router
from headwaters.core.rules import Rules, RulesProvider
from headwaters.core.state import Database
from headwaters.core.state_decisions import get_outcome, latest_decision_ts, recent_decisions
from headwaters.core.state_limits import active_walls
from headwaters.core.state_models import get_model, list_models
from headwaters.features import tray as tray_feature
from headwaters.gateway.openai_routes import build_gateway_app
from headwaters.gateway.server import HttpServer, build_app, build_status_page_app
from headwaters.mcp.endpoint import build_endpoint
from headwaters.mcp.jobs import recover_orphans
from headwaters.mcp.tools.context import ToolContext

__all__ = ["Daemon", "pick_ports", "run_in_daemon_thread"]

KEEP_WARM_TIMEOUT_S = 120.0
KEY_POLL_TIMEOUT_S = 15.0
# How long after a spawn the daemon looks for a tray that died on its first tick, and how often.
TRAY_START_GRACE_S = 1.0
TRAY_POLL_INTERVAL_S = 0.05
_log = get_logger("daemon")


async def run_in_daemon_thread(work: Callable[[], object]) -> None:
    """Run blocking ``work`` off the event loop in a *daemon* thread and wait for it.

    ``asyncio.to_thread`` uses the default executor, which ``asyncio.run`` joins on exit, so a
    slow network call in a background task would keep a stopped daemon alive (seen on Windows,
    where loading the certificate store alone takes seconds). A daemon thread is left behind
    instead; SQLite writes are transactions, so ending mid-task loses nothing.
    """
    loop = asyncio.get_running_loop()
    done: asyncio.Future[None] = loop.create_future()

    def finish(error: BaseException | None) -> None:
        if done.done():
            return
        if error is None:
            done.set_result(None)
        else:
            done.set_exception(error)

    def target() -> None:
        error: BaseException | None = None
        try:
            work()
        except Exception as exc:
            error = exc
        with contextlib.suppress(RuntimeError):  # the loop may already be closed
            loop.call_soon_threadsafe(finish, error)

    threading.Thread(target=target, name="headwaters-background", daemon=True).start()
    await done


@dataclass
class Daemon:
    db: Database
    sock: socket.socket
    ports: DaemonPorts
    token: str
    # ``RulesManager.current`` in production: the live rules, read at each use (HDW-131). The
    # daemon keeps no copy of its own (HDW-148, FU-131-2).
    rules_source: RulesProvider
    # The gateway socket bound by ``pick_ports`` (FU-201-6): the OpenAI-compatible app is served
    # on it, so the port in daemon.json is the one that is really listening. None in tests and
    # when ``gateway.enabled`` is false.
    gateway_sock: socket.socket | None = None
    # The dashboard socket bound by ``pick_ports`` (HDW-404): the read-only status page is served
    # on it. ``None`` in tests and when ``dashboard.enabled`` is false at startup.
    dashboard_sock: socket.socket | None = None
    # The credential store (HDW-401). Production leaves it None: a store over the *live* rules is
    # built at each use, so a change to the endpoint list is seen. Tests inject one over a fake
    # keyring backend. The executor, the router's key-scoped walls and the key-poll timer all read
    # this one keyring, so they agree on a key's alias.
    keys: KeyStore | None = None
    # The dashboard's CSRF token (HDW-503): one random value per daemon start, held in memory only.
    # It is what a page this daemon did not serve cannot produce, and it never reaches
    # ``daemon.json`` or a log line. A restart changes it, which only means the page must be
    # reloaded before its forms work again.
    dashboard_csrf: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    started_at: float = field(default_factory=time.time)
    engine: Engine | None = None
    last_keep_warm: KeepWarmResult | None = None
    last_refresh: str = "never"
    last_key_poll: KeyPollResult | None = None
    # ``RulesManager.reload`` in production: re-reads rules.yaml now (True when valid rules are in
    # force). ``headwaters_scope`` and ``POST /v1/reload`` call it, so a change is seen by the
    # next call without waiting for the file watcher.
    reload_rules: Callable[[], object] | None = None
    # ``RulesManager.last_error`` in production: why the last reload was rejected, or ``None``.
    rules_error: Callable[[], object] | None = None
    _server: HttpServer | None = None
    _gateway_server: HttpServer | None = None
    _dashboard_server: HttpServer | None = None
    _stop: asyncio.Event | None = None
    # The feature registry the status page shows: discovered once, flags re-read per page.
    _registry: FeatureRegistry | None = None
    # The rules the current engine was built from. Only the rebuild hook sets it, so a rebuild
    # started by the catalog refresh never goes back to older rules (and never has to read the
    # manager while it holds the build lock).
    _built_with: Rules | None = None
    _build_lock: threading.Lock = field(default_factory=threading.Lock)
    # The tray child this daemon started (HDW-504), or None. It is never waited on and never
    # killed on shutdown: a tray with no daemon is a normal red icon, and that is what lets the
    # tray restart the daemon.
    tray_child: Any = None

    @property
    def live_rules(self) -> Rules:
        return self.rules_source()

    @property
    def key_store(self) -> KeyStore:
        """The credential store in force: the injected one, else one over the live rules."""
        if self.keys is not None:
            return self.keys
        return KeyStore(self.live_rules)

    def rebuild_engine(self, rules: Rules | None = None, *, reuse_catalog: bool = False) -> Engine:
        """Build an engine and swap it in. A call already running keeps the engine it started on.

        ``rules`` is the reading the build uses (the rebuild hook passes the rules it was told
        about). ``reuse_catalog`` keeps the loaded catalog instead of reading it from disk.
        """
        # The live rules are read outside the lock: reading them may run the rebuild hook, which
        # takes the lock itself.
        first = self.live_rules if rules is None and self._built_with is None else None
        with self._build_lock:
            snapshot = rules if rules is not None else (self._built_with or first)
            assert snapshot is not None  # ``first`` is read whenever nothing else is known
            current = self.engine
            if reuse_catalog and current is not None:
                catalog, source = current.catalog, current.catalog_source
            else:
                catalog, source = updater.load_active()
            self.engine = build_engine(
                self.rules_source,
                catalog,
                source,
                self.db,
                snapshot=snapshot,
                key_store=self.keys,
            )
            self._built_with = snapshot
            return self.engine

    def on_rules_changed(self, rules: Rules) -> None:
        """``RulesManager`` calls this once new valid rules are in force: rebuild the engine.

        It may run on a request thread (the manager reloads at the next read), and the manager
        holds readers back until it returns, so the next request already sees the new engine.
        """
        self.rebuild_engine(rules, reuse_catalog=True)

    def reload_now(self) -> dict[str, Any]:
        """``POST /v1/reload``: re-read rules.yaml now and report the switches that are live."""
        accepted = bool(self.reload_rules()) if self.reload_rules is not None else True
        rules = self.live_rules  # after the reload: a privacy switch must be true when it answers
        reply: dict[str, Any] = {
            "applied": accepted,
            "private_lock": rules.private_lock,
            "airplane": rules.airplane,
            "default_scope": rules.default_scope,
        }
        if not accepted:
            error = self.rules_error() if self.rules_error is not None else None
            reply["error"] = (
                str(error) if error else "rules.yaml was rejected; the last good rules stay."
            )
        return reply

    def set_setting_now(self, key: str, value: bool | str) -> dict[str, Any]:
        """``POST /v1/scope``: write one editable setting, then report what is really in force.

        The write goes through ``rules_edit.set_setting`` -- the function ``headwaters scope``
        and the MCP ``headwaters_scope`` tool already call -- and is followed by the same
        reload-and-report path as ``POST /v1/reload``. So the tray, the CLI and the dashboard
        cannot disagree about what "scope is local" means: all of them read this answer.
        """
        previous, written = rules_edit.set_setting(key, value)
        report = self.reload_now()
        # A key this reply does not carry (``strategy``) cannot be confirmed against the live
        # rules here; the file was validated before it was written, so it is taken as written.
        live = report.get(key, written)
        applied = bool(report.get("applied")) and live == written
        reply: dict[str, Any] = {
            "ok": applied,
            "key": key,
            "value": written,
            "previous": previous,
            "default_scope": report.get("default_scope"),
            "private_lock": report.get("private_lock"),
            "airplane": report.get("airplane"),
            "applied": bool(report.get("applied")),
        }
        if not applied:
            reply["reason"] = str(report.get("error") or "the daemon kept its previous value")
        return reply

    def keys_payload(self) -> dict[str, Any]:
        """``GET /v1/keys``: every provider with its alias and where the key came from.

        ``provider``, ``alias`` and ``source`` only: no environment variable, no masked value
        and no secret leaves the daemon for a surface to show (HDW-504, D18).
        """
        return {"providers": provider_items(self.key_store)}

    def tray_status(self) -> dict[str, Any]:
        """The ``tray`` block of ``/v1/status``: everything the icon and the menu show."""
        rules = self.live_rules
        store = self.key_store
        rows = recent_decisions(self.db, limit=1)
        latest = rows[0] if rows else None
        outcome = get_outcome(self.db, latest.id) if latest is not None else None
        tier = None
        if latest is not None:
            model = get_model(self.db, latest.chosen)
            tier = None if model is None else model.tier
        warm = next((model.id for model in list_models(self.db) if model.warm), None)
        try:
            keys_set = sum(1 for info in store.entries() if info.source != NOT_SET)
        except Exception:  # a locked or broken store must not break /v1/status
            _log.warning("could not read the credential store", exc_info=True)
            keys_set = 0
        return {
            "scope": rules.default_scope,
            # Private Mode and Airplane both mean "nothing leaves this machine", so either one
            # makes the scope local; the purple lock is what says Private Mode itself.
            "effective_scope": (
                "local" if rules.private_lock or rules.airplane else rules.default_scope
            ),
            "tier": tier,
            "warm_model": warm,
            "walls": [wall.kind for wall in active_walls(self.db)],
            "last_status": None if outcome is None else outcome.status,
            "below_floor": bool(latest.below_floor) if latest is not None else False,
            "keys_set": keys_set,
            "anonymous_provider": any(not p.key_required for p in store.providers()),
        }

    def start_tray(self, launcher: Callable[[], Any] | None = None) -> Any:
        """Start the tray as this daemon's child when the ``tray`` feature flag is on (HDW-504).

        The logon task already runs ``headwaters start``, so a daemon that starts the tray means
        the one existing toggle (``features: {tray: true}``) controls both and no second
        scheduled task is added. Never raises: a tray that will not start must not stop the
        daemon. Returns the child, or None when the tray is off or unsupported here.
        """
        if launcher is None:
            if os.name != "nt":
                return None  # the notification-area backend exists on Windows only
            if os.environ.get(tray_feature.TEST_ENV):
                return None  # a test process must never leave a real icon behind
            if not self.live_rules.features.get(tray_feature.FEATURE_ID, tray_feature.DEFAULT):
                return None
            launcher = tray_feature.spawn_child
        try:
            self.tray_child = launcher()
        except Exception:  # a tray problem is not a daemon fault
            _log.warning("could not start the tray", exc_info=True)
            return None
        self._report_tray_death(self.tray_child)
        return self.tray_child

    @staticmethod
    def _report_tray_death(child: Any) -> None:
        """Say so when the tray dies right after starting, with the tail of its own log.

        A tray that exits in its first second used to be invisible: the daemon held a dead
        ``Popen`` and logged nothing, so no icon and no symptom ever met. Still never raises.
        """
        poll = getattr(child, "poll", None)
        if not callable(poll):
            return
        try:
            deadline = time.monotonic() + TRAY_START_GRACE_S
            while poll() is None and time.monotonic() < deadline:
                time.sleep(TRAY_POLL_INTERVAL_S)
            code = poll()
        except Exception:  # a stand-in child must not cost the daemon anything
            return
        if code is None:
            return
        tail = tray_feature.log_tail()
        detail = f"\n{tail}" if tail else ""
        if code == 0:
            # The instance lock is the ordinary reason: another tray already holds the icon.
            _log.info("the tray exited at once (exit 0); a tray may already be running%s", detail)
        else:
            _log.warning(
                "the tray exited at once (exit %s), so no icon will show; see %s%s",
                code,
                tray_feature.log_path(),
                detail,
            )

    def live_engine(self) -> Engine | None:
        """The engine a new request should use. Reading the rules first picks up an edit of
        rules.yaml and, through the rebuild hook, swaps in the engine built for it, so the
        request that notices a change already runs on the new engine (HDW-148)."""
        self.live_rules  # noqa: B018 - the read is the point: it reloads a changed file
        return self.engine

    def tool_context(self) -> ToolContext:
        """What the MCP tools read per call: the live engine and the live rules (HDW-204)."""
        return ToolContext(
            engine=self.live_engine,
            rules=lambda: self.live_rules,
            db=self.db,
            reload_rules=self.reload_rules or (lambda: None),
            configured_roots=lambda: self.live_rules.workspace.roots,
            key_store=lambda: self.key_store,
        )

    # ------------------------------------------------------------------ http payloads

    def health(self) -> dict[str, Any]:
        return {
            "pid": os.getpid(),
            "version": headwaters.__version__,
            "uptime_s": round(time.time() - self.started_at, 1),
        }

    def status(self) -> dict[str, Any]:
        rules = self.live_rules  # first: a changed file rebuilds the engine read next
        engine = self.engine
        warm = self.last_keep_warm
        catalog = None
        if engine is not None:
            catalog = {"source": engine.catalog_source, "version": engine.catalog.catalog_version}
        return {
            **self.health(),
            "ports": {
                "api": self.ports.api,
                "gateway": self.ports.gateway,
                "dashboard": self.ports.dashboard,
            },
            "scope": rules.default_scope,
            "private_lock": rules.private_lock,
            "airplane": rules.airplane,
            "catalog": catalog,
            "keep_warm": (
                None
                if warm is None
                else {"action": warm.action, "reason": warm.reason, "expires_at": warm.expires_at}
            ),
            "last_refresh": self.last_refresh,
            "tray": self.tray_status(),
        }

    def info(self) -> DaemonInfo:
        return DaemonInfo(os.getpid(), self.started_at, headwaters.__version__, self.ports)

    # ------------------------------------------------------------------ background work

    def refresh_once(self) -> None:
        """Catalog update (when allowed), then a model refresh when stale. Blocking."""
        changed = False
        try:
            result = updater.update(self.live_rules, manual=False)
            changed = result.outcome == "updated"
            self.last_refresh = result.outcome
        except Exception:  # a refresh problem must never stop the daemon
            _log.warning("catalog update failed", exc_info=True)
        try:
            catalog, _source = updater.load_active()
            rules = self.live_rules
            if discovery.needs_refresh(self.db, rules):
                found = discovery.refresh_models(self.db, rules, catalog)
                _log.info("models refreshed", extra={"models": found.models})
        except Exception:
            _log.warning("model refresh failed", exc_info=True)
        if changed or self.engine is None:
            self.rebuild_engine()  # a new catalog: read it from disk, keep the rules in force

    def keep_warm_once(self) -> KeepWarmResult:
        engine = self.engine
        warm = KeepWarm(
            self.live_rules,
            UrllibHttp(timeout=KEEP_WARM_TIMEOUT_S),
            remote_patterns=engine.remote_patterns if engine is not None else (),
            hardware=engine.hardware if engine is not None else (lambda: None),
            last_request=lambda: latest_decision_ts(self.db),
        )
        result = warm.tick()
        previous, self.last_keep_warm = self.last_keep_warm, result
        # One line per state change, not per tick: the interval is minutes and the tick is quiet.
        if previous is None or (previous.action, previous.reason) != (result.action, result.reason):
            _log.info(
                describe(result, warm.interval_s()),
                extra={"action": result.action, "reason": result.reason},
            )
        return result

    def key_poll_once(self) -> KeyPollResult:
        """Check the balance of each key with a spent paid wall; a passing check lifts the wall.

        Same blocking rule as discovery (airplane, Private lock, local default scope), read from
        the live rules. Blocking: it runs in a background thread. The key is the one the executor
        would use: the credential store first, then the environment variable (HDW-401), so a key
        saved with ``headwaters keys set`` also lifts its own paid wall.
        """
        engine = self.engine
        catalog = engine.catalog if engine is not None else updater.load_active()[0]
        quotas = QuotaTracker(self.db, lambda: self.live_rules, catalog.free_limits)
        store = self.key_store
        result = poll_paid_walls(
            self.db,
            quotas,
            UrllibHttp(timeout=KEY_POLL_TIMEOUT_S),
            self.live_rules,
            store.secret_for,
            key_url=f"{catalog.endpoints.openrouter_base_url}/key",
            provider_for=store.provider_for,
        )
        self.last_key_poll = result
        return result

    def key_poll_interval(self) -> float:
        return max(60.0, self.live_rules.walls.key_poll_minutes * 60)

    def keep_warm_interval(self) -> float:
        return KeepWarm(self.live_rules, UrllibHttp()).interval_s()

    def refresh_interval(self) -> float:
        return max(60.0, self.live_rules.models.refresh_ttl_s)

    async def _every(self, seconds: Callable[[], float], work: Callable[[], object]) -> None:
        stop = self._stop
        assert stop is not None
        while not stop.is_set():
            try:
                await run_in_daemon_thread(work)
            except Exception:
                _log.warning("background task failed", exc_info=True)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=seconds())

    # ------------------------------------------------------------------ lifecycle

    def request_stop(self) -> None:
        if self._stop is not None:
            self._stop.set()
        for server in (self._server, self._gateway_server, self._dashboard_server):
            if server is not None:
                server.request_shutdown()

    def gateway_app(self) -> Any:
        """The OpenAI-compatible app (HDW-301), reading the live engine and rules per request."""
        return build_gateway_app(
            token=self.token,
            router=self.gateway_router,
            rules=lambda: self.live_rules,
        )

    def gateway_router(self) -> Router | None:
        """The router of the engine in force, or None while the daemon is still building one."""
        engine = self.live_engine()
        return None if engine is None else engine.router

    def feature_registry(self) -> FeatureRegistry:
        """The registry the status page shows: discovered once, flags re-read from live rules."""
        registry = self._registry
        if registry is None:
            registry = FeatureRegistry()
            discover_features(registry)
            self._registry = registry
        registry.set_flags(self.live_rules.features)
        return registry

    def dashboard_page(self, section: str | None = None, error: str | None = None) -> str:
        """The status page (HDW-404, HDW-503), built from the live rules and the shared state.

        Blocking (SQLite reads and the credential store): ``build_status_page_app`` runs it off
        the event loop. The project rule is read from the daemon's working directory, as
        ``headwaters status`` does; an unreadable project file must not take the page down, so it
        falls back to the defaults. ``section`` is ``keys`` when the request asked for that
        section first, and ``error`` is the sentence a refused write came back with.
        """
        rules = self.live_rules
        try:
            project, project_path = load_project_config(Path.cwd())
        except Exception:  # an invalid .headwaters.yaml is reported by `headwaters status`
            _log.warning("project config unreadable", exc_info=True)
            project, project_path = ProjectConfig(), None
        engine = self.engine
        catalog = None
        if engine is not None:
            catalog = f"{engine.catalog_source} {engine.catalog.catalog_version}".strip()
        data = build_page(
            rules=rules,
            project=project,
            project_path=project_path,
            db=self.db,
            registry=self.feature_registry(),
            keys=self.key_store,
            csrf=self.dashboard_csrf,
            version=headwaters.__version__,
            catalog=catalog,
        )
        return render_html(data, section=section, error=error)

    async def serve(self) -> None:
        self._stop = asyncio.Event()
        # A previous daemon took its background jobs with it: fail them so none stays "running".
        # One short UPDATE, so it runs here before the first request, not on a worker thread.
        failed = recover_orphans(self.db)
        if failed:
            _log.info("failed jobs left by a previous daemon", extra={"jobs": failed})
        endpoint = build_endpoint(headwaters.__version__, tools=self.tool_context())
        app = build_app(
            token=self.token,
            health=self.health,
            status=self.status,
            on_shutdown=self.request_stop,
            on_reload=self.reload_now,
            on_scope=self.set_setting_now,
            on_keys=self.keys_payload,
            mcp_app=endpoint.app,
            mcp_lifespan=endpoint.lifespan,
        )
        self._server = HttpServer(app, self.sock)
        servers = [self._server]
        if self.gateway_sock is not None:
            self._gateway_server = HttpServer(self.gateway_app(), self.gateway_sock)
            servers.append(self._gateway_server)
        if self.dashboard_sock is not None:
            page = build_status_page_app(
                render=self.dashboard_page,
                keys=DashboardKeys(
                    store=lambda: self.key_store,
                    rules=lambda: self.live_rules,
                    db=self.db,
                ),
                csrf=self.dashboard_csrf,
                # The port the socket really bound, not ``rules.dashboard.port`` (FU-201-6): the
                # write guard compares the Host against the port a person can actually reach.
                port=self.ports.dashboard,
            )
            self._dashboard_server = HttpServer(page, self.dashboard_sock)
            servers.append(self._dashboard_server)
        serving = [asyncio.create_task(server.serve()) for server in servers]
        self.start_tray()
        tasks = [
            asyncio.create_task(self._announce(serving, servers)),
            asyncio.create_task(self._every(self.refresh_interval, self.refresh_once)),
            asyncio.create_task(self._every(self.keep_warm_interval, self.keep_warm_once)),
            asyncio.create_task(self._every(self.key_poll_interval, self.key_poll_once)),
        ]
        try:
            await asyncio.gather(*serving)
        finally:
            self._stop.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _announce(self, serving: list[asyncio.Task[None]], servers: list[HttpServer]) -> None:
        """Write the state file only once every server really accepts connections."""
        while not all(server.started for server in servers):
            if any(task.done() for task in serving):
                return
            await asyncio.sleep(0.05)
        write_info(self.info())
        _log.info(
            "daemon listening",
            extra={
                "port": self.ports.api,
                "gateway": self.ports.gateway,
                "dashboard": self.ports.dashboard,
                "pid": os.getpid(),
            },
        )


def pick_ports(
    rules: Rules, preferred: int | None, api_default: int
) -> tuple[socket.socket, socket.socket | None, socket.socket | None, DaemonPorts]:
    """Bind the API socket and, when enabled, the gateway and dashboard sockets. All are kept.

    FU-201-6: the gateway port used to be probed and recorded while nothing listened on it. It is
    bound through ``bind_first_free`` here and served by the daemon, so daemon.json names the port
    actually taken. HDW-404 does the same for the dashboard port, which now serves the read-only
    status page: ``ports.dashboard`` is ``None`` when the dashboard is switched off, because
    nothing listens then (the bind is at startup, so switching it on needs a daemon restart).
    """
    excluded = netports.read_excluded_ranges()
    sock = netports.bind_first_free(preferred or api_default, excluded=excluded)
    api = int(sock.getsockname()[1])
    taken = {api}
    gateway_sock: socket.socket | None = None
    gateway: int | None = None
    if rules.gateway.enabled:
        gateway_sock = netports.bind_first_free(rules.gateway.port, excluded=excluded, taken=taken)
        gateway = int(gateway_sock.getsockname()[1])
        taken.add(gateway)
    dashboard_sock: socket.socket | None = None
    dashboard: int | None = None
    if rules.dashboard.enabled:
        dashboard_sock = netports.bind_first_free(
            rules.dashboard.port, excluded=excluded, taken=taken
        )
        dashboard = int(dashboard_sock.getsockname()[1])
    return (
        sock,
        gateway_sock,
        dashboard_sock,
        DaemonPorts(api=api, gateway=gateway, dashboard=dashboard),
    )
