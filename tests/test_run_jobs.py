"""HDW-206: the job lifecycle (start, progress, result) and the async threshold."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from mcp import types

from headwaters.core.state_misc import create_job, update_job
from tests.run_support import CODE_TASK, Rig, cold_rig, start_and_finish, structured, text_of

ASK = {"question": CODE_TASK}


def test_a_call_over_the_threshold_returns_a_job_id_at_once(tmp_path: Path) -> None:
    rig = cold_rig(tmp_path)  # a cold 12 GB model: about 32 s expected, the limit is 20 s

    async def go() -> types.CallToolResult:
        result = await rig.call("headwaters_do", {"task": CODE_TASK})
        await rig.context.jobs.wait()
        return result

    result = asyncio.run(go())
    data = structured(result)
    assert data["ok"] is True
    assert data["job_id"]
    assert data["answer"] is None  # no answer yet: only the handle
    assert data["eta_s"] > 20
    assert data["model"] == "qwen:14b"
    assert "job_status" in text_of(result)


def test_a_fast_call_runs_in_the_foreground(tmp_path: Path) -> None:
    data = structured(asyncio.run(Rig(tmp_path).call("headwaters_do", {"task": CODE_TASK})))
    assert data["job_id"] is None
    assert data["answer"] == "Plain answer."


def test_the_threshold_is_the_users_setting(tmp_path: Path) -> None:
    rig = cold_rig(tmp_path, threshold_s=100000)  # slow model, huge limit: stays in the foreground
    data = structured(asyncio.run(rig.call("headwaters_do", {"task": CODE_TASK})))
    assert data["job_id"] is None
    assert data["answer"] == "Plain answer."


def test_async_true_starts_a_job_even_for_a_fast_call(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    started = asyncio.run(start_and_finish(rig, "headwaters_ask", {**ASK, "async": True}))
    assert started["ok"] is True


def test_a_job_runs_to_done_and_its_result_matches_the_foreground_shape(tmp_path: Path) -> None:
    rig = cold_rig(tmp_path)

    async def go() -> tuple[dict[str, Any], dict[str, Any]]:
        started = await start_and_finish(rig, "headwaters_do", {"task": CODE_TASK})
        args = {"job_id": started["job_id"]}
        status = structured(await rig.call("headwaters_job_status", args))
        done = structured(await rig.call("headwaters_job_result", args))
        return status, done

    status, done = asyncio.run(go())
    assert (status["status"], status["progress"], status["tool"]) == ("done", 1.0, "do")
    assert status["ok"] is True
    kept = done["result"]
    assert kept["structured"]["answer"] == "Plain answer."
    assert kept["structured"]["model"] == "qwen:14b"
    assert kept["structured"]["job_id"] is None  # the stored result is the foreground one
    assert kept["result_uri"].startswith("headwaters://result/")
    assert "Plain answer." in kept["text"]


def test_the_job_state_lives_in_sqlite_and_holds_no_answer_text(tmp_path: Path) -> None:
    rig = cold_rig(tmp_path)
    asyncio.run(start_and_finish(rig, "headwaters_do", {"task": "SECRET-TASK-WORDS"}))
    with rig.db.read() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM jobs")]
    assert len(rows) == 1
    stored = json.dumps(rows)
    assert "Plain answer." not in stored  # the model's answer stays in memory
    assert "SECRET-TASK-WORDS" not in stored
    assert rows[0]["status"] == "done"
    assert rows[0]["result_ref"].startswith("headwaters://result/")


def test_progress_is_written_to_the_row_and_never_goes_down(tmp_path: Path) -> None:
    import headwaters.mcp.tools.background as background

    rig = cold_rig(tmp_path)
    seen: list[float] = []
    original = background.update_job

    def spy(db: Any, job_id: str, **kw: Any) -> bool:
        if kw.get("progress") is not None:
            seen.append(kw["progress"])
        return original(db, job_id, **kw)

    background.update_job = spy  # type: ignore[assignment]
    try:
        asyncio.run(start_and_finish(rig, "headwaters_do", {"task": CODE_TASK}))
    finally:
        background.update_job = original
    assert len(seen) >= 4
    assert seen == sorted(seen)
    assert seen[-1] == 1.0


def test_a_running_job_reports_what_it_is_doing(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    create_job(rig.db, "j1", "running", tool="do")
    update_job(rig.db, "j1", progress=0.3, message="Loading qwen:14b into the GPU (cold start)")
    data = structured(asyncio.run(rig.call("headwaters_job_status", {"job_id": "j1"})))
    assert data["status"] == "running"
    assert data["progress"] == 0.3
    assert "cold start" in data["message"]
    result = structured(asyncio.run(rig.call("headwaters_job_result", {"job_id": "j1"})))
    assert result["result"] is None
    assert "not finished" in result["notes"][0]
