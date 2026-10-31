"""HDW-111: quota tracker and cooldowns, with a frozen clock for every transition."""

from __future__ import annotations

import random
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from freezegun import freeze_time
from freezegun.api import FrozenDateTimeFactory

from headwaters.core.catalog.http import SourceError
from headwaters.core.catalog.schema import FreeLimit
from headwaters.core.quotas import (
    KeyStatus,
    QuotaTracker,
    credit_class,
    credit_subject,
    key_subject,
    model_subject,
    parse_key_status,
    provider_subject,
)
from headwaters.core.rules import WallsRules
from headwaters.core.state import Database, open_database
from headwaters.core.state_limits import active_walls
from headwaters.core.walls import WallEvent

START = "2027-01-15 12:00:00"
MODEL = "m/free:1"
PROVIDER = "openrouter"


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return open_database(tmp_path / "state.db")


@pytest.fixture
def clock() -> Iterator[FrozenDateTimeFactory]:
    with freeze_time(START) as frozen:
        assert isinstance(frozen, FrozenDateTimeFactory)
        yield frozen


def tracker(db: Database, limits: list[FreeLimit] | None = None, **walls: Any) -> QuotaTracker:
    return QuotaTracker(db, WallsRules(**walls), limits or [], rng=random.Random(7))


def rate(retry_after: float | None = None, scope: str = "model") -> WallEvent:
    return WallEvent(kind="rate_limit", scope=scope, retry_after=retry_after)  # type: ignore[arg-type]


def exhausted(reset_at: float | None = None, scope: str = "key") -> WallEvent:
    return WallEvent(kind="exhausted", scope=scope, reset_at=reset_at)  # type: ignore[arg-type]


def overloaded() -> WallEvent:
    return WallEvent(kind="overloaded", scope="model")


def test_defaults_are_ok(db: Database, clock: FrozenDateTimeFactory) -> None:
    assert tracker(db).availability(MODEL, PROVIDER).status == "ok"


def test_non_wall_events_change_nothing(db: Database, clock: FrozenDateTimeFactory) -> None:
    t = tracker(db)
    for kind in ("context", "other"):
        event = WallEvent(kind=kind, scope="model")  # type: ignore[arg-type]
        assert t.record_wall(event, model=MODEL, provider=PROVIDER).action == "none"
    assert active_walls(db) == []


def test_rate_limit_cooldown_then_ok_after_reset(
    db: Database, clock: FrozenDateTimeFactory
) -> None:
    t = tracker(db)
    decision = t.record_wall(rate(5.0), model=MODEL, provider=PROVIDER)
    assert (decision.action, decision.state) == ("retry", "cooldown")
    assert decision.wait_s >= 5.0
    status = t.availability(MODEL, PROVIDER)
    assert status.status == "cooldown"
    assert status.eta is not None
    assert 4.9 <= status.eta <= decision.wait_s
    clock.tick(decision.wait_s + 1)
    assert t.availability(MODEL, PROVIDER).status == "ok"


def test_long_wait_cascades_immediately(db: Database, clock: FrozenDateTimeFactory) -> None:
    t = tracker(db, cascade_if_wait_over_s=8)
    decision = t.record_wall(rate(60.0), model=MODEL, provider=PROVIDER)
    assert decision.action == "cascade"
    assert t.availability(MODEL, PROVIDER).status == "cooldown"


def test_wait_exactly_at_threshold_still_retries(
    db: Database, clock: FrozenDateTimeFactory
) -> None:
    t = tracker(db, cascade_if_wait_over_s=8, backoff_base_s=0.001)
    assert t.record_wall(rate(8.0), model=MODEL, provider=PROVIDER).action == "retry"


def test_repeated_429s_back_off_exponentially_with_full_jitter(
    db: Database, clock: FrozenDateTimeFactory
) -> None:
    t = tracker(db, backoff_base_s=2, backoff_max_s=1000, cascade_if_wait_over_s=10_000)
    waits = []
    for _ in range(6):
        decision = t.record_wall(rate(None), model=MODEL, provider=PROVIDER)
        waits.append((decision.consecutive, decision.wait_s))
        clock.tick(decision.wait_s + 0.01)
    for attempt, wait in waits:
        assert 0.0 <= wait <= 2 * 2 ** (attempt - 1)
    assert [a for a, _ in waits] == [1, 2, 3, 4, 5, 6]
    assert len({round(w, 6) for _, w in waits}) > 1  # jittered, not constant


def test_backoff_is_capped(db: Database, clock: FrozenDateTimeFactory) -> None:
    t = tracker(db, backoff_base_s=2, backoff_max_s=5, cascade_if_wait_over_s=10_000)
    for _ in range(12):
        decision = t.record_wall(rate(None), model=MODEL, provider=PROVIDER)
        assert decision.wait_s <= 5
        clock.tick(decision.wait_s + 0.01)


def test_provider_hint_is_a_floor_for_the_wait(db: Database, clock: FrozenDateTimeFactory) -> None:
    t = tracker(db, backoff_base_s=0.001, cascade_if_wait_over_s=100)
    assert t.record_wall(rate(30.0), model=MODEL, provider=PROVIDER).wait_s >= 30.0


def test_success_clears_cooldown_and_resets_streak(
    db: Database, clock: FrozenDateTimeFactory
) -> None:
    t = tracker(db, cascade_if_wait_over_s=10_000)
    t.record_wall(rate(1.0), model=MODEL, provider=PROVIDER)
    t.record_wall(rate(1.0), model=MODEL, provider=PROVIDER)
    t.record_success(model=MODEL, provider=PROVIDER)
    assert t.availability(MODEL, PROVIDER).status == "ok"
    assert t.record_wall(rate(1.0), model=MODEL, provider=PROVIDER).consecutive == 1


def test_old_streak_is_forgotten(db: Database, clock: FrozenDateTimeFactory) -> None:
    t = tracker(db, cascade_if_wait_over_s=10_000)
    t.record_wall(rate(1.0), model=MODEL, provider=PROVIDER)
    t.record_wall(rate(1.0), model=MODEL, provider=PROVIDER)
    clock.tick(2 * 3600)
    assert t.record_wall(rate(1.0), model=MODEL, provider=PROVIDER).consecutive == 1


def test_scope_provider_cools_every_model_of_it(db: Database, clock: FrozenDateTimeFactory) -> None:
    t = tracker(db)
    t.record_wall(rate(5.0, scope="provider"), model=MODEL, provider=PROVIDER)
    assert t.availability("other/model", PROVIDER).status == "cooldown"
    assert t.availability("other/model", "openai").status == "ok"


def test_scope_model_leaves_other_models_alone(db: Database, clock: FrozenDateTimeFactory) -> None:
    t = tracker(db)
    t.record_wall(rate(5.0), model=MODEL, provider=PROVIDER)
    assert t.availability("other/model", PROVIDER).status == "ok"


def test_scope_key_uses_alias_and_falls_back_to_provider(
    db: Database, clock: FrozenDateTimeFactory
) -> None:
    t = tracker(db)
    t.record_wall(rate(5.0, scope="key"), model=MODEL, provider=PROVIDER, key="work")
    assert t.availability("x", PROVIDER, key="work").status == "cooldown"
    assert t.availability("x", PROVIDER, key="home").status == "ok"
    t.record_wall(rate(5.0, scope="key"), model=MODEL, provider="openai")
    assert t.availability("x", "openai").status == "cooldown"


def test_exhausted_without_reset_is_rechecked_after_the_configured_time(
    db: Database, clock: FrozenDateTimeFactory
) -> None:
    t = tracker(db, exhausted_recheck_s=900)
    decision = t.record_wall(exhausted(), model=MODEL, provider=PROVIDER, key="work")
    assert (decision.action, decision.state) == ("cascade", "exhausted")
    assert decision.until == pytest.approx(time.time() + 900)
    status = t.availability(MODEL, PROVIDER, key="work")
    assert status.status == "exhausted"
    assert status.eta == pytest.approx(900)
    clock.tick(600)
    t.record_success(model=MODEL, provider=PROVIDER, key="work")  # success does not lift it
    assert t.availability(MODEL, PROVIDER, key="work").status == "exhausted"
    clock.tick(301)
    assert t.availability(MODEL, PROVIDER, key="work").status == "ok"


def test_recheck_time_is_a_rule_with_a_default_of_fifteen_minutes() -> None:
    assert WallsRules().exhausted_recheck_s == 900
    with pytest.raises(ValueError, match="exhausted_recheck_s"):
        WallsRules(exhausted_recheck_s=0)


def test_exhausted_again_after_a_recheck_waits_the_full_time_again(
    db: Database, clock: FrozenDateTimeFactory
) -> None:
    t = tracker(db, exhausted_recheck_s=100)
    t.record_wall(exhausted(), model=MODEL, provider=PROVIDER, key="work")
    clock.tick(101)
    assert t.availability(MODEL, PROVIDER, key="work").status == "ok"
    again = t.record_wall(exhausted(), model=MODEL, provider=PROVIDER, key="work")
    assert again.until == pytest.approx(time.time() + 100)
    assert t.availability(MODEL, PROVIDER, key="work").status == "exhausted"


def test_a_past_reset_time_falls_back_to_the_recheck_time(
    db: Database, clock: FrozenDateTimeFactory
) -> None:
    t = tracker(db, exhausted_recheck_s=300)
    decision = t.record_wall(exhausted(time.time() - 5), model=MODEL, provider=PROVIDER, key="work")
    assert decision.until == pytest.approx(time.time() + 300)


def test_exhausted_lifts_at_reset_time(db: Database, clock: FrozenDateTimeFactory) -> None:
    t = tracker(db)
    reset = time.time() + 3600
    t.record_wall(exhausted(reset), model=MODEL, provider=PROVIDER, key="work")
    status = t.availability(MODEL, PROVIDER, key="work")
    assert status.status == "exhausted"
    assert status.eta is not None
    assert 3599 <= status.eta <= 3600
    clock.tick(3601)
    assert t.availability(MODEL, PROVIDER, key="work").status == "ok"


def test_exhausted_beats_cooldown(db: Database, clock: FrozenDateTimeFactory) -> None:
    t = tracker(db)
    t.record_wall(rate(5.0), model=MODEL, provider=PROVIDER)
    t.record_wall(exhausted(), model=MODEL, provider=PROVIDER, key="work")
    assert t.availability(MODEL, PROVIDER, key="work").status == "exhausted"


def test_overloaded_gets_short_retries_then_cascades(
    db: Database, clock: FrozenDateTimeFactory
) -> None:
    t = tracker(db, overloaded_max_retries=2, overloaded_backoff_s=1, overloaded_cooldown_s=30)
    first = t.record_wall(overloaded(), model=MODEL, provider=PROVIDER)
    second = t.record_wall(overloaded(), model=MODEL, provider=PROVIDER)
    third = t.record_wall(overloaded(), model=MODEL, provider=PROVIDER)
    assert (first.action, second.action, third.action) == ("retry", "retry", "cascade")
    assert first.wait_s <= 1
    assert second.wait_s <= 2
    assert third.wait_s == 30
    assert t.availability(MODEL, PROVIDER).status == "cooldown"
    clock.tick(31)
    assert t.availability(MODEL, PROVIDER).status == "ok"


def test_overloaded_zero_retries_cascades_at_once(
    db: Database, clock: FrozenDateTimeFactory
) -> None:
    t = tracker(db, overloaded_max_retries=0)
    assert t.record_wall(overloaded(), model=MODEL, provider=PROVIDER).action == "cascade"


LIMIT = FreeLimit(provider=PROVIDER, model=None, rpm=10, rpd=50)


def test_counters_soft_exhaust_at_percentage(db: Database, clock: FrozenDateTimeFactory) -> None:
    t = tracker(db, [LIMIT], soft_exhaust_pct=90)
    for _ in range(8):
        t.count_request(MODEL, PROVIDER)
    assert t.availability(MODEL, PROVIDER).status == "ok"
    t.count_request(MODEL, PROVIDER)  # 9 of 10 rpm = 90%
    assert t.availability(MODEL, PROVIDER).status == "soft"
    assert t.availability(MODEL, PROVIDER).usable
    assert t.availability("other/free:2", PROVIDER).status == "ok"  # counters are per model


def test_rpm_window_rolls_over_each_minute(db: Database, clock: FrozenDateTimeFactory) -> None:
    t = tracker(db, [LIMIT])
    for _ in range(9):
        t.count_request(MODEL, PROVIDER)
    assert t.availability(MODEL, PROVIDER).status == "soft"
    clock.tick(60)
    assert t.availability(MODEL, PROVIDER).status == "ok"


def test_rpd_window_rolls_over_at_utc_midnight(db: Database, clock: FrozenDateTimeFactory) -> None:
    t = tracker(db, [FreeLimit(provider=PROVIDER, rpd=10)], soft_exhaust_pct=50)
    for _ in range(5):
        t.count_request(MODEL, PROVIDER)
        clock.tick(61)  # spread over minutes so rpm never matters
    assert t.availability(MODEL, PROVIDER).status == "soft"
    clock.move_to("2027-01-16 00:00:01")
    assert t.availability(MODEL, PROVIDER).status == "ok"


def test_model_specific_limit_beats_provider_wide(
    db: Database, clock: FrozenDateTimeFactory
) -> None:
    specific = FreeLimit(provider=PROVIDER, model=MODEL, rpm=2)
    t = tracker(db, [LIMIT, specific], soft_exhaust_pct=100)
    t.count_request(MODEL, PROVIDER)
    t.count_request(MODEL, PROVIDER)
    assert t.availability(MODEL, PROVIDER).status == "soft"


def test_no_limit_means_no_counting(db: Database, clock: FrozenDateTimeFactory) -> None:
    t = tracker(db)
    for _ in range(100):
        t.count_request(MODEL, PROVIDER)
    assert t.availability(MODEL, PROVIDER).status == "ok"


def test_state_persists_across_tracker_instances(
    db: Database, clock: FrozenDateTimeFactory
) -> None:
    tracker(db).record_wall(rate(5.0), model=MODEL, provider=PROVIDER)
    assert tracker(db).availability(MODEL, PROVIDER).status == "cooldown"


def test_subject_names_are_namespaced() -> None:
    assert model_subject("a") == "model:a"
    assert provider_subject("OpenRouter") == "provider:openrouter"
    assert key_subject("work") == "key:work"


# --- credit classes (HDW-122, HDW-119 F3 / probe P9) -------------------------------------------

FREE_MODEL = "meta/llama:free"
PAID_MODEL = "anthropic/claude-paid"


def test_credit_class_and_subject_names() -> None:
    assert [credit_class(t) for t in ("paid", "openrouter_auto", "free_cloud", "local")] == [
        "paid",
        "paid",
        "free",
        "free",
    ]
    assert credit_subject(provider_subject("OpenRouter"), "paid") == "provider:openrouter:paid"
    assert credit_subject(key_subject("work"), "free_cloud") == "key:work:free"


def test_p9_paid_402_leaves_free_models_of_the_same_provider_ok(
    db: Database, clock: FrozenDateTimeFactory
) -> None:
    """P9: one OpenRouter 402 on a paid model used to block `:free` models for good."""
    t = tracker(db, exhausted_recheck_s=900)
    event = exhausted(scope="provider")
    t.record_wall(event, model=PAID_MODEL, provider=PROVIDER, tier="paid")
    assert [(w.subject, w.kind) for w in active_walls(db)] == [
        ("provider:openrouter:paid", "exhausted")
    ]
    paid = t.availability(PAID_MODEL, PROVIDER, tier="paid")
    assert (paid.status, paid.subject) == ("exhausted", "provider:openrouter:paid")
    assert paid.eta == pytest.approx(900)
    assert t.availability(FREE_MODEL, PROVIDER, tier="free_cloud").status == "ok"
    clock.tick(901)  # the wall lifts after the recheck time
    assert t.availability(PAID_MODEL, PROVIDER, tier="paid").status == "ok"


def test_credit_wall_on_the_key_stops_that_keys_paid_models_only(
    db: Database, clock: FrozenDateTimeFactory
) -> None:
    t = tracker(db)
    t.record_wall(exhausted(), model=PAID_MODEL, provider=PROVIDER, key="work", tier="paid")
    assert t.availability(PAID_MODEL, PROVIDER, "work", "paid").subject == "key:work:paid"
    assert t.availability(FREE_MODEL, PROVIDER, "work", "free_cloud").status == "ok"
    assert t.availability(PAID_MODEL, PROVIDER, "home", "paid").status == "ok"


def test_free_tier_exhaustion_records_a_free_subject(
    db: Database, clock: FrozenDateTimeFactory
) -> None:
    t = tracker(db)
    t.record_wall(exhausted(), model=FREE_MODEL, provider=PROVIDER, key="work", tier="free_cloud")
    assert [w.subject for w in active_walls(db)] == ["key:work:free"]
    assert t.availability(FREE_MODEL, PROVIDER, "work", "free_cloud").status == "exhausted"
    assert t.availability("other/free:2", PROVIDER, "work", "free_cloud").status == "exhausted"
    assert t.availability(PAID_MODEL, PROVIDER, "work", "paid").status == "ok"


def test_model_scope_exhaustion_stays_on_the_model(
    db: Database, clock: FrozenDateTimeFactory
) -> None:
    t = tracker(db)
    t.record_wall(exhausted(scope="model"), model=FREE_MODEL, provider=PROVIDER, tier="free_cloud")
    assert [w.subject for w in active_walls(db)] == [f"model:{FREE_MODEL}"]
    assert t.availability("other/free:2", PROVIDER, tier="free_cloud").status == "ok"


def test_tierless_calls_keep_the_cautious_reading(
    db: Database, clock: FrozenDateTimeFactory
) -> None:
    t = tracker(db)
    t.record_wall(exhausted(scope="provider"), model=PAID_MODEL, provider=PROVIDER)
    assert [w.subject for w in active_walls(db)] == ["provider:openrouter"]
    assert t.availability(FREE_MODEL, PROVIDER).status == "exhausted"
    assert t.availability(FREE_MODEL, PROVIDER, tier="free_cloud").status == "exhausted"


def test_unspecified_tier_sees_every_credit_class(
    db: Database, clock: FrozenDateTimeFactory
) -> None:
    t = tracker(db)
    t.record_wall(exhausted(scope="provider"), model=PAID_MODEL, provider="openai", tier="paid")
    assert t.availability(PAID_MODEL, "openai").status == "exhausted"
    assert t.availability(PAID_MODEL, "openai", tier="free_cloud").status == "ok"


def test_balance_check_lifts_the_paid_wall_not_the_free_one(
    db: Database, clock: FrozenDateTimeFactory
) -> None:
    t = tracker(db)
    t.record_wall(exhausted(), model=PAID_MODEL, provider=PROVIDER, key="work", tier="paid")
    t.record_wall(exhausted(), model=FREE_MODEL, provider=PROVIDER, key="work", tier="free_cloud")
    assert t.mark_balance_ok(provider=PROVIDER, key="work") == 1
    assert [w.subject for w in active_walls(db)] == ["key:work:free"]


def test_a_failed_balance_poll_marks_only_paid_models_and_it_expires(
    db: Database, clock: FrozenDateTimeFactory
) -> None:
    t = tracker(db, exhausted_recheck_s=900)
    t.poll_key(FakeHttp(key_payload(0.0)), "k", alias="work")
    assert [w.subject for w in active_walls(db)] == ["key:work:paid"]
    assert t.availability(FREE_MODEL, PROVIDER, "work", "free_cloud").status == "ok"
    clock.tick(901)
    assert active_walls(db) == []


# --- key polling ------------------------------------------------------------------------------


class FakeHttp:
    def __init__(self, payload: Any = None, error: bool = False) -> None:
        self.payload = payload
        self.error = error
        self.calls: list[tuple[str, str, dict[str, str] | None]] = []

    def request_json(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        self.calls.append((method, url, headers))
        if self.error:
            raise SourceError("down")
        return self.payload


def key_payload(remaining: float | None, budget_left: float | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {"label": "k", "limit_remaining": remaining, "usage": 3.5}
    if budget_left is not None:
        data["effective_budget"] = {"remaining_usd": budget_left}
    return {"data": data}


def test_parse_key_status() -> None:
    assert parse_key_status(key_payload(2.0)) == KeyStatus(False, 2.0, 3.5)
    assert parse_key_status(key_payload(0.0)).exhausted
    assert parse_key_status(key_payload(None)).exhausted is False  # unlimited key
    assert parse_key_status(key_payload(5.0, budget_left=0.0)).exhausted
    assert parse_key_status(key_payload(5.0, budget_left=1.0)).exhausted is False
    with pytest.raises(SourceError):
        parse_key_status({"nope": 1})


def test_poll_marks_exhausted_then_lifts_when_balance_returns(
    db: Database, clock: FrozenDateTimeFactory
) -> None:
    t = tracker(db, key_poll_minutes=10)
    http = FakeHttp(key_payload(0.0))
    status = t.poll_key(http, "sk-secret", alias="work")
    assert status is not None
    assert status.exhausted
    assert t.availability(MODEL, PROVIDER, key="work").status == "exhausted"
    assert http.calls[0][2] == {"Authorization": "Bearer sk-secret"}
    http.payload = key_payload(12.0)
    assert t.poll_key(http, "sk-secret", alias="work") is None  # not due yet
    clock.tick(10 * 60)
    status = t.poll_key(http, "sk-secret", alias="work")
    assert status is not None
    assert not status.exhausted
    assert t.availability(MODEL, PROVIDER, key="work").status == "ok"


def test_poll_every_n_minutes_and_force_after_paid_call(
    db: Database, clock: FrozenDateTimeFactory
) -> None:
    t = tracker(db, key_poll_minutes=5)
    http = FakeHttp(key_payload(9.0))
    assert t.poll_key(http, "k", alias="a") is not None
    clock.tick(299)
    assert t.poll_key(http, "k", alias="a") is None
    assert t.poll_key(http, "k", alias="a", force=True) is not None
    clock.tick(300)
    assert t.poll_key(http, "k", alias="a") is not None
    assert len(http.calls) == 3


@pytest.mark.parametrize("flag", ["airplane", "local_lock"])
def test_poll_skipped_in_airplane_mode_or_local_lock(
    db: Database, clock: FrozenDateTimeFactory, flag: str
) -> None:
    http = FakeHttp(key_payload(0.0))
    result = tracker(db).poll_key(
        http,
        "k",
        alias="a",
        force=True,
        airplane=flag == "airplane",
        local_lock=flag == "local_lock",
    )
    assert result is None
    assert http.calls == []
    assert active_walls(db) == []


def test_failed_poll_changes_nothing_and_retries_next_time(
    db: Database, clock: FrozenDateTimeFactory
) -> None:
    t = tracker(db)
    t.record_wall(exhausted(), model=MODEL, provider=PROVIDER, key="work")
    assert t.poll_key(FakeHttp(error=True), "k", alias="work") is None
    assert t.availability(MODEL, PROVIDER, key="work").status == "exhausted"
    assert t.poll_key(FakeHttp(key_payload(5.0)), "k", alias="work") is not None  # still due
    assert t.availability(MODEL, PROVIDER, key="work").status == "ok"
