"""HDW-206: the progress pump itself (heartbeat, lost steps, broken sinks) and what it may say."""

from __future__ import annotations

import asyncio
from pathlib import Path

from headwaters.core.router_types import ProgressEvent
from headwaters.mcp.tools.progress import Progress, Update
from tests.run_support import Rig, call_with_progress, cold_rig, structured

CODE_TASK = "write a function that adds two numbers"


def _event(stage: str, **kw: object) -> ProgressEvent:
    return ProgressEvent(stage, **kw)  # type: ignore[arg-type]


def test_a_slow_step_repeats_its_message_so_the_host_sees_life() -> None:
    async def scenario() -> list[Update]:
        seen: list[Update] = []

        async def sink(update: Update) -> None:
            seen.append(update)

        async with Progress([sink], heartbeat_s=0.02) as progress:
            progress(_event("executing", model="m", attempt=1))
            await asyncio.sleep(0.15)
        return seen

    seen = asyncio.run(scenario())
    assert len(seen) >= 3
    assert len({u.message for u in seen}) == 1  # the same words, again and again
    assert [u.step for u in seen] == list(range(1, len(seen) + 1))  # but a rising counter


def test_a_step_that_arrives_with_a_heartbeat_is_not_lost() -> None:
    async def scenario() -> set[str]:
        seen: list[Update] = []

        async def sink(update: Update) -> None:
            seen.append(update)

        async with Progress([sink], heartbeat_s=0.01) as progress:
            for i in range(30):
                progress(_event("executing", model=f"m{i}", attempt=i))
                await asyncio.sleep(0.004)
        return {u.model or "" for u in seen}

    assert {f"m{i}" for i in range(30)} <= asyncio.run(scenario())


def test_a_broken_sink_never_breaks_the_call() -> None:
    async def scenario() -> list[Update]:
        good: list[Update] = []

        async def bad(update: Update) -> None:
            raise RuntimeError("the host went away")

        async def ok(update: Update) -> None:
            good.append(update)

        async with Progress([bad, ok], heartbeat_s=0) as progress:
            progress(_event("analyzing"))
            progress(_event("done"))
        return good

    assert len(asyncio.run(scenario())) == 2


def test_leaving_the_block_on_an_error_stops_the_pump_at_once() -> None:
    async def scenario() -> bool:
        try:
            async with Progress([], heartbeat_s=0.01) as progress:
                progress(_event("analyzing"))
                raise ValueError("the call failed")
        except ValueError:
            return True
        return False

    assert asyncio.run(scenario()) is True


def test_progress_messages_never_carry_request_text(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    secret = "PRIVATE-TASK-WORDS-12345"
    _result, seen = asyncio.run(
        call_with_progress(rig, "headwaters_do", {"task": f"write a function about {secret}"})
    )
    assert seen
    assert all(secret not in (m or "") for _p, _t, m in seen)


def test_a_cold_model_shows_the_loading_message_through_a_real_call(tmp_path: Path) -> None:
    rig = cold_rig(tmp_path, threshold_s=100000)  # slow, but still run in the foreground
    result, seen = asyncio.run(call_with_progress(rig, "headwaters_do", {"task": CODE_TASK}))
    assert structured(result)["ok"] is True
    assert any("cold start" in (m or "") for _p, _t, m in seen)
