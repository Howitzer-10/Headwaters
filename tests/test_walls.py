"""HDW-110: wall classifier, with realistically shaped bodies for each provider."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from headwaters.core.catalog.loader import SEED_RELATIVE_PATH, load_catalog
from headwaters.core.catalog.schema import WallPattern
from headwaters.core.walls import WallClassifier, WallEvent, classify, parse_wait

REPO_ROOT = Path(__file__).resolve().parent.parent
NOW = 1_800_000_000.0  # fixed clock (epoch seconds)

CATALOG = load_catalog(REPO_ROOT / SEED_RELATIVE_PATH)
CLASSIFIER = WallClassifier(CATALOG.wall_patterns)


def run(
    status: int, body: object, provider: str, headers: dict[str, str] | None = None
) -> WallEvent:
    return CLASSIFIER.classify(status, headers, body, provider, now=NOW)


def err(kind: str, message: str, **extra: object) -> str:
    return json.dumps({"error": {"message": message, "type": kind, **extra}})


# --- OpenAI -----------------------------------------------------------------------------------

OPENAI_QUOTA = err(
    "insufficient_quota",
    "You exceeded your current quota, please check your plan and billing details.",
    code="insufficient_quota",
)
OPENAI_RATE = err(
    "requests",
    "Rate limit reached for gpt-4o in organization org-x on requests per min (RPM): Limit 3.",
    code="rate_limit_exceeded",
)
OPENAI_CONTEXT = err(
    "invalid_request_error",
    "This model's maximum context length is 128000 tokens. However, your messages resulted in "
    "130012 tokens. Please reduce the length of the messages.",
    code="context_length_exceeded",
)


def test_openai_insufficient_quota_is_exhausted_key_scope() -> None:
    event = run(429, OPENAI_QUOTA, "openai", {"retry-after": "20"})
    assert (event.kind, event.scope) == ("exhausted", "key")
    assert event.is_wall


def test_openai_rate_limit_reads_reset_headers() -> None:
    headers = {"x-ratelimit-reset-requests": "20s", "x-ratelimit-reset-tokens": "6m0s"}
    event = run(429, OPENAI_RATE, "openai", headers)
    assert (event.kind, event.scope) == ("rate_limit", "model")
    assert event.retry_after == pytest.approx(360.0)  # the latest reset wins
    assert event.reset_at == pytest.approx(NOW + 360.0)


def test_openai_context_length() -> None:
    event = run(400, OPENAI_CONTEXT, "openai")
    assert (event.kind, event.scope) == ("context", "model")
    assert not event.is_wall
    assert event.retry_after is None


# --- Anthropic --------------------------------------------------------------------------------


def anthropic(kind: str, message: str) -> str:
    return json.dumps({"type": "error", "error": {"type": kind, "message": message}})


def test_anthropic_rate_limit_error_with_retry_after() -> None:
    body = anthropic("rate_limit_error", "Number of request tokens has exceeded your limit.")
    event = run(429, body, "anthropic", {"Retry-After": "17"})
    assert (event.kind, event.scope) == ("rate_limit", "model")
    assert event.retry_after == pytest.approx(17.0)


def test_anthropic_reset_header_is_rfc3339() -> None:
    body = anthropic("rate_limit_error", "slow down")
    headers = {"anthropic-ratelimit-requests-reset": "2027-01-15T08:00:30Z"}
    event = run(429, body, "anthropic", headers)
    assert event.reset_at == 1_800_000_030.0  # 2027-01-15T08:00:30Z
    assert event.retry_after == pytest.approx(30.0)


def test_anthropic_overloaded_529() -> None:
    body = anthropic("overloaded_error", "Overloaded")
    event = run(529, body, "anthropic")
    assert (event.kind, event.scope) == ("overloaded", "provider")


def test_anthropic_credit_balance_too_low_is_exhausted() -> None:
    body = anthropic(
        "invalid_request_error",
        "Your credit balance is too low to access the Anthropic API. Please go to Plans & "
        "Billing to upgrade or purchase credits.",
    )
    event = run(400, body, "anthropic")
    assert (event.kind, event.scope) == ("exhausted", "key")


def test_anthropic_prompt_too_long_is_context() -> None:
    body = anthropic("invalid_request_error", "prompt is too long: 215000 tokens > 200000 maximum")
    assert run(400, body, "anthropic").kind == "context"


# --- Gemini -----------------------------------------------------------------------------------


def gemini(quota_id: str, retry: str | None) -> str:
    violation = {"quotaMetric": "generate_content_free_tier_requests", "quotaId": quota_id}
    details: list[dict[str, object]] = [
        {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [violation]}
    ]
    if retry:
        details.append({"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry})
    return json.dumps(
        {
            "error": {
                "code": 429,
                "message": f"You exceeded your current quota. Quota exceeded for {quota_id}.",
                "status": "RESOURCE_EXHAUSTED",
                "details": details,
            }
        }
    )


def test_gemini_daily_quota_is_exhausted() -> None:
    body = gemini("GenerateRequestsPerDayPerProjectPerModel-FreeTier", "41s")
    event = run(429, body, "gemini")
    assert (event.kind, event.scope) == ("exhausted", "model")


def test_gemini_per_minute_is_rate_limit_with_retry_delay_from_body() -> None:
    body = gemini("GenerateRequestsPerMinutePerProjectPerModel-FreeTier", "30s")
    event = run(429, body, "gemini")
    assert (event.kind, event.scope) == ("rate_limit", "model")
    assert event.retry_after == pytest.approx(30.0)


def test_gemini_rate_limit_without_hint_has_no_wait() -> None:
    event = run(429, gemini("GenerateRequestsPerMinutePerProjectPerModel", None), "gemini")
    assert event.kind == "rate_limit"
    assert event.retry_after is None
    assert event.reset_at is None


def test_gemini_context_length() -> None:
    body = json.dumps(
        {
            "error": {
                "code": 400,
                "status": "INVALID_ARGUMENT",
                "message": "The input token count (1200000) exceeds the maximum number of "
                "tokens allowed (1048576).",
            }
        }
    )
    assert run(400, body, "gemini").kind == "context"


# --- OpenRouter -------------------------------------------------------------------------------


def openrouter(code: int, message: str) -> str:
    return json.dumps({"error": {"code": code, "message": message}})


def test_openrouter_insufficient_credits_is_provider_scope() -> None:
    body = openrouter(
        402, "Insufficient credits. Add more using https://openrouter.ai/settings/credits"
    )
    event = run(402, body, "openrouter")
    assert (event.kind, event.scope) == ("exhausted", "provider")


def test_openrouter_402_cannot_afford_max_tokens() -> None:
    body = openrouter(
        402,
        "This request requires more credits, or fewer max_tokens. You can only afford 4000.",
    )
    assert run(402, body, "openrouter").scope == "provider"


def test_openrouter_free_model_rate_limit_uses_epoch_ms_reset() -> None:
    body = openrouter(429, "Provider returned error: rate-limited upstream")
    reset_ms = str(int((NOW + 45) * 1000))
    event = run(429, body, "openrouter", {"X-RateLimit-Limit": "20", "X-RateLimit-Reset": reset_ms})
    assert (event.kind, event.scope) == ("rate_limit", "model")
    assert event.retry_after == pytest.approx(45.0)


def test_openrouter_free_models_per_day_is_exhausted() -> None:
    body = openrouter(
        429,
        "Rate limit exceeded: free-models-per-day. Add 10 credits to unlock 1000 free model "
        "requests per day",
    )
    reset_ms = str(int((NOW + 3600) * 1000))
    event = run(429, body, "openrouter", {"X-RateLimit-Reset": reset_ms})
    assert (event.kind, event.scope) == ("exhausted", "key")
    assert event.retry_after == pytest.approx(3600.0)


def test_openrouter_context_length() -> None:
    body = openrouter(400, "This endpoint's maximum context length is 32768 tokens.")
    assert run(400, body, "openrouter").kind == "context"


# --- Ollama -----------------------------------------------------------------------------------


def test_ollama_busy_queue_is_overloaded() -> None:
    body = json.dumps({"error": "server busy, please try again. maximum pending requests exceeded"})
    event = run(503, body, "ollama")
    assert (event.kind, event.scope) == ("overloaded", "provider")


def test_ollama_missing_model_is_not_a_wall() -> None:
    event = run(404, json.dumps({"error": "model 'nope:7b' not found"}), "ollama")
    assert event.kind == "other"
    assert not event.is_wall
    assert event.pattern_index is None


# --- OpenCode ---------------------------------------------------------------------------------


def opencode(kind: str, message: str) -> str:
    return json.dumps({"type": "error", "error": {"type": kind, "message": message}})


def test_opencode_free_usage_limit_is_rate_limit_provider_scope() -> None:
    body = opencode("FreeUsageLimitError", "Rate limit exceeded")
    event = run(429, body, "opencode", {"retry-after": "3600"})
    assert (event.kind, event.scope) == ("rate_limit", "provider")
    assert event.retry_after == pytest.approx(3600.0)


def test_opencode_credits_error_is_exhausted() -> None:
    event = run(402, opencode("CreditsError", "Insufficient balance"), "opencode")
    assert (event.kind, event.scope) == ("exhausted", "provider")


# --- Generic behaviour ------------------------------------------------------------------------


def test_generic_402_503_429_fall_back_to_wildcard_patterns() -> None:
    assert run(402, "payment required", "some-new-provider").kind == "exhausted"
    assert run(503, "", "some-new-provider").kind == "overloaded"
    assert run(429, "", "some-new-provider").kind == "rate_limit"


def test_overloaded_body_with_unusual_status() -> None:
    body = anthropic("overloaded_error", "Overloaded")
    assert run(200, body, "some-new-provider").kind == "overloaded"


@pytest.mark.parametrize("status", [200, 400, 401, 404, 500])
def test_non_wall_statuses_are_other(status: int) -> None:
    event = run(status, '{"error": "boom"}', "openai")
    assert event.kind == "other"
    assert event.retry_after is None
    assert event.reset_at is None


def test_other_ignores_reset_headers() -> None:
    event = run(500, "oops", "openai", {"Retry-After": "30"})
    assert event.kind == "other"
    assert event.retry_after is None


def test_body_types_bytes_dict_none() -> None:
    assert run(429, OPENAI_QUOTA.encode(), "openai").kind == "exhausted"
    assert run(429, json.loads(OPENAI_QUOTA), "openai").kind == "exhausted"
    assert run(429, None, "openai").kind == "rate_limit"
    assert run(429, b"\xff\xfe insufficient_quota", "openai").kind == "exhausted"
    assert run(429, object(), "openai").kind == "rate_limit"


def test_provider_match_is_case_insensitive() -> None:
    assert run(429, OPENAI_QUOTA, "OpenAI").kind == "exhausted"


def test_priority_body_beats_status_and_specific_beats_wildcard() -> None:
    patterns = [
        WallPattern(provider="*", status_codes=[429], kind="rate_limit", scope="model"),
        WallPattern(provider="p", status_codes=[429], kind="overloaded", scope="provider"),
        WallPattern(provider="*", body_regex="special", kind="exhausted", scope="key"),
    ]
    classifier = WallClassifier(patterns)
    assert classifier.classify(429, None, "x", "p", now=NOW).kind == "overloaded"
    assert classifier.classify(429, None, "x", "q", now=NOW).kind == "rate_limit"
    assert classifier.classify(429, None, "special", "p", now=NOW).kind == "exhausted"


def test_patterns_come_from_the_catalog_only() -> None:
    empty = WallClassifier([])
    assert empty.classify(429, None, OPENAI_QUOTA, "openai", now=NOW).kind == "other"


def test_module_level_classify_uses_catalog_and_requires_a_source() -> None:
    assert classify(402, {}, "", "x", catalog=CATALOG, now=NOW).kind == "exhausted"
    assert classify(429, {}, "", "x", patterns=CATALOG.wall_patterns).kind == "rate_limit"
    with pytest.raises(ValueError, match="catalog or patterns"):
        classify(429, {}, "", "x")


def test_event_carries_no_body_text() -> None:
    assert "quota" not in repr(run(429, OPENAI_QUOTA, "openai"))


# --- Header and wait parsing ------------------------------------------------------------------


def test_retry_after_seconds_and_fractions() -> None:
    assert run(429, "", "x", {"retry-after": "12"}).retry_after == pytest.approx(12.0)
    assert run(429, "", "x", {"retry-after": "1.5"}).retry_after == pytest.approx(1.5)


def test_retry_after_http_date() -> None:
    event = run(429, "", "x", {"Retry-After": "Fri, 15 Jan 2027 08:00:30 GMT"})
    assert event.reset_at == 1_800_000_030.0
    assert event.retry_after == pytest.approx(30.0)


def test_retry_after_in_the_past_clamps_to_zero() -> None:
    event = run(429, "", "x", {"Retry-After": "Mon, 01 Jan 2024 00:00:00 GMT"})
    assert event.retry_after == 0.0


def test_retry_after_ms_header() -> None:
    assert run(429, "", "x", {"retry-after-ms": "2500"}).retry_after == pytest.approx(2.5)


def test_retry_after_wins_over_reset_headers() -> None:
    headers = {"retry-after": "5", "x-ratelimit-reset": "9999"}
    assert run(429, "", "x", headers).retry_after == pytest.approx(5.0)


def test_unparseable_headers_are_ignored() -> None:
    headers = {"retry-after": "soon", "x-ratelimit-reset": "later", "retry-after-ms": "nan"}
    event = run(429, "", "x", headers)
    assert event.kind == "rate_limit"
    assert event.retry_after is None


def test_reset_header_epoch_seconds_and_relative_seconds() -> None:
    assert run(429, "", "x", {"x-ratelimit-reset": str(int(NOW) + 30)}).retry_after == 30.0
    assert run(429, "", "x", {"x-ratelimit-reset": "30"}).retry_after == 30.0
    assert run(429, "", "x", {"ratelimit-reset": "7"}).retry_after == 7.0


@pytest.mark.parametrize(
    ("text", "seconds"),
    [("30", 30.0), ("1.5s", 1.5), ("6m0s", 360.0), ("1h2m3s", 3723.0), ("250ms", 0.25)],
)
def test_parse_wait_durations(text: str, seconds: float) -> None:
    assert parse_wait(text, NOW) == pytest.approx(NOW + seconds)


@pytest.mark.parametrize("text", ["", "  ", "abc", "-5", "inf", "5x", "12 parsecs"])
def test_parse_wait_rejects_garbage(text: str) -> None:
    assert parse_wait(text, NOW) is None


def test_parse_wait_naive_timestamp_is_utc() -> None:
    assert parse_wait("2027-01-15T08:00:30", NOW) == parse_wait("2027-01-15T08:00:30Z", NOW)


def test_default_clock_is_used_when_now_is_omitted() -> None:
    event = CLASSIFIER.classify(429, {"retry-after": "10"}, "", "x")
    assert event.retry_after == pytest.approx(10.0, abs=1.0)
