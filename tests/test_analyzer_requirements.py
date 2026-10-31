"""HDW-106: analyzer layer 1. Fixture prompts produce the expected requirements."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from headwaters.core.analyzer import Analyzer, AnalyzerInput, Requirements
from headwaters.core.catalog.loader import SEED_RELATIVE_PATH, load_catalog
from headwaters.core.catalog.schema import AnalyzerHints, Catalog
from tests.analyzer_fixtures_a import CLINE_SYSTEM, FIXTURES_A, TOOL, user
from tests.analyzer_fixtures_b import FIXTURES_B

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURES = [*FIXTURES_A, *FIXTURES_B]


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return load_catalog(REPO_ROOT / SEED_RELATIVE_PATH)


@pytest.fixture(scope="module")
def analyzer(catalog: Catalog) -> Analyzer:
    return Analyzer.from_catalog(catalog)


def check(expected: dict[str, Any], req: Requirements, threshold: float) -> None:
    hard, soft = req.hard, req.soft
    for key, want in expected.items():
        if key in ("tools", "vision", "json"):
            assert getattr(hard, key) is want, key
        elif key == "min_ctx_at_least":
            assert hard.min_ctx >= want, key
        elif key == "min_tokens":
            assert req.est_tokens >= want, key
        elif key == "min_complexity":
            assert soft.complexity >= want, key
        elif key == "confidence_ok":
            assert (req.confidence >= threshold) is want, (key, req.confidence)
        else:
            assert getattr(soft, key) == want, (key, getattr(soft, key))


def test_fixture_count_meets_acceptance() -> None:
    assert len(FIXTURES) >= 30
    assert len({name for name, _, _ in FIXTURES}) == len(FIXTURES)


@pytest.mark.parametrize(("name", "inp", "expected"), FIXTURES, ids=[f[0] for f in FIXTURES])
def test_fixture(
    analyzer: Analyzer, name: str, inp: AnalyzerInput, expected: dict[str, Any]
) -> None:
    req = analyzer.analyze(inp)
    assert req.source == "deterministic"
    assert 1 <= req.soft.complexity <= 5
    check(expected, req, analyzer.hints.confidence_threshold)
    assert req.ambiguous == (req.confidence < analyzer.hints.confidence_threshold)


def test_token_estimate_uses_code_and_prose_rates() -> None:
    analyzer = Analyzer(AnalyzerHints())
    assert analyzer.estimate_tokens(["a" * 400], 0, 0) == 100
    fenced = "```py\n" + "x" * 350 + "```"
    # 350 code chars / 3.5 = 100; the 9 fence-marker chars count as prose: 9 / 4 -> ceil 103
    assert analyzer.estimate_tokens([fenced], 0, 0) == 103


def test_injected_token_counter_wins() -> None:
    analyzer = Analyzer(AnalyzerHints(), token_counter=lambda _text: 7)
    req = analyzer.analyze(AnalyzerInput([user("anything at all")]))
    assert req.est_tokens == 7


def test_empty_hints_are_safe() -> None:
    req = Analyzer(AnalyzerHints()).analyze(AnalyzerInput([user("Prove x")]))
    assert not req.soft.reasoning_hint
    assert req.soft.task == "chat"


def test_malformed_messages_do_not_crash(analyzer: Analyzer) -> None:
    messages: list[dict[str, Any]] = [
        {"role": "user"},
        {"role": "user", "content": None},
        {"role": "user", "content": [42, None, {"type": "weird"}, "plain text"]},
        {"role": "assistant", "tool_calls": "oops"},
        {"role": "assistant", "tool_calls": [{"function": {"arguments": {"a": 1}}}, "bad"]},
    ]
    req = analyzer.analyze(AnalyzerInput(messages))
    assert req.hard.vision is False


def test_nested_tool_result_image_counts(analyzer: Analyzer) -> None:
    messages = [user([{"type": "tool_result", "content": [{"type": "image", "source": {}}]}])]
    assert analyzer.analyze(AnalyzerInput(messages)).hard.vision is True


def test_anthropic_tool_use_counts_as_code_tokens(analyzer: Analyzer) -> None:
    block = {"type": "tool_use", "input": {"content": "x" * 700}}
    req = analyzer.analyze(AnalyzerInput([{"role": "assistant", "content": [block]}]))
    assert req.est_tokens >= 200


def test_response_format_string_and_schema_key(analyzer: Analyzer) -> None:
    assert analyzer.analyze(AnalyzerInput([user("x")], response_format="json")).hard.json
    schema_only = AnalyzerInput([user("x")], response_format={"schema": {"a": 1}})
    assert analyzer.analyze(schema_only).hard.json


def test_does_not_mutate_input(analyzer: Analyzer) -> None:
    messages = [user("```python\nx=1\n```")]
    before = repr(messages)
    analyzer.analyze(AnalyzerInput(messages))
    assert repr(messages) == before


def test_long_user_message_scans_head_and_tail(analyzer: Analyzer) -> None:
    text = "prove this\n" + "filler " * 5000 + "\nthanks"
    assert analyzer.analyze(AnalyzerInput([user(text)])).soft.reasoning_hint


def test_under_five_ms_for_typical_request(analyzer: Analyzer) -> None:
    inp = AnalyzerInput(
        [{"role": "system", "content": CLINE_SYSTEM}, user("refactor main.py\n```py\nx=1\n```")],
        tools=[TOOL],
        headers={"User-Agent": "Cline"},
    )
    analyzer.analyze(inp)  # warm up
    runs = 20
    start = time.perf_counter()
    for _ in range(runs):
        analyzer.analyze(inp)
    per_call_ms = (time.perf_counter() - start) / runs * 1000
    assert per_call_ms < 5, per_call_ms


def test_seed_hints_are_data_not_code(catalog: Catalog) -> None:
    hints = catalog.analyzer_hints
    assert hints.agent_user_agents
    assert hints.duck_keywords
    assert hints.extension_languages[".py"] == "python"


@pytest.mark.parametrize(
    "bad",
    [
        {"reasoning_keywords": ["(unclosed"]},
        {"task_keywords": {"nonsense": ["x"]}},
        {"task_keywords": {"code": ["("]}},
        {"extension_languages": {"py": "python"}},
        {"confidence_threshold": 2},
    ],
)
def test_hint_validation_rejects_bad_data(bad: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match=r"."):
        AnalyzerHints.model_validate(bad)
