"""HDW-146: a host that cancels a call stops it on the daemon, through the shim.

A real host client cancels its ``tools/call``; the shim's work is cancelled, the daemon client
cancels its own request, and the model call under the tool stops. The call here is a foreground one
(``headwaters_do`` below the async threshold), which is the case a job does not cover.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from mcp import Client

from headwaters.mcp.shim import build_shim_server
from tests.run_support import CODE_TASK, Rig
from tests.shim_ask_support import rig_link


async def _cancel_a_blocked_call(rig: Rig, mode: str) -> tuple[bool, bool, bool]:
    """Cancel a host call whose model call is blocked: (started, model_cancelled, link_reusable)."""
    started = asyncio.Event()
    state = {"cancelled": False}

    async def hold(request: Any) -> Any:
        started.set()
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            state["cancelled"] = True
            raise

    rig.engine.router.run = hold  # type: ignore[method-assign]
    link = rig_link(rig)
    async with Client(build_shim_server(link), mode=mode) as host:
        call = asyncio.create_task(host.call_tool("headwaters_do", {"task": CODE_TASK}))
        await asyncio.wait_for(started.wait(), 10)
        call.cancel()
        for _ in range(200):  # the cancellation travels host -> shim -> daemon client -> tool
            if state["cancelled"]:
                break
            await asyncio.sleep(0.02)
        listed = await host.list_tools()  # the shim and its daemon connection still work
    await link.close()
    return started.is_set(), state["cancelled"], bool(listed.tools)


def test_a_host_cancel_reaches_the_daemon_and_stops_the_model_call(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    started, model_cancelled, reusable = asyncio.run(_cancel_a_blocked_call(rig, "legacy"))
    assert started is True
    assert model_cancelled is True
    assert reusable is True


def test_a_host_cancel_on_the_modern_protocol_reaches_the_daemon_too(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    started, model_cancelled, reusable = asyncio.run(_cancel_a_blocked_call(rig, "auto"))
    assert (started, model_cancelled, reusable) == (True, True, True)
