"""HDW-146: the shim asks inside its host; a direct client and a shim client are both asked.

A fake host answers yes, no and cancel. The shim's link opens real clients of the real tools, so a
spend question travels host -> shim -> daemon client -> tools and the answer comes back the same
way. A host that cannot be asked takes the ``force`` text fallback (D9), through the shim as well.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest
from mcp import Client

from headwaters.mcp.forward import MODERN_PROTOCOL, forward_elicitation
from headwaters.mcp.shim import build_shim_server
from tests.run_support import CODE_TASK, PAID, paid_rig, structured
from tests.shim_ask_support import FakeHost, rig_link

DO = {"task": CODE_TASK}


async def _through_shim(
    tmp_path: Path, host: FakeHost | None, args: dict[str, Any], *, mode: str = "auto"
) -> tuple[Any, Any, list[Any]]:
    """One ``headwaters_do`` from a fake host through the shim. Returns result, rig, openings."""
    rig = paid_rig(tmp_path)
    openings: list[Any] = []
    link = rig_link(rig, openings, elicitation=forward_elicitation)
    kwargs: dict[str, Any] = {"mode": mode}
    if host is not None:
        kwargs["elicitation_callback"] = host
    async with Client(build_shim_server(link), **kwargs) as client:
        result = await client.call_tool("headwaters_do", args)
    await link.close()
    return result, rig, openings


def test_a_direct_client_is_asked_and_a_yes_runs_the_call(tmp_path: Path) -> None:
    host = FakeHost("accept", True)
    rig = paid_rig(tmp_path)
    data = structured(asyncio.run(rig.call("headwaters_do", DO, elicit=host)))
    assert data["ok"] is True
    assert data["model"] == PAID
    assert len(host.questions) == 1
    assert PAID in host.questions[0]
    assert rig.cloud_calls == 1


@pytest.mark.parametrize("mode", ["auto", "legacy"])
def test_a_shim_client_is_asked_inside_its_host_and_a_yes_runs_the_call(
    tmp_path: Path, mode: str
) -> None:
    host = FakeHost("accept", True)
    result, rig, openings = asyncio.run(_through_shim(tmp_path, host, DO, mode=mode))
    data = structured(result)
    assert data["ok"] is True, data
    assert data["confirmation_required"] is False
    assert len(host.questions) == 1  # asked once, inside the host
    assert PAID in host.questions[0]
    assert rig.cloud_calls == 1
    assert openings == [forward_elicitation]  # the daemon client declared elicitation


@pytest.mark.parametrize("mode", ["auto", "legacy"])
def test_through_the_shim_a_no_runs_nothing(tmp_path: Path, mode: str) -> None:
    host = FakeHost("accept", False)
    result, rig, _ = asyncio.run(_through_shim(tmp_path, host, DO, mode=mode))
    data = structured(result)
    assert data["error"]["code"] == "declined"
    assert rig.cloud_calls == 0
    assert len(host.questions) == 1


@pytest.mark.parametrize("mode", ["auto", "legacy"])
def test_through_the_shim_a_cancel_runs_nothing(tmp_path: Path, mode: str) -> None:
    host = FakeHost("cancel")
    result, rig, _ = asyncio.run(_through_shim(tmp_path, host, DO, mode=mode))
    assert structured(result)["error"]["code"] == "declined"
    assert rig.cloud_calls == 0


def test_through_the_shim_a_decline_runs_nothing(tmp_path: Path) -> None:
    host = FakeHost("decline")
    result, rig, _ = asyncio.run(_through_shim(tmp_path, host, DO))
    assert structured(result)["error"]["code"] == "declined"
    assert rig.cloud_calls == 0


@pytest.mark.parametrize("mode", ["auto", "legacy"])
def test_a_host_that_cannot_be_asked_still_uses_the_force_flag_through_the_shim(
    tmp_path: Path, mode: str
) -> None:
    result, rig, openings = asyncio.run(_through_shim(tmp_path, None, DO, mode=mode))
    data = structured(result)
    assert data["confirmation_required"] is True
    assert data["confirm"]["retry_with"] == {"force": True}
    assert rig.cloud_calls == 0
    assert openings == [None]  # the daemon client declared no elicitation
    forced = asyncio.run(_through_shim(tmp_path / "f", None, {**DO, "force": True}, mode=mode))
    assert structured(forced[0])["ok"] is True
    assert forced[1].cloud_calls == 1


def test_force_does_not_skip_the_question_for_a_host_that_can_be_asked_through_the_shim(
    tmp_path: Path,
) -> None:
    host = FakeHost("accept", False)
    result, rig, _ = asyncio.run(_through_shim(tmp_path, host, {**DO, "force": True}))
    assert structured(result)["error"]["code"] == "declined"  # the user still had the say
    assert rig.cloud_calls == 0


def test_a_call_that_needs_no_yes_is_not_asked_about_through_the_shim(tmp_path: Path) -> None:
    rig = paid_rig(tmp_path, price_out=0.001)
    host = FakeHost()

    async def go() -> Any:
        link = rig_link(rig, elicitation=forward_elicitation)
        async with Client(build_shim_server(link), elicitation_callback=host) as client:
            result = await client.call_tool("headwaters_do", DO)
        await link.close()
        return result

    assert structured(asyncio.run(go()))["ok"] is True
    assert host.questions == []


@pytest.mark.parametrize(("mode", "callback_used"), [("legacy", 1), ("auto", 0)])
def test_each_protocol_era_takes_its_own_path_to_the_host(
    tmp_path: Path, mode: str, callback_used: int
) -> None:
    """A handshake-era host is asked through the daemon client's callback; a 2026-07-28 host gets
    the daemon's input-required result back and answers on the retry (the callback is not used)."""
    rig = paid_rig(tmp_path)
    host = FakeHost()
    used: list[int] = []

    async def counting(context: Any, params: Any) -> Any:
        used.append(1)
        return await forward_elicitation(context, params)

    async def go() -> Any:
        link = rig_link(rig, elicitation=counting)
        async with Client(build_shim_server(link), mode=mode, elicitation_callback=host) as client:
            result = await client.call_tool("headwaters_do", DO)
        await link.close()
        return result

    assert structured(asyncio.run(go()))["ok"] is True
    assert len(used) == callback_used
    assert len(host.questions) == 1  # either way the host was asked exactly once


def test_the_modern_protocol_constant_matches_the_sdk() -> None:
    from mcp_types.version import MODERN_PROTOCOL_VERSIONS

    assert MODERN_PROTOCOL in MODERN_PROTOCOL_VERSIONS


def test_the_question_text_names_the_model_and_cost_but_not_the_task(tmp_path: Path) -> None:
    host = FakeHost("accept", False)
    asyncio.run(_through_shim(tmp_path, host, DO))
    assert "adds two numbers" not in host.questions[0]  # no request text in a question
    assert "$" in host.questions[0]
