"""HDW-114 golden table C: GPU profiles, battery, GPU busy, unscored models, pin, premium."""

from __future__ import annotations

from typing import Any

import pytest

from headwaters.core.policy.errors import NoRouteError
from tests.policy_golden_support import COOLDOWN, build, check, run
from tests.test_policy_golden_a import CASES as TABLE_A
from tests.test_policy_golden_b import CASES as TABLE_B
from tests.test_policy_scoring import fit

Case = tuple[str, dict[str, Any], dict[str, Any]]
MYSTERY = {"new:mystery": {"tier": "free_cloud", "scores": {}}}
NO_FIT = fit("no_fit", "weights_too_large")

CASES: list[Case] = [
    (
        "gpu-12gb-offload-still-ok",
        {"scope": "local", "mods": {"loc14": {"fit": fit("offload", "kv_exceeds_budget")}}},
        {"model": "loc14"},
    ),
    (
        "gpu-offload-ranks-below-equal-fit",  # HDW-133: same score, only the fit differs
        {
            "scope": "local",
            "mods": {
                "loc14": {"fit": fit("offload", "kv_exceeds_budget")},
                "loc7": {"scores": {"code": 85.0}},
            },
        },
        {"model": "loc7", "fallbacks": ["loc14"]},
    ),
    (
        "gpu-offload-loses-to-free-cloud",
        {"mods": {"loc14": {"fit": fit("offload", "kv_exceeds_budget")}}},
        {"model": "free1"},
    ),
    (
        "gpu-12gb-big-model-excluded",
        {"mods": {"loc14": {"fit": fit("no_fit", "weights_too_large")}}},
        {"model": "free1"},
    ),
    (
        "gpu-16gb-both-fit",
        {"scope": "local", "mods": {"loc7": {"scores": {"code": 81.0}}}},
        {"model": "loc14", "fallbacks": ["loc7"]},
    ),
    (
        "gpu-small-model-cannot-beat-big",
        {"scope": "local", "mods": {"loc7": {"scores": {"code": 82.0}}}},
        {"model": "loc14"},
    ),
    (
        "battery-suggests-cloud",
        {"battery": "prefer_cloud", "drop": ["free1"]},
        {"model": "loc14", "sugg": ["use_cloud"]},
    ),
    (
        "battery-never-leaves-local",
        {"battery": "prefer_cloud", "scope": "local"},
        {"model": "loc14", "sugg": ["use_cloud"]},
    ),
    ("battery-normal-no-hint", {"battery": "normal"}, {"model": "loc14", "sugg": []}),
    (
        "battery-local-only-drops-cloud",
        {"battery": "local_only", "complexity": 5},
        {
            "model": "loc14",
            "rung": "best_effort",
            "below": True,
            "verify": True,
            "fallbacks": ["loc7"],
            "sugg": ["pull_model", "split_task"],
        },
    ),
    (
        "battery-local-only-nothing-fits-errors",
        {"battery": "local_only", "mods": {"loc14": {"fit": NO_FIT}, "loc7": {"fit": NO_FIT}}},
        {"error": "no_candidates"},
    ),
    (
        "gpu-busy-suggests-cloud",
        {"busy": True, "drop": ["free1"]},
        {"model": "loc14", "sugg": ["use_cloud"]},
    ),
    (
        "battery-penalty-flips-to-free",
        {"battery": "prefer_cloud", "mods": {"free1": {"scores": {"code": 95.0}}}},
        {"model": "free1", "sugg": []},
    ),
    (
        "unscored-model-never-clears",
        {"only": ["new:mystery", "paid1"], "mods": MYSTERY},
        {"model": "paid1", "rung": "paid"},
    ),
    (
        "unscored-only-best-effort",
        {"only": ["new:mystery"], "mods": MYSTERY},
        {"model": "mystery", "rung": "best_effort", "below": True, "verify": True},
    ),
    ("pin-wins", {"pin": "free2"}, {"model": "free2", "rung": "pin"}),
    (
        "pin-below-floor-flagged",
        {"pin": "loc7"},
        {"model": "loc7", "rung": "pin", "below": True, "verify": True},
    ),
    (
        "pin-walled-errors",
        {"pin": "free2", "mods": {"free2": {"availability": COOLDOWN}}},
        {"error": "pin_rejected", "sugg": ["wait"]},
    ),
    ("pin-unknown-errors", {"pin": "nope"}, {"error": "pin_rejected"}),
    ("pin-beats-force", {"pin": "free1", "force": "premium"}, {"model": "free1", "rung": "pin"}),
    (
        "premium-never-automatic",
        {"complexity": 5, "drop": ["paid1", "orauto"]},
        {"model": "loc14", "below": True},
    ),
    ("premium-force", {"force": "premium"}, {"model": "prem", "rung": "force"}),
    (
        "premium-force-local-scope-denied",
        {"force": "premium", "scope": "local"},
        {"error": "premium_unavailable"},
    ),
]


@pytest.mark.parametrize(("spec", "expect"), [c[1:] for c in CASES], ids=[c[0] for c in CASES])
def test_golden_c(spec: dict[str, Any], expect: dict[str, Any]) -> None:
    check(spec, expect)


def test_no_route_error_as_dict_is_structured() -> None:
    err = run({"scope": "local", "mods": {"loc14": {"fit": NO_FIT}, "loc7": {"fit": NO_FIT}}})
    payload = err.as_dict()
    assert payload["code"] == "no_candidates"
    assert {s["code"] for s in payload["suggestions"]} >= {"pull_model", "use_cloud"}
    assert isinstance(payload["rejected"], list)


def test_local_below_floor_is_never_raised() -> None:
    for spec in (
        {"scope": "local", "complexity": 5},
        {"scope": "local", "agentic": True},
        {"locks": ["private_lock"], "complexity": 5},
        {"battery": "local_only", "complexity": 5},
    ):
        assert not isinstance(run(spec), NoRouteError), spec


def test_no_golden_row_leaves_the_local_tier_in_local_scope() -> None:
    tiers = {c.id: c.tier for c in build(None, (), ())}
    local_only = [
        spec
        for _, spec, _ in [*TABLE_B, *CASES]
        if spec.get("scope") == "local" or spec.get("locks") or spec.get("battery") == "local_only"
    ]
    assert len(local_only) >= 8
    for spec in local_only:
        got = run(spec)
        if isinstance(got, NoRouteError):
            continue
        assert tiers[got.model] == "local", spec
        assert {tiers[m] for m in got.fallbacks} <= {"local"}, spec


def test_the_offload_penalty_is_named_in_the_reasons() -> None:
    """FU-133-1: the points lost to system RAM are in the reasons, not only in the score."""
    got = run({"scope": "local", "mods": {"loc14": {"fit": fit("offload", "kv_exceeds_budget")}}})
    assert not isinstance(got, NoRouteError)
    assert got.breakdown.offload_penalty == 30.0
    assert any("offloaded to system RAM: -30.0 points" in r for r in got.reasons)


def test_golden_table_has_at_least_40_scenarios() -> None:
    assert len(TABLE_A) + len(TABLE_B) + len(CASES) >= 40
