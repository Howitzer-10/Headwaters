"""HDW-148: ``POST /v1/reload``, ``DaemonClient.reload`` and the CLI confirmation.

``headwaters private on|off`` and ``headwaters scope <x>`` return only after the daemon reports the
new value; with no daemon they say so and exit 0; with a daemon that does not confirm they exit
non-zero with "saved, but the running daemon has not confirmed it".
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from headwaters.cli import daemon_sync
from headwaters.core.daemon_client import DaemonClient, DaemonError
from headwaters.gateway.server import build_app
from tests.cli_support import home, invoke, invoke_json
from tests.rebuild_support import INFO, TOKEN, DaemonRig, daemon_client, finder


@pytest.fixture
def rig(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[DaemonRig]:
    # The CLI and the daemon share one home: the CLI writes the file the daemon watches.
    home(tmp_path, monkeypatch)
    made = DaemonRig(tmp_path, monkeypatch, cloud=True, home=tmp_path)
    yield made
    made.close()


def _app(**kw: Any) -> TestClient:
    app = build_app(
        token=TOKEN, health=lambda: {}, status=lambda: {}, on_shutdown=lambda: None, **kw
    )
    return TestClient(app, base_url="http://127.0.0.1:4002")


AUTH = {"Authorization": f"Bearer {TOKEN}"}


def test_reload_needs_the_token_and_post() -> None:
    client = _app(on_reload=lambda: {"applied": True})
    assert client.post("/v1/reload").status_code == 401
    assert client.post("/v1/reload", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/v1/reload", headers=AUTH).status_code == 405
    reply = client.post("/v1/reload", headers=AUTH)
    assert (reply.status_code, reply.json()) == (200, {"ok": True, "applied": True})


def test_reload_without_a_handler_is_not_supported() -> None:
    assert _app().post("/v1/reload", headers=AUTH).status_code == 501


def test_a_crashing_reload_is_a_500_that_names_nothing() -> None:
    def boom() -> dict[str, Any]:
        raise RuntimeError("secret detail")

    reply = _app(on_reload=boom).post("/v1/reload", headers=AUTH)
    assert reply.status_code == 500
    assert "secret detail" not in reply.text


def test_reload_reports_the_live_switches_after_the_read(rig: DaemonRig) -> None:
    rig.save("private_lock: true\nairplane: true\ndefault_scope: local\n")
    reply = daemon_client(rig).reload()
    assert reply == {
        "ok": True,
        "applied": True,
        "private_lock": True,
        "airplane": True,
        "default_scope": "local",
    }


def test_reload_of_a_rejected_file_says_so_and_keeps_the_last_good_rules(rig: DaemonRig) -> None:
    rig.save("private_lock: true\n")
    assert daemon_client(rig).reload()["private_lock"] is True
    rig.save("private_lock: false\nstrategy: nonsense\n")
    reply = daemon_client(rig).reload()
    assert reply["applied"] is False
    assert "strategy" in reply["error"]
    assert reply["private_lock"] is True  # the lock stays on: the last good rules are in force


def test_the_client_raises_when_the_daemon_refuses(rig: DaemonRig) -> None:
    app = build_app(token="other", health=lambda: {}, status=lambda: {}, on_shutdown=lambda: None)
    from tests.rebuild_support import ClientHttp

    http = ClientHttp(TestClient(app, base_url="http://127.0.0.1:4002"))
    with pytest.raises(DaemonError):
        DaemonClient(INFO, TOKEN, http=http).reload()


def _use(monkeypatch: pytest.MonkeyPatch, client: DaemonClient | None) -> None:
    monkeypatch.setattr(daemon_sync, "find_running", finder(client))


def test_private_on_returns_only_after_the_daemon_reports_it(
    rig: DaemonRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use(monkeypatch, daemon_client(rig))
    assert rig.ask().decision.tier == "free_cloud"
    result, data = invoke_json("private", "on")
    assert result.exit_code == 0, result.output
    assert data["daemon"] == {
        "running": True,
        "confirmed": True,
        "private_lock": True,
        "airplane": False,
        "default_scope": "auto",
    }
    assert rig.manager.current.private_lock is True  # the daemon's own view, not the file's
    assert "running daemon confirmed" in invoke("private", "on").stdout
    clients = rig.cloud_clients
    assert rig.ask().decision.tier == "local"  # a request sent right after cannot reach a cloud
    assert rig.cloud_clients == clients


def test_private_off_waits_for_the_daemon_too(
    rig: DaemonRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use(monkeypatch, daemon_client(rig))
    invoke("private", "on")
    result, data = invoke_json("private", "off")
    assert result.exit_code == 0
    assert data["daemon"]["private_lock"] is False
    assert rig.manager.current.private_lock is False


def test_scope_waits_for_the_daemon_and_prints_its_confirmation(
    rig: DaemonRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use(monkeypatch, daemon_client(rig))
    result = invoke("scope", "local")
    assert result.exit_code == 0, result.output
    assert "running daemon confirmed" in result.stdout
    assert rig.manager.current.default_scope == "local"
    _, data = invoke_json("scope", "free")
    assert data["daemon"]["default_scope"] == "free"


def test_showing_the_state_does_not_call_the_daemon(
    rig: DaemonRig, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []
    monkeypatch.setattr(daemon_sync, "find_running", lambda: calls.append(1))
    assert invoke("private").exit_code == 0
    assert invoke("scope").exit_code == 0
    assert calls == []
