"""HDW-139: seed catalog classifier entry, laziness markers and wall pattern fixtures."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from headwaters.core.catalog.loader import SEED_RELATIVE_PATH, load_catalog
from headwaters.core.quotas import QuotaTracker
from headwaters.core.rules import WallsRules
from headwaters.core.state import open_database
from headwaters.core.verify_basic import BasicVerifier
from headwaters.core.walls import WallClassifier

REPO_ROOT = Path(__file__).resolve().parent.parent
CATALOG = load_catalog(REPO_ROOT / SEED_RELATIVE_PATH)
CLASSIFIER = WallClassifier(CATALOG.wall_patterns)
NOW = 1_800_000_000.0


# --- classifier entry (F21) -------------------------------------------------------------------


@pytest.mark.parametrize("profile", ["laptop_12gb", "desktop_16gb"])
def test_classifier_is_a_tiny_text_model_on_both_profiles(profile: str) -> None:
    ids = CATALOG.gpu_profiles[profile].recommended["classifier"]
    assert len(ids) == 1
    entry = next(m for m in CATALOG.models if m.id == ids[0])
    assert entry.modalities == ["text"]
    assert entry.provider == "ollama"
    assert entry.size_gb is not None
    assert entry.size_gb <= 2.0
    vision = CATALOG.gpu_profiles[profile].recommended["vision"]
    assert ids[0] not in vision


# --- laziness markers -------------------------------------------------------------------------

LAZY = [
    ("python", "def f():\n    # rest of the code\n    pass"),
    ("python", "def f():\n    # rest of code here\n    pass"),
    ("javascript", "function f() {\n  // existing code\n}"),
    ("javascript", "function f() {\n  // ... existing code ...\n}"),
    ("python", "class A:\n    # ... remaining methods unchanged\n    pass"),
    ("python", "x = 1\n...remaining\n"),
    ("python", "def f():\n    pass  # TODO: implement"),
    ("python", "def f():\n    pass  # todo implement later"),
]


@pytest.fixture(scope="module")
def verifier() -> BasicVerifier:
    return BasicVerifier.from_catalog(CATALOG)


@pytest.mark.parametrize(("lang", "body"), LAZY)
def test_verifier_catches_each_laziness_marker(
    verifier: BasicVerifier, lang: str, body: str
) -> None:
    report = verifier.verify_report(f"```{lang}\n{body}\n```\n")
    assert any(r.code == "placeholder" for r in report.reasons), body


def test_normal_code_is_not_flagged(verifier: BasicVerifier) -> None:
    code = "```python\ndef rest(x):\n    # the existing user is kept\n    return x + 1\n```\n"
    assert not any(r.code == "placeholder" for r in verifier.verify_report(code).reasons)


def test_laziness_patterns_are_marked_verify() -> None:
    lazy = [p for p in CATALOG.placeholder_patterns if p.name.startswith("lazy-")]
    assert len(lazy) == 4
    assert all(p.verify for p in lazy)


# --- wall pattern fixtures: kind, scope and credit class --------------------------------------
# Bodies follow each provider's documented error shape (sources in docs/ASSUMPTIONS.md).


def _err(**fields: object) -> str:
    return json.dumps({"error": fields})


# (id, provider, status, body, kind, scope, tier, expected subject)
WALL_CASES = [
    (
        "openrouter-402-credits",
        "openrouter",
        402,
        _err(
            code=402,
            message="Insufficient credits. Add more using https://openrouter.ai/settings/credits",
        ),
        "exhausted",
        "provider",
        "paid",
        "provider:openrouter:paid",
    ),
    (
        "openrouter-402-afford",
        "openrouter",
        402,
        _err(
            code=402,
            message="This request requires more credits, or fewer max_tokens. "
            "You can only afford 4000.",
        ),
        "exhausted",
        "provider",
        "paid",
        "provider:openrouter:paid",
    ),
    (
        "openrouter-free-per-day",
        "openrouter",
        429,
        _err(
            code=429,
            message="Rate limit exceeded: free-models-per-day. Add 10 credits to unlock "
            "1000 free model requests per day",
            metadata={"headers": {"X-RateLimit-Limit": "50"}},
        ),
        "exhausted",
        "key",
        "free_cloud",
        "key:k1:free",
    ),
    (
        "openrouter-429-rate",
        "openrouter",
        429,
        _err(
            code=429, message="Rate limit exceeded", metadata={"error_type": "rate_limit_exceeded"}
        ),
        "rate_limit",
        "model",
        "free_cloud",
        "model:m",
    ),
    (
        "openai-insufficient-quota",
        "openai",
        429,
        _err(
            message="You exceeded your current quota, please check your plan and billing details.",
            type="insufficient_quota",
            code="insufficient_quota",
        ),
        "exhausted",
        "key",
        "paid",
        "key:k1:paid",
    ),
    (
        "openai-rate-limit",
        "openai",
        429,
        _err(
            message="Rate limit reached for gpt-4o on requests per min (RPM): Limit 3.",
            type="requests",
        ),
        "rate_limit",
        "model",
        "paid",
        "model:m",
    ),
    (
        "anthropic-credit-400",
        "anthropic",
        400,
        json.dumps(
            {
                "type": "error",
                "error": {
                    "type": "invalid_request_error",
                    "message": "Your credit balance is too low to access the Anthropic API.",
                },
            }
        ),
        "exhausted",
        "key",
        "paid",
        "key:k1:paid",
    ),
    (
        "anthropic-credit-402",
        "anthropic",
        402,
        json.dumps(
            {
                "type": "error",
                "error": {
                    "type": "billing_error",
                    "message": "Your credit balance is too low to access the Anthropic API.",
                },
            }
        ),
        "exhausted",
        "key",
        "paid",
        "key:k1:paid",
    ),
    (
        "anthropic-rate-limit",
        "anthropic",
        429,
        json.dumps(
            {"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}}
        ),
        "rate_limit",
        "model",
        "paid",
        "model:m",
    ),
    (
        "gemini-daily",
        "gemini",
        429,
        json.dumps(
            {
                "error": {
                    "code": 429,
                    "status": "RESOURCE_EXHAUSTED",
                    "message": "You exceeded your current quota. Quota exceeded for metric: "
                    "generate_content_free_tier_requests, limit: 50 per day",
                }
            }
        ),
        "exhausted",
        "model",
        "free_cloud",
        "model:m",
    ),
    (
        "gemini-per-minute",
        "gemini",
        429,
        json.dumps(
            {
                "error": {
                    "code": 429,
                    "status": "RESOURCE_EXHAUSTED",
                    "message": "Resource has been exhausted (e.g. check quota).",
                }
            }
        ),
        "rate_limit",
        "model",
        "free_cloud",
        "model:m",
    ),
    (
        "generic-402",
        "somebody",
        402,
        "Payment required",
        "exhausted",
        "key",
        "paid",
        "key:k1:paid",
    ),
    ("generic-429", "somebody", 429, "Too many requests", "rate_limit", "model", "paid", "model:m"),
    (
        "generic-503",
        "somebody",
        503,
        "Service unavailable",
        "overloaded",
        "model",
        "paid",
        "model:m",
    ),
]


@pytest.mark.parametrize(
    ("provider", "status", "body", "kind", "scope", "tier", "subject"),
    [case[1:] for case in WALL_CASES],
    ids=[case[0] for case in WALL_CASES],
)
def test_wall_fixture_classifies_to_kind_scope_and_credit_class(
    tmp_path: Path,
    provider: str,
    status: int,
    body: str,
    kind: str,
    scope: str,
    tier: str,
    subject: str,
) -> None:
    event = CLASSIFIER.classify(status, None, body, provider, now=NOW)
    assert (event.kind, event.scope) == (kind, scope)
    tracker = QuotaTracker(open_database(tmp_path / "state.db"), WallsRules(), [])
    decision = tracker.record_wall(event, model="m", provider=provider, key="k1", tier=tier)
    assert decision.subject == subject
