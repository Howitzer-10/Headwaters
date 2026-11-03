"""HDW-306: local scope is proved at the socket, not only in the policy.

Every test here runs a real local-scoped request through one surface — the OpenAI face, the MCP
tools, the classifier, verification, a cascade or an error path — with two guards at once:
``forbid_network`` (the HDW-120 monkeypatch) and ``audit_egress`` (the FU-120-3 audit hook).
The accept-when is that opus review can see no code path constructs a cloud client in local
scope; the evidence is ``cloud_clients == 0`` / ``cloud_calls == 0`` in each test plus the audit
hook's own self-tests below.
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
from collections.abc import Iterator
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest
from starlette.types import ASGIApp

from headwaters.core.analyzer import build_chain
from headwaters.core.catalog import updater
from headwaters.core.catalog.loader import SEED_RELATIVE_PATH, load_catalog
from headwaters.core.rules import CatalogRules, LocalTier, Rules
from headwaters.core.state_models import list_models, upsert_model
from headwaters.core.verify_basic import BasicVerifier
from headwaters.gateway.openai_routes import build_gateway_app
from tests.executor_support import REPO_ROOT
from tests.frontdoor_support import Rig as McpRig
from tests.frontdoor_support import structured
from tests.net_spy import audit_egress, forbid_network
from tests.rebuild_support import TOKEN, DaemonRig, ProgrammableProvider
from tests.router_rig import Rig
from tests.router_support import OK, RATE_LIMITED, candidate
from tests.test_analyzer_classifier import VAGUE, answer
from tests.test_router import LOCAL, pool

BASE = "http://127.0.0.1:4000"
AUTH = {"authorization": f"Bearer {TOKEN}"}
URL = "/v1/chat/completions"
LOCAL_ALIAS = "headwaters/local"
CLOUD_ALIAS = "headwaters/cloud"
SEEDED_LOCAL = "qwen:14b"  # the model the daemon rig seeds as local (rebuild_support)
CODE_TASK = "write a function that adds two numbers"
ASK: list[dict[str, Any]] = [{"role": "user", "content": CODE_TASK}]
CATALOG_URL = "https://catalog.example.com/catalog.json"


def gateway(rig: DaemonRig) -> ASGIApp:
    """The OpenAI face as the daemon serves it: live engine, live rules, the daemon's token."""
    return build_gateway_app(
        token=TOKEN,
        router=rig.daemon.gateway_router,
        rules=lambda: rig.manager.current,
    )


def post(app: ASGIApp, body: dict[str, Any], *, scope: str | None = None) -> httpx.Response:
    """One request through the ASGI app, so the routing headers are readable."""
    headers = AUTH if scope is None else {**AUTH, "x-headwaters-scope": scope}

    async def go() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
            return await client.post(URL, json=body, headers=headers)

    return asyncio.run(go())


class _Classifier(BaseHTTPRequestHandler):
    """The tiny classifier's Ollama endpoint, on loopback only."""

    body: bytes = b""
    seen: list[dict[str, Any]] = []  # noqa: RUF012

    def do_POST(self) -> None:
        length = int(self.headers["Content-Length"])
        _Classifier.seen.append(json.loads(self.rfile.read(length)))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(_Classifier.body)

    def log_message(self, format: str, *args: Any) -> None:
        return


@pytest.fixture
def classifier_server() -> Iterator[HTTPServer]:
    _Classifier.seen = []
    _Classifier.body = json.dumps(answer("chat", 2, "low")).encode()
    httpd = HTTPServer(("127.0.0.1", 0), _Classifier)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield httpd
    httpd.shutdown()
    httpd.server_close()


# ---------------------------------------------------------------- the audit hook itself (FU-120-3)


def test_the_audit_hook_refuses_a_non_loopback_connect() -> None:
    with pytest.raises(AssertionError, match="audit hook"), audit_egress() as attempted:
        socket.create_connection(("203.0.113.9", 9), timeout=0.01)
    assert attempted == ["203.0.113.9"]


def test_the_audit_hook_refuses_a_non_loopback_lookup() -> None:
    with pytest.raises(AssertionError, match="audit hook"), audit_egress() as attempted:
        socket.getaddrinfo("example.com", 80)
    assert attempted == ["example.com"]


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.2", "::1", "localhost"])
def test_the_audit_hook_allows_loopback_lookups(host: str) -> None:
    with audit_egress() as attempted:
        socket.getaddrinfo(host, 0)
    assert attempted == []


# ---------------------------------------------------------------------------- gateway and MCP


def test_the_gateway_keeps_a_local_request_on_the_machine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = ProgrammableProvider()
    rig = DaemonRig(tmp_path, monkeypatch, cloud=True, provider=provider)
    try:
        with audit_egress() as remote:
            opened = forbid_network(monkeypatch, allow_loopback=True)
            response = post(gateway(rig), {"model": LOCAL_ALIAS, "messages": ASK})
        assert response.status_code == 200, response.text
        assert response.headers["x-headwaters-model"] == SEEDED_LOCAL
        assert response.headers["x-headwaters-scope"] == "local"
        assert provider.calls == [SEEDED_LOCAL]  # only the local model was ever asked
        assert rig.cloud_clients == 0
        assert not opened
        assert not remote
    finally:
        rig.close()


def test_the_mcp_tools_keep_local_requests_on_the_machine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = McpRig(tmp_path, cloud=True)  # a better cloud model exists, but scope is local
    with audit_egress() as remote:
        opened = forbid_network(monkeypatch, allow_loopback=True)
        do = structured(
            asyncio.run(rig.call("headwaters_do", {"task": CODE_TASK, "scope": "local"}))
        )
        ask = structured(
            asyncio.run(rig.call("headwaters_ask", {"question": CODE_TASK, "scope": "local"}))
        )
    assert (do["tier"], ask["tier"]) == ("local", "local")
    assert do["model"] == SEEDED_LOCAL
    assert rig.cloud_calls == 0
    assert not opened
    assert not remote


def test_airplane_mode_routes_local_and_skips_the_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = Rig(tmp_path, {LOCAL: OK}, pool(), rules=Rules(airplane=True))
    with audit_egress() as remote:
        opened = forbid_network(monkeypatch, allow_loopback=True)  # asyncio's own pipe is loopback
        result = rig.run()
        update = updater.update(
            Rules(airplane=True, catalog=CatalogRules(url=CATALOG_URL)),
            directory=tmp_path / "cache",
        )
    assert result.decision.model == LOCAL
    assert result.decision.tier == "local"  # airplane narrowed the pool to local only
    assert rig.cloud_clients == 0
    assert update.outcome == "disabled"
    assert "airplane" in update.message
    assert not opened
    assert not remote


def test_the_classifier_refines_over_loopback_only(
    tmp_path: Path, classifier_server: HTTPServer
) -> None:
    catalog = load_catalog(REPO_ROOT / SEED_RELATIVE_PATH)
    tier = LocalTier(host=f"http://127.0.0.1:{classifier_server.server_address[1]}")
    chain = build_chain(catalog, tier)
    rig = Rig(
        tmp_path,
        {LOCAL: OK},
        [candidate(LOCAL, "local", 86.0)],
        analyzer=chain,
    )
    with audit_egress() as remote:
        result = rig.run(messages=[{"role": "user", "content": VAGUE}])
    assert result.decision.model == LOCAL
    assert _Classifier.seen  # layer 3 really asked the local classifier
    assert rig.cloud_clients == 0
    assert not remote


def test_verification_runs_offline_and_stays_local(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = Rig(
        tmp_path,
        {LOCAL: OK},
        [candidate(LOCAL, "local", 86.0)],
        verifiers=[BasicVerifier()],
    )
    with audit_egress() as remote:
        opened = forbid_network(monkeypatch, allow_loopback=True)
        result = rig.run(verify="always")
    assert result.decision.model == LOCAL
    verify = result.verify
    assert verify is not None
    assert verify.ran
    assert verify.passed is not False
    assert not verify.problems
    assert rig.cloud_clients == 0
    assert not opened
    assert not remote


def test_a_local_wall_cascades_to_the_next_local_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = "qwen-local-b"
    rig = Rig(
        tmp_path,
        {LOCAL: [RATE_LIMITED], other: OK},
        [candidate(LOCAL, "local", 90.0), candidate(other, "local", 80.0)],
    )
    with audit_egress() as remote:
        opened = forbid_network(monkeypatch, allow_loopback=True)
        result = rig.run(scope="local")
    assert result.decision.model == other
    assert rig.dispatch.calls == [LOCAL, other]  # both local, in order
    assert rig.cloud_clients == 0
    assert not opened
    assert not remote


def test_handoff_answers_without_asking_any_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = McpRig(tmp_path, cloud=True)
    with audit_egress() as remote:
        opened = forbid_network(monkeypatch, allow_loopback=True)
        result = structured(asyncio.run(rig.call("headwaters_handoff", {"goal": "carry on"})))
    assert "carry on" in json.dumps(result)
    assert rig.cloud_calls == 0
    assert rig.backend.requests == []  # no model, local or cloud, was asked
    assert not opened
    assert not remote


def test_a_local_error_never_falls_back_to_cloud(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = McpRig(tmp_path, cloud=True)
    local = next(m for m in list_models(rig.db) if m.tier == "local")
    upsert_model(rig.db, replace(local, ctx=1024))  # too small for the task: a local-only error
    with audit_egress() as remote:
        opened = forbid_network(monkeypatch, allow_loopback=True)
        result = asyncio.run(rig.call("headwaters_do", {"task": CODE_TASK, "scope": "local"}))
    data = structured(result)
    assert result.is_error
    assert data["ok"] is False
    assert data["error"]["code"]
    assert data["error"]["fix"]
    assert rig.cloud_calls == 0
    assert rig.backend.requests == []
    assert not opened
    assert not remote


def test_the_private_lock_stops_the_gateway_before_any_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = ProgrammableProvider()
    rig = DaemonRig(tmp_path, monkeypatch, cloud=True, provider=provider)
    try:
        rig.save("private_lock: true\n")
        rig.manager.reload()
        with audit_egress() as remote:
            opened = forbid_network(monkeypatch, allow_loopback=True)
            response = post(
                gateway(rig), {"model": "vendor/free:free", "messages": ASK}, scope="cloud"
            )
        assert response.status_code == 400, response.text
        # The OpenAI face answers a locked cloud pin with the policy refusal that fired first;
        # either code means the request was refused, and no client was ever constructed.
        assert response.json()["error"]["code"] in {"private_lock_conflict", "pin_rejected"}
        assert provider.calls == []
        assert rig.cloud_clients == 0
        assert not opened
        assert not remote
    finally:
        rig.close()


def test_the_private_lock_stops_the_tools_before_any_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig = McpRig(tmp_path, cloud=True, rules=Rules(private_lock=True))
    with audit_egress() as remote:
        opened = forbid_network(monkeypatch, allow_loopback=True)
        result = asyncio.run(rig.call("headwaters_do", {"task": CODE_TASK, "scope": "cloud"}))
    data = structured(result)
    assert result.is_error
    assert data["error"]["code"] == "private_lock_conflict"
    assert rig.cloud_calls == 0
    assert rig.backend.requests == []
    assert not opened
    assert not remote
