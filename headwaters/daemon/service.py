"""The daemon: owns the router, catalog refresh and keep-warm (HDW-201)."""

from __future__ import annotations

import asyncio
import contextlib
import os
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import headwaters
from headwaters.core import netports
from headwaters.core.catalog import discovery, updater
from headwaters.core.catalog.http import UrllibHttp
from headwaters.core.daemon_info import DaemonInfo, DaemonPorts, write_info
from headwaters.core.engine import Engine, build_engine
from headwaters.core.keepwarm import KeepWarm, KeepWarmResult
from headwaters.core.key_poll import KeyPollResult, poll_paid_walls
from headwaters.core.logging import get_logger
from headwaters.core.quotas import QuotaTracker
from headwaters.core.rules import Rules, RulesProvider
from headwaters.core.state import Database
from headwaters.gateway.server import HttpServer, build_app
from headwaters.mcp.endpoint import build_endpoint
from headwaters.mcp.jobs import recover_orphans
from headwaters.mcp.tools.context import ToolContext

__all__ = ["Daemon", "pick_ports", "run_in_daemon_thread"]

KEEP_WARM_TIMEOUT_S = 120.0
KEY_POLL_TIMEOUT_S = 15.0
KEY_ENV = {"openrouter": "OPENROUTER_API_KEY"}  # litellm's standard variable (HDW-401: keyring)
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
    _stop: asyncio.Event | None = None
    # The rules the current engine was built from. Only the rebuild hook sets it, so a rebuild
    # started by the catalog refresh never goes back to older rules (and never has to read the
    # manager while it holds the build lock).
    _built_with: Rules | None = None
    _build_lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def live_rules(self) -> Rules:
        return self.rules_source()

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
                self.rules_source, catalog, source, self.db, snapshot=snapshot
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
            "keep_warm": None if warm is None else {"action": warm.action, "reason": warm.reason},
            "last_refresh": self.last_refresh,
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
        )
        result = warm.tick()
        self.last_keep_warm = result
        return result

    def key_poll_once(self) -> KeyPollResult:
        """Check the balance of each key with a spent paid wall; a passing check lifts the wall.

        Same blocking rule as discovery (airplane, Private lock, local default scope), read from
        the live rules. Blocking: it runs in a background thread. The key comes from the
        provider's standard environment variable (the credential store is HDW-401).
        """
        engine = self.engine
        catalog = engine.catalog if engine is not None else updater.load_active()[0]
        quotas = QuotaTracker(self.db, lambda: self.live_rules, catalog.free_limits)
        result = poll_paid_walls(
            self.db,
            quotas,
            UrllibHttp(timeout=KEY_POLL_TIMEOUT_S),
            self.live_rules,
            lambda provider: os.environ.get(KEY_ENV.get(provider, "")) or None,
            key_url=f"{catalog.endpoints.openrouter_base_url}/key",
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
        if self._server is not None:
            self._server.request_shutdown()

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
            mcp_app=endpoint.app,
            mcp_lifespan=endpoint.lifespan,
        )
        self._server = HttpServer(app, self.sock)
        serving = asyncio.create_task(self._server.serve())
        tasks = [
            asyncio.create_task(self._announce(serving)),
            asyncio.create_task(self._every(self.refresh_interval, self.refresh_once)),
            asyncio.create_task(self._every(self.keep_warm_interval, self.keep_warm_once)),
            asyncio.create_task(self._every(self.key_poll_interval, self.key_poll_once)),
        ]
        try:
            await serving
        finally:
            self._stop.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _announce(self, serving: asyncio.Task[None]) -> None:
        """Write the state file only once the server really accepts connections."""
        while not (self._server is not None and self._server.started):
            if serving.done():
                return
            await asyncio.sleep(0.05)
        write_info(self.info())
        _log.info("daemon listening", extra={"port": self.ports.api, "pid": os.getpid()})


def pick_ports(
    rules: Rules, preferred: int | None, api_default: int
) -> tuple[socket.socket, DaemonPorts]:
    """Bind the API socket (kept) and probe free ports for the gateway and the dashboard."""
    excluded = netports.read_excluded_ranges()
    sock = netports.bind_first_free(preferred or api_default, excluded=excluded)
    api = int(sock.getsockname()[1])
    taken = {api}
    gateway = netports.pick_free_port(rules.gateway.port, excluded=excluded, taken=taken)
    taken.add(gateway)
    dashboard = netports.pick_free_port(rules.dashboard.port, excluded=excluded, taken=taken)
    return sock, DaemonPorts(api=api, gateway=gateway, dashboard=dashboard)
