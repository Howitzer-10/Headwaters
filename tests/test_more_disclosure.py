"""HDW-205: progressive disclosure through a real MCP client (headwaters_more, list_changed)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from mcp import Client

from headwaters.mcp.meta import META_CLIENT
from headwaters.mcp.tools import descriptions
from tests.frontdoor_support import Rig, structured, text_of

ADVANCED = sorted(descriptions.ADVANCED)


def _host(name: str) -> Any:
    """``_meta`` naming this host (typed Any: the SDK's meta type is a TypedDict)."""
    return {META_CLIENT: name}


def _names(listed: Any) -> list[str]:
    return sorted(t.name for t in listed.tools)


async def _list(client: Client, host: str) -> list[str]:
    meta: Any = {META_CLIENT: host}
    return _names(await client.list_tools(meta=meta, cache_mode="bypass"))


def test_advanced_tools_are_hidden_until_the_host_asks(tmp_path: Path) -> None:
    async def go() -> tuple[list[str], list[str], list[str]]:
        rig = Rig(tmp_path)
        async with rig.client() as c:
            before = await _list(c, "host-a")
            await c.call_tool("headwaters_more", {}, meta=_host("host-a"))
            after_a = await _list(c, "host-a")
            after_b = await _list(c, "host-b")
            return before, after_a, after_b

    before, after_a, after_b = asyncio.run(go())
    assert len(before) == 8
    assert not set(ADVANCED) & set(before)
    assert set(ADVANCED) <= set(after_a)  # host-a asked: it now sees all seventeen
    assert len(after_a) == 17
    assert after_b == before  # host-b did not: its list is unchanged


def test_more_lists_each_advanced_tool_with_a_summary(tmp_path: Path) -> None:
    result = asyncio.run(Rig(tmp_path).call("headwaters_more", {}))
    data = structured(result)
    assert data["enabled"] is True
    assert sorted(t["name"] for t in data["tools"]) == ADVANCED
    assert all(t["summary"] for t in data["tools"])
    assert "headwaters_recommend_model" in text_of(result)


def test_more_publishes_a_list_change_to_a_listening_client(tmp_path: Path) -> None:
    async def go() -> str:
        rig = Rig(tmp_path)
        async with rig.client() as c, c.listen(tools_list_changed=True) as sub:
            await c.call_tool("headwaters_more", {}, meta=_host("h"))
            event = await asyncio.wait_for(sub.__anext__(), 3)
            return type(event).__name__

    assert asyncio.run(go()) == "ToolsListChanged"


def test_asking_twice_is_harmless_and_does_not_announce_again(tmp_path: Path) -> None:
    async def go() -> list[str]:
        rig = Rig(tmp_path)
        async with rig.client() as c:
            for _ in range(2):
                await c.call_tool("headwaters_more", {}, meta=_host("h"))
            return await _list(c, "h")

    assert len(asyncio.run(go())) == 17


def test_an_unlisted_advanced_tool_still_answers_but_cannot_widen_anything(
    tmp_path: Path,
) -> None:
    """Disclosure is about list size, not safety: no advanced tool can loosen a lock."""
    rig = Rig(tmp_path)
    result = asyncio.run(rig.call("headwaters_quota_status", {}))
    assert result.is_error is False
    assert rig.edits == []
