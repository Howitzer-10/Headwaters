"""HDW-201: finding, starting and stopping the daemon, with no process spawned."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from headwaters.core import daemon_control, paths
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


def test_start_names_an_invalid_rules_file() -> None:
    """FU-202-6: exit 2 is a bad rules.yaml, not a mystery exit code."""
    clock = Clock()
    result = start_daemon(
        find=lambda: None,
        spawn=lambda argv: Child(exit_code=2),  # type: ignore[arg-type,return-value]
        sleep=clock.sleep,
        clock=clock,
    )
    assert result.outcome == "failed"
    assert "code 2" in result.message
    assert "rules.yaml could not be read or parsed" in result.message


def test_start_names_the_lock_holder() -> None:
    clock = Clock()
    result = start_daemon(
        find=lambda: None,
        spawn=lambda argv: Child(exit_code=3),  # type: ignore[arg-type,return-value]
        sleep=clock.sleep,
        clock=clock,
    )
    assert "code 3" in result.message
    assert "holds the lock" in result.message
    assert "headwaters stop --force" in result.message


def test_start_names_a_locked_out_port_range() -> None:
    clock = Clock()
    result = start_daemon(
        find=lambda: None,
        spawn=lambda argv: Child(exit_code=4),  # type: ignore[arg-type,return-value]
        sleep=clock.sleep,
        clock=clock,
    )
    assert "code 4" in result.message
    assert "no API port was free" in result.message
    assert "excludedportrange" in result.message


def test_start_quotes_an_unknown_exit_code() -> None:
    clock = Clock()
    result = start_daemon(
        find=lambda: None,
        spawn=lambda argv: Child(exit_code=7),  # type: ignore[arg-type,return-value]
        sleep=clock.sleep,
        clock=clock,
    )
    assert result.message.startswith("The daemon exited with code 7")
    assert "headwaters.log" in result.message


def test_force_stop_kills_the_pid_when_the_api_does_not_answer() -> None:
    """FU-201-10: a daemon.json that outlives its daemon is cleared by killing the pid."""
    clock = Clock()
    write_info(INFO)
    state = {"alive": True}
    killed: list[int] = []

    def kill(pid: int) -> None:
        killed.append(pid)
        state["alive"] = False

    result = stop_daemon(
        force=True,
        find=lambda: None,
        alive=lambda pid: state["alive"],
        kill=kill,
        sleep=clock.sleep,
        clock=clock,
    )
    assert result.outcome == "forced"
    assert killed == [INFO.pid]
    assert result.info is not None
    assert result.info.pid == INFO.pid
    assert "kill" in result.message


def test_force_stop_kills_after_a_refused_shutdown() -> None:
    class Refusing(Found):
        def shutdown(self) -> dict[str, Any]:
            raise DaemonError("HTTP 401")

    clock = Clock()
    state = {"alive": True}
    killed: list[int] = []

    def kill(pid: int) -> None:
        killed.append(pid)
        state["alive"] = False

    result = stop_daemon(
        force=True,
        find=lambda: Refusing(),  # type: ignore[arg-type,return-value]
        alive=lambda pid: state["alive"],
        kill=kill,
        sleep=clock.sleep,
        clock=clock,
    )
    assert result.outcome == "forced"
    assert killed == [INFO.pid]


def test_force_stop_gives_up_when_the_pid_survives() -> None:
    clock = Clock()
    write_info(INFO)
    result = stop_daemon(
        force=True,
        find=lambda: None,
        alive=lambda pid: True,
        kill=lambda pid: None,
        timeout_s=2,
        sleep=clock.sleep,
        clock=clock,
    )
    assert result.outcome == "failed"
    assert "still running after a kill" in result.message


def test_force_stop_never_kills_this_process(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    clock = Clock()
    write_info(INFO)
    monkeypatch.setattr(os, "getpid", lambda: INFO.pid)
    result = stop_daemon(
        force=True,
        find=lambda: None,
        alive=lambda pid: True,
        kill=lambda pid: None,
        timeout_s=1,
        sleep=clock.sleep,
        clock=clock,
    )
    assert result.outcome == "failed"
    assert "Refusing" in result.message


def test_force_stop_reports_a_kill_that_raises() -> None:
    def kill(pid: int) -> None:
        raise OSError("access denied")

    clock = Clock()
    write_info(INFO)
    result = stop_daemon(
        force=True,
        find=lambda: None,
        alive=lambda pid: True,
        kill=kill,
        timeout_s=1,
        sleep=clock.sleep,
        clock=clock,
    )
    assert result.outcome == "failed"
    assert "Could not kill the daemon" in result.message
    assert "access denied" in result.message


def test_stop_without_force_never_kills() -> None:
    clock = Clock()
    write_info(INFO)
    killed: list[int] = []
    result = stop_daemon(
        find=lambda: None,
        alive=lambda pid: True,
        kill=killed.append,
        sleep=clock.sleep,
        clock=clock,
    )
    assert result.outcome == "not_running"
    assert killed == []


# ------------------------------------- how the daemon is detached from the terminal (no spawn)


class FakePopen:
    """Records the keywords each spawn attempt used, and can refuse the first attempt."""

    def __init__(self, refuse: int = 0) -> None:
        self.refuse = refuse
        self.calls: list[dict[str, Any]] = []

    def __call__(self, argv: Any, **kwargs: Any) -> Any:
        self.calls.append({"argv": list(argv), **kwargs})
        if len(self.calls) <= self.refuse:
            raise OSError("a job object refused the breakaway")
        return Child()


def _windows_spawn(monkeypatch: pytest.MonkeyPatch, refuse: int = 0) -> FakePopen:
    fake = FakePopen(refuse=refuse)
    monkeypatch.setattr(daemon_control.sys, "platform", "win32")
    monkeypatch.setattr(daemon_control.subprocess, "Popen", fake)
    return fake


def test_the_daemon_is_spawned_without_detached_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """DETACHED_PROCESS is what makes Windows 11 show a blank terminal window for the daemon.

    Measured on Windows 11 with Windows Terminal as the default terminal: every DETACHED_PROCESS
    child of the probe got a console, and one new Windows Terminal window appeared for it -
    CREATE_NO_WINDOW in the same call was ignored. GetConsoleWindow came back NULL, and no
    window appeared, only for the combinations that leave DETACHED_PROCESS out.
    """
    fake = _windows_spawn(monkeypatch)
    daemon_control._spawn_detached(daemon_control.daemon_command())
    flags = fake.calls[0]["creationflags"]
    assert flags & daemon_control._NO_WINDOW
    assert flags & daemon_control._NEW_GROUP
    assert flags & daemon_control._BREAKAWAY
    assert not flags & daemon_control._DETACHED


def test_a_windowless_child_cannot_write_to_a_console_it_cannot_see(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _windows_spawn(monkeypatch)
    daemon_control._spawn_detached(daemon_control.daemon_command())
    kwargs = fake.calls[0]
    for stream in ("stdin", "stdout", "stderr"):
        assert kwargs[stream] is subprocess.DEVNULL
    assert kwargs["close_fds"] is True
    assert kwargs["env"]["PYTHONUTF8"] == "1"


def test_a_refused_breakaway_is_retried_with_plain_detach_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A job that forbids breakaway must not turn into "the daemon will not start"."""
    fake = _windows_spawn(monkeypatch, refuse=1)
    daemon_control._spawn_detached(daemon_control.daemon_command())
    assert len(fake.calls) == 2
    first = fake.calls[0]["creationflags"]
    assert first == daemon_control._DAEMON_FLAGS | daemon_control._BREAKAWAY
    second = fake.calls[1]["creationflags"]
    assert second == daemon_control._DAEMON_FLAGS
    assert not second & daemon_control._DETACHED


def test_elsewhere_the_daemon_gets_a_new_session_and_no_windows_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakePopen()
    monkeypatch.setattr(daemon_control.sys, "platform", "linux")
    monkeypatch.setattr(daemon_control.subprocess, "Popen", fake)
    daemon_control._spawn_detached(daemon_control.daemon_command())
    assert fake.calls[0]["start_new_session"] is True
    assert "creationflags" not in fake.calls[0]


# ------------------------- the mark a deliberate stop leaves for the watchdog (HDW-521)


class _Found:
    """A DaemonClient stand-in that answers a shutdown like the real one."""

    info = INFO
    stopped = False

    def shutdown(self) -> dict[str, Any]:
        _Found.stopped = True
        return {"ok": True}


def test_a_confirmed_stop_leaves_the_watchdog_hold() -> None:
    from headwaters.core import daemon_hold

    assert not daemon_hold.is_held()

    result = stop_daemon(find=lambda: _Found(), alive=lambda pid: False)  # type: ignore[arg-type,return-value]

    assert result.outcome == "stopped"
    assert daemon_hold.is_held()
    assert '"outcome": "stopped"' in daemon_hold.hold_path().read_text(encoding="utf-8")


def test_a_refused_stop_leaves_no_hold_so_the_watchdog_keeps_trying() -> None:
    from headwaters.core import daemon_hold

    class Refusing(_Found):
        def shutdown(self) -> dict[str, Any]:
            raise DaemonError("HTTP 401")

    assert stop_daemon(find=lambda: Refusing()).outcome == "failed"  # type: ignore[arg-type,return-value]
    assert not daemon_hold.is_held()


def test_start_clears_the_hold() -> None:
    from headwaters.core import daemon_hold

    daemon_hold.hold()
    assert daemon_hold.is_held()

    spawned: list[Any] = []
    result = start_daemon(find=lambda: _Found(), spawn=lambda argv: spawned.append(argv))  # type: ignore[arg-type,return-value]

    assert result.outcome == "already_running"
    assert not daemon_hold.is_held()
    assert spawned == []
