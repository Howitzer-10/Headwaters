"""Shared rig for the HDW-148 tests: a real ``Daemon`` over a real ``RulesManager``.

The daemon is wired as ``daemon/runner.py`` wires it (rules source, reload, rebuild hook) with two
differences: the catalog is the seed file, and every engine it builds gets scripted backends, so
no network or GPU is touched. ``rig.builds`` has one entry per engine built, newest last.
"""

from __future__ import annotations

import asyncio
import os
import socket
from collections import deque
from collections.abc import AsyncIterator, Callable, Sequence
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from headwaters.core import paths
from headwaters.core.catalog.http import SourceError
from headwaters.core.catalog.loader import SEED_RELATIVE_PATH, load_catalog
from headwaters.core.daemon_client import DaemonClient
from headwaters.core.daemon_info import DaemonInfo, DaemonPorts
from headwaters.core.engine import Engine, build_engine
from headwaters.core.executor.types import BackendRequest, Delta
from headwaters.core.router_types import RouteRequest, RouteResult
from headwaters.core.rules_watch import RulesManager
from headwaters.core.state import open_database
from headwaters.daemon import service
from headwaters.daemon.service import Daemon
from headwaters.gateway.server import build_app
from tests.cli_support import add_model, gpu_state

REPO_ROOT = Path(__file__).resolve().parent.parent
TOKEN = "t0ken-for-tests"
INFO = DaemonInfo(4321, 1.0, "0.0.0", DaemonPorts(4002, 4000, 4001))
ASK = [{"role": "user", "content": "write a function that adds two numbers"}]


class Backend:
    """Answers ``reply``; with a ``gate`` it first waits for the test to open it."""

    def __init__(self, reply: str, gate: asyncio.Event | None = None) -> None:
        self.reply = reply
        self.gate = gate
        self.requests: list[BackendRequest] = []

    async def stream(self, request: BackendRequest) -> AsyncIterator[Delta]:
        self.requests.append(request)
        if self.gate is not None:
            await self.gate.wait()
        yield Delta(content=self.reply)
        yield Delta(finish_reason="stop")


Step = Delta | BaseException


class ProgrammableProvider(Backend):
    """``Backend`` for every model, programmed per model id (HDW-305).

    ``on(model, *calls)`` queues the next calls to that model: each argument is one call's script,
    the steps of which play in order, so an agent loop's two turns are two arguments. A step that
    is an exception is raised where it sits, which is how a wall is played. A model with no queue
    answers ``reply``, so a test only writes the turns it is about.
    """

    def __init__(self, reply: str = "answer from the mock") -> None:
        super().__init__(reply)
        self.calls: list[str] = []  # every model call, in order
        self._queues: dict[str, deque[Sequence[Step]]] = {}

    def on(self, model: str, *calls: Sequence[Step]) -> ProgrammableProvider:
        """Queue the next calls to ``model``."""
        self._queues.setdefault(model, deque()).extend(calls)
        return self

    async def stream(self, request: BackendRequest) -> AsyncIterator[Delta]:
        self.requests.append(request)
        self.calls.append(request.target.model_id)
        queue = self._queues.get(request.target.model_id)
        if not queue:
            yield Delta(content=self.reply)
            yield Delta(finish_reason="stop")
            return
        for step in queue.popleft():
            if isinstance(step, BaseException):
                raise step
            yield step


class DaemonRig:
    def __init__(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        *,
        cloud: bool = False,
        home: Path | None = None,
        provider: Backend | None = None,
    ) -> None:
        monkeypatch.setenv(paths.HOME_ENV_VAR, str(home or tmp_path / "home"))
        self.catalog = load_catalog(REPO_ROOT / SEED_RELATIVE_PATH)
        self.builds: list[Engine] = []
        self.backends: list[Backend] = []
        self.hosts: list[str] = []  # the Ollama host of every local call
        self.cloud_clients = 0
        self.db = open_database(tmp_path / "state.db")
        add_model(self.db, "qwen:14b", "local", 80.0, agentic=80.0, warm=True, size_vram_gb=8.0)
        if cloud:
            add_model(self.db, "vendor/free:free", "free_cloud", 98.0, agentic=98.0, ctx=131072)
        real = build_engine

        def wired(*args: Any, **kwargs: Any) -> Engine:
            # A programmed provider can serve every local and cloud call, so a test scripts one
            # model per turn (HDW-305); otherwise each build answers a plain reply.
            backend: Backend = (
                provider
                if provider is not None
                else Backend(f"answer from engine {len(self.builds) + 1}")
            )
            self.backends.append(backend)

            def cloud_factory() -> Backend:
                self.cloud_clients += 1
                return backend

            def local_factory(host: str) -> Backend:
                self.hosts.append(host)
                return backend

            engine = real(
                *args,
                hardware=lambda: gpu_state(16),
                local_factory=local_factory,
                cloud_factory=cloud_factory,
                enable_classifier=False,
                **kwargs,
            )
            self.builds.append(engine)
            return engine

        monkeypatch.setattr(service, "build_engine", wired)
        monkeypatch.setattr(service.updater, "load_active", lambda: (self.catalog, "seed"))
        self.manager = RulesManager()  # writes the default rules.yaml under the isolated home
        self.sock = socket.socket()
        self.daemon = Daemon(
            self.db,
            self.sock,
            DaemonPorts(4002, 4000, 4001),
            TOKEN,
            rules_source=lambda: self.manager.current,
            reload_rules=self.manager.reload,
            rules_error=lambda: self.manager.last_error,
        )
        self.manager.set_on_reload(self.daemon.on_rules_changed)
        self.daemon.rebuild_engine()

    def close(self) -> None:
        self.sock.close()

    @property
    def path(self) -> Path:
        return self.manager.path

    def save(self, text: str) -> None:
        """A hand edit: write the file and move its mtime forward, never call reload."""
        before = self.path.stat().st_mtime_ns
        self.path.write_text(text, encoding="utf-8", newline="\n")
        os.utime(self.path, ns=(before + 10_000_000, before + 10_000_000))

    def engine(self) -> Engine:
        engine = self.daemon.live_engine()
        assert engine is not None
        return engine

    def ask(self, engine: Engine | None = None, scope: str | None = None) -> RouteResult:
        """One request through ``engine`` (default: the live one) on a fresh event loop."""
        target = engine or self.engine()
        request = RouteRequest(messages=ASK, scope=scope, surface="mcp")  # type: ignore[arg-type]
        return asyncio.run(target.router.run(request))


class ClientHttp:
    """An ``HttpClient`` that talks to the ASGI app in-process, so ``DaemonClient`` is real."""

    def __init__(self, client: TestClient) -> None:
        self._client = client

    def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        path = "/" + url.split("/", 3)[3]
        reply = self._client.request(method, path, headers=headers, json=body)
        if reply.status_code >= 400:
            raise SourceError(f"HTTP {reply.status_code} from 127.0.0.1")
        return reply.json()


def daemon_client(rig: DaemonRig) -> DaemonClient:
    """A ``DaemonClient`` wired to ``rig.daemon`` through its real ASGI app."""
    app = build_app(
        token=TOKEN,
        health=rig.daemon.health,
        status=rig.daemon.status,
        on_shutdown=lambda: None,
        on_reload=rig.daemon.reload_now,
    )
    http = ClientHttp(TestClient(app, base_url="http://127.0.0.1:4002"))
    return DaemonClient(INFO, TOKEN, http=http)


def finder(client: DaemonClient | None) -> Callable[[], DaemonClient | None]:
    return lambda: client
