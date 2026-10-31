"""HDW-201: the daemon state file, the per-user token and the single-instance lock."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from headwaters.core import daemon_info, paths
from headwaters.core.daemon_info import DaemonInfo, DaemonPorts
from headwaters.core.instance_lock import InstanceLock


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(paths.HOME_ENV_VAR, str(tmp_path))
    return tmp_path


def _info(pid: int = 1234) -> DaemonInfo:
    return DaemonInfo(pid, 1000.5, "0.0.0", DaemonPorts(4002, 4000, 4001))


def test_info_round_trips_and_exposes_urls() -> None:
    daemon_info.write_info(_info())
    found = daemon_info.read_info()
    assert found == _info()
    assert found is not None
    assert found.api_url == "http://127.0.0.1:4002"
    assert found.mcp_url == "http://127.0.0.1:4002/mcp"


def test_missing_info_is_none() -> None:
    assert daemon_info.read_info() is None


@pytest.mark.parametrize(
    "text",
    [
        "",
        "not json",
        "[]",
        '{"schema": 2}',
        '{"schema": 1, "pid": 1}',
        '{"schema": 1, "pid": "x", "started_at": 1, "version": "v", "host": "h",'
        ' "mcp_path": "/mcp", "ports": {"api": 4002}}',
        '{"schema": 1, "pid": 1, "started_at": 1, "version": "v", "host": "h",'
        ' "mcp_path": "/mcp", "ports": {"api": 99999}}',
        '{"schema": 1, "pid": 1, "started_at": 1, "version": "v", "host": "h",'
        ' "mcp_path": "/mcp", "ports": {"api": true}}',
    ],
)
def test_damaged_info_is_ignored_not_fatal(text: str) -> None:
    paths.get_paths().ensure_dirs()
    daemon_info.state_file_path().write_text(text, encoding="utf-8")
    assert daemon_info.read_info() is None


def test_remove_info_only_for_the_owning_pid() -> None:
    daemon_info.write_info(_info(pid=10))
    assert not daemon_info.remove_info(pid=11)
    assert daemon_info.read_info() is not None
    assert daemon_info.remove_info(pid=10)
    assert daemon_info.read_info() is None
    assert not daemon_info.remove_info()


def test_token_is_created_once_and_reused() -> None:
    first = daemon_info.load_or_create_token()
    assert len(first) >= 32
    assert daemon_info.load_or_create_token() == first
    assert daemon_info.read_token() == first


def test_empty_token_file_is_replaced() -> None:
    paths.get_paths().ensure_dirs()
    daemon_info.token_path().write_text("\n", encoding="utf-8")
    assert daemon_info.read_token() is None
    assert daemon_info.load_or_create_token()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_token_file_is_owner_only() -> None:
    daemon_info.load_or_create_token()
    assert daemon_info.token_path().stat().st_mode & 0o077 == 0


def test_lock_is_exclusive_and_released_cleanly() -> None:
    first, second = InstanceLock(), InstanceLock()
    assert first.acquire()
    assert first.acquire()  # re-entering is a no-op
    assert not second.acquire()
    first.release()
    assert second.acquire()
    second.release()
    second.release()  # releasing twice is harmless


def test_lock_is_held_across_processes(tmp_path: Path) -> None:
    holder = InstanceLock()
    assert holder.acquire()
    code = (
        "from headwaters.core.instance_lock import InstanceLock\nprint(InstanceLock().acquire())\n"
    )
    other = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env={**__import__("os").environ, paths.HOME_ENV_VAR: str(tmp_path)},
        check=True,
    )
    assert other.stdout.strip() == "False"
    holder.release()
    again = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env={**__import__("os").environ, paths.HOME_ENV_VAR: str(tmp_path)},
        check=True,
    )
    assert again.stdout.strip() == "True"
