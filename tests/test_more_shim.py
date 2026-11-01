"""HDW-205: the shim identifies its host to the daemon and announces a changed tool list."""

from __future__ import annotations

import asyncio
from pathlib import Path

from mcp import Client

from headwaters.core.rules_edit import set_setting
from headwaters.mcp.meta import META_CLIENT
from headwaters.mcp.shim import build_shim_server
from tests.mcp_shim_support import FakeUpstream, make_link


def test_every_call_and_listing_carries_the_same_client_id() -> None:
    upstream = FakeUpstream()

    async def scenario() -> None:
        link = make_link([upstream])
        async with Client(build_shim_server(link)) as client:
            await client.list_tools()
            await client.call_tool("headwaters_status", {})
            await client.list_tools()
        await link.close()

    asyncio.run(scenario())
    ids = {m[META_CLIENT] for m in [*upstream.metas, *upstream.list_metas]}
    assert len(ids) == 1
    assert len(next(iter(ids))) >= 8  # random and not guessable


def test_two_shims_have_different_client_ids() -> None:
    first, second = FakeUpstream(), FakeUpstream()

    async def scenario() -> None:
        for upstream in (first, second):
            link = make_link([upstream])
            async with Client(build_shim_server(link)) as client:
                await client.list_tools()
            await link.close()

    asyncio.run(scenario())
    assert first.list_metas[0][META_CLIENT] != second.list_metas[0][META_CLIENT]


def test_calling_more_makes_the_shim_announce_a_tool_list_change() -> None:
    upstream = FakeUpstream()

    async def scenario() -> str:
        link = make_link([upstream])
        async with (
            Client(build_shim_server(link)) as client,
            client.listen(tools_list_changed=True) as sub,
        ):
            await client.call_tool("headwaters_more", {})
            event = await asyncio.wait_for(sub.__anext__(), 3)
            await link.close()
            return type(event).__name__

    assert asyncio.run(scenario()) == "ToolsListChanged"


def test_other_tools_do_not_announce_a_change() -> None:
    upstream = FakeUpstream()

    async def scenario() -> bool:
        link = make_link([upstream])
        async with (
            Client(build_shim_server(link)) as client,
            client.listen(tools_list_changed=True) as sub,
        ):
            await client.call_tool("headwaters_status", {})
            try:
                await asyncio.wait_for(sub.__anext__(), 0.5)
            except TimeoutError:
                return False
            finally:
                await link.close()
            return True

    assert asyncio.run(scenario()) is False


def test_strategy_can_be_edited_in_rules_yaml_keeping_comments(tmp_path: Path) -> None:
    target = tmp_path / "rules.yaml"
    target.write_text("# top\nstrategy: cost_saver  # why\n", encoding="utf-8", newline="\n")
    assert set_setting("strategy", "quality_first", target) == ("cost_saver", "quality_first")
    assert target.read_text(encoding="utf-8") == "# top\nstrategy: quality_first  # why\n"
