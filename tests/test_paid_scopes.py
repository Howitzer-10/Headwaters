"""HDW-502: the daily paid-tier budget and handback advice.

Golden on purpose: every scenario here is a fixed input with a fixed expected result, so a change
to the cap arithmetic, the advice trigger or the parity of the feature-off path fails loudly.

Two states are pinned for parity, the way HDW-501 pinned the ledger: the flag off in the real
discovered registry, and the package absent from discovery. Both must leave ``auto``/``cloud``
exactly as they were — no paid model, no budget reason, no advisory — with status still answering.
"""

from __future__ import annotations

import pkgutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from headwaters.control.status_page import build_page
from headwaters.core.features import FeatureRegistry, discover_features
from headwaters.core.plugins import BudgetCheck
from headwaters.core.policy.errors import NoRouteError
from headwaters.core.rules import Rules
from headwaters.core.scope import PAID_SCOPES_FEATURE_ID, paid_scopes_available
from headwaters.core.state import Database, open_database
from headwaters.core.state_decisions import DecisionRow, OutcomeRow, insert_decision, save_outcome
from headwaters.core.state_misc import get_handoff, get_session, latest_handoff_id, pin_session
from headwaters.features.paid_scopes.advice import (
    HANDOFF_KIND,
    Advisory,
    advisory_text,
    host_label,
    is_handback,
    subscribed_hosts,
)
from headwaters.features.paid_scopes.budget import Budget
from headwaters.features.paid_scopes.spend import daily_spend, day_start, priced_ids
from tests.router_rig import Rig
from tests.router_support import OK, candidate

FREE = "openrouter/free-model:free"
LOCAL = "qwen-local"
PAID = "anthropic/paid-model"

# A fixed clock, so "today" is a fixed window. 20731 * 86400 = 1791158400 is the UTC midnight.
NOW = 1_791_194_400.0
MIDNIGHT = 1_791_158_400.0
TODAY = MIDNIGHT + 60.0
YESTERDAY = MIDNIGHT - 60.0


# ------------------------------------------------------------------ fixtures


@dataclass(frozen=True)
class _Priced:
    """The part of ``ModelEntry`` that ``priced_ids`` reads."""

    id: str
    price_in: float | None
    price_out: float | None


@dataclass(frozen=True)
class _CatalogFixture:
    models: tuple[_Priced, ...]


def priced_catalog(*ids: str) -> _CatalogFixture:
    return _CatalogFixture(tuple(_Priced(mid, 3.0, 15.0) for mid in ids))


def unrated_catalog() -> _CatalogFixture:
    return _CatalogFixture((_Priced(PAID, None, None),))


def _spend_row(db: Database, model: str, cost: float, *, ts: float | None = None) -> int:
    """One routed request: the decision the router logs plus the outcome that carries the cost."""
    decision_id = insert_decision(
        db, DecisionRow("gateway", "auto", "quality_first", chosen=model, ts=ts)
    )
    save_outcome(db, OutcomeRow(decision_id, "ok", cost_usd=cost))
    return decision_id


def _db(tmp_path: Path) -> Database:
    return open_database(tmp_path / "state.db")


def _rules(**overrides: Any) -> Rules:
    return Rules.model_validate(overrides)


def _registry(rules: Rules, db: Database, catalog: object) -> FeatureRegistry:
    """The real discovered registry, with the services the engine provides at start-up."""
    registry = FeatureRegistry()
    discover_features(registry)
    registry.set_flags(rules.features)
    registry.provide_services({"rules": rules, "db": db, "catalog": catalog})
    return registry


# ------------------------------------------------------------------ P2: spend


def test_day_start_is_utc_midnight() -> None:
    assert day_start(NOW) == MIDNIGHT
    assert day_start(MIDNIGHT) == MIDNIGHT  # idempotent on the boundary itself
    assert MIDNIGHT % 86_400.0 == 0.0


def test_priced_ids_needs_both_rates() -> None:
    assert priced_ids(priced_catalog(FREE, PAID)) == frozenset({FREE, PAID})
    assert priced_ids(unrated_catalog()) == frozenset()
    assert priced_ids(None) == frozenset()  # a router with no catalog prices nothing


def test_daily_spend_counts_today_only(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _spend_row(db, PAID, 1.25, ts=TODAY)
    _spend_row(db, PAID, 0.75, ts=TODAY + 30)
    _spend_row(db, PAID, 9.00, ts=YESTERDAY)  # yesterday does not count against today
    spend = daily_spend(db, priced_catalog(PAID), now=NOW)
    assert spend.total_usd == pytest.approx(2.0)
    assert spend.requests == 2
    assert spend.unpriced == 0


def test_daily_spend_counts_an_unpriced_paid_request_instead_of_costing_it_at_zero(
    tmp_path: Path,
) -> None:
    db = _db(tmp_path)
    _spend_row(db, PAID, 0.0, ts=TODAY)
    spend = daily_spend(db, unrated_catalog(), now=NOW)
    assert spend.total_usd == 0.0  # never invented
    assert spend.unpriced == 1  # but never pretended free either (D7)


# ------------------------------------------------------------------ P2: the cap


def _budget(db: Database, catalog: object, *, cap: float = 5.0) -> Budget:
    rules = _rules(budget={"paid_daily_usd": cap})
    registry = FeatureRegistry(services={"rules": rules, "db": db, "catalog": catalog})
    return Budget(registry, clock=lambda: NOW)


def test_budget_allows_a_call_that_lands_exactly_on_the_cap(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _spend_row(db, PAID, 4.5, ts=TODAY)
    check = _budget(db, priced_catalog(PAID)).check(model=PAID, tier="paid", estimated_usd=0.5)
    assert check == BudgetCheck(True, 4.5, 5.0)


def test_budget_refuses_a_call_over_the_cap_and_says_why(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _spend_row(db, PAID, 4.5, ts=TODAY)
    check = _budget(db, priced_catalog(PAID)).check(model=PAID, tier="paid", estimated_usd=0.6)
    assert check.allowed is False
    assert check.spent_usd == 4.5
    assert check.cap_usd == 5.0
    assert check.reason == (
        f"paid-tier budget: $4.50 of $5.00 spent today (UTC); {PAID} is estimated at $0.60"
    )


def test_budget_of_zero_refuses_any_paid_call_that_costs_anything(tmp_path: Path) -> None:
    db = _db(tmp_path)
    check = _budget(db, priced_catalog(PAID), cap=0.0).check(
        model=PAID, tier="paid", estimated_usd=0.01
    )
    assert check.allowed is False
    assert check.cap_usd == 0.0


def test_budget_without_live_services_does_not_refuse() -> None:
    # A registry with no rules or no state database has nothing to measure against: no cap.
    assert Budget(FeatureRegistry()).check(
        model=PAID, tier="paid", estimated_usd=10.0
    ) == BudgetCheck(True)


# ------------------------------------------------------------------ P3: advice


@pytest.mark.parametrize(
    ("below_floor", "tiers", "hosts", "mode", "expected"),
    [
        (True, ("local",), ("copilot",), "inline", True),
        (True, ("local", "free_cloud"), ("copilot",), "header_only", True),
        (True, ("local", "paid"), ("copilot",), "inline", False),  # paid is already in scope
        (False, ("local",), ("copilot",), "inline", False),  # the answer met the floor
        (True, ("local",), (), "inline", False),  # no subscription host to hand back to
        (True, ("local",), ("copilot",), "off", False),  # the user switched advice off
    ],
)
def test_is_handback_matrix(
    below_floor: bool, tiers: tuple[str, ...], hosts: tuple[str, ...], mode: str, expected: bool
) -> None:
    assert is_handback(below_floor=below_floor, tiers=tiers, hosts=hosts, mode=mode) is expected


def test_advisory_text_is_fixed() -> None:
    assert advisory_text("GitHub Copilot", 7) == (
        "Handback: no paid tier is in scope and the best available model is below the quality "
        "floor for this task. GitHub Copilot runs on your subscription — continue there. "
        "Handoff packet 7 is saved for the next session."
    )
    assert advisory_text("GitHub Copilot", None) == (
        "Handback: no paid tier is in scope and the best available model is below the quality "
        "floor for this task. GitHub Copilot runs on your subscription — continue there."
    )
    assert host_label("copilot") == "GitHub Copilot"
    assert host_label("some_host") == "Some Host"  # an unmapped host is still readable


def test_subscribed_hosts_are_sorted_and_truthy_only() -> None:
    rules = _rules(hosts={"subscription": {"vscode": True, "copilot": True, "cursor": False}})
    assert subscribed_hosts(rules) == ("copilot", "vscode")


def _advising(db: Database, *, advice: str = "inline") -> Advisory:
    rules = _rules(advice=advice, hosts={"subscription": {"copilot": True, "cursor": False}})
    return Advisory(FeatureRegistry(services={"rules": rules, "db": db}))


def _advise(plugin: Advisory, *, session: str = "s1", below_floor: bool = True) -> str | None:
    return plugin.advise(
        model=LOCAL,
        tier="local",
        below_floor=below_floor,
        floor=70.0,
        score=20.0,
        scope_tiers=("local",),
        session=session,
    )


def test_advice_stores_one_packet_per_session(tmp_path: Path) -> None:
    db = _db(tmp_path)
    first = _advise(_advising(db))
    second = _advise(_advising(db))
    packet_id = latest_handoff_id(db, "s1")
    assert packet_id is not None
    assert first == second == advisory_text("GitHub Copilot", packet_id)
    with db.read() as c:
        assert c.execute("SELECT count(*) AS n FROM handoffs").fetchone()["n"] == 1
    packet = get_handoff(db, packet_id)
    assert packet is not None
    assert packet["kind"] == HANDOFF_KIND
    assert packet["reason"] == "below_floor_no_paid"
    assert packet["host"] == "copilot"
    assert packet["model"] == LOCAL
    assert packet["score"] == 20.0
    assert packet["scope_tiers"] == ["local"]


def test_advice_links_the_packet_to_the_session_row(tmp_path: Path) -> None:
    db = _db(tmp_path)
    pin_session(db, "s1", LOCAL, "local")
    assert _advise(_advising(db)) is not None
    session = get_session(db, "s1")
    assert session is not None
    assert session.handoff_packet_id == latest_handoff_id(db, "s1")


def test_advice_is_silent_when_off(tmp_path: Path) -> None:
    db = _db(tmp_path)
    assert _advise(_advising(db, advice="off")) is None
    assert latest_handoff_id(db, "s1") is None
    assert _advise(_advising(db), below_floor=False) is None  # met the floor
    assert latest_handoff_id(db, "s1") is None


# ------------------------------------------------------------------ end to end


def _paid_rules(*, cap: float = 5.0, advice: str = "off") -> Rules:
    return _rules(
        strategy="primary_first",
        advice=advice,
        hosts={"subscription": {"copilot": True}},
        budget={"paid_daily_usd": cap},
    )


def _rig(
    tmp_path: Path,
    *,
    rules: Rules,
    scripts: dict[str, Any],
    candidates: list[Any] | None = None,
    spend: float | None = None,
) -> Rig:
    registry = FeatureRegistry()
    discover_features(registry)
    registry.set_flags(rules.features)
    rig = Rig(
        tmp_path,
        scripts,
        candidates
        if candidates is not None
        else [
            candidate(FREE, "free_cloud", 90.0),
            candidate(LOCAL, "local", 86.0),
            candidate(PAID, "paid", 96.0, price_in=3.0, price_out=15.0, is_primary=True),
        ],
        rules=rules,
        registry=registry,
    )
    # The engine provides these three; the rig has to do it by hand (HDW-501's shape).
    registry.provide_services(
        {"rules": rules, "db": rig.db, "catalog": priced_catalog(FREE, LOCAL, PAID)}
    )
    if spend is not None:
        _spend_row(rig.db, PAID, spend)  # "today" is the real clock the plugin reads
    return rig


def test_paid_under_the_cap_is_chosen(tmp_path: Path) -> None:
    rules = _paid_rules()
    rig = _rig(tmp_path, rules=rules, scripts={PAID: OK}, spend=0.0)
    result = rig.run()
    assert result.decision.model == PAID
    assert result.decision.advisory is None  # advice is off, and nothing to hand back either
    assert not any("paid-tier budget" in reason for reason in result.decision.reasons)


def test_paid_over_the_cap_falls_back_to_free(tmp_path: Path) -> None:
    rules = _paid_rules(cap=5.0)
    rig = _rig(tmp_path, rules=rules, scripts={FREE: OK}, spend=4.9999)
    result = rig.run()
    assert result.decision.model == FREE
    assert "paid" not in result.decision.scope.tiers
    assert any("paid-tier budget:" in reason for reason in result.decision.scope.reasons)
    assert rig.dispatch.calls == [FREE]  # the paid model was never called


def test_over_the_cap_with_no_free_or_local_model_is_budget_exhausted(tmp_path: Path) -> None:
    rules = _paid_rules(cap=5.0)
    rig = _rig(
        tmp_path,
        rules=rules,
        scripts={PAID: OK},
        candidates=[candidate(PAID, "paid", 96.0, price_in=3.0, price_out=15.0, is_primary=True)],
        spend=4.9999,
    )
    with pytest.raises(NoRouteError) as caught:
        rig.run()
    assert caught.value.code == "budget_exhausted"
    assert "paid-tier budget for today is spent" in str(caught.value)
    assert rig.dispatch.calls == []


def test_a_below_floor_local_request_carries_a_handback_line(tmp_path: Path) -> None:
    rules = _paid_rules(advice="inline")
    rig = _rig(
        tmp_path,
        rules=rules,
        scripts={LOCAL: OK},
        candidates=[candidate(LOCAL, "local", 20.0)],  # far below the code floor
    )
    result = rig.run(scope="local", session_id="s1")
    assert result.decision.model == LOCAL
    assert result.decision.below_floor is True
    # The router names the session itself (a derived key), so read the packet it stored.
    with rig.db.read() as c:
        row = c.execute("SELECT id, session FROM handoffs").fetchall()
    assert len(row) == 1
    assert row[0]["session"]  # the decision named a session to hand back to
    assert result.decision.advisory == advisory_text("GitHub Copilot", row[0]["id"])
    packet = get_handoff(rig.db, row[0]["id"])
    assert packet is not None
    assert packet["kind"] == HANDOFF_KIND


# ------------------------------------------------------------------ parity


def _parity_rig(tmp_path: Path, registry: FeatureRegistry) -> Rig:
    rules = _paid_rules()  # paid configured, primary first: the paid model is what would be picked
    rig = Rig(
        tmp_path,
        {FREE: OK, PAID: OK},
        [
            candidate(FREE, "free_cloud", 90.0),
            candidate(PAID, "paid", 96.0, price_in=3.0, price_out=15.0, is_primary=True),
        ],
        rules=rules,
        registry=registry,
    )
    registry.provide_services({"rules": rules, "db": rig.db, "catalog": priced_catalog(FREE, PAID)})
    return rig


def _assert_feature_off_behaviour(rig: Rig) -> None:
    for scope in ("auto", "cloud"):
        result = rig.run(scope=scope)
        assert result.first_choice is None, scope  # the free model answered, nothing cascaded
        assert result.decision.model == FREE, scope
        assert result.decision.advisory is None, scope
        assert "paid" not in result.decision.scope.tiers, scope
        assert any("paid tiers excluded" in r for r in result.decision.scope.reasons), scope
    assert rig.dispatch.calls == [FREE, FREE]  # the paid key was never used


def test_feature_off_keeps_auto_and_cloud_unchanged(tmp_path: Path) -> None:
    rules = _paid_rules()
    db = _db(tmp_path / "status")
    registry = _registry(rules, db, priced_catalog(FREE, PAID))
    registry.set_flags({PAID_SCOPES_FEATURE_ID: False})
    assert paid_scopes_available(registry) is False
    _assert_feature_off_behaviour(_parity_rig(tmp_path, registry))


def test_package_absent_from_discovery_keeps_auto_and_cloud_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = pkgutil.iter_modules

    def without_paid_scopes(*args: Any, **kw: Any) -> list[Any]:
        return [m for m in real(*args, **kw) if m.name != "paid_scopes"]

    monkeypatch.setattr(pkgutil, "iter_modules", without_paid_scopes)
    rules = _paid_rules()
    db = _db(tmp_path / "status")
    registry = _registry(rules, db, priced_catalog(FREE, PAID))
    assert paid_scopes_available(registry) is False
    assert registry.enabled(PAID_SCOPES_FEATURE_ID) is False
    _assert_feature_off_behaviour(_parity_rig(tmp_path, registry))


def test_feature_on_uses_the_paid_model(tmp_path: Path) -> None:
    # The mirror image of the two parity tests: if this stops picking paid, they prove nothing.
    rules = _paid_rules()
    rig = _parity_rig(tmp_path, _registry(rules, _db(tmp_path / "status"), priced_catalog(PAID)))
    result = rig.run()
    assert result.decision.model == PAID
    assert rig.dispatch.calls == [PAID]


@pytest.mark.parametrize("enabled", [True, False])
def test_status_page_answers_in_both_states(tmp_path: Path, enabled: bool) -> None:
    rules = _paid_rules()
    db = _db(tmp_path)
    registry = _registry(rules, db, priced_catalog(FREE, PAID))
    registry.set_flags({PAID_SCOPES_FEATURE_ID: enabled})
    page = build_page(rules=rules, project=None, db=db, registry=registry)
    assert page.scope_error is None
    assert paid_scopes_available(registry) is enabled
