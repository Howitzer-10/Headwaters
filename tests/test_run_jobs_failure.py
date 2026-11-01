"""HDW-206: a job that fails, a daemon that restarts, and too many jobs at once."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from mcp import types

from headwaters.core.state_misc import create_job, get_job, update_job
from headwaters.mcp.jobs import JobRegistry, recover_orphans
from tests.run_support import CODE_TASK, Rig, cold_rig, start_and_finish, structured


def test_a_failed_job_keeps_a_structured_error(tmp_path: Path) -> None:
    rig = cold_rig(tmp_path)

    async def boom(request: Any) -> Any:
        raise RuntimeError("secret provider text")

    async def go() -> types.CallToolResult:
        original = rig.engine.router.run
        rig.engine.router.run = boom  # type: ignore[method-assign]
        try:
            started = await start_and_finish(rig, "headwaters_do", {"task": CODE_TASK})
        finally:
            rig.engine.router.run = original  # type: ignore[method-assign]
        return await rig.call("headwaters_job_status", {"job_id": started["job_id"]})

    failed = asyncio.run(go())
    data = structured(failed)
    assert data["status"] == "failed"
    assert data["ok"] is False
    assert failed.is_error is True
    assert data["error"]["code"] == "internal_error"
    assert "secret provider text" not in json.dumps(data)  # an unexpected message is never passed


def test_job_result_of_a_failed_job_is_the_same_error(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    create_job(rig.db, "j2", "running")
    err = {"code": "no_candidates", "message": "Nothing fits.", "fix": "Pull a model."}
    update_job(rig.db, "j2", status="failed", error_json=json.dumps(err))
    data = structured(asyncio.run(rig.call("headwaters_job_result", {"job_id": "j2"})))
    assert data["error"]["code"] == "no_candidates"
    assert data["status"] == "failed"


def test_an_unknown_job_is_a_clear_error(tmp_path: Path) -> None:
    result = asyncio.run(Rig(tmp_path).call("headwaters_job_status", {"job_id": "nope"}))
    assert structured(result)["error"]["code"] == "unknown_job"
    assert result.is_error is True


def test_a_daemon_restart_fails_unfinished_jobs_and_keeps_finished_ones(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    create_job(rig.db, "run", "running")
    create_job(rig.db, "wait", "queued")
    create_job(rig.db, "fin", "running")
    update_job(rig.db, "fin", status="done", result_ref="headwaters://result/x")
    assert recover_orphans(rig.db) == 2
    states = []
    for job in ("run", "wait", "fin"):
        row = get_job(rig.db, job)
        assert row is not None
        states.append(row.status)
    assert states == ["failed", "failed", "done"]
    again = structured(asyncio.run(rig.call("headwaters_job_status", {"job_id": "run"})))
    assert again["error"]["code"] == "daemon_restarted"


def test_a_finished_job_after_a_restart_says_the_answer_is_gone(tmp_path: Path) -> None:
    rig = cold_rig(tmp_path)

    async def go() -> dict[str, Any]:
        started = await start_and_finish(rig, "headwaters_do", {"task": CODE_TASK})
        rig.context.jobs = JobRegistry()  # a new daemon run: nothing in memory
        return structured(await rig.call("headwaters_job_result", {"job_id": started["job_id"]}))

    data = asyncio.run(go())
    assert data["status"] == "done"
    assert "restarted" in data["notes"][0]
    assert data["result"]["result_uri"].startswith("headwaters://result/")


def test_too_many_jobs_at_once_is_refused_with_a_fix(tmp_path: Path) -> None:
    rig = cold_rig(tmp_path, max_jobs=1)
    gate = asyncio.Event()

    async def hold(request: Any) -> Any:
        await gate.wait()
        raise RuntimeError("released")

    async def go() -> types.CallToolResult:
        original = rig.engine.router.run
        rig.engine.router.run = hold  # type: ignore[method-assign]
        try:
            first = structured(await rig.call("headwaters_do", {"task": CODE_TASK}))
            assert first["job_id"]
            second = await rig.call("headwaters_do", {"task": CODE_TASK})
            gate.set()
            await rig.context.jobs.wait()
            return second
        finally:
            rig.engine.router.run = original  # type: ignore[method-assign]

    second = asyncio.run(go())
    assert structured(second)["error"]["code"] == "too_many_jobs"
    assert second.is_error is True
    with rig.db.read() as c:
        assert c.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1  # none for the refused
