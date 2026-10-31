"""HDW-114 golden table A: base ladder, strategies, optimistic rung, walls.

Pool (tests/policy_golden_support.py): code floor is 80 at complexity 2. loc14 85 (total 82),
free1 82 (79), free2 75, loc7 70, paid1 95 (primary), orauto 90, orfree 60, prem 99 (premium).
"""

from __future__ import annotations

from typing import Any

import pytest

from headwaters.core.quotas import Availability
from tests.policy_golden_support import COOLDOWN, EXHAUSTED, check

NO_LOW = ("loc14", "free1")
FREE_ROUTER = {"tiers": {"openrouter_free_router": {"enabled": True, "max_complexity": 2}}}
WALL_PAID = {"paid1": {"availability": COOLDOWN}, "orauto": {"availability": COOLDOWN}}
ROUTERS = ["orfree", "paid1", "orauto"]
Case = tuple[str, dict[str, Any], dict[str, Any]]

CASES: list[Case] = [
    (
        "default-local-wins",
        {},
        {
            "model": "loc14",
            "rung": "cheap",
            "below": False,
            "fallbacks": ["free1", "paid1", "orauto"],
        },
    ),
    ("no-local-free-cloud", {"drop": ["loc14"]}, {"model": "free1", "rung": "cheap"}),
    (
        "cheap-short-goes-paid",
        {"drop": NO_LOW},
        {"model": "paid1", "rung": "paid", "fallbacks": ["orauto", "free2", "loc7"]},
    ),
    (
        "optimistic-with-plugin",
        {"drop": NO_LOW, "escalation": True},
        {"model": "free2", "rung": "optimistic", "below": True, "verify": True},
    ),
    (
        "optimistic-too-far",
        {"drop": NO_LOW, "escalation": True, "mods": {"free2": {"scores": {"code": 60.0}}}},
        {"model": "paid1", "rung": "paid"},
    ),
    (
        "optimistic-margin-from-rules",
        {
            "drop": NO_LOW,
            "escalation": True,
            "mods": {"free2": {"scores": {"code": 60.0}}},
            "rules": {"floors": {"optimistic_margin": 12}},
        },
        {"model": "loc7", "rung": "optimistic"},
    ),
    (
        "optimistic-needs-verifiable",
        {
            "task": "reasoning",
            "escalation": True,
            "drop": ["loc14"],
            "mods": {
                "free1": {"scores": {"reasoning": 78.0}},
                "paid1": {"scores": {"reasoning": 95.0}},
            },
        },
        {"model": "paid1", "rung": "paid"},
    ),
    (
        "optimistic-json-counts",
        {
            "task": "chat",
            "json": True,
            "escalation": True,
            "only": ["free1", "paid1"],
            "mods": {"free1": {"scores": {"chat": 50.0}}},
        },
        {"model": "free1", "rung": "optimistic", "verify": True},
    ),
    (
        "no-json-goes-paid",
        {
            "task": "chat",
            "escalation": True,
            "only": ["free1", "paid1"],
            "mods": {"free1": {"scores": {"chat": 50.0}}},
        },
        {"model": "paid1", "rung": "paid"},
    ),
    (
        "tie-goes-local",
        {"mods": {"free1": {"scores": {"code": 85.0}}}},
        {"model": "loc14", "fallbacks": ["free1"]},
    ),
    ("complexity-1-lower-floor", {"complexity": 1, "drop": ["loc14"]}, {"model": "free1"}),
    ("complexity-5-needs-paid", {"complexity": 5}, {"model": "paid1", "rung": "paid"}),
    ("high-risk-raises-floor", {"risk": "high"}, {"model": "paid1", "below": False}),
    ("chat-easy-stays-local", {"task": "chat"}, {"model": "loc14", "rung": "cheap"}),
    ("quality-first-still-cheap-first", {"strategy": "quality_first"}, {"model": "loc14"}),
    (
        "primary-first",
        {"strategy": "primary_first"},
        {"model": "paid1", "rung": "paid", "fallbacks": ["orauto", "loc14", "free1"]},
    ),
    (
        "primary-first-walled-uses-auto",
        {"strategy": "primary_first", "mods": {"paid1": {"availability": COOLDOWN}}},
        {"model": "orauto", "rung": "paid"},
    ),
    (
        "primary-first-local-scope",
        {"strategy": "primary_first", "scope": "local"},
        {"model": "loc14"},
    ),
    ("wall-local-exhausted", {"mods": {"loc14": {"availability": EXHAUSTED}}}, {"model": "free1"}),
    (
        "wall-free-cooldown-paid",
        {"drop": ["loc14"], "mods": {"free1": {"availability": COOLDOWN}}},
        {"model": "paid1", "rung": "paid"},
    ),
    (
        "wall-soft-still-usable",
        {"drop": ["loc14"], "mods": {"free1": {"availability": Availability("soft")}}},
        {"model": "free1", "rung": "cheap"},
    ),
    ("wall-paid-only-keeps-cheap", {"mods": WALL_PAID}, {"model": "loc14"}),
    (
        "wall-paid-best-effort",
        {"drop": NO_LOW, "mods": WALL_PAID},
        {
            "model": "free2",
            "rung": "best_effort",
            "below": True,
            "verify": True,
            "fallbacks": ["loc7"],
        },
    ),
    (
        "wall-paid-free-router-appended",
        {"drop": NO_LOW, "mods": WALL_PAID, "rules": FREE_ROUTER},
        {"model": "free2", "fallbacks": ["loc7", "orfree"]},
    ),
    (
        "wall-free-router-only",
        {"only": ROUTERS, "mods": WALL_PAID, "rules": FREE_ROUTER},
        {"model": "orfree", "rung": "free_router", "below": True, "verify": True},
    ),
    (
        "wall-free-router-complexity",
        {"only": ROUTERS, "mods": WALL_PAID, "rules": FREE_ROUTER, "complexity": 3},
        {"error": "no_candidates", "sugg": ["wait"]},
    ),
    (
        "wall-free-router-disabled",
        {"only": ROUTERS, "mods": WALL_PAID},
        {"error": "no_candidates", "sugg": ["wait"]},
    ),
    (
        "wall-all-cheap-and-paid1",
        {
            "mods": {
                k: {"availability": COOLDOWN} for k in ("loc14", "free1", "free2", "loc7", "paid1")
            }
        },
        {"model": "orauto", "rung": "paid"},
    ),
    (
        "wall-everything",
        {
            "mods": {
                k: {"availability": COOLDOWN}
                for k in ("loc14", "loc7", "free1", "free2", "paid1", "orauto", "orfree", "prem")
            }
        },
        {"error": "no_candidates", "sugg": ["wait"]},
    ),
    (
        "wall-no-eta-no-wait",
        {
            "only": ["paid1", "orauto"],
            "mods": {"paid1": {"availability": EXHAUSTED}, "orauto": {"availability": EXHAUSTED}},
        },
        {"error": "no_candidates", "etas": {}},
    ),
]


@pytest.mark.parametrize(("spec", "expect"), [c[1:] for c in CASES], ids=[c[0] for c in CASES])
def test_golden_a(spec: dict[str, Any], expect: dict[str, Any]) -> None:
    check(spec, expect)
