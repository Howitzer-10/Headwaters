"""HDW-146: a cancelled job stops its model call; the cancel tool is safe to call at any time."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from headwaters.core.state_misc import create_job, get_job, update_job
from headwaters.mcp.jobs import JobRegistry
from tests.run_support import CODE_TASK, Rig, cold_rig, structured


async def _start_blocked_job(rig: Rig) -> tuple[str, asyncio.Event, dict[str, bool]]:
    """Start a job whose model call waits on a gate; report whether the call was cancelled."""
    gate = asyncio.Event()
    state = {"cancelled": False}

    async def hold(request: Any) -> Any:
        try:
            await gate.wait()
        except asyncio.CancelledError:
            state["cancelled"] = True
            raise
        raise RuntimeError("released")

    rig.engine.router.run = hold  # type: ignore[method-assign]
    started = structured(await rig.call("headwaters_do", {"task": CODE_TASK}))
    job_id = started["job_id"]
    for _ in range(100):  # let the task reach the model call
        await asyncio.sleep(0.01)
        row = get_job(rig.db, job_id)
        if row is not None and row.status == "running":
            break
    return job_id, gate, state


def test_cancelling_a_running_job_stops_the_model_call_and_marks_it_cancelled(
    tmp_path: Path,
) -> None:
    rig = cold_rig(tmp_path)

    async def go() -> tuple[dict[str, Any], dict[str, bool], str, int]:
        job_id, _gate, state = await _start_blocked_job(rig)
        cancelled = structured(await rig.call("headwaters_job_cancel", {"job_id": job_id}))
        await rig.context.jobs.wait(job_id)
        row = get_job(rig.db, job_id)
        assert row is not None
        return cancelled, state, row.status, rig.context.jobs.active()

    cancelled, state, status, active = asyncio.run(go())
    assert cancelled["ok"] is True
    assert cancelled["status"] == "cancelled"
    assert state["cancelled"] is True  # the model call itself was stopped
    assert status == "cancelled"  # and stays cancelled: the task's own end did not rewrite it
    assert active == 0


def test_a_cancelled_job_has_no_answer_and_is_not_an_error_of_the_daemon(tmp_path: Path) -> None:
    rig = cold_rig(tmp_path)

    async def go() -> tuple[dict[str, Any], dict[str, Any]]:
        job_id, _gate, _state = await _start_blocked_job(rig)
        await rig.call("headwaters_job_cancel", {"job_id": job_id})
        await rig.context.jobs.wait(job_id)
        args = {"job_id": job_id}
        return (
            structured(await rig.call("headwaters_job_status", args)),
            structured(await rig.call("headwaters_job_result", args)),
        )

    status, result = asyncio.run(go())
    assert status["status"] == "cancelled"
    assert status["error"] is None  # not "daemon_restarted": the user did it
    assert "cancelled" in result["notes"][0]
    assert result["result"] is None


def test_cancelling_twice_or_after_the_end_is_harmless(tmp_path: Path) -> None:
    rig = cold_rig(tmp_path)

    async def go() -> tuple[dict[str, Any], dict[str, Any]]:
        job_id, _gate, _state = await _start_blocked_job(rig)
        await rig.call("headwaters_job_cancel", {"job_id": job_id})
        await rig.context.jobs.wait(job_id)
        again = structured(await rig.call("headwaters_job_cancel", {"job_id": job_id}))
        create_job(rig.db, "fin", "running")
        update_job(rig.db, "fin", status="done", result_ref="headwaters://result/x")
        late = structured(await rig.call("headwaters_job_cancel", {"job_id": "fin"}))
        return again, late

    again, late = asyncio.run(go())
    assert again["status"] == "cancelled"
    assert "already ended" in again["notes"][0]
    assert late["status"] == "done"  # a finished job is never turned into a cancelled one
    assert "already ended" in late["notes"][0]


def test_cancelling_an_unknown_job_is_a_clear_error(tmp_path: Path) -> None:
    result = asyncio.run(Rig(tmp_path).call("headwaters_job_cancel", {"job_id": "nope"}))
    assert structured(result)["error"]["code"] == "unknown_job"
    assert result.is_error is True


def test_a_job_left_by_an_earlier_daemon_can_still_be_marked_cancelled(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    create_job(rig.db, "old", "running")  # no task here: a restart left the row
    data = structured(asyncio.run(rig.call("headwaters_job_cancel", {"job_id": "old"})))
    assert data["status"] == "cancelled"
    assert "earlier daemon" in data["notes"][0]


def test_a_stopping_daemon_still_fails_its_jobs_instead_of_cancelling_them(
    tmp_path: Path,
) -> None:
    rig = cold_rig(tmp_path)

    async def go() -> str:
        job_id, _gate, _state = await _start_blocked_job(rig)
        for task in list(rig.context.jobs._tasks.values()):  # a shutdown cancels every task
            task.cancel()
        await rig.context.jobs.wait(job_id)
        row = get_job(rig.db, job_id)
        assert row is not None
        return row.status

    assert asyncio.run(go()) == "failed"


def test_a_slow_progress_write_cannot_bring_a_cancelled_job_back(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    create_job(rig.db, "j", "running")
    update_job(rig.db, "j", status="cancelled")
    assert update_job(rig.db, "j", status="running", progress=0.5) is False
    row = get_job(rig.db, "j")
    assert row is not None
    assert (row.status, row.progress) == ("cancelled", 0.0)


def test_the_registry_says_whether_a_cancel_reached_a_task() -> None:
    registry = JobRegistry()

    async def go() -> tuple[bool, bool, bool]:
        gate = asyncio.Event()
        registry.start("a", gate.wait())  # type: ignore[arg-type]
        missing = registry.cancel("zzz")
        hit = registry.cancel("a")
        flagged = registry.was_cancelled("a")
        await registry.wait("a")
        return missing, hit, flagged

    missing, hit, flagged = asyncio.run(go())
    assert (missing, hit, flagged) == (False, True, True)
    assert registry.was_cancelled("a") is False  # forgotten once the task ended
