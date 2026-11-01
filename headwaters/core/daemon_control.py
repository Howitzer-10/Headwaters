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
from headwaters.core.daemon_info import DaemonInfo, read_info, remove_info

__all__ = [
    "DAEMON_MODULE",
    "ControlResult",
    "daemon_command",
    "start_daemon",
    "stop_daemon",
]

DAEMON_MODULE = "headwaters.daemon"
_DETACHED = 0x00000008  # DETACHED_PROCESS
_NEW_GROUP = 0x00000200  # CREATE_NEW_PROCESS_GROUP
_NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW
_BREAKAWAY = 0x01000000  # CREATE_BREAKAWAY_FROM_JOB
START_TIMEOUT_S = 25.0
STOP_TIMEOUT_S = 15.0

Outcome = Literal["started", "already_running", "stopped", "not_running", "stale_cleaned", "failed"]
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


def _spawn_detached(argv: Sequence[str]) -> subprocess.Popen[bytes]:
    env = {**os.environ, "PYTHONUTF8": "1"}
    if sys.platform == "win32":
        flags = _DETACHED | _NEW_GROUP | _NO_WINDOW
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
            log = paths.log_dir() / "headwaters.log"
            return ControlResult(
                "failed", f"The daemon exited with code {child.returncode}; see {log}."
            )
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


def stop_daemon(
    *,
    find: Finder = find_running,
    timeout_s: float = STOP_TIMEOUT_S,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    alive: Callable[[int], bool] = _pid_alive,
) -> ControlResult:
    """Ask the daemon to shut down gracefully and wait until it is gone."""
    client = find()
    if client is None:
        stale = read_info()
        if stale is not None and not alive(stale.pid):
            remove_info(stale.pid)
            return ControlResult("stale_cleaned", "No daemon was running; removed a stale file.")
        return ControlResult("not_running", "The daemon is not running.")
    info = client.info
    try:
        client.shutdown()
    except DaemonError as exc:
        return ControlResult("failed", f"The daemon refused to stop: {exc}", info)
    deadline = clock() + timeout_s
    while clock() < deadline:
        if not alive(info.pid) and read_info() is None:
            return ControlResult("stopped", "The daemon has stopped.", info)
        sleep(0.2)
    return ControlResult("failed", f"The daemon (pid {info.pid}) is still running.", info)
