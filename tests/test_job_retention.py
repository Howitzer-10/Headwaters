"""HDW-146: finished jobs past ``mcp.job_retention_days`` are deleted; the rest are kept."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic import ValidationError

from headwaters.core.rules import Rules
from headwaters.core.state_misc import create_job, get_job
from headwaters.mcp.jobs import prune_finished_jobs
from tests.run_support import CODE_TASK, Rig, cold_rig

DAY = 86_400.0
NOW = 1_000_000.0
IDS = ("old_done", "old_failed", "old_cancelled", "new_done", "old_running", "old_queued")


def _seed(rig: Rig) -> None:
    rows = (
        ("old_done", "done", 10),
        ("old_failed", "failed", 10),
        ("old_cancelled", "cancelled", 10),
        ("new_done", "done", 1),
        ("old_running", "running", 10),
        ("old_queued", "queued", 10),
    )
    for job_id, status, age_days in rows:
        create_job(rig.db, job_id, status)
        with rig.db.write() as c:
            c.execute(
                "UPDATE jobs SET updated = ? WHERE job_id = ?", (NOW - age_days * DAY, job_id)
            )


def test_finished_jobs_past_the_retention_are_deleted_and_the_rest_kept(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    _seed(rig)
    assert prune_finished_jobs(rig.db, 7, now=NOW) == 3
    kept = {job_id for job_id in IDS if get_job(rig.db, job_id) is not None}
    assert kept == {"new_done", "old_running", "old_queued"}  # unfinished jobs are never deleted


def test_a_job_just_inside_the_retention_is_kept(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    _seed(rig)
    assert prune_finished_jobs(rig.db, 11, now=NOW) == 0  # the oldest rows are 10 days old


def test_starting_a_job_prunes_by_the_users_retention_setting(tmp_path: Path) -> None:
    rig = cold_rig(tmp_path)
    rig.rules = Rules.model_validate({"mcp": {"async_threshold_s": 20, "job_retention_days": 2}})
    create_job(rig.db, "stale", "done")
    create_job(rig.db, "recent", "done")
    with rig.db.write() as c:
        c.execute("UPDATE jobs SET updated = updated - ? WHERE job_id = 'stale'", (3 * DAY,))

    async def go() -> None:
        await rig.call("headwaters_do", {"task": CODE_TASK})
        await rig.context.jobs.wait()

    asyncio.run(go())
    assert get_job(rig.db, "stale") is None
    assert get_job(rig.db, "recent") is not None


def test_the_default_retention_is_a_week_and_must_be_positive() -> None:
    assert Rules().mcp.job_retention_days == 7
    for bad in (0, -1):
        with pytest.raises(ValidationError):
            Rules.model_validate({"mcp": {"job_retention_days": bad}})
