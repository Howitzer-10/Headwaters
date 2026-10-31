"""HDW-122: credit walls hit paid models only, expire, carry the key alias; 429s are counted."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from headwaters.core.catalog.schema import FreeLimit
from headwaters.core.executor.errors import ProviderError
from headwaters.core.policy.filters import Candidate
from headwaters.core.rules import Rules
from headwaters.core.state_limits import active_walls
from tests.executor_support import ScriptedBackend, Step, make_executor
from tests.router_rig import Rig
from tests.router_support import NO_CREDIT, OK, RATE_LIMITED, candidate, registry_with

PROVIDER = "openrouter"
PAID = "openrouter/paid-model"
FREE = "openrouter/free-model:free"
# A real OpenRouter 402 body: the seed pattern gives it scope ``provider`` (the P9 shape).
OPENROUTER_402 = ProviderError(402, {}, '{"error":{"code":402,"message":"Insufficient credits"}}')
FREE_PER_DAY = ProviderError(429, {}, "Rate limit exceeded: free-models-per-day. Add 10 credits")
RECHECK_S = 600
OTHER = [{"role": "user", "content": "another question entirely"}]
THIRD = [{"role": "user", "content": "a third, different question"}]


class Clock:
    def __init__(self) -> None:
        self.now = 1_800_000_000.0

    def __call__(self) -> float:
        return self.now


def pool() -> list[Candidate]:
    return [
        candidate(
            PAID, "paid", 96.0, provider=PROVIDER, price_in=3.0, price_out=15.0, is_primary=True
        ),
        candidate(FREE, "free_cloud", 90.0, provider=PROVIDER),
    ]


def rig(
    tmp_path: Path,
    scripts: dict[str, Sequence[Step]],
    *,
    strategy: str = "primary_first",
    clock: Clock | None = None,
    key_alias_for: Callable[[str], str | None] | None = None,
    free_limits: Sequence[FreeLimit] = (),
    walls: dict[str, float] | None = None,
) -> Rig:
    rules = Rules.model_validate(
        {"strategy": strategy, "walls": {"exhausted_recheck_s": RECHECK_S, **(walls or {})}}
    )
    return Rig(
        tmp_path,
        scripts,
        pool(),
        registry=registry_with(paid=True),
        rules=rules,
        key_alias_for=key_alias_for,
        free_limits=free_limits,
        clock=clock,
    )


def subjects(r: Rig, clock: Clock) -> list[str]:
    return [w.subject for w in active_walls(r.db, clock.now)]


# --- F3 / P9 ------------------------------------------------------------------------------------


def test_p9_paid_402_leaves_the_free_openrouter_model_ok_and_the_wall_lifts(
    tmp_path: Path,
) -> None:
    """P9 regression: one paid 402 used to write ``provider:openrouter`` with ``until=None``."""
    clock = Clock()
    r = rig(tmp_path, {PAID: [OPENROUTER_402], FREE: OK}, clock=clock)
    result = r.run()
    assert (result.first_choice, result.decision.model) == (PAID, FREE)
    walls = active_walls(r.db, clock.now)
    assert [(w.subject, w.kind) for w in walls] == [("provider:openrouter:paid", "exhausted")]
    assert walls[0].until == clock.now + RECHECK_S
    assert r.quotas.availability(FREE, PROVIDER, tier="free_cloud").status == "ok"
    paid = r.quotas.availability(PAID, PROVIDER, tier="paid")
    assert (paid.status, paid.eta) == ("exhausted", RECHECK_S)
    # The next request goes straight to the free model; the paid model is not retried.
    assert r.run(messages=OTHER).decision.model == FREE
    assert r.dispatch.calls == [PAID, FREE, FREE]
    clock.now += RECHECK_S + 1  # the wall lifts after the recheck time
    assert r.quotas.availability(PAID, PROVIDER, tier="paid").status == "ok"
    assert subjects(r, clock) == []


def test_after_the_wall_lifts_the_paid_model_is_tried_again(tmp_path: Path) -> None:
    clock = Clock()
    r = rig(tmp_path, {PAID: [OPENROUTER_402], FREE: OK}, clock=clock)
    r.run()
    r.dispatch.backends[PAID].script = list(OK)  # the user topped up their credit
    clock.now += RECHECK_S + 1
    assert r.run(messages=THIRD).decision.model == PAID
    assert r.dispatch.calls == [PAID, FREE, PAID]


def test_generic_402_with_a_key_alias_walls_that_keys_paid_models(tmp_path: Path) -> None:
    clock = Clock()
    r = rig(tmp_path, {PAID: [NO_CREDIT], FREE: OK}, clock=clock, key_alias_for=lambda p: "work")
    r.run()
    assert subjects(r, clock) == ["key:work:paid"]
    assert r.quotas.availability(FREE, PROVIDER, "work", "free_cloud").status == "ok"
    assert r.run(messages=OTHER).decision.model == FREE


def test_generic_402_without_a_key_alias_uses_the_provider_paid_subject(tmp_path: Path) -> None:
    clock = Clock()
    r = rig(tmp_path, {PAID: [NO_CREDIT], FREE: OK}, clock=clock)
    r.run()
    assert subjects(r, clock) == ["provider:openrouter:paid"]
    assert r.quotas.availability(FREE, PROVIDER, tier="free_cloud").status == "ok"


def test_free_tier_exhaustion_walls_free_models_only(tmp_path: Path) -> None:
    clock = Clock()
    r = rig(
        tmp_path,
        {PAID: OK, FREE: [FREE_PER_DAY]},
        strategy="cost_saver",
        clock=clock,
        key_alias_for=lambda p: "work",
    )
    result = r.run()  # the free model is tried first and is out of its daily allowance
    assert (result.first_choice, result.decision.model) == (FREE, PAID)
    assert subjects(r, clock) == ["key:work:free"]
    assert r.quotas.availability(FREE, PROVIDER, "work", "free_cloud").status == "exhausted"
    assert r.quotas.availability(PAID, PROVIDER, "work", "paid").status == "ok"


# --- key alias reaches the tracker ---------------------------------------------------------------


def test_the_router_passes_the_key_alias_to_success_bookkeeping(tmp_path: Path) -> None:
    r = rig(tmp_path, {PAID: OK, FREE: OK}, clock=Clock(), key_alias_for=lambda p: "work")
    seen: list[tuple[str, str | None]] = []
    original = r.quotas.record_success

    def spy(*, model: str, provider: str, key: str | None = None) -> None:
        seen.append((model, key))
        original(model=model, provider=provider, key=key)

    r.quotas.record_success = spy  # type: ignore[method-assign]
    r.run()
    assert seen == [(PAID, "work")]


def test_only_the_providers_that_have_a_key_get_an_alias(tmp_path: Path) -> None:
    clock = Clock()
    r = rig(
        tmp_path,
        {PAID: [NO_CREDIT], FREE: OK},
        clock=clock,
        key_alias_for=lambda p: "work" if p == "anthropic" else None,
    )
    r.run()
    assert subjects(r, clock) == ["provider:openrouter:paid"]


# --- F22: count_request also runs on a 429 -------------------------------------------------------

LIMIT = FreeLimit(provider=PROVIDER, model=None, rpm=10, rpd=50)


def counts(r: Rig, model: str) -> list[int]:
    with r.db.read() as c:
        rows = c.execute("SELECT count FROM quotas WHERE subject = ?", (f"model:{model}",))
        return [row["count"] for row in rows]


def test_a_429_is_counted_against_the_free_limits(tmp_path: Path) -> None:
    r = rig(
        tmp_path,
        {PAID: OK, FREE: [RATE_LIMITED]},
        strategy="cost_saver",
        clock=Clock(),
        free_limits=[LIMIT],
    )
    result = r.run()  # the free model is rate limited first, then the router cascades
    assert (result.first_choice, result.decision.model) == (FREE, PAID)
    assert counts(r, FREE) == [1, 1]  # one rpm window and one rpd window


def test_a_success_is_counted_once(tmp_path: Path) -> None:
    r = rig(
        tmp_path, {PAID: OK, FREE: OK}, strategy="cost_saver", clock=Clock(), free_limits=[LIMIT]
    )
    assert r.run().decision.model == FREE
    assert counts(r, FREE) == [1, 1]


def test_other_failures_are_not_counted(tmp_path: Path) -> None:
    r = rig(
        tmp_path,
        {PAID: OK, FREE: [ProviderError(503, {}, "")]},
        strategy="cost_saver",
        clock=Clock(),
        free_limits=[LIMIT],
        walls={"overloaded_max_retries": 0},
    )
    result = r.run()
    assert (result.first_choice, result.decision.model) == (FREE, PAID)
    assert counts(r, FREE) == []


def test_a_counted_429_moves_the_model_toward_soft_exhaustion(tmp_path: Path) -> None:
    """Soft-exhaustion no longer lags real usage: the 429 used one of the day allowance."""
    clock = Clock()
    one_a_day = FreeLimit(provider=PROVIDER, model=None, rpd=1)
    r = rig(
        tmp_path,
        {PAID: OK, FREE: [RATE_LIMITED]},
        strategy="cost_saver",
        clock=clock,
        free_limits=[one_a_day],
    )
    r.run()
    assert r.quotas.availability(FREE, PROVIDER, tier="free_cloud").status == "cooldown"
    clock.now += 61  # the 429 cooldown is over; the day allowance is still spent
    assert r.quotas.availability(FREE, PROVIDER, tier="free_cloud").status == "soft"


# --- the executor's key alias hook ---------------------------------------------------------------


def test_executor_key_alias_defaults_to_none() -> None:
    assert make_executor(ScriptedBackend([])).key_alias("openrouter") is None


def test_executor_key_alias_comes_from_the_key_store_callback() -> None:
    ex = make_executor(ScriptedBackend([]), key_alias_for=lambda p: f"alias-{p}")
    assert ex.key_alias("openrouter") == "alias-openrouter"


def test_executor_key_alias_ignores_a_blank_alias_and_a_broken_key_store() -> None:
    assert make_executor(ScriptedBackend([]), key_alias_for=lambda p: "").key_alias("x") is None

    def broken(provider: str) -> str | None:
        raise RuntimeError("keyring locked")

    assert make_executor(ScriptedBackend([]), key_alias_for=broken).key_alias("x") is None
