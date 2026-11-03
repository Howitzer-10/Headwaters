"""FU-201-6: the gateway port in daemon.json is the port that is really listening.

``pick_ports`` binds the gateway socket with ``bind_first_free`` (kept), and the daemon serves the
OpenAI app on that socket. The dashboard port is still only a probe (HDW-401).
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time

import httpx
import pytest

from headwaters.core.rules import DashboardRules, GatewayRules, Rules
from headwaters.daemon.service import pick_ports
from headwaters.gateway.openai_routes import build_gateway_app
from headwaters.gateway.server import HttpServer

TOKEN = "t0ken-for-tests"
API_PORT = 47813
GATEWAY_PORT = 47811
DASHBOARD_PORT = 47812


def rules_for(*, gateway: bool = True) -> Rules:
    return Rules(
        gateway=GatewayRules(enabled=gateway, port=GATEWAY_PORT),
        dashboard=DashboardRules(enabled=True, port=DASHBOARD_PORT),
    )


def test_pick_ports_binds_the_gateway_port() -> None:
    sock, gateway_sock, ports = pick_ports(rules_for(), API_PORT, API_PORT)
    try:
        assert gateway_sock is not None
        assert ports.gateway is not None
        # The recorded port is the port the socket holds, not a port that was merely free.
        assert ports.gateway == gateway_sock.getsockname()[1]
        assert ports.api == sock.getsockname()[1]
        assert len({ports.api, ports.gateway, ports.dashboard}) == 3
        assert ports.gateway >= GATEWAY_PORT  # the preferred port, moved only when it is taken
    finally:
        sock.close()
        if gateway_sock is not None:
            gateway_sock.close()


def test_pick_ports_leaves_the_gateway_alone_when_it_is_disabled() -> None:
    sock, gateway_sock, ports = pick_ports(rules_for(gateway=False), API_PORT, API_PORT)
    try:
        assert gateway_sock is None
        assert ports.gateway is None
    finally:
        sock.close()


def test_a_taken_gateway_port_moves_up_and_is_reported() -> None:
    rules = rules_for()
    sock, gateway_sock, ports = pick_ports(rules, API_PORT, API_PORT)
    try:
        assert gateway_sock is not None
        assert ports.gateway is not None
        # While the first socket holds the preferred port, a second daemon cannot take it.
        other_sock, other_gateway, other = pick_ports(rules, API_PORT + 1, API_PORT)
        try:
            assert other_gateway is not None
            assert other.gateway is not None
            assert other.gateway != ports.gateway
            assert other.gateway != other.api
        finally:
            other_sock.close()
            if other_gateway is not None:
                other_gateway.close()
    finally:
        sock.close()
        if gateway_sock is not None:
            gateway_sock.close()


def test_the_gateway_app_is_served_on_the_bound_port() -> None:
    rules = rules_for()
    sock, gateway_sock, ports = pick_ports(rules, API_PORT, API_PORT)
    assert gateway_sock is not None
    assert ports.gateway is not None
    app = build_gateway_app(token=TOKEN, router=lambda: None, rules=lambda: rules)
    server = HttpServer(app, gateway_sock)
    thread = threading.Thread(target=lambda: asyncio.run(server.serve()), daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10.0
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.started
        base = f"http://127.0.0.1:{ports.gateway}"
        health = httpx.get(f"{base}/health", timeout=5.0)
        assert health.status_code == 200
        assert health.json()["gateway"] == "openai"
        anonymous = httpx.get(f"{base}/v1/models", timeout=5.0)
        assert anonymous.status_code == 401
        listing = httpx.get(
            f"{base}/v1/models", headers={"Authorization": f"Bearer {TOKEN}"}, timeout=5.0
        )
        assert listing.status_code == 200
        assert next(model["id"] for model in listing.json()["data"]) == "headwaters/auto"
    finally:
        server.request_shutdown()
        thread.join(timeout=10.0)
        sock.close()


def test_pick_ports_still_probes_the_dashboard() -> None:
    sock, gateway_sock, ports = pick_ports(rules_for(), API_PORT, API_PORT)
    try:
        assert ports.dashboard is not None
        assert ports.dashboard >= DASHBOARD_PORT
        assert ports.dashboard not in {ports.api, ports.gateway}
        # Nothing holds the dashboard port: it is a probe, not a binding, until HDW-401.
        probe = socket.socket()
        try:
            probe.bind(("127.0.0.1", ports.dashboard))
        finally:
            probe.close()
    finally:
        sock.close()
        if gateway_sock is not None:
            gateway_sock.close()


@pytest.mark.parametrize("preferred", [API_PORT, None])
def test_the_api_port_is_bound_the_same_way(preferred: int | None) -> None:
    sock, gateway_sock, ports = pick_ports(rules_for(), preferred, API_PORT)
    try:
        assert ports.api == sock.getsockname()[1]
    finally:
        sock.close()
        if gateway_sock is not None:
            gateway_sock.close()
