"""HDW-201: finding, starting and stopping the daemon, with no process spawned."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from headwaters.core import paths
from headwaters.core.catalog.http import SourceError
from headwaters.core.daemon_client import DaemonClient, DaemonError, find_running
from headwaters.core.daemon_control import daemon_command, start_daemon, stop_daemon
from headwaters.core.daemon_info import DaemonInfo, DaemonPorts, load_or_create_token, write_info

INFO = DaemonInfo(4321, 1.0, "0.0.0", DaemonPorts(4002, 4000, 4001))


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(paths.HOME_ENV_VAR, str(tmp_path))


class FakeHttp:
    def __init__(self, *replies: Any) -> None:
        self.replies = list(replies)
        self.calls: list[tuple[str, str, dict[str, str] | None, dict[str, Any] | None]] = []

    def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        self.calls.append((method, url, headers, body))
        reply = self.replies.pop(0) if self.replies else {"ok": True, "pid": INFO.pid}
        if isinstance(reply, Exception):
            raise reply
        return reply


class Child:
    def __init__(self, exit_code: int | None = None) -> None:
        self.returncode = exit_code

    def poll(self) -> int | None:
        return self.returncode


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def test_client_sends_the_token_only_to_authenticated_paths() -> None:
    http = FakeHttp()
    client = DaemonClient(INFO, "tok", http=http)
    client.health()
    client.status()
    client.shutdown()
    (health, status, stop) = http.calls
    assert health[:2] == ("GET", "http://127.0.0.1:4002/health")
    assert health[2] is None
    assert status[2] == {"Authorization": "Bearer tok"}
    assert (stop[0], stop[1]) == ("POST", "http://127.0.0.1:4002/v1/shutdown")


def test_client_without_a_token_refuses_authenticated_calls() -> None:
    with pytest.raises(DaemonError, match="token"):
        DaemonClient(INFO, None, http=FakeHttp()).status()


def test_client_turns_transport_errors_and_odd_replies_into_daemon_errors() -> None:
    with pytest.raises(DaemonError, match="down"):
        DaemonClient(INFO, "t", http=FakeHttp(SourceError("down"))).health()
    with pytest.raises(DaemonError, match="unexpected"):
        DaemonClient(INFO, "t", http=FakeHttp([1, 2])).health()


def test_client_refuses_a_state_file_that_points_off_the_machine() -> None:
    remote = DaemonInfo(1, 1.0, "0", DaemonPorts(4002), host="10.0.0.5")
    with pytest.raises(DaemonError, match="loopback"):
        DaemonClient(remote, "t")


def test_find_running_needs_a_state_file() -> None:
    assert find_running(http=FakeHttp()) is None


def test_find_running_accepts_a_matching_health_reply() -> None:
    write_info(INFO)
    load_or_create_token()
    client = find_running(http=FakeHttp({"ok": True, "pid": INFO.pid}))
    assert client is not None
    assert client.info == INFO


@pytest.mark.parametrize(
    "reply",
    [SourceError("refused"), {"ok": False}, {"ok": True, "pid": 1}, {"ok": True}],
)
def test_find_running_rejects_a_dead_or_foreign_listener(reply: Any) -> None:
    write_info(INFO)
    assert find_running(http=FakeHttp(reply)) is None


def test_daemon_command_runs_the_module_with_this_python() -> None:
    argv = daemon_command(4100)
    assert argv[1:] == ["-m", "headwaters.daemon", "--port", "4100"]
    assert daemon_command()[1:] == ["-m", "headwaters.daemon"]


class Found:
    def __init__(self, info: DaemonInfo = INFO) -> None:
        self.info = info
        self.stopped = False

    def shutdown(self) -> dict[str, Any]:
        self.stopped = True
        return {"ok": True}


def test_start_does_nothing_when_a_daemon_is_running() -> None:
    spawned: list[Any] = []
    result = start_daemon(find=lambda: Found(), spawn=lambda argv: spawned.append(argv))  # type: ignore[arg-type,return-value]
    assert result.outcome == "already_running"
    assert spawned == []


def test_start_spawns_once_and_waits_for_health() -> None:
    clock = Clock()
    answers = iter([None, None, Found()])
    spawned: list[list[str]] = []

    def spawn(argv: Any) -> Child:
        spawned.append(list(argv))
        return Child()

    result = start_daemon(
        3000,
        find=lambda: next(answers),  # type: ignore[arg-type,return-value]
        spawn=spawn,  # type: ignore[arg-type]
        sleep=clock.sleep,
        clock=clock,
    )
    assert result.outcome == "started"
    assert len(spawned) == 1
    assert spawned[0][-2:] == ["--port", "3000"]


def test_start_reports_a_daemon_that_dies_on_launch() -> None:
    clock = Clock()
    result = start_daemon(
        find=lambda: None,
        spawn=lambda argv: Child(exit_code=2),  # type: ignore[arg-type,return-value]
        sleep=clock.sleep,
        clock=clock,
    )
    assert result.outcome == "failed"
    assert "code 2" in result.message
    assert "headwaters.log" in result.message


def test_start_gives_up_after_the_timeout() -> None:
    clock = Clock()
    result = start_daemon(
        find=lambda: None,
        spawn=lambda argv: Child(),  # type: ignore[arg-type,return-value]
        timeout_s=3,
        sleep=clock.sleep,
        clock=clock,
    )
    assert result.outcome == "failed"
    assert not result.ok


class _FakeProcess:
    def __init__(self, status: str) -> None:
        self._status = status

    def status(self) -> str:
        return self._status


def test_pid_alive_treats_a_zombie_as_gone(monkeypatch: pytest.MonkeyPatch) -> None:
    """Linux CI: a stopped daemon is a zombie child until reaped, and pid_exists still says True."""
    import psutil

    from headwaters.core.daemon_control import _pid_alive

    monkeypatch.setattr(psutil, "pid_exists", lambda pid: True)
    monkeypatch.setattr(psutil, "Process", lambda pid: _FakeProcess(psutil.STATUS_ZOMBIE))
    assert _pid_alive(99) is False
    monkeypatch.setattr(psutil, "Process", lambda pid: _FakeProcess(psutil.STATUS_SLEEPING))
    assert _pid_alive(99) is True


def test_pid_alive_is_false_for_a_missing_or_vanishing_pid(monkeypatch: pytest.MonkeyPatch) -> None:
    import psutil

    from headwaters.core.daemon_control import _pid_alive

    monkeypatch.setattr(psutil, "pid_exists", lambda pid: False)
    assert _pid_alive(99) is False

    def vanish(pid: int) -> _FakeProcess:
        raise psutil.NoSuchProcess(pid)

    monkeypatch.setattr(psutil, "pid_exists", lambda pid: True)
    monkeypatch.setattr(psutil, "Process", vanish)
    assert _pid_alive(99) is False


def test_stop_reports_not_running_and_cleans_a_stale_file() -> None:
    assert stop_daemon(find=lambda: None).outcome == "not_running"
    write_info(INFO)
    cleaned = stop_daemon(find=lambda: None, alive=lambda pid: False)
    assert cleaned.outcome == "stale_cleaned"
    write_info(INFO)
    # A live pid with an unreachable API is not ours to clean up.
    assert stop_daemon(find=lambda: None, alive=lambda pid: True).outcome == "not_running"


def test_stop_waits_until_the_process_is_gone() -> None:
    clock = Clock()
    found = Found()
    write_info(INFO)
    polls = iter([True, True, False])

    def alive(pid: int) -> bool:
        alive_now = next(polls)
        if not alive_now:
            from headwaters.core.daemon_info import remove_info

            remove_info()
        return alive_now

    result = stop_daemon(find=lambda: found, alive=alive, sleep=clock.sleep, clock=clock)  # type: ignore[arg-type,return-value]
    assert result.outcome == "stopped"
    assert found.stopped


def test_stop_fails_loudly_when_the_daemon_will_not_die() -> None:
    clock = Clock()
    result = stop_daemon(
        find=lambda: Found(),  # type: ignore[arg-type,return-value]
        alive=lambda pid: True,
        timeout_s=2,
        sleep=clock.sleep,
        clock=clock,
    )
    assert result.outcome == "failed"
    assert "still running" in result.message


def test_stop_reports_a_refused_shutdown() -> None:
    class Refusing(Found):
        def shutdown(self) -> dict[str, Any]:
            raise DaemonError("HTTP 401")

    result = stop_daemon(find=lambda: Refusing())  # type: ignore[arg-type,return-value]
    assert result.outcome == "failed"
    assert "401" in result.message
