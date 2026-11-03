"""HDW-513: per-request failure memory, tier fairness and the honest terminal error.

All providers are mocked. The dead-free-tier scenario is the live one from the ticket: no cloud
key exists, every ``:free`` call answers 401, and a healthy local model sits behind it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from headwaters.core.executor.errors import ProviderError
from headwaters.core.policy.filters import Candidate
from headwaters.core.router import RouterConfig
from headwaters.core.router_failures import redact
from headwaters.core.router_types import RouteFailed
from tests.router_rig import Rig
from tests.router_support import OK, candidate

UNAUTHORISED = ProviderError(401, {}, "", kind="http")  # what a keyless OpenRouter call returns
BROKEN = ProviderError(500, {}, "", kind="http")


def _fail(model: Candidate) -> ProviderError:
    return UNAUTHORISED if model.tier == "free_cloud" else BROKEN


def tier_pool() -> list[Candidate]:
    """Two free-cloud models ranked above two local ones, as in decisions 9-12."""
    return [
        candidate("or/free-a:free", "free_cloud", 90.0, provider="openrouter"),
        candidate("or/free-b:free", "free_cloud", 89.0, provider="openrouter"),
        candidate("qwen3-vl:8b", "local", 80.0),
        candidate("gemma4:12b", "local", 79.0),
    ]


def test_a_failed_model_is_never_chosen_twice_in_one_request(tmp_path: Path) -> None:
    models = [
        candidate("or/free-a:free", "free_cloud", 90.0, provider="openrouter"),
        candidate("qwen3-vl:8b", "local", 80.0),
    ]
    scripts = {m.id: [_fail(m)] for m in models}
    rig = Rig(tmp_path, scripts, models, config=RouterConfig(max_attempts=4))
    with pytest.raises(RouteFailed) as info:
        rig.run()
    assert len(rig.dispatch.calls) == len(set(rig.dispatch.calls)) == 2
    assert info.value.code == "no_route_left"


def test_a_dead_tier_cannot_consume_every_attempt(tmp_path: Path) -> None:
    models = tier_pool()
    scripts = {m.id: [_fail(m)] for m in models}
    rig = Rig(tmp_path, scripts, models, config=RouterConfig(max_attempts=4))
    with pytest.raises(RouteFailed) as info:
        rig.run()
    assert info.value.code == "attempts_exhausted"
    tiers = {c.id: c.tier for c in models}
    assert [tiers[m] for m in rig.dispatch.calls] == [
        "free_cloud",
        "local",
        "free_cloud",
        "local",
    ]


def test_local_is_reached_after_a_dead_free_tier(tmp_path: Path) -> None:
    models = tier_pool()
    rig = Rig(
        tmp_path,
        {models[0].id: [UNAUTHORISED], models[2].id: OK},
        models,
        config=RouterConfig(max_attempts=2),
        api_key_for=lambda provider: None,
    )
    result = rig.run()
    assert result.decision.model == models[2].id
    assert result.first_choice == models[0].id
    assert rig.dispatch.calls == [models[0].id, models[2].id]
    assert [a.status for a in result.attempts] == ["auth_missing_key", "ok"]
    # Slice A is visible to the user, and it counts what actually failed - not the provider skip.
    assert "attempt 2 of 2: skipping 1 model that already failed in this request" in result.reasons


def test_the_fallbacks_alternate_between_tiers(tmp_path: Path) -> None:
    models = tier_pool()
    rig = Rig(tmp_path, {models[0].id: OK}, models)
    result = rig.run()
    assert result.decision.model == models[0].id
    assert result.decision.fallbacks == (models[2].id, models[1].id, models[3].id)
    assert any(r.startswith("tier fairness: free_cloud, local") for r in result.decision.reasons)


def test_an_unauthenticated_provider_is_recorded_as_auth_not_a_model_failure(
    tmp_path: Path,
) -> None:
    models = tier_pool()
    rig = Rig(
        tmp_path,
        {models[0].id: [UNAUTHORISED], models[2].id: OK},
        models,
        config=RouterConfig(max_attempts=3),
        api_key_for=lambda provider: None,  # the key store has no key for any cloud provider
    )
    result = rig.run()
    assert result.decision.model == models[2].id
    assert result.attempts[0].status == "auth_missing_key"
    # The whole provider is skipped: its second model never costs an attempt.
    assert rig.dispatch.calls == [models[0].id, models[2].id]
    assert "no key for openrouter: skipping its 2 models" in result.reasons


def test_the_terminal_error_names_the_missing_key_and_the_fix_command(tmp_path: Path) -> None:
    models = tier_pool()
    rig = Rig(
        tmp_path,
        {models[0].id: [UNAUTHORISED]},
        models,
        config=RouterConfig(max_attempts=1),
        api_key_for=lambda provider: None,
    )
    with pytest.raises(RouteFailed) as info:
        rig.run()
    message = info.value.message
    assert message.splitlines()[0] == "All 1 allowed attempts failed."
    assert (
        "- attempt 1: or/free-a:free (free_cloud, openrouter) - authentication: "
        "no OpenRouter key configured"
    ) in message
    assert "Save a key with: headwaters keys set openrouter" in message
    # Requirement (4): a healthy tier that was never tried is named, not hidden.
    assert "Local models were available in this scope but never tried" in message
    assert len(message.splitlines()) <= 8


def test_an_exhausted_request_lists_every_attempt_as_one_line(tmp_path: Path) -> None:
    models = tier_pool()
    scripts = {m.id: [_fail(m)] for m in models}
    rig = Rig(tmp_path, scripts, models, config=RouterConfig(max_attempts=4))
    with pytest.raises(RouteFailed) as info:
        rig.run()
    lines = info.value.message.splitlines()
    assert lines[0] == "All 4 allowed attempts failed."
    attempts = [line for line in lines if line.startswith("- attempt ")]
    assert [line.split(":")[0] for line in attempts] == [
        "- attempt 1",
        "- attempt 2",
        "- attempt 3",
        "- attempt 4",
    ]
    assert "provider_error (HTTP 500)" in attempts[1]


def test_credential_looking_text_is_redacted(tmp_path: Path) -> None:
    assert redact("key sk-or-v1-abcdef123456 rejected") == "key **** rejected"
    assert redact("Authorization: Bearer abcdef123456") == "Authorization: Bearer ****"
    assert redact("api_key=xyz123&x=1") == "api_key=****&x=1"
    assert redact("plain text") == "plain text"


def test_a_token_in_a_model_id_never_reaches_the_terminal_error(tmp_path: Path) -> None:
    model = candidate("sk-or-v1-secret123456", "free_cloud", 90.0, provider="openrouter")
    rig = Rig(
        tmp_path,
        {model.id: [UNAUTHORISED]},
        [model],
        config=RouterConfig(max_attempts=1),
        api_key_for=lambda provider: None,
    )
    with pytest.raises(RouteFailed) as info:
        rig.run()
    assert "secret123456" not in info.value.message
    assert "- attempt 1: **** (free_cloud, openrouter)" in info.value.message
