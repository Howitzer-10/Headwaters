"""HDW-114 golden table B: vision, agentic, scopes, private lock. Same pool as table A."""

from __future__ import annotations

from typing import Any

import pytest

from tests.policy_golden_support import check
from tests.test_policy_scoring import fit

Case = tuple[str, dict[str, Any], dict[str, Any]]
VISION = {"modalities": ["text", "image"]}
NO_FIT = fit("no_fit", "weights_too_large")


def img(mid: str) -> dict[str, Any]:
    return {mid: {"row": VISION}}


CASES: list[Case] = [
    ("vision-needs-image-model", {"vision": True, "mods": img("free1")}, {"model": "free1"}),
    ("vision-local-vision-model", {"vision": True, "mods": img("loc14")}, {"model": "loc14"}),
    (
        "vision-paid-fallback",
        {"vision": True, "mods": img("paid1")},
        {"model": "paid1", "rung": "paid"},
    ),
    (
        "vision-none-capable",
        {"vision": True},
        {"error": "no_candidates", "sugg": ["add_capable_model"]},
    ),
    (
        "vision-local-scope-none",
        {"vision": True, "scope": "local", "mods": img("paid1")},
        {"error": "no_candidates"},
    ),
    (
        "agentic-needs-tools-score",
        {"agentic": True, "complexity": 1},
        {"model": "paid1", "rung": "paid"},
    ),
    (
        "agentic-free-with-tools",
        {
            "agentic": True,
            "complexity": 1,
            "mods": {"free1": {"scores": {"code": 90.0, "agentic_tools": 90.0}}},
        },
        {"model": "free1", "rung": "cheap"},
    ),
    (
        "agentic-tools-flag-required",
        {
            "agentic": True,
            "tools": True,
            "complexity": 1,
            "mods": {"free1": {"row": {"tools_ok": False}}},
        },
        {"model": "paid1"},
    ),
    (
        "agentic-local-scope",
        {"agentic": True, "scope": "local"},
        {
            "model": "loc14",
            "rung": "best_effort",
            "below": True,
            "verify": True,
            "fallbacks": ["loc7"],
            "sugg": ["pull_model", "split_task", "use_cloud"],
        },
    ),
    (
        # HDW-119 probe P2: a trivial agent turn, local model code 88 / agentic 85. The floor is
        # 70 + 5 + 15 (agentic_bonus) = 90, so it is below the floor but is still served.
        "agentic-local-scope-p2",
        {
            "agentic": True,
            "tools": True,
            "complexity": 1,
            "scope": "local",
            "only": ["loc14"],
            "mods": {"loc14": {"scores": {"code": 88.0, "agentic_tools": 85.0}}},
        },
        {"model": "loc14", "rung": "best_effort", "below": True, "verify": True},
    ),
    # loc7 is below the floor too, so it is only a fallback; nothing leaves the local tier.
    ("scope-local-picks-local", {"scope": "local"}, {"model": "loc14", "fallbacks": []}),
    (
        "scope-local-too-hard",
        {"scope": "local", "complexity": 5},
        {
            "model": "loc14",
            "rung": "best_effort",
            "below": True,
            "verify": True,
            "fallbacks": ["loc7"],
            "sugg": ["pull_model", "split_task", "use_cloud"],
        },
    ),
    (
        "scope-local-no-model-fits",
        {"scope": "local", "mods": {"loc14": {"fit": NO_FIT}, "loc7": {"fit": NO_FIT}}},
        {"error": "no_candidates", "sugg": ["pull_model", "use_cloud"]},
    ),
    (
        "scope-local-ctx-too-big",
        {"scope": "local", "min_ctx": 200_000},
        {"error": "no_candidates", "sugg": ["split_task"]},
    ),
    (
        "scope-free-skips-paid",
        {"scope": "free", "complexity": 5},
        {"model": "loc14", "rung": "best_effort", "below": True, "verify": True},
    ),
    ("scope-free-picks-local", {"scope": "free"}, {"model": "loc14"}),
    ("scope-cloud-skips-local", {"scope": "cloud"}, {"model": "free1", "rung": "cheap"}),
    (
        "scope-cloud-escalates-to-paid",
        {"scope": "cloud", "complexity": 5},
        {"model": "paid1", "rung": "paid"},
    ),
    ("scope-auto-paid-last", {"scope": "auto", "drop": ["loc14", "free1"]}, {"model": "paid1"}),
    ("private-lock-local-only", {"locks": ["private_lock"]}, {"model": "loc14", "sugg": []}),
    (
        "private-lock-never-cloud",
        {"locks": ["private_lock"], "complexity": 5},
        {
            "model": "loc14",
            "rung": "best_effort",
            "below": True,
            "verify": True,
            "sugg": ["pull_model", "split_task"],  # a lock forbids the use_cloud hint
        },
    ),
    (
        "private-lock-no-pin-escape",
        {"locks": ["private_lock"], "pin": "paid1"},
        {"error": "pin_rejected"},
    ),
    (
        "private-lock-battery-no-cloud-hint",
        {"locks": ["private_lock"], "battery": "prefer_cloud"},
        {"model": "loc14", "sugg": []},
    ),
    (
        # HDW-128 (F9): the lock does not narrow `local`, but it is in force: no cloud hint.
        "private-lock-default-local-battery-no-cloud-hint",
        {
            "locks": ["private_lock"],
            "default_scope": "local",
            "scope": None,
            "battery": "prefer_cloud",
        },
        {"model": "loc14", "sugg": []},
    ),
    (
        "private-lock-requested-local-busy-no-cloud-hint",
        {"locks": ["private_lock"], "scope": "local", "busy": True},
        {"model": "loc14", "sugg": []},
    ),
    (
        "airplane-battery-no-cloud-hint",
        {"locks": ["airplane"], "scope": "local", "battery": "prefer_cloud"},
        {"model": "loc14", "sugg": []},
    ),
    (
        "no-lock-local-scope-below-floor-keeps-cloud-hint",
        {"scope": "local", "complexity": 5},
        {"model": "loc14", "below": True, "sugg": ["pull_model", "split_task", "use_cloud"]},
    ),
]


@pytest.mark.parametrize(("spec", "expect"), [c[1:] for c in CASES], ids=[c[0] for c in CASES])
def test_golden_b(spec: dict[str, Any], expect: dict[str, Any]) -> None:
    check(spec, expect)
