"""HDW-516: the fallback tier order, ``interleave`` (HDW-513, unchanged) or ``descend``.

``descend`` exhausts one tier before the next, walking ``TIER_WALK`` in ``router.tier_direction``
order. The interleaved order is the default and every older test still pins it; these tests pin
the new order and the things it must not change: a pin, the floor flags, the attempt bound, and
the removal of a failed model.
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from headwaters.core.executor.errors import ProviderError
from headwaters.core.policy.filters import Candidate
from headwaters.core.policy.ladder import descend_tiers
from headwaters.core.policy.types import Entry
from headwaters.core.router import RouterConfig
from headwaters.core.router_types import RouteFailed, RouteRequest
from headwaters.core.rules import (
    TIER_DIRECTIONS,
    TIER_ORDERS,
    RouterRules,
    Rules,
    TierDirection,
    TierOrder,
    tier_order_label,
)
from headwaters.core.scope import TIER_WALK, tier_walk
from headwaters.core.state_limits import record_wall
from headwaters.mcp.tools.status import scope_info
from tests.router_rig import Rig
from tests.router_support import MESSAGES, OK, candidate, registry_with

UNAUTHORISED = ProviderError(401, {}, "", kind="http")
BROKEN = ProviderError(500, {}, "", kind="http")


def rules(order: TierOrder = "interleave", direction: TierDirection = "cloud_first") -> Rules:
    return Rules(router=RouterRules(tier_order=order, tier_direction=direction))


def tier_pool() -> list[Candidate]:
    """Two free-cloud models ranked above two local ones, as in the HDW-513 tests."""
    return [
        candidate("or/free-a:free", "free_cloud", 90.0, provider="openrouter"),
        candidate("or/free-b:free", "free_cloud", 89.0, provider="openrouter"),
        candidate("qwen3-vl:8b", "local", 80.0),
        candidate("gemma4:12b", "local", 79.0),
    ]


def paid_pool() -> list[Candidate]:
    """Two models in each of the three tiers the ``paid_scopes`` feature unlocks.

    A paid candidate enters the ladder only as a primary (or as ``openrouter/auto``), so the
    paid pair is marked primary, exactly as the router tests do.
    """
    paid = [
        candidate(
            "vendor/paid-a", "paid", 99.0, provider="anthropic", price_in=3.0, is_primary=True
        ),
        candidate(
            "vendor/paid-b", "paid", 98.0, provider="anthropic", price_in=3.0, is_primary=True
        ),
    ]
    return paid + tier_pool()


def order_of(rig: Rig) -> list[str]:
    """The decision and its fallbacks in order, from a dry run that calls no model."""
    planned = asyncio.run(rig.router.plan(RouteRequest(messages=MESSAGES)))
    return [planned.decision.model, *planned.decision.fallbacks]


def _entry(tier: str) -> Entry:
    """``descend_tiers`` reads only ``entry[0].tier``, so a stub is enough for the walk itself."""
    return cast(Entry, (SimpleNamespace(tier=tier), None, "cheap"))


def test_the_tier_walk_is_cloud_first_and_its_exact_reverse() -> None:
    assert TIER_WALK == ("paid", "openrouter_auto", "free_cloud", "local")
    assert tier_walk("cloud_first") == TIER_WALK
    assert tier_walk("local_first") == tuple(reversed(TIER_WALK))


def test_the_label_only_names_the_direction_for_descend() -> None:
    assert tier_order_label("interleave", "local_first") == "interleave"
    assert tier_order_label("descend", "cloud_first") == "descend (cloud_first)"
    assert tier_order_label("descend", "local_first") == "descend (local_first)"


def test_interleave_is_still_the_default_order(tmp_path: Path) -> None:
    rig = Rig(tmp_path, {}, tier_pool())
    assert order_of(rig) == ["or/free-a:free", "qwen3-vl:8b", "or/free-b:free", "gemma4:12b"]


def test_descend_cloud_first_exhausts_a_tier_before_the_next(tmp_path: Path) -> None:
    rig = Rig(tmp_path, {}, tier_pool(), rules=rules("descend", "cloud_first"))
    assert order_of(rig) == ["or/free-a:free", "or/free-b:free", "qwen3-vl:8b", "gemma4:12b"]


def test_descend_local_first_is_the_reverse(tmp_path: Path) -> None:
    rig = Rig(tmp_path, {}, tier_pool(), rules=rules("descend", "local_first"))
    assert order_of(rig) == ["qwen3-vl:8b", "gemma4:12b", "or/free-a:free", "or/free-b:free"]


def test_descend_keeps_the_score_order_inside_a_tier(tmp_path: Path) -> None:
    pool = [
        candidate("or/free-a:free", "free_cloud", 95.0, provider="openrouter"),
        candidate("or/free-b:free", "free_cloud", 90.0, provider="openrouter"),
        candidate("qwen3-vl:8b", "local", 99.0),
    ]
    rig = Rig(tmp_path, {}, pool, rules=rules("descend", "cloud_first"))
    # The best-ranked model overall does not lead: its tier comes last cloud-first, and inside
    # each tier the ladder's own order (here, the score) survives the sort.
    assert order_of(rig) == ["or/free-a:free", "or/free-b:free", "qwen3-vl:8b"]


def test_descend_puts_an_unknown_tier_last_and_stays_stable() -> None:
    entries = [_entry("mystery"), _entry("local"), _entry("free_cloud"), _entry("mystery")]
    walked = descend_tiers(entries, "cloud_first")
    assert [entry[0].tier for entry in walked] == ["free_cloud", "local", "mystery", "mystery"]


def test_descend_does_not_claim_tier_fairness(tmp_path: Path) -> None:
    rig = Rig(tmp_path, {}, tier_pool(), rules=rules("descend", "cloud_first"))
    result = asyncio.run(rig.router.plan(RouteRequest(messages=MESSAGES)))
    reasons = list(result.decision.reasons)
    assert not any("interleaved" in reason for reason in reasons)
    assert any(reason.startswith("tier order: descend (cloud_first)") for reason in reasons)


def test_a_pin_still_leads_under_descend(tmp_path: Path) -> None:
    rig = Rig(tmp_path, {}, tier_pool(), rules=rules("descend", "cloud_first"))
    planned = asyncio.run(rig.router.plan(RouteRequest(messages=MESSAGES, pin="gemma4:12b")))
    assert planned.decision.model == "gemma4:12b"  # last in the descend order, pinned anyway
    assert planned.decision.rung == "pin"


def test_descend_keeps_descending_after_a_failed_model_is_removed(tmp_path: Path) -> None:
    models = tier_pool()
    rig = Rig(
        tmp_path,
        {models[0].id: [UNAUTHORISED], models[1].id: OK},
        models,
        rules=rules("descend", "cloud_first"),
        config=RouterConfig(max_attempts=4),
    )
    result = rig.run()
    assert rig.dispatch.calls == [models[0].id, models[1].id]  # the second free tier, not local
    assert result.decision.model == models[1].id
    assert models[0].id not in result.decision.fallbacks


def test_descend_respects_the_attempt_bound(tmp_path: Path) -> None:
    models = tier_pool()
    scripts = {m.id: [BROKEN] for m in models}
    rig = Rig(
        tmp_path,
        scripts,
        models,
        rules=rules("descend", "cloud_first"),
        config=RouterConfig(max_attempts=3),
    )
    with pytest.raises(RouteFailed) as info:
        rig.run()
    assert info.value.code == "attempts_exhausted"
    assert rig.dispatch.calls == [models[0].id, models[1].id, models[2].id]


def test_below_floor_and_verification_survive_descend(tmp_path: Path) -> None:
    pool = [
        candidate("or/free-a:free", "free_cloud", 60.0, provider="openrouter"),
        candidate("qwen3-vl:8b", "local", 55.0),
    ]
    rig = Rig(tmp_path, {}, pool, rules=rules("descend", "cloud_first"))
    planned = asyncio.run(rig.router.plan(RouteRequest(messages=MESSAGES)))
    assert planned.decision.model == "or/free-a:free"
    assert planned.decision.below_floor is True
    assert planned.decision.verify_required is True
    assert planned.decision.fallbacks == ("qwen3-vl:8b",)


def test_the_scope_info_reports_the_order() -> None:
    assert scope_info(None, Rules(), None).tier_order == "interleave"
    info = scope_info(None, rules("descend", "local_first"), None)
    assert info.tier_order == "descend (local_first)"
    assert info.tier_direction == "local_first"


def test_descend_cloud_first_exhausts_the_paid_tier_first(tmp_path: Path) -> None:
    rig = Rig(
        tmp_path,
        {},
        paid_pool(),
        registry=registry_with(paid=True),
        rules=rules("descend", "cloud_first"),
    )
    assert order_of(rig) == [
        "vendor/paid-a",
        "vendor/paid-b",
        "or/free-a:free",
        "or/free-b:free",
        "qwen3-vl:8b",
        "gemma4:12b",
    ]


def test_descend_local_first_puts_the_paid_tier_last(tmp_path: Path) -> None:
    rig = Rig(
        tmp_path,
        {},
        paid_pool(),
        registry=registry_with(paid=True),
        rules=rules("descend", "local_first"),
    )
    assert order_of(rig) == [
        "qwen3-vl:8b",
        "gemma4:12b",
        "or/free-a:free",
        "or/free-b:free",
        "vendor/paid-a",
        "vendor/paid-b",
    ]


def test_the_direction_is_ignored_under_interleave(tmp_path: Path) -> None:
    rig = Rig(tmp_path, {}, tier_pool(), rules=rules("interleave", "local_first"))
    assert rig.rules.router.tier_direction == "local_first"  # validated and kept, just unused
    assert order_of(rig) == ["or/free-a:free", "qwen3-vl:8b", "or/free-b:free", "gemma4:12b"]


def test_a_strict_pin_does_not_fail_over_under_descend(tmp_path: Path) -> None:
    rig = Rig(
        tmp_path,
        {"or/free-a:free": [BROKEN]},
        tier_pool(),
        rules=rules("descend", "cloud_first"),
    )
    with pytest.raises(RouteFailed):
        rig.run(pin="or/free-a:free", pin_strict=True)
    assert rig.dispatch.calls == ["or/free-a:free"]


def test_a_walled_tier_is_skipped_not_entered(tmp_path: Path) -> None:
    rig = Rig(tmp_path, {}, tier_pool(), rules=rules("descend", "cloud_first"))
    for model in ("or/free-a:free", "or/free-b:free"):
        record_wall(rig.db, f"model:{model}", "rate_limit", until=time.time() + 600)
    planned = asyncio.run(rig.router.plan(RouteRequest(messages=MESSAGES)))
    assert [planned.decision.model, *planned.decision.fallbacks] == ["qwen3-vl:8b", "gemma4:12b"]
    walled = {r.model_id for r in planned.rejected if r.reason.startswith("unavailable_")}
    assert {"or/free-a:free", "or/free-b:free"} <= walled


def test_local_scope_with_cloud_first_is_still_local_only(tmp_path: Path) -> None:
    rig = Rig(
        tmp_path,
        {"qwen3-vl:8b": OK},
        tier_pool(),
        rules=rules("descend", "cloud_first"),
    )
    result = rig.run(scope="local")
    assert result.decision.model == "qwen3-vl:8b"
    assert rig.cloud_clients == 0  # cloud_first never constructs a cloud client in local scope
    assert rig.dispatch.calls == ["qwen3-vl:8b"]


def test_the_schema_carries_both_new_settings() -> None:
    path = Path(__file__).resolve().parents[1] / "catalog" / "rules.schema.json"
    router = json.loads(path.read_text(encoding="utf-8"))["$defs"]["RouterRules"]["properties"]
    assert router["tier_order"]["enum"] == list(TIER_ORDERS)
    assert router["tier_order"]["default"] == "interleave"
    assert router["tier_direction"]["enum"] == list(TIER_DIRECTIONS)
    assert router["tier_direction"]["default"] == "cloud_first"
