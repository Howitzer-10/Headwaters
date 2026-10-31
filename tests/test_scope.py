from __future__ import annotations

from typing import Any

import pytest

from headwaters.core.features import FeatureRegistry, FeatureSpec
from headwaters.core.project_config import ProjectConfig
from headwaters.core.rules import Rules
from headwaters.core.scope import (
    ALL_TIERS,
    PAID_SCOPES_FEATURE_ID,
    SCOPE_TIERS,
    ScopeError,
    paid_scopes_available,
    parse_model_name,
    resolve_scope,
)
from headwaters.core.secrets_detect import SecretMatch

SECRET = (SecretMatch("sk-style-api-key", 0, 40),)
AUTO = ALL_TIERS
FREE = frozenset({"free_cloud", "local"})
LOCAL = frozenset({"local"})
CLOUD = frozenset({"paid", "openrouter_auto", "free_cloud"})
FREE_CLOUD_ONLY = frozenset({"free_cloud"})

PLC = "private_lock_conflict"
PRC = "project_rule_conflict"

# (id, default_scope, private_lock, privacy, secret, secrets_action, requested, paid, expected)
# ``expected`` is a tier set, or an error code string.
Row = tuple[str, str, bool, str, bool, str, str | None, bool, Any]
GOLDEN: list[Row] = [
    # Plain scopes, paid-scopes feature on.
    ("auto", "auto", False, "any", False, "local", None, True, AUTO),
    ("free", "free", False, "any", False, "local", None, True, FREE),
    ("local", "local", False, "any", False, "local", None, True, LOCAL),
    ("cloud", "cloud", False, "any", False, "local", None, True, CLOUD),
    # Requested beats the default (both directions).
    ("req-local-over-auto", "auto", False, "any", False, "local", "local", True, LOCAL),
    ("req-cloud-over-local", "local", False, "any", False, "local", "cloud", True, CLOUD),
    ("req-free-over-cloud", "cloud", False, "any", False, "local", "free", True, FREE),
    # Without the paid-scopes feature, paid tiers drop out.
    ("auto-no-paid", "auto", False, "any", False, "local", None, False, FREE),
    ("cloud-no-paid", "cloud", False, "any", False, "local", None, False, FREE_CLOUD_ONLY),
    ("free-no-paid", "free", False, "any", False, "local", None, False, FREE),
    ("local-no-paid", "local", False, "any", False, "local", None, False, LOCAL),
    # Private lock.
    ("lock-auto", "auto", True, "any", False, "local", None, True, LOCAL),
    ("lock-free", "free", True, "any", False, "local", None, True, LOCAL),
    ("lock-local", "local", True, "any", False, "local", None, True, LOCAL),
    ("lock-req-local", "auto", True, "any", False, "local", "local", True, LOCAL),
    ("lock-req-cloud", "auto", True, "any", False, "local", "cloud", True, PLC),
    ("lock-req-cloud-no-paid", "auto", True, "any", False, "local", "cloud", False, PLC),
    ("lock-req-free", "auto", True, "any", False, "local", "free", True, LOCAL),
    # Project rule (tighten only).
    ("proj-free-on-auto", "auto", False, "free", False, "local", None, True, FREE),
    ("proj-local-on-auto", "auto", False, "local", False, "local", None, True, LOCAL),
    ("proj-local-on-free", "free", False, "local", False, "local", None, True, LOCAL),
    ("proj-any-keeps-local", "local", False, "any", False, "local", None, True, LOCAL),
    ("proj-free-keeps-local", "local", False, "free", False, "local", None, True, LOCAL),
    ("proj-free-req-cloud", "auto", False, "free", False, "local", "cloud", True, FREE_CLOUD_ONLY),
    ("proj-local-req-cloud", "auto", False, "local", False, "local", "cloud", True, PRC),
    ("proj-local-req-cloud-no-paid", "auto", False, "local", False, "local", "cloud", False, PRC),
    # Secret detection.
    ("secret-auto", "auto", False, "any", True, "local", None, True, LOCAL),
    ("secret-free", "free", False, "any", True, "local", None, True, LOCAL),
    ("secret-cloud", "cloud", False, "any", True, "local", None, True, "secret_conflict"),
    ("secret-refuse", "auto", False, "any", True, "refuse", None, True, "secret_refused"),
    ("secret-off", "auto", False, "any", True, "off", None, True, AUTO),
    ("secret-off-cloud", "cloud", False, "any", True, "off", None, True, CLOUD),
    ("secret-local-scope", "local", False, "any", True, "local", None, True, LOCAL),
    ("secret-refuse-local", "local", False, "any", True, "refuse", None, True, "secret_refused"),
    # Combined locks: tighter always wins.
    ("lock+proj+secret", "auto", True, "free", True, "local", None, True, LOCAL),
    ("proj-free+secret", "auto", False, "free", True, "local", None, True, LOCAL),
    ("lock-req-cloud-secret-off", "auto", True, "any", True, "off", "cloud", True, PLC),
    ("proj-local+secret-req-cloud", "auto", False, "local", True, "local", "cloud", True, PRC),
]


@pytest.mark.parametrize(
    ("case", "default", "lock", "privacy", "secret", "action", "requested", "paid", "expected"),
    GOLDEN,
    ids=[row[0] for row in GOLDEN],
)
def test_golden_precedence_table(
    case: str,
    default: str,
    lock: bool,
    privacy: str,
    secret: bool,
    action: str,
    requested: str | None,
    paid: bool,
    expected: Any,
) -> None:
    rules = Rules.model_validate(
        {"default_scope": default, "private_lock": lock, "secrets": {"action": action}}
    )
    project = ProjectConfig.model_validate({"privacy": privacy})
    kwargs: dict[str, Any] = {
        "requested": requested,
        "secret_matches": SECRET if secret else (),
        "paid_scopes": paid,
    }
    if isinstance(expected, str):
        with pytest.raises(ScopeError) as caught:
            resolve_scope(rules, project, **kwargs)
        assert caught.value.code == expected
        assert caught.value.message
        assert caught.value.fix_hint
    else:
        decision = resolve_scope(rules, project, **kwargs)
        assert decision.tiers == expected, decision.reasons


def test_golden_table_has_at_least_25_unique_rows() -> None:
    assert len(GOLDEN) >= 25
    assert len({row[0] for row in GOLDEN}) == len(GOLDEN)


def test_scope_tier_definitions_match_the_ticket() -> None:
    assert SCOPE_TIERS["auto"] == {"paid", "openrouter_auto", "free_cloud", "local"}
    assert SCOPE_TIERS["free"] == {"free_cloud", "local"}
    assert SCOPE_TIERS["local"] == {"local"}
    assert SCOPE_TIERS["cloud"] == {"paid", "openrouter_auto", "free_cloud"}


def test_project_rules_can_only_tighten() -> None:
    """For every scope/lock combination, adding a project rule never adds tiers."""
    for default in ("auto", "free", "local", "cloud"):
        for lock in (False, True):
            for requested in (None, "auto", "free", "local", "cloud"):
                rules = Rules.model_validate({"default_scope": default, "private_lock": lock})
                try:
                    base = resolve_scope(rules, None, requested=requested, paid_scopes=True)
                except ScopeError:
                    continue
                for privacy in ("any", "free", "local"):
                    project = ProjectConfig.model_validate({"privacy": privacy})
                    try:
                        with_rule = resolve_scope(
                            rules, project, requested=requested, paid_scopes=True
                        )
                    except ScopeError:
                        continue  # refusing is tighter than anything
                    assert with_rule.tiers <= base.tiers


def test_nothing_ever_exceeds_the_requested_scope() -> None:
    for default in ("auto", "free", "local", "cloud"):
        rules = Rules.model_validate({"default_scope": default})
        for secret in (SECRET, ()):
            try:
                decision = resolve_scope(rules, secret_matches=secret, paid_scopes=True)
            except ScopeError:
                continue
            assert decision.tiers <= SCOPE_TIERS[default]


def test_override_lifts_secret_lock_only() -> None:
    rules = Rules.model_validate({"default_scope": "cloud"})
    decision = resolve_scope(rules, secret_matches=SECRET, secret_override=True, paid_scopes=True)
    assert decision.tiers == CLOUD
    assert any("override" in r for r in decision.reasons)
    locked = Rules.model_validate({"default_scope": "cloud", "private_lock": True})
    with pytest.raises(ScopeError) as caught:
        resolve_scope(locked, secret_matches=SECRET, secret_override=True, paid_scopes=True)
    assert caught.value.code == PLC
    with pytest.raises(ScopeError) as caught:
        resolve_scope(
            rules,
            ProjectConfig.model_validate({"privacy": "local"}),
            secret_matches=SECRET,
            secret_override=True,
            paid_scopes=True,
        )
    assert caught.value.code == PRC


def test_override_lets_the_user_proceed_past_refuse() -> None:
    rules = Rules.model_validate({"secrets": {"action": "refuse"}})
    decision = resolve_scope(rules, secret_matches=SECRET, secret_override=True, paid_scopes=True)
    assert decision.tiers == AUTO


def test_paid_exclusion_is_explained() -> None:
    decision = resolve_scope(Rules.model_validate({"default_scope": "cloud"}), paid_scopes=False)
    assert decision.tiers == FREE_CLOUD_ONLY
    assert any("paid tiers excluded" in r and "not enabled" in r for r in decision.reasons)


def test_no_paid_note_when_nothing_to_exclude() -> None:
    decision = resolve_scope(Rules.model_validate({"default_scope": "local"}), paid_scopes=False)
    assert not any("paid" in r for r in decision.reasons)


def test_locks_are_recorded() -> None:
    rules = Rules.model_validate({"default_scope": "auto", "private_lock": True})
    decision = resolve_scope(rules, ProjectConfig.model_validate({"privacy": "free"}))
    assert decision.locks == ("private_lock",)
    assert decision.local_only
    assert not decision.allows_cloud


def test_decision_helpers() -> None:
    decision = resolve_scope(Rules(), paid_scopes=True)
    assert decision.scope == "auto"
    assert decision.allows_cloud
    assert not decision.local_only
    assert (decision.strategy, decision.verify) == ("cost_saver", "agentic")
    assert resolve_scope(Rules.model_validate({"default_scope": "free"})).scope == "free"


@pytest.mark.parametrize(
    ("model", "scope", "strategy", "verify"),
    [
        ("headwaters/auto", "auto", None, None),
        ("headwaters/free", "free", None, None),
        ("headwaters/local", "local", None, None),
        ("headwaters/cloud", "cloud", None, None),
        ("headwaters/free+verify", "free", None, "always"),
        ("headwaters/auto+quality", "auto", "quality_first", None),
        ("headwaters/cloud+primary+noverify", "cloud", "primary_first", "off"),
        ("Headwaters/Local", "local", None, None),
        ("headwaters/quality", "auto", "quality_first", None),
        ("headwaters/auto+quality+verify", "auto", "quality_first", "always"),
    ],
)
def test_parse_model_name(model: str, scope: str, strategy: str | None, verify: str | None) -> None:
    parsed = parse_model_name(model)
    assert parsed is not None
    assert (parsed.scope, parsed.strategy, parsed.verify) == (scope, strategy, verify)


@pytest.mark.parametrize("model", ["gpt-4o", "openai/gpt-4o", "", "claude-sonnet"])
def test_parse_ignores_foreign_models(model: str) -> None:
    assert parse_model_name(model) is None


@pytest.mark.parametrize(
    ("model", "code"),
    [
        ("headwaters/bogus", "unknown_model"),
        ("headwaters/", "unknown_model"),
        ("headwaters/free+turbo", "unknown_suffix"),
        ("headwaters/free+", "unknown_suffix"),
    ],
)
def test_parse_rejects_unknown(model: str, code: str) -> None:
    with pytest.raises(ScopeError) as caught:
        parse_model_name(model)
    assert caught.value.code == code
    assert caught.value.fix_hint


def test_suffix_cannot_loosen_a_lock() -> None:
    parsed = parse_model_name("headwaters/auto+quality")
    assert parsed is not None
    decision = resolve_scope(
        Rules.model_validate({"private_lock": True}),
        requested=parsed.scope,
        strategy=parsed.strategy,
    )
    assert decision.tiers == LOCAL
    assert decision.strategy == "quality_first"


def test_paid_scopes_feature_flag() -> None:
    registry = FeatureRegistry()
    assert not paid_scopes_available(registry)
    registry.register(FeatureSpec(id=PAID_SCOPES_FEATURE_ID, star=True, title="Paid scopes"))
    assert paid_scopes_available(registry)
    registry.set_flags({PAID_SCOPES_FEATURE_ID: False})
    assert not paid_scopes_available(registry)

    cloud = resolve_scope(Rules.model_validate({"default_scope": "cloud"}), paid_scopes=False)
    assert cloud.scope == "cloud"


def test_reasons_never_contain_secret_text() -> None:
    secret_text = "sk-" + "Zq9" * 12
    match = SecretMatch("sk-style-api-key", 0, len(secret_text))
    decision = resolve_scope(Rules(), secret_matches=[match], paid_scopes=True)
    assert secret_text not in " ".join(decision.reasons)
    assert decision.secret_patterns == ("sk-style-api-key",)


def test_local_unavailable_gives_fix_hint() -> None:
    rules = Rules.model_validate({"default_scope": "local"})
    with pytest.raises(ScopeError) as caught:
        resolve_scope(rules, local_available=False, local_fix_hint="pull qwen2.5vl:7b")
    assert caught.value.code == "local_unavailable"
    assert caught.value.fix_hint == "pull qwen2.5vl:7b"
    assert caught.value.as_dict()["code"] == "local_unavailable"
    with pytest.raises(ScopeError) as default_hint:
        resolve_scope(rules, local_available=False)
    assert "Ollama" in default_hint.value.fix_hint


def test_local_unavailable_irrelevant_when_cloud_allowed() -> None:
    decision = resolve_scope(Rules(), local_available=False, paid_scopes=True)
    assert "local" in decision.tiers


def test_private_lock_error_names_the_fix() -> None:
    with pytest.raises(ScopeError) as caught:
        resolve_scope(Rules.model_validate({"private_lock": True}), requested="cloud")
    assert "Private lock" in caught.value.fix_hint


def test_strategy_and_verify_overrides() -> None:
    decision = resolve_scope(Rules(), strategy="quality_first", verify="always")
    assert (decision.strategy, decision.verify) == ("quality_first", "always")
