"""HDW-148: what ``headwaters private`` and ``scope`` do when the daemon cannot confirm.

- No daemon running: say so, exit 0 (the file is the truth; the daemon reads it at start).
- A daemon process that is alive but unreachable, errors, rejects the file or reports another
  value: exit non-zero with "saved, but the running daemon has not confirmed it".
In every case the setting is already saved in rules.yaml.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from headwaters.cli import daemon_sync
from headwaters.core.daemon_client import DaemonError
from headwaters.core.daemon_info import DaemonInfo, DaemonPorts
from headwaters.core.rules_io import load_rules
from tests.cli_support import home, invoke, invoke_json
from tests.rebuild_support import INFO

NOT_CONFIRMED = "saved, but the running daemon has not confirmed it"


class FakeDaemon:
    info = INFO

    def __init__(self, reply: dict[str, Any] | Exception) -> None:
        self._reply = reply
        self.calls = 0

    def reload(self) -> dict[str, Any]:
        self.calls += 1
        if isinstance(self._reply, Exception):
            raise self._reply
        return self._reply


def _live(**over: Any) -> dict[str, Any]:
    reply = {
        "ok": True,
        "applied": True,
        "private_lock": True,
        "airplane": False,
        "default_scope": "auto",
    }
    return {**reply, **over}


@pytest.fixture
def cli_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return home(tmp_path, monkeypatch)


def _find(monkeypatch: pytest.MonkeyPatch, daemon: object | None) -> None:
    monkeypatch.setattr(daemon_sync, "find_running", lambda: daemon)


def _state_file(monkeypatch: pytest.MonkeyPatch, pid: int | None, alive: bool) -> None:
    info = None if pid is None else DaemonInfo(pid, 1.0, "0", DaemonPorts(4002))
    monkeypatch.setattr(daemon_sync, "read_info", lambda: info)
    monkeypatch.setattr(daemon_sync, "pid_alive", lambda p: alive)


def _saved(cli_home: Path) -> bool:
    return load_rules(cli_home / "config" / "rules.yaml").private_lock


def test_no_daemon_running_says_so_and_exits_zero(
    cli_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _find(monkeypatch, None)
    _state_file(monkeypatch, None, False)
    result = invoke("private", "on")
    assert result.exit_code == 0, result.output
    assert "No daemon is running" in result.stdout
    assert _saved(cli_home) is True


def test_a_stale_state_file_counts_as_no_daemon(
    cli_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _find(monkeypatch, None)
    _state_file(monkeypatch, 999_999, alive=False)  # the pid is gone: a crash left the file
    assert invoke("private", "on").exit_code == 0


def test_no_daemon_in_json(cli_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _find(monkeypatch, None)
    _state_file(monkeypatch, None, False)
    result, data = invoke_json("scope", "local")
    assert result.exit_code == 0
    assert data["daemon"] == {"running": False, "confirmed": False}


def test_a_live_process_that_does_not_answer_is_not_confirmed(
    cli_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _find(monkeypatch, None)  # ``find_running`` found nothing, but the pid in daemon.json lives
    _state_file(monkeypatch, 4321, alive=True)
    result = invoke("private", "on")
    assert result.exit_code == 1
    assert NOT_CONFIRMED in result.output
    assert _saved(cli_home) is True  # the file is still the truth


def test_an_unreachable_api_is_not_confirmed(
    cli_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _find(monkeypatch, FakeDaemon(DaemonError("cannot reach 127.0.0.1:4002")))
    result = invoke("private", "on")
    assert result.exit_code == 1
    assert NOT_CONFIRMED in result.output
    assert "cannot reach" in result.output


def test_a_daemon_that_rejects_the_file_is_not_confirmed(
    cli_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _find(monkeypatch, FakeDaemon(_live(applied=False, error="strategy: bad", private_lock=False)))
    result = invoke("private", "on")
    assert result.exit_code == 1
    assert NOT_CONFIRMED in result.output
    assert "strategy: bad" in result.output


def test_a_daemon_that_reports_the_old_value_is_not_confirmed(
    cli_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _find(monkeypatch, FakeDaemon(_live(private_lock=False)))
    result = invoke("private", "on")
    assert result.exit_code == 1
    assert NOT_CONFIRMED in result.output
    assert "private_lock" in result.output


def test_a_wrong_scope_report_is_not_confirmed(
    cli_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _find(monkeypatch, FakeDaemon(_live(default_scope="auto")))
    result = invoke("scope", "local")
    assert result.exit_code == 1
    assert NOT_CONFIRMED in result.output


def test_the_failure_is_a_json_error_on_stdout(
    cli_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _find(monkeypatch, FakeDaemon(DaemonError("HTTP 401")))
    result = invoke("private", "on", "--json")
    assert result.exit_code == 1
    error = json.loads(result.stdout)["error"]
    assert error["code"] == "daemon_not_confirmed"
    assert NOT_CONFIRMED in error["message"]


def test_a_confirming_daemon_is_asked_exactly_once(
    cli_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    daemon = FakeDaemon(_live())
    _find(monkeypatch, daemon)
    assert invoke("private", "on").exit_code == 0
    assert daemon.calls == 1


def test_an_invalid_rules_file_never_reaches_the_daemon(
    cli_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.cli_support import write_rules

    write_rules(cli_home, "airplane: maybe\n")
    daemon = FakeDaemon(_live())
    _find(monkeypatch, daemon)
    assert invoke("private", "on").exit_code == 2  # the edit itself was refused
    assert daemon.calls == 0
