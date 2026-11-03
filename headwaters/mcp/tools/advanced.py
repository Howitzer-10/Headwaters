"""Logic of the advanced tools that read or write small bits of state (HDW-205).

``list_local_models``, ``quota_status``, ``job_status`` / ``job_result``, ``pin_model`` and
``feedback``. All are local: no model call and no network. Prompt text and file contents never
appear in any result.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from headwaters.core.candidates import fit_for_row
from headwaters.core.catalog.merge import CatalogLookup
from headwaters.core.engine import Engine
from headwaters.core.hardware.sysmem import expected_tps_for
from headwaters.core.quotas import model_subject
from headwaters.core.state_decisions import get_outcome, set_user_feedback
from headwaters.core.state_limits import get_quota
from headwaters.core.state_misc import (
    UNFINISHED_JOB_STATES,
    JobRow,
    cancel_unfinished_job,
    get_job,
    kv_delete,
    kv_get,
    kv_set,
)
from headwaters.core.state_models import list_models
from headwaters.mcp.threads import run_blocking
from headwaters.mcp.tools.context import ToolContext, ToolFailure
from headwaters.mcp.tools.schemas import ErrorInfo
from headwaters.mcp.tools.schemas_more import (
    FeedbackResult,
    JobResult,
    LocalModelInfo,
    LocalModelsResult,
    PinResult,
    QuotaLine,
    QuotaResult,
)
from headwaters.mcp.tools.status import PIN_KEY, gpu_info, walls_now

__all__ = [
    "FEEDBACK_VALUES",
    "cancel_job",
    "give_feedback",
    "job_result",
    "job_status",
    "list_local",
    "pin_model",
    "quota_status",
]

FEEDBACK_VALUES = ("up", "down")


def list_local(tc: ToolContext, engine: Engine | None) -> LocalModelsResult:
    """Fit, context and speed of every local model on this GPU (the last discovery refresh)."""
    hw = engine.hardware() if engine is not None else None
    notes: list[str] = []
    if engine is None:
        notes.append("Headwaters is still loading its model list.")
    if hw is None or hw.gpu is None:
        notes.append("No GPU was detected, so fit is unknown.")
    lookup = CatalogLookup(engine.catalog) if engine is not None else None
    kv_type = tc.rules().tiers.local.kv_cache_type
    models: list[LocalModelInfo] = []
    for row in list_models(tc.state(), "local"):
        fit = fit_for_row(row, hw.budget_gb, 4096, kv_type) if hw is not None else None
        entry = lookup.find([row.id]) if lookup is not None else None
        tps = expected_tps_for(entry, hw.profile) if entry is not None and hw is not None else None
        models.append(
            LocalModelInfo(
                model=row.id,
                fit=fit.status if fit is not None else "unknown",
                num_ctx=fit.num_ctx if fit is not None else None,
                max_ctx=fit.max_ctx_tokens if fit is not None and fit.usable else None,
                size_gb=row.size_gb,
                warm=row.warm,
                expected_tps=tps,
                thinking=row.reasoning_ok,
            )
        )
    if not models:
        notes.append("No local models are known yet. Run `headwaters models refresh`.")
    return LocalModelsResult(gpu=gpu_info(hw), models=models, notes=notes)


def quota_status(tc: ToolContext, engine: Engine | None) -> QuotaResult:
    """Walls plus each non-local model's availability and request counters."""
    db = tc.state()
    lines: list[QuotaLine] = []
    now = time.time()
    moment = datetime.fromtimestamp(now, UTC)
    rpm_window = "rpm:" + moment.strftime("%Y-%m-%dT%H:%M")
    rpd_window = "rpd:" + moment.strftime("%Y-%m-%d")
    for row in list_models(db):
        subject = model_subject(row.id)
        rpm, rpd = get_quota(db, subject, rpm_window), get_quota(db, subject, rpd_window)
        if row.tier == "local":
            # FU-205-9: a local model spends no provider quota, but it belongs in the answer:
            # it is what runs when every key is out.
            lines.append(
                QuotaLine(
                    model=row.id,
                    status="local",
                    tier=row.tier,
                    warm=row.warm,
                    rpm_used=rpm.count if rpm else 0,
                    rpm_limit=rpm.limit if rpm else None,
                    rpd_used=rpd.count if rpd else 0,
                    rpd_limit=rpd.limit if rpd else None,
                )
            )
            continue
        if rpm is None and rpd is None:
            continue
        lines.append(
            QuotaLine(
                model=row.id,
                status="soft"
                if (rpm and rpm.soft_exhausted) or (rpd and rpd.soft_exhausted)
                else "ok",
                tier=row.tier,
                warm=row.warm,
                rpm_used=rpm.count if rpm else 0,
                rpm_limit=rpm.limit if rpm else None,
                rpd_used=rpd.count if rpd else 0,
                rpd_limit=rpd.limit if rpd else None,
            )
        )
    return QuotaResult(walls=walls_now(tc), models=lines)


def _no_job(job_id: str) -> JobResult:
    return JobResult(
        ok=False,
        job_id=job_id,
        error=ErrorInfo(
            code="unknown_job",
            message="No job has that id.",
            fix="Use the job_id a tool returned when it started the work in the background.",
        ),
    )


def _job_error(row: JobRow) -> ErrorInfo | None:
    """The structured failure kept with a job that did not finish."""
    if row.status != "failed":
        return None
    if row.error_json:
        try:
            return ErrorInfo.model_validate_json(row.error_json)
        except ValueError:
            pass
    return ErrorInfo(code="job_failed", message="The job failed.", fix="Run the task again.")


def _view(row: JobRow) -> JobResult:
    error = _job_error(row)
    return JobResult(
        ok=error is None,
        job_id=row.job_id,
        status=row.status,
        progress=row.progress,
        message=row.message,
        tool=row.tool,
        error=error,
    )


def job_status(tc: ToolContext, job_id: str) -> JobResult:
    row = get_job(tc.state(), job_id)
    return _no_job(job_id) if row is None else _view(row)


def job_result(tc: ToolContext, job_id: str) -> JobResult:
    """The finished job's compact result (the one the foreground call would have returned).

    It is kept in the daemon's memory, like ``headwaters://result/<id>``: after a daemon restart a
    finished job still has its row and its result link, but not the answer text (SQLite never
    holds output text), and the result says so.
    """
    row = get_job(tc.state(), job_id)
    if row is None:
        return _no_job(job_id)
    view = _view(row)
    if row.status == "failed":
        return view
    if row.status == "cancelled":
        return view.model_copy(update={"notes": ["The job was cancelled; there is no answer."]})
    if row.status != "done":
        return view.model_copy(update={"notes": ["The job has not finished; ask again later."]})
    kept = tc.jobs.result_of(job_id)
    link = {"result_uri": row.result_ref} if row.result_ref else {}
    if kept is None:
        note = "The answer is no longer in memory (the daemon restarted). Run the task again."
        update: dict[str, Any] = {"notes": [note], "result": link or None}
        return view.model_copy(update=update)
    return view.model_copy(update={"result": {**kept, **link}})


async def cancel_job(tc: ToolContext, job_id: str) -> JobResult:
    """Stop a queued or running background job (HDW-146).

    The task is cancelled, which cancels the model call under it; the row then says ``cancelled``.
    A job that already ended is left as it is and the result says so. Whatever the model call
    already cost stays in the ledger: only the rest of the work is stopped.
    """
    db = tc.state()
    row = await run_blocking(lambda: get_job(db, job_id))
    if row is None:
        return _no_job(job_id)
    if row.status not in UNFINISHED_JOB_STATES:
        note = f"The job had already ended ({row.status}); there was nothing to cancel."
        return _view(row).model_copy(update={"notes": [note]})
    stopped = tc.jobs.cancel(job_id)  # on the event loop: ``Task.cancel`` is not thread safe
    await run_blocking(lambda: cancel_unfinished_job(db, job_id, "Cancelled by the user"))
    fresh = await run_blocking(lambda: get_job(db, job_id))
    view = _view(fresh if fresh is not None else row)
    if fresh is not None and fresh.status != "cancelled":  # it finished a moment before
        note = f"The job had already ended ({fresh.status}); there was nothing to cancel."
        return view.model_copy(update={"notes": [note]})
    notes = ["The job was asked to stop; the model call under it is cancelled."]
    if not stopped:
        notes = ["The job had no running task here (an earlier daemon left it); it is marked."]
    return view.model_copy(update={"notes": notes})


def pin_model(tc: ToolContext, model: str | None) -> PinResult:
    """Pin one model for every later call of the front-door tools, or clear the pin.

    The pin only picks among models the scope already allows: the hard filter and the locks still
    apply, so a pin can never widen the scope. An unknown id is refused.
    """
    db = tc.state()
    before = kv_get(db, PIN_KEY)
    previous = before if isinstance(before, str) and before else None
    if model is None or not model.strip():
        kv_delete(db, PIN_KEY)
        return PinResult(
            ok=True, pinned_model=None, previous=previous, notes=["The pin is cleared."]
        )
    wanted = model.strip()
    known = {row.id for row in list_models(db)}
    if wanted not in known:
        return PinResult(
            ok=False,
            previous=previous,
            error=ErrorInfo(
                code="unknown_model",
                message=f"Headwaters does not know a model called {wanted!r}.",
                fix="Use an id from headwaters_list_local_models or `headwaters models list`.",
            ),
        )
    kv_set(db, PIN_KEY, wanted)
    note = "Requests use this model while the scope allows it; otherwise they are refused."
    return PinResult(ok=True, pinned_model=wanted, previous=previous, notes=[note])


def give_feedback(tc: ToolContext, decision_id: int, rating: str) -> FeedbackResult:
    """Record a thumbs up or down on one decision (``up`` or ``down``)."""
    if rating not in FEEDBACK_VALUES:
        raise ToolFailure(
            "bad_rating", f"rating must be one of {FEEDBACK_VALUES}.", "Use up or down."
        )
    db = tc.state()
    if get_outcome(db, decision_id) is None:
        return FeedbackResult(
            ok=False,
            error=ErrorInfo(
                code="unknown_decision",
                message="No finished request has that id.",
                fix="Use the decision_id from a result's result_uri detail.",
            ),
        )
    recorded = set_user_feedback(db, decision_id, rating)
    return FeedbackResult(ok=True, recorded=recorded)
