"""Logic of the advanced tools that read or write small bits of state (HDW-205).

``list_local_models``, ``quota_status``, ``job_status`` / ``job_result``, ``pin_model`` and
``feedback``. All are local: no model call and no network. Prompt text and file contents never
appear in any result.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime

from headwaters.core.candidates import fit_for_row
from headwaters.core.catalog.merge import CatalogLookup
from headwaters.core.engine import Engine
from headwaters.core.hardware.sysmem import expected_tps_for
from headwaters.core.quotas import model_subject
from headwaters.core.state_decisions import get_outcome, set_user_feedback
from headwaters.core.state_limits import get_quota
from headwaters.core.state_misc import get_job, kv_delete, kv_get, kv_set
from headwaters.core.state_models import list_models
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
        if row.tier == "local":
            continue
        subject = model_subject(row.id)
        rpm, rpd = get_quota(db, subject, rpm_window), get_quota(db, subject, rpd_window)
        if rpm is None and rpd is None:
            continue
        lines.append(
            QuotaLine(
                model=row.id,
                status="soft"
                if (rpm and rpm.soft_exhausted) or (rpd and rpd.soft_exhausted)
                else "ok",
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
            fix="Background jobs arrive with HDW-206; today every call runs and returns directly.",
        ),
    )


def job_status(tc: ToolContext, job_id: str) -> JobResult:
    row = get_job(tc.state(), job_id)
    if row is None:
        return _no_job(job_id)
    return JobResult(ok=True, job_id=row.job_id, status=row.status, progress=row.progress)


def job_result(tc: ToolContext, job_id: str) -> JobResult:
    row = get_job(tc.state(), job_id)
    if row is None:
        return _no_job(job_id)
    if row.status not in ("done", "completed") or not row.result_ref:
        return JobResult(
            ok=True,
            job_id=row.job_id,
            status=row.status,
            progress=row.progress,
            notes=["The job has not finished; ask again later."],
        )
    return JobResult(
        ok=True,
        job_id=row.job_id,
        status=row.status,
        progress=row.progress,
        result={"result_uri": row.result_ref},
    )


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
