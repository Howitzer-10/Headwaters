"""HDW-206: progress during cold loads, drafting and cascades (the progress stream)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from headwaters.core.router_types import ProgressEvent
from headwaters.mcp.tools.progress import STAGE_FRACTION, Progress, Update, describe
from tests.run_support import CODE_TASK, Rig, call_with_progress, structured


def _event(stage: str, **kw: object) -> ProgressEvent:
    return ProgressEvent(stage, **kw)  # type: ignore[arg-type]


async def _collect(events: list[ProgressEvent], *, cold_model: str | None = None) -> list[Update]:
    seen: list[Update] = []

    async def sink(update: Update) -> None:
        seen.append(update)

    async with Progress([sink], heartbeat_s=0, cold_model=cold_model) as progress:
        for event in events:
            progress(event)
    return seen


def test_a_foreground_call_streams_progress_that_only_rises(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    result, seen = asyncio.run(call_with_progress(rig, "headwaters_do", {"task": CODE_TASK}))
    assert result.is_error is False
    assert len(seen) >= 4  # analyzing, scope, deciding, executing ... done
    values = [p for p, _total, _msg in seen]
    assert values == sorted(set(values))  # strictly rising: MCP requires it
    messages = [m for _p, _t, m in seen]
    assert messages[0] == "Reading the request"
    assert messages[-1] == "Done"
    assert any("qwen:14b" in (m or "") for m in messages)


def test_a_host_that_did_not_ask_for_progress_still_gets_its_answer(tmp_path: Path) -> None:
    data = structured(asyncio.run(Rig(tmp_path).call("headwaters_do", {"task": CODE_TASK})))
    assert data["ok"] is True


def test_progress_covers_ask_too(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    _result, seen = asyncio.run(call_with_progress(rig, "headwaters_ask", {"question": CODE_TASK}))
    assert [m for _p, _t, m in seen][-1] == "Done"


def test_a_cold_local_model_is_announced_as_loading() -> None:
    events = [
        _event("analyzing"),
        _event("executing", model="big:70b", attempt=1),
        _event("executing", model="big:70b", attempt=1, detail="first_token"),
        _event("done", model="big:70b"),
    ]
    seen = asyncio.run(_collect(events, cold_model="big:70b"))
    messages = [u.message for u in seen]
    assert "Loading big:70b into the GPU (cold start); this can take a while" in messages
    assert "big:70b is answering" in messages


def test_a_warm_model_is_not_announced_as_loading() -> None:
    seen = asyncio.run(_collect([_event("executing", model="warm:7b", attempt=1)]))
    assert seen[0].message == "Waiting for warm:7b"


def test_only_the_first_call_to_a_cold_model_is_a_cold_start() -> None:
    events = [
        _event("executing", model="big:70b", attempt=1),
        _event("retrying", model="big:70b", attempt=1, detail="rate_limit"),
        _event("executing", model="big:70b", attempt=2),
    ]
    messages = [u.message for u in asyncio.run(_collect(events, cold_model="big:70b"))]
    assert sum("cold start" in m for m in messages) == 1


def test_a_cascade_and_a_retry_are_reported_and_the_fraction_never_goes_back() -> None:
    events = [
        _event("deciding"),
        _event("executing", model="a", attempt=1),
        _event("cascading", model="a", attempt=1, detail="rate_limited"),
        _event("deciding"),
        _event("executing", model="b", attempt=2),
        _event("verifying", model="b"),
        _event("escalating", model="b", detail="verify_failed"),
        _event("executing", model="c", attempt=3),
        _event("done", model="c"),
    ]
    seen = asyncio.run(_collect(events))
    messages = [u.message for u in seen]
    assert "a hit a limit (rate limited); trying another" in messages
    assert "The check failed; trying a stronger model than b" in messages
    fractions = [u.fraction for u in seen]
    assert fractions == sorted(fractions)  # the 'deciding' after the cascade does not go back
    assert fractions[-1] == 1.0
    assert [u.step for u in seen] == list(range(1, len(seen) + 1))


def test_a_failed_check_is_reported_in_the_last_message() -> None:
    last = describe(_event("done", model="m", detail="verify_failed"))
    assert "failed its check" in last
    assert STAGE_FRACTION["done"] == 1.0
