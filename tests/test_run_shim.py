"""HDW-206: progress and the force-flag fallback survive the stdio shim in front of the daemon."""

from __future__ import annotations

import asyncio
from pathlib import Path

from mcp import Client

from headwaters.mcp.shim import build_shim_server
from tests.mcp_shim_support import FakeUpstream, make_link
from tests.run_support import CODE_TASK, cold_rig, paid_rig, structured


def test_the_daemons_progress_reaches_the_host_through_the_shim() -> None:
    upstream = FakeUpstream()
    upstream.progress = ["Reading the request", "Waiting for qwen:14b", "Done"]
    seen: list[tuple[float, str | None]] = []

    async def on_progress(progress: float, total: float | None, message: str | None) -> None:
        seen.append((progress, message))

    async def scenario() -> None:
        link = make_link([upstream])
        async with Client(build_shim_server(link)) as client:
            await client.call_tool("headwaters_do", {"task": "t"}, progress_callback=on_progress)
        await link.close()

    asyncio.run(scenario())
    assert seen == [(1.0, "Reading the request"), (2.0, "Waiting for qwen:14b"), (3.0, "Done")]


def test_a_host_that_did_not_ask_for_progress_is_not_bothered() -> None:
    upstream = FakeUpstream()
    upstream.progress = ["a", "b"]

    async def scenario() -> str:
        link = make_link([upstream])
        async with Client(build_shim_server(link)) as client:
            result = await client.call_tool("headwaters_do", {"task": "t"})
        await link.close()
        return result.content[0].text  # type: ignore[union-attr,no-any-return]

    assert asyncio.run(scenario()) == "ok"


def test_host_to_shim_to_the_real_tools_carries_progress_end_to_end(tmp_path: Path) -> None:
    rig = cold_rig(tmp_path, threshold_s=100000)  # slow, but run in the foreground

    class Upstream(FakeUpstream):
        async def call_tool(  # type: ignore[no-untyped-def,override]
            self, name, arguments=None, *, meta=None, progress_callback=None
        ):
            async with rig.client() as inner:
                return await inner.call_tool(
                    name, arguments, meta=meta, progress_callback=progress_callback
                )

    seen: list[str] = []

    async def on_progress(progress: float, total: float | None, message: str | None) -> None:
        seen.append(message or "")

    async def scenario() -> None:
        link = make_link([Upstream()])
        async with Client(build_shim_server(link)) as client:
            await client.call_tool(
                "headwaters_do", {"task": CODE_TASK}, progress_callback=on_progress
            )
        await link.close()

    asyncio.run(scenario())
    assert seen[0] == "Reading the request"
    assert seen[-1] == "Done"
    assert any("cold start" in m for m in seen)


def test_through_the_shim_a_host_that_cannot_be_asked_uses_the_force_flag(tmp_path: Path) -> None:
    rig = paid_rig(tmp_path)  # the shim's daemon client declares no elicitation capability
    refused = structured(asyncio.run(rig.call("headwaters_do", {"task": CODE_TASK})))
    assert refused["confirmation_required"] is True
    assert refused["confirm"]["retry_with"] == {"force": True}
    assert rig.cloud_calls == 0
