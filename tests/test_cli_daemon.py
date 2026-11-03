"""HDW-201: ``headwaters start|stop|daemon-status`` and the CLI's import hygiene."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from headwaters.cli import cmd_daemon
from headwaters.core.daemon_control import ControlResult
from headwaters.core.daemon_info import DaemonInfo, DaemonPorts
from tests.cli_support import home, invoke, invoke_json

INFO = DaemonInfo(77, 1.0, "0.0.0", DaemonPorts(4002, 4000, 4001))


@pytest.fixture
def cli_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return home(tmp_path, monkeypatch)


def _patch(monkeypatch: pytest.MonkeyPatch, name: str, result: ControlResult) -> list[Any]:
    calls: list[Any] = []

    def fake(*args: Any, **kw: Any) -> ControlResult:
        calls.append(args)
        return result

    monkeypatch.setattr(cmd_daemon, name, fake)
    return calls


def test_start_prints_the_endpoint(cli_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch(
        monkeypatch, "start_daemon", ControlResult("started", "The daemon is running.", INFO)
    )
    result = invoke("start", "--port", "4100")
    assert result.exit_code == 0
    assert "http://127.0.0.1:4002/mcp" in result.stdout
    assert calls == [(4100,)]


def test_start_json(cli_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, "start_daemon", ControlResult("already_running", "Already.", INFO))
    result, data = invoke_json("start")
    assert result.exit_code == 0
    assert data["outcome"] == "already_running"
    assert data["daemon"]["ports"] == {"api": 4002, "gateway": 4000, "dashboard": 4001}


def test_start_failure_exits_nonzero(cli_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, "start_daemon", ControlResult("failed", "It died."))
    result = invoke("start")
    assert result.exit_code == 1
    assert "It died." in result.output


def test_stop_and_not_running(cli_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, "stop_daemon", ControlResult("stopped", "The daemon has stopped.", INFO))
    assert invoke("stop").exit_code == 0
    _patch(monkeypatch, "stop_daemon", ControlResult("not_running", "The daemon is not running."))
    result, data = invoke_json("stop")
    assert result.exit_code == 0
    assert data["outcome"] == "not_running"


def test_stop_force_passes_the_flag(cli_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[dict[str, Any]] = []

    def fake(**kw: Any) -> ControlResult:
        seen.append(kw)
        return ControlResult("forced", "The daemon (pid 77) was killed.", INFO)

    monkeypatch.setattr(cmd_daemon, "stop_daemon", fake)
    result = invoke("stop", "--force")
    assert result.exit_code == 0
    assert seen == [{"force": True}]
    assert "was killed" in result.output


def test_stop_without_force_does_not_ask_for_it(
    cli_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[dict[str, Any]] = []

    def fake(**kw: Any) -> ControlResult:
        seen.append(kw)
        return ControlResult("stopped", "The daemon has stopped.", INFO)

    monkeypatch.setattr(cmd_daemon, "stop_daemon", fake)
    assert invoke("stop").exit_code == 0
    assert seen == [{"force": False}]


def test_status_when_nothing_runs(cli_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cmd_daemon, "find_running", lambda: None)
    assert "not running" in invoke("daemon-status").stdout
    _, data = invoke_json("daemon-status")
    assert data == {"ok": True, "running": False}


def test_status_of_a_running_daemon(cli_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class Client:
        info = INFO

        def status(self) -> dict[str, Any]:
            return {
                "pid": 77,
                "version": "0.0.0",
                "ports": {"gateway": 4000, "dashboard": 4001},
                "keep_warm": {"action": "skipped", "reason": "nothing_loaded"},
            }

    monkeypatch.setattr(cmd_daemon, "find_running", lambda: Client())
    text = invoke("daemon-status").stdout
    assert "pid 77" in text
    assert "nothing_loaded" in text
    _, data = invoke_json("daemon-status")
    assert data["running"] is True
    assert data["daemon"]["mcp_url"] == "http://127.0.0.1:4002/mcp"


def test_status_reports_an_unanswering_daemon(
    cli_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from headwaters.core.daemon_client import DaemonError

    class Client:
        info = INFO

        def status(self) -> dict[str, Any]:
            raise DaemonError("HTTP 401")

    monkeypatch.setattr(cmd_daemon, "find_running", lambda: Client())
    result = invoke("daemon-status")
    assert result.exit_code == 1
    assert "401" in result.output


def test_importing_the_cli_does_not_load_the_server_stack() -> None:
    """The CLI starts the daemon as a child process; it never imports the web or MCP stacks."""
    code = (
        "import sys, json\n"
        "import headwaters.cli.main\n"
        "heavy = ['starlette', 'uvicorn', 'mcp', 'litellm', 'fastapi']\n"
        "print(json.dumps([m for m in heavy if m in sys.modules]))\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, timeout=60
    )
    assert json.loads(done.stdout.strip().splitlines()[-1]) == []
