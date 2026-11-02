"""Background jobs for the model tools (HDW-206, D9: the job_id pattern is the baseline).

``start_job`` writes the job row (SQLite: ``jobs``), starts the work as a task on the daemon's event
loop and returns the id at once. The task reports each router step into the row (status, a 0..1
progress that never goes down, a short fixed message), and ends in ``done`` with a link to the full
detail, or ``failed`` with a structured error. The answer itself stays in memory (``JobRegistry``),
because the database never holds output text; ``headwaters_job_result`` reads it from there.

The MCP Tasks extension is not used: a host has to declare it, and D9 keeps the ``job_id`` pattern
as the one every host can follow. Local scope is untouched: the job runs the same prepared request
through the same router.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from collections.abc import Callable

from pydantic import BaseModel

from headwaters.core.logging import get_logger
from headwaters.core.state_misc import create_job, update_job
from headwaters.mcp.jobs import RESTART_ERROR, prune_finished_jobs
from headwaters.mcp.threads import run_blocking
from headwaters.mcp.tools.context import ToolContext, ToolFailure
from headwaters.mcp.tools.estimate import Estimate
from headwaters.mcp.tools.flow import Prepared, run_prepared
from headwaters.mcp.tools.frontdoor import classify
from headwaters.mcp.tools.outcome import Outcome
from headwaters.mcp.tools.progress import Progress, Update

__all__ = ["Shaper", "start_job"]

_log = get_logger("mcp.jobs")

Shaper = Callable[[Outcome], tuple[BaseModel, str]]
"""Turns a finished outcome into the tool's structured result and its text rendering."""


async def start_job(
    tc: ToolContext, tool: str, prepared: Prepared, estimate: Estimate, shape: Shaper
) -> str:
    """Create the job and start it. Raises ``ToolFailure`` when too many jobs already run."""
    if tc.jobs.active() >= tc.settings.max_jobs:
        raise ToolFailure(
            "too_many_jobs",
            f"{tc.jobs.active()} background jobs are already running.",
            "Wait for one to finish (headwaters_job_status), then try again.",
        )
    db = tc.state()
    retention = tc.rules().mcp.job_retention_days
    # HDW-146: old finished rows go when a new job starts, so the table needs no timer of its own.
    await run_blocking(lambda: prune_finished_jobs(db, retention))
    job_id = secrets.token_urlsafe(9)
    await run_blocking(lambda: create_job(db, job_id, "queued", tool=tool))
    tc.jobs.start(job_id, _run(tc, job_id, tool, prepared, estimate, shape))
    return job_id


async def _run(
    tc: ToolContext, job_id: str, tool: str, prepared: Prepared, estimate: Estimate, shape: Shaper
) -> None:
    db = tc.state()

    async def into_row(update: Update) -> None:
        await run_blocking(
            lambda: update_job(
                db, job_id, status="running", progress=update.fraction, message=update.message
            )
        )

    try:
        await run_blocking(lambda: update_job(db, job_id, status="running", message="Starting"))
        cold = estimate.model if estimate.cold else None
        heartbeat = tc.settings.progress_heartbeat_s
        async with Progress([into_row], heartbeat_s=heartbeat, cold_model=cold) as progress:
            outcome = await run_prepared(tc, prepared, progress)
        model, text = shape(outcome)
        tc.jobs.keep_result(job_id, {"structured": model.model_dump(mode="json"), "text": text})
        await run_blocking(
            lambda: update_job(
                db, job_id, status="done", progress=1.0, result_ref=outcome.uri, message="Done"
            )
        )
    except asyncio.CancelledError:
        if tc.jobs.was_cancelled(
            job_id
        ):  # the user cancelled it: the model call is already stopped
            update_job(db, job_id, status="cancelled", message="Cancelled")
        else:  # the daemon is stopping: leave no job "running"
            update_job(db, job_id, status="failed", error_json=json.dumps(RESTART_ERROR))
        raise
    except Exception as exc:
        failure = classify(exc, tool)
        error = failure.error.model_dump_json()
        _log.info("job failed", extra={"tool": tool, "code": failure.error.code})
        await run_blocking(
            lambda: update_job(
                db, job_id, status="failed", error_json=error, message=failure.error.code
            )
        )
