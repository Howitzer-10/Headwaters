"""The pull hint never names a model you already have (HDW-512).

The defect this covers: ``headwaters pick --tools --tokens 15000 --scope local`` chose
``qwen2.5-coder:14b`` and then suggested ``pull qwen2.5-coder:14b`` — the model it had just
decided to use, already installed on the machine.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from headwaters.core.analyzer.requirements import HardRequirements
from headwaters.core.catalog.schema import GpuProfile
from headwaters.core.policy.errors import Suggestion
from headwaters.core.policy.filters import Rejection
from headwaters.core.policy.ladder import no_route, suggestions_for
from headwaters.core.policy.types import PolicyContext, PolicyRequest
from headwaters.core.router_prep import pull_hint
from headwaters.core.rules import Rules
from headwaters.core.scope import resolve_scope
from tests.router_rig import Rig
from tests.router_support import OK, candidate, requirements

PROFILES = {
    "laptop_12gb": GpuProfile(
        vram_gb=12,
        recommended={"agentic_coder": ["coder:14b", "coder:7b", "tiny:3b"]},
    ),
}


def tool_reqs():
    return dataclasses.replace(
        requirements("code"), hard=HardRequirements(False, True, False, 15000)
    )


def test_an_installed_model_is_never_recommended_for_pulling() -> None:
    hint = pull_hint(tool_reqs(), None, PROFILES, installed=["coder:14b"])
    assert hint == "pull coder:7b"  # the next uninstalled one, not the one we already have


def test_installed_matching_ignores_case_and_the_latest_tag() -> None:
    assert pull_hint(tool_reqs(), None, PROFILES, installed=["CODER:14B:latest"]) == "pull coder:7b"
    assert pull_hint(tool_reqs(), None, PROFILES, installed=["coder:14b:latest"]) == "pull coder:7b"


def test_no_hint_at_all_when_every_recommended_model_is_installed() -> None:
    have = ["coder:14b", "coder:7b", "tiny:3b"]
    assert pull_hint(tool_reqs(), None, PROFILES, installed=have) is None


def test_the_defect_does_not_reproduce_through_the_router(tmp_path: Path) -> None:
    """The chosen local model is installed, so the hint must not point back at it."""
    chosen = candidate("coder:14b", "local", 10.0)  # low score: lands below the floor
    rigged = Rig(tmp_path, {"coder:14b": OK}, [chosen], reqs=tool_reqs(), gpu_profiles=PROFILES)
    decision = rigged.run(scope="local").decision
    assert decision.model == "coder:14b"
    assert decision.below_floor
    pulls = [s for s in decision.suggestions if s.code == "pull_model"]
    assert pulls, "the generic advice survives"
    # It may name the next model that is not installed, never the one it just picked.
    assert pulls[0].text == "Pull a larger local model that fits your GPU: pull coder:7b."
    assert "coder:14b" not in pulls[0].text


def test_the_hint_still_names_a_model_when_one_is_genuinely_missing(tmp_path: Path) -> None:
    chosen = candidate("tiny:3b", "local", 10.0)
    rigged = Rig(tmp_path, {"tiny:3b": OK}, [chosen], reqs=tool_reqs(), gpu_profiles=PROFILES)
    decision = rigged.run(scope="local").decision
    pull = next(s for s in decision.suggestions if s.code == "pull_model")
    assert "coder:14b" in pull.text


def test_a_tool_shortfall_says_the_schemas_take_context() -> None:
    """No model fits *with tool schemas*: say that, do not blame the pull alone."""
    local = candidate("small:3b", "local", 10.0)
    req = PolicyRequest(requirements=tool_reqs(), scope=resolve_scope(Rules(), requested="local"))
    tips = no_route(
        "local_unavailable",
        "nothing fits",
        (Rejection("small:3b", "no_fit_vram"),),
        [local],
        req,
    ).suggestions
    pull = next(s for s in tips if s.code == "pull_model")
    assert "tool schemas" in pull.text


def test_the_plain_text_hint_hides_nothing_about_installed_models() -> None:
    """The wording lives in one place; ``suggestions_for`` reuses it (no duplicated strings)."""
    local = candidate("coder:14b", "local", 10.0)
    req = PolicyRequest(requirements=tool_reqs(), scope=resolve_scope(Rules(), requested="local"))
    ctx = PolicyContext(candidates=[local], rules=Rules(), pull_hint=None)
    tips = suggestions_for(local, req, ctx, below_floor=True)
    assert Suggestion("pull_model", "Pull a larger local model that fits your GPU.") in tips
