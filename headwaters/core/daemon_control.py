"""``headwaters start`` / ``stop``: launch and stop the shared daemon (HDW-201).

The daemon is started as a child process (``python -m headwaters.daemon``), never imported, so
the CLI stays free of the web framework and the MCP SDK (see ``.importlinter``).
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from headwaters.core import paths
from headwaters.core.daemon_client import DaemonClient, DaemonError, find_running
from headwaters.core.daemon_info import DaemonInfo, lock_path, read_info, remove_info

__all__ = [
    "DAEMON_MODULE",
    "ControlResult",
    "daemon_command",
    "pid_alive",
    "start_daemon",
    "stop_daemon",
]

DAEMON_MODULE = "headwaters.daemon"
_DETACHED = 0x00000008  # DETACHED_PROCESS: NOT used, see below - it opens a console window
_NEW_GROUP = 0x00000200  # CREATE_NEW_PROCESS_GROUP
_NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW: a console of its own, with no window
_BREAKAWAY = 0x01000000  # CREATE_BREAKAWAY_FROM_JOB
# The daemon must own a console that no window is attached to, and that the terminal it was
# started from cannot close. Two measured facts decide the flags (Windows 11, Windows Terminal as
# the default terminal, evidence in the commit message):
#   * DETACHED_PROCESS and CREATE_NO_WINDOW are mutually exclusive: with DETACHED_PROCESS set,
#     CREATE_NO_WINDOW is ignored and the child is given a console anyway - which Windows Terminal
#     then shows as a blank window. So DETACHED_PROCESS *alone* still opens a window, and closing
#     it kills the daemon with it.
#   * CREATE_NO_WINDOW on its own gives the child a console with no window at all: nothing on
#     screen, and its own console, so closing the terminal that started it does not touch it.
_DAEMON_FLAGS = _NO_WINDOW | _NEW_GROUP
START_TIMEOUT_S = 25.0
STOP_TIMEOUT_S = 15.0

Outcome = Literal[
    "started", "already_running", "stopped", "forced", "not_running", "stale_cleaned", "failed"
]

# The daemon's own exit codes (headwaters/daemon/runner.py). They are copied rather than imported:
# the daemon is a root package and nothing may import it (see .importlinter).
DAEMON_EXIT_CONFIG = 2
DAEMON_EXIT_ALREADY_RUNNING = 3
DAEMON_EXIT_NO_PORT = 4
EXCLUDED_RANGE_COMMAND = "netsh int ipv4 show excludedportrange protocol=tcp"
Finder = Callable[[], DaemonClient | None]
Spawner = Callable[[Sequence[str]], "subprocess.Popen[bytes]"]


@dataclass(frozen=True)
class ControlResult:
    outcome: Outcome
    message: str
    info: DaemonInfo | None = None

    @property
    def ok(self) -> bool:
        return self.outcome != "failed"


def daemon_command(port: int | None = None) -> list[str]:
    argv = [sys.executable, "-m", DAEMON_MODULE]
    if port is not None:
        argv += ["--port", str(port)]
    return argv


def _detach_flags() -> int:
    """The creation flags for the daemon on Windows: no window, its own group, its own console.

    Never ``DETACHED_PROCESS`` as well: see ``_DAEMON_FLAGS``.
    """
    return _DAEMON_FLAGS


def _spawn_detached(argv: Sequence[str]) -> subprocess.Popen[bytes]:
    env = {**os.environ, "PYTHONUTF8": "1"}
    if sys.platform == "win32":
        flags = _detach_flags()
        # The daemon has a console with no window, so nothing may write to it: all three standard
        # handles are DEVNULL, or the daemon dies on its first write.
        # A host that runs the shim in a Job Object (kill-on-close) would reap the daemon with it;
        # break away when the job allows it, and fall back when it does not.
        for extra in (_BREAKAWAY, 0):
            try:
                return subprocess.Popen(
                    list(argv),
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env=env,
                    close_fds=True,
                    creationflags=flags | extra,
                )
            except OSError:
                if not extra:
                    raise
        raise AssertionError("unreachable")  # pragma: no cover
    return subprocess.Popen(
        list(argv),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
        close_fds=True,
        start_new_session=True,
    )


def _start_failure(returncode: int | None) -> str:
    """Tell the daemon's known failure modes apart instead of quoting a bare exit code."""
    log = paths.log_dir() / "headwaters.log"
    if returncode == DAEMON_EXIT_CONFIG:
        return (
            "The daemon exited with code 2: rules.yaml could not be read or parsed. "
            f"Fix the file and try again; see {log}."
        )
    if returncode == DAEMON_EXIT_ALREADY_RUNNING:
        return (
            "The daemon exited with code 3: another daemon holds the lock "
            f"({lock_path()}) but its API did not answer. Run 'headwaters stop --force' and "
            f"start again; see {log}."
        )
    if returncode == DAEMON_EXIT_NO_PORT:
        return (
            "The daemon exited with code 4: no API port was free. A locked-out port range or "
            f"another program is holding it; check '{EXCLUDED_RANGE_COMMAND}'; see {log}."
        )
    return f"The daemon exited with code {returncode}; see {log}."


def start_daemon(
    port: int | None = None,
    *,
    find: Finder = find_running,
    spawn: Spawner = _spawn_detached,
    timeout_s: float = START_TIMEOUT_S,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> ControlResult:
    """Start the daemon in the background and wait until it answers ``/health``."""
    running = find()
    if running is not None:
        return ControlResult("already_running", "The daemon is already running.", running.info)
    child = spawn(daemon_command(port))
    deadline = clock() + timeout_s
    while clock() < deadline:
        client = find()
        if client is not None:
            return ControlResult("started", "The daemon is running.", client.info)
        if child.poll() is not None:
            return ControlResult("failed", _start_failure(child.returncode))
        sleep(0.2)
    return ControlResult("failed", f"The daemon did not answer within {timeout_s:.0f} s.")


def _pid_alive(pid: int) -> bool:
    """True while ``pid`` is a running process. A zombie (exited, not yet reaped by its parent)
    counts as gone: ``psutil.pid_exists`` is still True for it on POSIX, so a daemon started by
    this process would otherwise look alive after a clean stop."""
    try:
        import psutil

        if not psutil.pid_exists(pid):
            return False
        try:
            return bool(psutil.Process(pid).status() != psutil.STATUS_ZOMBIE)
        except psutil.NoSuchProcess:
            return False
    except Exception:
        return False


pid_alive = _pid_alive


def _kill_pid(pid: int) -> None:
    """Kill ``pid`` outright. A process that is already gone counts as success."""
    try:
        import psutil
    except Exception as exc:  # pragma: no cover - psutil is a hard dependency
        raise RuntimeError(f"psutil is not available ({exc})") from exc
    try:
        process = psutil.Process(pid)
        process.kill()
    except psutil.NoSuchProcess:
        return


def _force_stop(
    info: DaemonInfo,
    *,
    kill: Callable[[int], None],
    alive: Callable[[int], bool],
    timeout_s: float,
    sleep: Callable[[float], None],
    clock: Callable[[], float],
) -> ControlResult:
    """Kill the pid recorded in ``daemon.json`` when the API does not answer (FU-201-10)."""
    if info.pid == os.getpid():
        return ControlResult("failed", "Refusing to kill this process.", info)
    try:
        kill(info.pid)
    except Exception as exc:
        return ControlResult("failed", f"Could not kill the daemon (pid {info.pid}): {exc}", info)
    deadline = clock() + timeout_s
    while clock() < deadline:
        if not alive(info.pid):
            remove_info(info.pid)
            return ControlResult(
                "forced", f"The daemon (pid {info.pid}) did not answer, so it was killed.", info
            )
        sleep(0.2)
    return ControlResult(
        "failed", f"The daemon (pid {info.pid}) is still running after a kill.", info
    )


def stop_daemon(
    *,
    force: bool = False,
    find: Finder = find_running,
    timeout_s: float = STOP_TIMEOUT_S,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    alive: Callable[[int], bool] = _pid_alive,
    kill: Callable[[int], None] = _kill_pid,
) -> ControlResult:
    """Ask the daemon to shut down gracefully and wait until it is gone.

    With ``force``, a daemon that does not answer, refuses, or outlasts the timeout is killed by
    the pid in ``daemon.json`` (FU-201-10).
    """

    def forced(info: DaemonInfo) -> ControlResult:
        return _force_stop(
            info, kill=kill, alive=alive, timeout_s=timeout_s, sleep=sleep, clock=clock
        )

    client = find()
    if client is None:
        stale = read_info()
        if stale is None:
            return ControlResult("not_running", "The daemon is not running.")
        if not alive(stale.pid):
            remove_info(stale.pid)
            return ControlResult("stale_cleaned", "No daemon was running; removed a stale file.")
        if force:
            return forced(stale)
        return ControlResult("not_running", "The daemon is not running.")
    info = client.info
    try:
        client.shutdown()
    except DaemonError as exc:
        if force:
            return forced(info)
        return ControlResult("failed", f"The daemon refused to stop: {exc}", info)
    deadline = clock() + timeout_s
    while clock() < deadline:
        if not alive(info.pid) and read_info() is None:
            return ControlResult("stopped", "The daemon has stopped.", info)
        sleep(0.2)
    if force:
        return forced(info)
    return ControlResult("failed", f"The daemon (pid {info.pid}) is still running.", info)
