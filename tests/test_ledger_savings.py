"""HDW-501: the ledger's counterfactual, and what happens when the feature is not there.

The figures are estimates of what a reference model would have charged for the same tokens. These
tests pin the three honesty rules (an estimate is labelled, an unpriced request is counted rather
than costed at zero, no rate is ever invented) and the clean-removal promise: with the feature
disabled, or with its folder absent from discovery, the product answers without a savings figure.
"""

from __future__ import annotations

import asyncio
import pkgutil
import time
from pathlib import Path
from typing import Any

from headwaters.core import router_log
from headwaters.core.catalog.loader import SEED_RELATIVE_PATH, load_catalog
from headwaters.core.executor.types import ExecResult, Usage
from headwaters.core.features import FeatureRegistry, discover_features
from headwaters.core.rules import LedgerRules, Rules
from headwaters.core.scope import TierName
from headwaters.core.state import Database, open_database
from headwaters.core.state_decisions import DecisionRow, OutcomeRow, insert_decision, save_outcome
from headwaters.features.ledger.rates import find_entry, priced_models, resolve_reference
from headwaters.features.ledger.report import (
    RequestRow,
    build_report,
    read_requests,
    render_summary,
)
from tests.cli_support import add_model
from tests.executor_support import decision as executor_decision
from tests.frontdoor_support import Rig, structured

REPO_ROOT = Path(__file__).resolve().parent.parent
SONNET = "claude-sonnet-4-5"  # the one paid, dated model in the seed catalog
GHOST = "acme/ghost-1"  # a paid model the catalog does not rate (its rate was dropped)
LOCAL = "qwen:14b"
DAY = 86_400.0
NOW = 1_760_000_000.0


def catalog() -> Any:
    return load_catalog(REPO_ROOT / SEED_RELATIVE_PATH)


def rated() -> frozenset[str]:
    return priced_models(catalog())


def rate() -> Any:
    found, reason = resolve_reference("", catalog())
    assert found is not None, reason
    return found


def row(
    *,
    decision_id: int = 1,
    ts: float = NOW,
    model: str | None = "qwen:14b",
    tier: str = "local",
    tokens_in: int | None = 1_000_000,
    tokens_out: int | None = 1_000_000,
    cost: float | None = 0.0,
    status: str | None = "ok",
) -> RequestRow:
    return RequestRow(decision_id, ts, model, tier, status, tokens_in, tokens_out, cost)


def test_the_reference_is_the_catalog_model_chosen_from_data() -> None:
    """No hard-coded slug: an unset reference resolves to the catalog's most expensive model."""
    found = rate()
    assert found.model == SONNET
    assert (found.price_in, found.price_out) == (3.0, 15.0)
    assert found.price_date is not None
    assert found.source == "catalog"


def test_a_named_reference_is_used_and_an_unknown_one_is_not_substituted() -> None:
    entry = find_entry(catalog(), "claude-sonnet-4-5-20250929")  # the catalog's alias
    assert entry is not None
    assert entry.id == SONNET
    named, reason = resolve_reference(SONNET.upper(), catalog())
    assert named is not None
    assert named.source == "rules"
    assert reason == ""
    missing, why = resolve_reference("gpt-nope", catalog())
    assert missing is None
    assert "not in the catalog" in why


def test_a_catalog_model_without_a_rate_yields_no_estimate() -> None:
    """A local model carries no rate, so naming it is unpriced — never a zero-cost reference."""
    local = next(entry for entry in catalog().models if entry.price_in is None)
    assert local.id
    found, reason = resolve_reference(local.id, catalog())
    assert found is None
    assert "no dated catalog rate" in reason


def test_the_estimate_is_labelled_and_priced_from_the_dated_rate() -> None:
    report = build_report([row()], rate(), now=NOW, window_days=30, rated=rated())
    totals = report["totals"]
    assert report["available"] is True
    assert totals["actual_usd"] == 0.0
    assert totals["counterfactual_usd"] == 18.0  # 1M in at $3 + 1M out at $15
    assert totals["estimated_savings_usd"] == 18.0
    assert report["reference"]["price_date"] == rate().price_date
    label = report["label"]
    assert "Estimate, not money in hand" in label
    assert SONNET in label
    assert "1 of 1 requests" in label


def test_unpriced_requests_are_left_out_and_counted() -> None:
    rows = [
        row(),
        row(decision_id=2, model="mystery", tier="paid", cost=None, tokens_in=10, tokens_out=10),
    ]
    report = build_report(rows, rate(), now=NOW, window_days=30, rated=rated())
    totals = report["totals"]
    assert totals["requests"] == 2
    assert totals["priced_requests"] == 1
    assert totals["unpriced_requests"] == 1
    assert totals["counterfactual_usd"] == 18.0  # only the priced one
    assert "1 had no comparable rate" in report["label"]
    assert "1 of 2 requests" in report["label"]
    assert "mystery" in report["label"]


def _written(db: Database, model: str, tier: TierName) -> None:
    """Route one request through the router's own logger, so the stored row is a real one.

    ``router_log.cost_usd`` is handed ``None`` prices for both calls, which is what ``router.py``
    passes for a model the catalog does not rate: the spend lands as ``0.0``, not NULL.
    """
    result = ExecResult(
        model=model,
        tier=tier,
        content="ok",
        reasoning="",
        tool_calls=(),
        finish_reason="stop",
        usage=Usage(prompt_tokens=1_000_000, completion_tokens=1_000_000),
        first_token_s=0.1,
    )
    stored = router_log.write(
        db,
        router_log.LogEntry(
            surface="cli",
            session="s",
            scope="local",
            strategy="balanced",
            status="ok",
            latency_ms=10,
            decision=executor_decision(tier=tier, model=model),
            result=result,
            cost_usd=router_log.cost_usd(None, None, result),
        ),
    )
    assert stored is not None


def test_a_paid_model_the_catalog_does_not_rate_is_unpriced_not_free(tmp_path: Path) -> None:
    """The stored spend cannot answer this: the real writer records 0.0 for an unrated paid model.

    Counting that as priced would turn the model's whole counterfactual into "savings", so the
    catalog's rated set decides, and the label names the model that was left out.
    """
    db = open_database(tmp_path / "state.db")
    add_model(db, GHOST, "paid", 80.0)
    _written(db, GHOST, "paid")
    rows = read_requests(db, since=0.0)
    assert [written.cost_usd for written in rows] == [0.0]  # indistinguishable from free
    report = build_report(rows, rate(), now=time.time(), window_days=30, rated=rated())
    totals = report["totals"]
    assert totals["unpriced_requests"] == 1
    assert totals["priced_requests"] == 0
    assert totals["counterfactual_usd"] == 0.0
    assert totals["estimated_savings_usd"] == 0.0
    assert GHOST in report["label"]
    assert "no catalog rate for" in report["label"]


def test_a_local_request_priced_at_zero_still_produces_savings(tmp_path: Path) -> None:
    """The opposite case, through the same writer: a local request is zero for real."""
    db = open_database(tmp_path / "state.db")
    add_model(db, LOCAL, "local", 80.0, warm=True, size_vram_gb=8.0)
    _written(db, LOCAL, "local")
    rows = read_requests(db, since=0.0)
    assert [written.cost_usd for written in rows] == [0.0]
    report = build_report(rows, rate(), now=time.time(), window_days=30, rated=rated())
    totals = report["totals"]
    assert totals["priced_requests"] == 1
    assert totals["unpriced_requests"] == 0
    assert totals["free_or_local_requests"] == 1
    assert totals["actual_usd"] == 0.0
    assert totals["counterfactual_usd"] == 18.0
    assert totals["estimated_savings_usd"] == 18.0


def test_a_request_without_token_counts_is_unpriced_too() -> None:
    report = build_report([row(tokens_in=None)], rate(), now=NOW, window_days=30, rated=rated())
    assert report["totals"]["unpriced_requests"] == 1
    assert report["totals"]["counterfactual_usd"] == 0.0


def test_free_and_local_requests_are_counted_by_tier() -> None:
    rows = [row(tier="local"), row(decision_id=2, tier="free_cloud", cost=0.5)]
    totals = build_report(rows, rate(), now=NOW, window_days=30, rated=rated())["totals"]
    assert totals["free_or_local_requests"] == 2
    assert totals["by_tier"] == {"free_cloud": 1, "local": 1}
    assert totals["actual_usd"] == 0.5
    assert totals["estimated_savings_usd"] == 35.5


def test_the_window_and_this_week_are_reported_separately() -> None:
    rows = [row(ts=NOW - 2 * DAY), row(decision_id=2, ts=NOW - 20 * DAY, cost=1.0)]
    report = build_report(rows, rate(), now=NOW, window_days=30, rated=rated())
    assert report["totals"]["requests"] == 2
    assert report["week"]["requests"] == 1
    assert len(report["by_day"]) == 2


def test_days_are_utc_and_models_are_grouped() -> None:
    rows = [row(ts=NOW), row(decision_id=2, ts=NOW + 30.0, model="qwen:14b", cost=2.0)]
    report = build_report(rows, rate(), now=NOW, window_days=30, rated=rated())
    assert report["day_basis"] == "utc"
    assert [day["day"] for day in report["by_day"]] == sorted(
        day["day"] for day in report["by_day"]
    )
    models = report["by_model"]
    assert len(models) == 1
    assert models[0]["model"] == "qwen:14b"
    assert models[0]["requests"] == 2
    assert models[0]["actual_usd"] == 2.0
    assert models[0]["estimated_savings_usd"] == 34.0


def test_an_empty_window_says_so_instead_of_printing_savings() -> None:
    report = build_report([], rate(), now=NOW, window_days=30, rated=rated())
    assert report["totals"]["requests"] == 0
    assert "nothing to price" in report["label"]


def _database(tmp_path: Path) -> Database:
    db = open_database(tmp_path / "state.db")
    add_model(db, "qwen:14b", "local", 80.0, warm=True, size_vram_gb=8.0)
    decision = insert_decision(
        db, DecisionRow(surface="cli", scope="local", strategy="balanced", chosen="qwen:14b")
    )
    save_outcome(
        db,
        OutcomeRow(
            decision_id=decision,
            status="ok",
            tokens_in=1_000_000,
            tokens_out=1_000_000,
            cost_usd=0.0,
        ),
    )
    return db


def _services(db: Database, rules: Rules | None = None) -> dict[str, object]:
    return {"db": db, "catalog": catalog(), "rules": rules or Rules()}


def test_the_summary_reads_the_state_database(tmp_path: Path) -> None:
    report = render_summary(_services(_database(tmp_path)))
    assert report["available"] is True
    assert report["totals"]["requests"] == 1
    assert report["totals"]["estimated_savings_usd"] == 18.0
    assert report["by_model"][0]["model"] == "qwen:14b"


def test_the_window_and_reference_come_from_rules(tmp_path: Path) -> None:
    db = _database(tmp_path)
    rules = Rules(ledger=LedgerRules(reference_model=SONNET, window_days=1))
    report = render_summary(_services(db, rules))
    assert report["window_days"] == 1
    assert report["reference"]["model"] == SONNET


def test_a_missing_service_is_a_stated_reason_not_a_crash(tmp_path: Path) -> None:
    report = render_summary({"catalog": catalog(), "rules": Rules()})
    assert report["available"] is False
    assert "state database is not wired" in report["reason"]
    assert report["label"].startswith("Savings: ")
    report = render_summary({"db": open_database(tmp_path / "state.db"), "rules": Rules()})
    assert report["available"] is False
    assert "catalog is not wired" in report["reason"]


def test_discovery_registers_the_ledger_and_a_disabled_flag_removes_it(tmp_path: Path) -> None:
    registry = FeatureRegistry(services=_services(_database(tmp_path)))
    registered = discover_features(registry)
    assert "headwaters.features.ledger" in registered
    summary = registry.dashboard_summary()
    assert summary is not None
    assert summary["available"] is True

    registry.set_flags({"ledger": False})
    assert registry.dashboard_summary() is None


def test_a_machine_without_the_ledger_folder_discovers_nothing_to_show(
    monkeypatch: Any,
) -> None:
    """Discovery is by presence on disk, so deleting the folder is the removal mechanism."""
    real = pkgutil.iter_modules

    def without_ledger(path: Any) -> list[Any]:
        return [info for info in real(path) if info.name != "ledger"]

    monkeypatch.setattr(pkgutil, "iter_modules", without_ledger)
    registry = FeatureRegistry()
    discover_features(registry)
    assert registry.get("ledger") is None
    assert registry.dashboard_summary() is None


def test_status_reports_the_estimate_for_a_real_engine(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    decision = insert_decision(
        rig.db,
        DecisionRow(surface="mcp", scope="local", strategy="balanced", chosen="qwen:14b"),
    )
    save_outcome(
        rig.db,
        OutcomeRow(
            decision_id=decision, status="ok", tokens_in=1_000, tokens_out=2_000, cost_usd=0.0
        ),
    )
    data = structured(asyncio.run(rig.call("headwaters_status", {})))
    savings = data["savings"]
    assert savings is not None
    assert savings["available"] is True
    assert savings["reference"]["model"] == SONNET
    assert savings["totals"]["priced_requests"] == 1
    assert savings["totals"]["counterfactual_usd"] == 0.033


def test_status_answers_without_savings_when_the_ledger_is_off(tmp_path: Path) -> None:
    rig = Rig(tmp_path, rules=Rules(features={"ledger": False}))
    data = structured(asyncio.run(rig.call("headwaters_status", {})))
    assert data["savings"] is None
    assert data["scope"]["effective"]  # the rest of the answer is unaffected
    assert data["catalog"] == "seed"
