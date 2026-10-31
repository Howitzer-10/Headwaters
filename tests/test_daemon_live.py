"""HDW-201: a real daemon process, two real clients, one shared instance.

Starts ``python -m headwaters.daemon`` in an isolated ``HEADWATERS_HOME`` and talks to it over
loopback HTTP and the streamable-HTTP MCP transport. No network beyond 127.0.0.1 is needed, but the
daemon's background refresh may try to reach Ollama / the catalog host; airplane mode is set so it
does not go further than loopback.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from headwaters.core import paths
from headwaters.core.daemon_client import DaemonClient, find_running
from headwaters.core.daemon_control import start_daemon, stop_daemon
from headwaters.core.daemon_info import read_info, read_token

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


def _start() -> DaemonClient:
    result = start_daemon(timeout_s=45)
    assert result.ok, result.message
    client = find_running()
    assert client is not None
    return client


def test_two_clients_share_one_daemon(live_home: Path) -> None:
    first = _start()
    second = start_daemon()  # a second start must find the first, not spawn another
    assert second.outcome == "already_running"
    assert second.info is not None
    assert second.info.pid == first.info.pid
    assert first.health()["pid"] == first.info.pid
    other = find_running()
    assert other is not None
    assert other.status()["pid"] == first.status()["pid"]


def test_a_second_daemon_process_exits_without_taking_over(live_home: Path) -> None:
    first = _start()
    done = subprocess.run(
        [sys.executable, "-m", "headwaters.daemon"],
        capture_output=True,
        timeout=60,
        env={**os.environ, paths.HOME_ENV_VAR: str(live_home)},
        check=False,
    )
    assert done.returncode == 3  # EXIT_ALREADY_RUNNING
    still = find_running()
    assert still is not None
    assert still.info.pid == first.info.pid


def test_state_file_lists_loopback_ports_and_endpoint(live_home: Path) -> None:
    client = _start()
    info = read_info()
    assert info is not None
    assert info.host == "127.0.0.1"
    assert info.ports.api == client.info.ports.api
    assert info.ports.gateway is not None
    assert info.ports.dashboard is not None
    assert len({info.ports.api, info.ports.gateway, info.ports.dashboard}) == 3
    assert info.mcp_url.endswith("/mcp")


def test_daemon_listens_on_loopback_only(live_home: Path) -> None:
    import psutil

    client = _start()
    listeners = [
        c.laddr
        for c in psutil.Process(client.info.pid).net_connections(kind="tcp")
        if c.status == psutil.CONN_LISTEN
    ]
    assert listeners, "the daemon is not listening"
    assert {addr.ip for addr in listeners} == {"127.0.0.1"}


def test_api_refuses_requests_without_the_token(live_home: Path) -> None:
    client = _start()
    anonymous = DaemonClient(client.info, None)
    with pytest.raises(Exception, match=r"token|401|Unauthorized"):
        anonymous.status()
    wrong = DaemonClient(client.info, "not-the-token")
    with pytest.raises(Exception, match=r"401|HTTP"):
        wrong.status()
    assert read_token() is not None


def test_two_mcp_clients_connect_to_the_same_endpoint(live_home: Path) -> None:
    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client

    daemon = _start()
    token = read_token()
    assert token is not None

    async def connect(name: str) -> str:
        import httpx2

        http = httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"})
        async with http, Client(streamable_http_client(daemon.info.mcp_url, http_client=http)) as c:
            tools = await c.list_tools()
            assert tools.tools == []  # tools arrive with HDW-202
            return name

    async def both() -> list[str]:
        return list(await asyncio.gather(connect("ide-a"), connect("ide-b")))

    assert asyncio.run(both()) == ["ide-a", "ide-b"]
    assert daemon.health()["pid"] == daemon.info.pid  # still the one daemon


def test_mcp_endpoint_rejects_a_missing_token(live_home: Path) -> None:
    import urllib.error
    import urllib.request

    daemon = _start()
    request = urllib.request.Request(
        daemon.info.mcp_url,
        data=b"{}",
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with pytest.raises(urllib.error.HTTPError) as raised:
        urllib.request.urlopen(request, timeout=10)
    assert raised.value.code == 401


def test_graceful_stop_removes_the_state_file_and_frees_the_port(live_home: Path) -> None:
    daemon = _start()
    port = daemon.info.ports.api
    started = time.monotonic()
    result = stop_daemon(timeout_s=20)
    assert result.outcome == "stopped", result.message
    assert time.monotonic() - started < 20
    assert read_info() is None
    assert find_running() is None
    from headwaters.core import netports

    netports.bind_loopback(port).close()  # the port is free again
    assert stop_daemon().outcome == "not_running"


def test_a_stale_state_file_is_cleaned_up_and_not_trusted(live_home: Path) -> None:
    from headwaters.core.daemon_info import DaemonInfo, DaemonPorts, write_info

    write_info(DaemonInfo(2_000_000_000, time.time(), "0.0.0", DaemonPorts(4999)))
    assert find_running() is None
    assert stop_daemon().outcome == "stale_cleaned"
    assert read_info() is None
