"""The running side of background jobs (HDW-206, D9: keep the job_id pattern).

A job's state (status, progress, message, error, result link) lives in the SQLite ``jobs`` table so
every tool call and a restarted daemon can read it. The asyncio task that does the work cannot be
stored, so this registry only keeps a strong reference to each task until it ends. A daemon that
stops takes its tasks with it: ``recover_orphans`` marks those jobs failed on the next start, so
no job is left "running" forever. Nothing here touches request text.
"""

from __future__ import annotations

import asyncio
import json
from collections import OrderedDict
from collections.abc import Coroutine
from typing import Any

from headwaters.core.state import Database
from headwaters.core.state_misc import fail_unfinished_jobs

__all__ = ["RESTART_ERROR", "JobRegistry", "recover_orphans"]

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
        task.add_done_callback(lambda _t: self._tasks.pop(job_id, None))

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
