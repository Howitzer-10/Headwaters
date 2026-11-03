"""``headwaters status --open``: the shortcut and the itch action (HDW-405).

The command starts the daemon when it is down and opens the bound dashboard port. No test opens a
browser or touches HTTP: ``websocket``-free fakes stand in for the two daemon calls.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from headwaters.cli import cmd_status
from headwaters.core import daemon_info
from tests.cli_support import home, invoke, invoke_json

URL = "http://127.0.0.1:4501/"


def _client(dashboard: int | None = 4501) -> Any:
    info = daemon_info.DaemonInfo(
        pid=1,
        started_at=0.0,
        version="test",
        ports=daemon_info.DaemonPorts(api=4002, dashboard=dashboard),
    )
    return SimpleNamespace(info=info)


def _runtime(enabled: bool = True) -> Any:
    return SimpleNamespace(rules=SimpleNamespace(dashboard=SimpleNamespace(enabled=enabled)))


def test_the_page_is_opened_without_a_browser(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    home(tmp_path, monkeypatch)
    opened: list[str] = []
    monkeypatch.setattr(cmd_status, "status_page_url", lambda rt: (URL, None))
    monkeypatch.setattr(cmd_status.webbrowser, "open", opened.append)

    result = invoke("status", "--open")

    assert result.exit_code == 0
    assert URL in result.stdout
    assert opened == [URL]


def test_a_missing_dashboard_is_a_clear_error(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    home(tmp_path, monkeypatch)
    opened: list[str] = []
    monkeypatch.setattr(
        cmd_status, "status_page_url", lambda rt: (None, "the dashboard is disabled")
    )
    monkeypatch.setattr(cmd_status.webbrowser, "open", opened.append)

    result = invoke("status", "--open")

    assert result.exit_code != 0
    assert "the dashboard is disabled" in result.stdout + result.stderr
    assert opened == []


def test_json_reports_the_url_and_opens_nothing(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    home(tmp_path, monkeypatch)
    opened: list[str] = []
    monkeypatch.setattr(cmd_status, "status_page_url", lambda rt: (URL, None))
    monkeypatch.setattr(cmd_status.webbrowser, "open", opened.append)

    result, data = invoke_json("status", "--open")

    assert result.exit_code == 0
    assert data == {"ok": True, "url": URL}
    assert opened == []


def test_a_running_daemon_is_reused() -> None:
    started: list[int | None] = []

    url, problem = cmd_status.status_page_url(
        _runtime(),
        find=lambda: _client(),
        start=lambda port: started.append(port),
    )

    assert (url, problem) == (URL, None)
    assert started == []


def test_a_cold_machine_starts_the_daemon_and_reads_the_bound_port() -> None:
    client = _client(4567)
    calls: list[str] = []

    def find() -> Any:
        # Down on the first look, up after the start, like the real daemon.json.
        calls.append("find")
        return client if len(calls) > 1 else None

    url, problem = cmd_status.status_page_url(
        _runtime(),
        find=find,
        start=lambda port: SimpleNamespace(ok=True, info=client.info, message=""),
    )

    assert problem is None
    assert url == "http://127.0.0.1:4567/"
    assert calls == ["find", "find"]


def test_a_failed_start_reports_the_daemon_message() -> None:
    url, problem = cmd_status.status_page_url(
        _runtime(),
        find=lambda: None,
        start=lambda port: SimpleNamespace(ok=False, info=None, message="no runtime"),
    )

    assert url is None
    assert problem == "no runtime"


def test_a_daemon_without_a_dashboard_port_says_so() -> None:
    url, problem = cmd_status.status_page_url(_runtime(), find=lambda: _client(None))

    assert url is None
    assert problem is not None
    assert "dashboard" in problem


def test_a_disabled_dashboard_never_starts_the_daemon() -> None:
    started: list[int | None] = []

    url, problem = cmd_status.status_page_url(
        _runtime(enabled=False), find=lambda: _client(), start=started.append
    )

    assert url is None
    assert problem is not None
    assert "dashboard.enabled" in problem
    assert started == []
