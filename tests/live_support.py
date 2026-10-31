"""Shared fixture for the live (real process) tests: no daemon or shim may outlive its test.

Cleanup finds processes by command line, not by the pid in ``daemon.json``: the state file can be
gone already (a daemon killed with its client's Job Object), and on Windows the venv launcher
and the real interpreter are two unrelated processes. Only processes that appeared during the
test are killed, so a daemon the developer started by hand is never touched.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import psutil
import pytest

from headwaters.core import paths
from headwaters.core.daemon_control import stop_daemon

__all__ = ["live_home", "sweep_new", "target_pids"]

MARKERS = ("headwaters.daemon", "headwaters.mcp")
SWEEP_TIMEOUT_S = 10.0


def target_pids() -> set[int]:
    """Pids of running ``python -m headwaters.daemon`` / ``headwaters.mcp`` processes."""
    found: set[int] = set()
    for proc in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmd = " ".join(proc.info["cmdline"] or [])
        except (psutil.Error, TypeError):
            continue
        if "-m" in cmd and any(marker in cmd for marker in MARKERS):
            found.add(int(proc.info["pid"]))
    return found


def sweep_new(before: set[int]) -> list[int]:
    """Kill every matching process that was not in ``before``; return the pids killed."""
    killed: list[int] = []
    for pid in sorted(target_pids() - before):
        try:
            psutil.Process(pid).kill()
            killed.append(pid)
        except psutil.Error:
            pass
    deadline = time.monotonic() + SWEEP_TIMEOUT_S
    while time.monotonic() < deadline and (target_pids() - before):
        time.sleep(0.1)
    return killed


@pytest.fixture
def live_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """An isolated ``HEADWATERS_HOME`` in airplane mode; leaves no process behind."""
    before = target_pids()
    monkeypatch.setenv(paths.HOME_ENV_VAR, str(tmp_path))
    config = tmp_path / "config"
    config.mkdir()
    (config / "rules.yaml").write_text("version: 1\nairplane: true\n", encoding="utf-8")
    try:
        yield tmp_path
    finally:
        stop_daemon(timeout_s=20)  # graceful first
        sweep_new(before)  # then anything that is left, whatever its state file says
