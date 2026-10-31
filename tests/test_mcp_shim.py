"""HDW-202: the shim forwards to the daemon through DaemonLink; no daemon or network involved."""

from __future__ import annotations

import asyncio

import pytest
from mcp import Client, MCPError, types

from headwaters.core.daemon_client import DaemonClient
from headwaters.core.daemon_control import ControlResult
from headwaters.mcp import link as link_mod
from headwaters.mcp.link import DaemonLink, DaemonUnavailable, ensure_daemon
from headwaters.mcp.shim import build_shim_server
from tests.mcp_shim_support import INFO, FakeUpstream, make_link


def test_tools_list_and_call_are_forwarded() -> None:
    upstream = FakeUpstream()

    async def scenario() -> None:
        link = make_link([upstream])
        async with Client(build_shim_server(link)) as client:
            listed = await client.list_tools()
            assert [t.name for t in listed.tools] == ["headwaters_status"]
            result = await client.call_tool("headwaters_status", {"a": 1})
            assert result.content[0].text == "ok"  # type: ignore[union-attr]
        await link.close()

    asyncio.run(scenario())
    assert upstream.calls == [("headwaters_status", {"a": 1})]


def test_a_dropped_connection_is_reopened_for_list() -> None:
    broken, fresh = FakeUpstream(), FakeUpstream()
    broken.fail = ConnectionError("gone")
    ensured: list[int] = []

    async def scenario() -> None:
        link = make_link([broken, fresh], ensured)
        async with Client(build_shim_server(link)) as client:
            listed = await client.list_tools()  # retried once on a fresh connection
            assert len(listed.tools) == 1
        await link.close()

    asyncio.run(scenario())
    assert len(ensured) == 2  # the daemon was looked up again after the failure


def test_a_failed_tool_call_is_not_retried_and_becomes_an_error_result() -> None:
    broken = FakeUpstream()
    broken.fail = ConnectionError("gone")
    spare = FakeUpstream()

    async def scenario() -> types.CallToolResult:
        link = make_link([broken, spare])
        async with Client(build_shim_server(link)) as client:
            result = await client.call_tool("headwaters_do", {})
        await link.close()
        return result

    result = asyncio.run(scenario())
    assert result.is_error
    assert "headwaters start" in result.content[0].text  # type: ignore[union-attr]
    assert spare.calls == []  # a call that may have run is never repeated


def test_daemon_errors_pass_through_untouched() -> None:
    upstream = FakeUpstream()
    upstream.fail = MCPError(-32602, "bad arguments")

    async def scenario() -> None:
        link = make_link([upstream])
        async with Client(build_shim_server(link)) as client:
            with pytest.raises(MCPError, match="bad arguments"):
                await client.call_tool("headwaters_do", {})
        await link.close()

    asyncio.run(scenario())


def test_no_daemon_gives_a_clear_list_error() -> None:
    async def ensure() -> DaemonClient:
        raise DaemonUnavailable("The daemon did not answer within 25 s.")

    async def scenario() -> None:
        link = DaemonLink(ensure)
        async with Client(build_shim_server(link)) as client:
            with pytest.raises(MCPError, match="headwaters start"):
                await client.list_tools()

    asyncio.run(scenario())


def test_ensure_daemon_uses_a_running_daemon_without_starting() -> None:
    started: list[int] = []
    client = DaemonClient(INFO, "t")

    def start() -> ControlResult:
        started.append(1)
        return ControlResult("started", "ok", INFO)

    assert asyncio.run(ensure_daemon(find=lambda: client, start=start)) is client
    assert started == []


def test_cold_start_launches_the_daemon_once() -> None:
    started: list[int] = []
    client = DaemonClient(INFO, "t")
    state = {"up": False}

    def find() -> DaemonClient | None:
        return client if state["up"] else None

    def start() -> ControlResult:
        started.append(1)
        state["up"] = True
        return ControlResult("started", "ok", INFO)

    assert asyncio.run(ensure_daemon(find=find, start=start)) is client
    assert started == [1]


def test_a_daemon_that_cannot_start_is_reported() -> None:
    def start() -> ControlResult:
        return ControlResult("failed", "The daemon exited with code 2.")

    with pytest.raises(DaemonUnavailable, match="exited with code 2"):
        asyncio.run(ensure_daemon(find=lambda: None, start=start))


def test_a_daemon_that_starts_but_does_not_answer_is_reported() -> None:
    def start() -> ControlResult:
        return ControlResult("started", "ok", INFO)

    with pytest.raises(DaemonUnavailable, match="does not answer"):
        asyncio.run(ensure_daemon(find=lambda: None, start=start))


def test_open_daemon_client_needs_a_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(link_mod, "read_token", lambda: None)

    async def scenario() -> None:
        async with link_mod.open_daemon_client(DaemonClient(INFO, "t")):
            pass

    with pytest.raises(DaemonUnavailable, match="token"):
        asyncio.run(scenario())


def test_prewarm_swallows_an_unavailable_daemon() -> None:
    async def ensure() -> DaemonClient:
        raise DaemonUnavailable("nope")

    asyncio.run(DaemonLink(ensure).prewarm())
