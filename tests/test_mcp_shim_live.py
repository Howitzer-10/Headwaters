"""HDW-202: the real shim process, a real MCP client over stdio, and a real daemon (slow)."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters

from headwaters.core import paths
from headwaters.core.daemon_client import find_running
from headwaters.core.daemon_control import start_daemon, stop_daemon
from headwaters.core.daemon_info import read_info

pytestmark = pytest.mark.slow


@pytest.fixture
def live_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setenv(paths.HOME_ENV_VAR, str(tmp_path))
    config = tmp_path / "config"
    config.mkdir()
    (config / "rules.yaml").write_text("version: 1\nairplane: true\n", encoding="utf-8")
    yield tmp_path
    stop_daemon(timeout_s=20)
    info = read_info()
    if info is not None:  # a failed test must not leave a daemon behind
        import psutil

        if psutil.pid_exists(info.pid):
            psutil.Process(info.pid).kill()


def _params(home: Path) -> StdioServerParameters:
    env = {**os.environ, paths.HOME_ENV_VAR: str(home), "PYTHONUTF8": "1"}
    return StdioServerParameters(command=sys.executable, args=["-m", "headwaters.mcp"], env=env)


def test_a_client_drives_the_shim_and_a_cold_start_launches_the_daemon(live_home: Path) -> None:
    assert find_running() is None  # cold: nothing is running

    async def scenario() -> tuple[list[str], int | None]:
        async with Client(_params(live_home)) as client:
            info = client.server_info
            assert info is not None
            assert info.name == "headwaters"
            listed = await client.list_tools()  # the daemon has no tools yet (HDW-203+)
            # Checked while connected: the SDK's stdio client runs the shim in a kill-on-close
            # Windows Job Object, which also reaps a daemon the shim started (see the report).
            daemon = find_running()
            return [t.name for t in listed.tools], daemon.info.pid if daemon else None

    names, pid = asyncio.run(scenario())
    assert names == []
    assert pid is not None, "the shim did not start the daemon"


def test_two_shims_share_one_daemon(live_home: Path) -> None:
    started = start_daemon(timeout_s=45)  # a daemon outside the clients' process trees
    assert started.ok, started.message
    first = find_running()
    assert first is not None

    async def one() -> None:
        async with Client(_params(live_home)) as client:
            await client.list_tools()
            current = find_running()
            assert current is not None
            assert current.info.pid == first.info.pid  # reused, not a second daemon

    async def both() -> None:
        await asyncio.gather(one(), one())

    asyncio.run(both())
    still = find_running()
    assert still is not None
    assert still.info.pid == first.info.pid


def test_shim_stdout_is_json_rpc_only(live_home: Path) -> None:
    """Raw pipes: every stdout line is a JSON-RPC message, and logs stay on stderr."""
    request = (
        '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25",'
        '"capabilities":{},"clientInfo":{"name":"t","version":"0"}}}\n'
        '{"jsonrpc":"2.0","method":"notifications/initialized"}\n'
        '{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n'
    )
    params = _params(live_home)
    done = subprocess.run(
        [params.command, *params.args],
        input=request.encode("utf-8"),
        capture_output=True,
        timeout=90,
        env=params.env,
        check=False,
    )
    lines = [line for line in done.stdout.decode("utf-8").splitlines() if line.strip()]
    assert lines, done.stderr.decode("utf-8", "replace")
    for line in lines:
        message = json.loads(line)  # raises on any non-JSON byte on stdout
        assert message["jsonrpc"] == "2.0"
    assert any(json.loads(line).get("id") == 1 for line in lines)
    assert done.returncode == 0


def test_a_shim_started_while_the_daemon_is_stopped_restarts_it(live_home: Path) -> None:
    started = start_daemon(timeout_s=45)
    assert started.ok, started.message
    assert stop_daemon(timeout_s=20).outcome == "stopped"
    assert find_running() is None

    async def scenario() -> bool:
        async with Client(_params(live_home)) as client:
            await client.list_tools()
            return find_running() is not None

    assert asyncio.run(scenario())
