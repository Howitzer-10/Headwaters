"""HDW-206: the estimate that decides "background" and "ask first", and local scope staying shut."""

from __future__ import annotations

import asyncio
from pathlib import Path

from headwaters.core.rules import Rules
from headwaters.mcp.tools.estimate import estimate_call
from headwaters.mcp.tools.flow import prepare_request
from tests.run_support import CODE_TASK, PAID, Rig, cold_rig, paid_rig, structured


def _estimate(rig: Rig, **kw: object):  # type: ignore[no-untyped-def]
    async def go():  # type: ignore[no-untyped-def]
        prepared = await prepare_request(
            rig.context,
            "do",
            task=CODE_TASK,
            want="answer",
            paths=[],
            images=[],
            scope=kw.get("scope"),  # type: ignore[arg-type]
            meta=None,
            verify_always=True,
        )
        return await estimate_call(prepared, rig.context.settings)

    return asyncio.run(go())


def test_a_warm_local_model_is_quick_and_free(tmp_path: Path) -> None:
    est = _estimate(Rig(tmp_path))
    assert est.model == "qwen:14b"
    assert est.cold is False
    assert est.cost_usd == 0.0
    assert est.duration_s < 20


def test_a_cold_local_model_adds_its_load_time(tmp_path: Path) -> None:
    warm = _estimate(Rig(tmp_path / "w"))
    cold = _estimate(cold_rig(tmp_path / "c"))
    assert cold.cold is True
    assert cold.duration_s > warm.duration_s + 10  # 12 GB at the load rate
    assert cold.cost_usd == 0.0


def test_a_paid_model_costs_its_catalog_price(tmp_path: Path) -> None:
    est = _estimate(paid_rig(tmp_path))
    assert est.model == PAID
    assert est.cost_usd > 1.0  # 5000 per million output tokens, ~400 tokens
    assert est.cold is False


def test_the_estimate_is_a_dry_run_that_writes_nothing_and_calls_no_model(tmp_path: Path) -> None:
    rig = paid_rig(tmp_path)
    _estimate(rig)
    with rig.db.read() as c:
        assert c.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    assert rig.backend.requests == []
    assert rig.cloud_calls == 0


def test_local_scope_stays_local_through_the_new_path(tmp_path: Path) -> None:
    rig = paid_rig(tmp_path)  # a better, costly paid model exists
    args = {"task": CODE_TASK, "scope": "local"}
    data = structured(asyncio.run(rig.call("headwaters_do", args)))
    assert data["ok"] is True
    assert data["tier"] == "local"
    assert data["confirmation_required"] is False  # local costs nothing: nobody is asked
    assert rig.cloud_calls == 0


def test_a_background_job_in_local_scope_builds_no_cloud_client(tmp_path: Path) -> None:
    rig = paid_rig(tmp_path)
    args = {"task": CODE_TASK, "scope": "local", "async": True}
    started = structured(asyncio.run(rig.call("headwaters_do", args)))
    assert started["job_id"]
    asyncio.run(rig.context.jobs.wait())
    assert rig.cloud_calls == 0
    assert started["tier"] == "local"


def test_the_private_lock_beats_the_confirmation_not_the_other_way(tmp_path: Path) -> None:
    rig = paid_rig(tmp_path)
    rig.rules = rig.rules.model_copy(update={"private_lock": True})
    data = structured(asyncio.run(rig.call("headwaters_do", {"task": CODE_TASK})))
    assert data["tier"] == "local"  # the lock narrowed the scope before any cost was worked out
    assert data["confirmation_required"] is False
    assert rig.cloud_calls == 0


def test_a_refused_request_is_still_reported_and_logged_by_the_router(tmp_path: Path) -> None:
    rig = Rig(tmp_path, rules=Rules(private_lock=True))
    with rig.db.write() as c:
        c.execute("DELETE FROM models")  # nothing local to serve a locked request
    data = structured(asyncio.run(rig.call("headwaters_do", {"task": CODE_TASK})))
    assert data["ok"] is False
    assert data["error"]["code"]
    with rig.db.read() as c:  # the router wrote its usual early-refusal row
        assert c.execute("SELECT COUNT(*) FROM decisions").fetchone()[0] == 1


def test_loosening_the_scope_per_call_needs_the_user(tmp_path: Path) -> None:
    rig = Rig(tmp_path, rules=Rules(default_scope="local"), cloud=True)
    args = {"task": CODE_TASK, "scope": "free"}
    data = structured(asyncio.run(rig.call("headwaters_do", args)))
    assert data["confirmation_required"] is True
    assert data["confirm"]["reasons"] == ["scope_loosening"]
    assert data["confirm"]["requested_scope"] == "free"
    assert data["confirm"]["default_scope"] == "local"
    assert rig.cloud_calls == 0


def test_tightening_the_scope_per_call_needs_nobody(tmp_path: Path) -> None:
    rig = Rig(tmp_path, cloud=True)
    data = structured(asyncio.run(rig.call("headwaters_do", {"task": CODE_TASK, "scope": "local"})))
    assert data["ok"] is True
    assert data["confirmation_required"] is False
