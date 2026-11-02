"""The running side of background jobs (HDW-206, D9: keep the job_id pattern).

A job's state (status, progress, message, error, result link) lives in the SQLite ``jobs`` table so
every tool call and a restarted daemon can read it. The asyncio task that does the work cannot be
stored, so this registry only keeps a strong reference to each task until it ends. A daemon that
stops takes its tasks with it: ``recover_orphans`` marks those jobs failed on the next start, so
no job is left "running" forever. A job the user cancels (HDW-146) ends ``cancelled``, which the
registry tells apart from a daemon that is stopping. Finished rows are deleted after
``mcp.job_retention_days``. Nothing here touches request text.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import OrderedDict
from collections.abc import Coroutine
from typing import Any

from headwaters.core.state import Database
from headwaters.core.state_misc import delete_finished_jobs_before, fail_unfinished_jobs

__all__ = ["RESTART_ERROR", "JobRegistry", "prune_finished_jobs", "recover_orphans"]

SECONDS_PER_DAY = 86_400.0

RESTART_ERROR = {
    "code": "daemon_restarted",
    "message": "The Headwaters daemon stopped before this job finished.",
    "fix": "Run the task again.",
}


MAX_KEPT_RESULTS = 50  # first-draft bound, like the result store


class JobRegistry:
    """Running tasks, plus the finished jobs' compact results.

    The results hold the model's answer, so they stay in memory only (never in SQLite, which keeps
    no output text). After a daemon restart a finished job still has its row and its
    ``result_ref`` link, but ``result_of`` answers None.
    """

    def __init__(self, max_results: int = MAX_KEPT_RESULTS) -> None:
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._cancelled: set[str] = set()
        self._results: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._max_results = max(1, max_results)

    def keep_result(self, job_id: str, payload: dict[str, Any]) -> None:
        self._results[job_id] = payload
        while len(self._results) > self._max_results:
            self._results.popitem(last=False)

    def result_of(self, job_id: str) -> dict[str, Any] | None:
        return self._results.get(job_id)

    def start(self, job_id: str, work: Coroutine[Any, Any, None]) -> None:
        """Run ``work`` on the daemon's event loop, past the end of the request that asked."""
        task = asyncio.get_running_loop().create_task(work, name=f"headwaters-job-{job_id}")
        self._tasks[job_id] = task
        task.add_done_callback(lambda _t: self._ended(job_id))

    def _ended(self, job_id: str) -> None:
        self._tasks.pop(job_id, None)
        self._cancelled.discard(job_id)

    def cancel(self, job_id: str) -> bool:
        """Ask a running job to stop (the model call under it is cancelled with it).

        True when a live task was asked to stop; False when there is none (the job already ended,
        or a restart left its row with no task).
        """
        task = self._tasks.get(job_id)
        if task is None or task.done():
            return False
        self._cancelled.add(job_id)
        task.cancel()
        return True

    def was_cancelled(self, job_id: str) -> bool:
        """True when the user cancelled this job (as opposed to a daemon that is stopping)."""
        return job_id in self._cancelled

    def active(self) -> int:
        return len(self._tasks)

    async def wait(self, job_id: str | None = None) -> None:
        """Wait for one job (or all of them) to end. For tests and an orderly shutdown."""
        tasks = [self._tasks[job_id]] if job_id in self._tasks else list(self._tasks.values())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


def recover_orphans(db: Database) -> int:
    """Fail every job a previous daemon run left queued or running. Returns how many."""
    return fail_unfinished_jobs(db, json.dumps(RESTART_ERROR))


def prune_finished_jobs(db: Database, retention_days: float, *, now: float | None = None) -> int:
    """Delete finished job rows (done, failed, cancelled) older than ``retention_days``.

    Returns how many rows went. A queued or running job is never deleted.
    """
    moment = time.time() if now is None else now
    return delete_finished_jobs_before(db, moment - retention_days * SECONDS_PER_DAY)
