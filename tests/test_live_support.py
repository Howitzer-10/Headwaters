"""The live-test sweeper kills strays by command line, and only the ones it did not start with."""

from __future__ import annotations

import subprocess
import sys
import time

import psutil
import pytest

from tests.live_support import sweep_new, target_pids

IDLE = "import time; time.sleep(120)"


def _stray(module: str) -> subprocess.Popen[bytes]:
    """A process whose command line looks like ``python -m <module>`` but only sleeps."""
    code = f"import sys; sys.argv=['-m', {module!r}]; {IDLE}"
    return subprocess.Popen(
        [sys.executable, "-c", code, "-m", module],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _wait_listed(pid: int) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if pid in target_pids():
            return
        time.sleep(0.1)
    pytest.fail(f"pid {pid} never showed up in target_pids()")


def test_sweep_kills_new_matches_and_spares_older_ones() -> None:
    old = _stray("headwaters.daemon")
    try:
        _wait_listed(old.pid)
        before = target_pids()
        new_daemon, new_shim = _stray("headwaters.daemon"), _stray("headwaters.mcp")
        try:
            _wait_listed(new_daemon.pid)
            _wait_listed(new_shim.pid)
            killed = sweep_new(before)
            assert {new_daemon.pid, new_shim.pid} <= set(killed)
            assert old.pid not in killed
            assert psutil.pid_exists(old.pid)
            assert new_daemon.wait(timeout=10) is not None
            assert new_shim.wait(timeout=10) is not None
        finally:
            for proc in (new_daemon, new_shim):
                if proc.poll() is None:
                    proc.kill()
    finally:
        old.kill()
        old.wait(timeout=10)


def test_sweep_with_nothing_new_kills_nothing() -> None:
    assert sweep_new(target_pids()) == []


def test_other_python_processes_are_not_matched() -> None:
    other = subprocess.Popen(
        [sys.executable, "-c", IDLE],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        time.sleep(0.5)
        assert other.pid not in target_pids()
    finally:
        other.kill()
        other.wait(timeout=10)
