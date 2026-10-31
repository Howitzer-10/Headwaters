"""HDW-107: analyzer layer 3 (tiny CPU classifier): payload, cache, fallback, parsing."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from headwaters.core.analyzer import Analyzer, AnalyzerChain, AnalyzerInput, TinyLlmClassifier
from headwaters.core.analyzer.classifier import parse_label, prompt_text
from headwaters.core.analyzer.ollama_client import ClassifierUnavailableError
from headwaters.core.catalog.loader import SEED_RELATIVE_PATH, load_catalog
from headwaters.core.catalog.schema import Catalog
from headwaters.core.rules import ClassifierRules

REPO_ROOT = Path(__file__).resolve().parent.parent
VAGUE = "I was thinking about stuff and things around the house. " * 40
CLEAR = "write a poem about autumn"


class FakeClient:
    def __init__(self, reply: Any = None, error: Exception | None = None) -> None:
        self.reply = reply
        self.error = error
        self.payloads: list[Mapping[str, Any]] = []
        self.timeouts: list[float] = []

    def chat(self, payload: Mapping[str, Any], timeout_s: float) -> Mapping[str, Any]:
        self.payloads.append(payload)
        self.timeouts.append(timeout_s)
        if self.error is not None:
            raise self.error
        assert isinstance(self.reply, dict)
        return self.reply


def answer(task: str = "code", complexity: int = 4, risk: str = "medium") -> dict[str, Any]:
    body = {"task": task, "complexity": complexity, "risk": risk}
    return {"message": {"role": "assistant", "content": json.dumps(body)}}


def msg(text: str) -> AnalyzerInput:
    return AnalyzerInput([{"role": "user", "content": text}])


@pytest.fixture(scope="module")
def catalog() -> Catalog:
    return load_catalog(REPO_ROOT / SEED_RELATIVE_PATH)


@pytest.fixture(scope="module")
def base(catalog: Catalog) -> Analyzer:
    return Analyzer.from_catalog(catalog)


def test_no_gpu_option_and_schema_sent() -> None:
    client = FakeClient(answer())
    TinyLlmClassifier("tiny:1b", client=client).classify("hello")
    payload = client.payloads[0]
    assert payload["options"]["num_gpu"] == 0
    assert payload["stream"] is False
    assert payload["format"]["required"] == ["task", "complexity", "risk"]
    assert payload["format"]["properties"]["complexity"]["maximum"] == 5
    assert payload["model"] == "tiny:1b"


def test_default_timeout_is_two_seconds_and_cpu_only() -> None:
    client = FakeClient(answer())
    TinyLlmClassifier("m", client=client).classify("x")
    assert client.timeouts == [2]
    assert ClassifierRules().num_gpu == 0


def test_cache_hit_skips_second_call() -> None:
    client = FakeClient(answer("extract", 2, "low"))
    layer = TinyLlmClassifier("m", client=client)
    first = layer.classify("same prompt")
    second = layer.classify("same prompt")
    assert first == second
    assert layer.calls == 1
    assert len(client.payloads) == 1
    layer.classify("other prompt")
    assert layer.calls == 2


def test_cache_key_is_sha256_and_model_scoped() -> None:
    a = TinyLlmClassifier("m1", client=FakeClient(answer()))
    b = TinyLlmClassifier("m2", client=FakeClient(answer()))
    assert len(a.cache_key("secret prompt")) == 64
    assert a.cache_key("p") != b.cache_key("p")
    assert "secret" not in a.cache_key("secret prompt")


def test_cache_evicts_oldest() -> None:
    layer = TinyLlmClassifier("m", client=FakeClient(answer()), cache_size=2)
    for text in ("a", "b", "c"):
        layer.classify(text)
    layer.classify("c")
    assert layer.calls == 3
    layer.classify("a")
    assert layer.calls == 4


ERRORS = [TimeoutError(), OSError("down"), ClassifierUnavailableError("x")]


@pytest.mark.parametrize("error", ERRORS)
def test_timeout_and_errors_fall_back_to_layer1(base: Analyzer, error: Exception) -> None:
    layer = TinyLlmClassifier("m", client=FakeClient(error=error))
    expected = base.analyze(msg(VAGUE))
    assert expected.ambiguous
    assert AnalyzerChain(base, layer3=layer).analyze(msg(VAGUE)) == expected
    assert layer.calls == 1


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "[1]",
        json.dumps({"task": "bogus", "complexity": 2, "risk": "low"}),
        json.dumps({"task": "code", "complexity": "2", "risk": "low"}),
        json.dumps({"task": "code", "complexity": True, "risk": "low"}),
        json.dumps({"task": "code", "complexity": 2, "risk": "x"}),
    ],
)
def test_bad_replies_fall_back_and_are_not_cached(base: Analyzer, content: str) -> None:
    reply = {"message": {"content": content}}
    layer = TinyLlmClassifier("m", client=FakeClient(reply))
    assert parse_label(reply) is None
    assert AnalyzerChain(base, layer3=layer).analyze(msg(VAGUE)) == base.analyze(msg(VAGUE))
    layer.classify("p")
    layer.classify("p")
    assert layer.calls == 3  # failures are retried, never cached


def test_reply_without_message_is_rejected() -> None:
    assert parse_label({}) is None
    assert parse_label({"message": {"content": 5}}) is None


def test_complexity_is_clamped() -> None:
    label = parse_label(answer("code", 9, "low"))
    assert label is not None
    assert label.complexity == 5


def test_runs_only_when_ambiguous(base: Analyzer) -> None:
    client = FakeClient(answer("creative", 3, "low"))
    chain = AnalyzerChain(base, layer3=TinyLlmClassifier("m", client=client))
    assert chain.analyze(msg(CLEAR)) == base.analyze(msg(CLEAR))
    assert client.payloads == []
    refined = chain.analyze(msg(VAGUE))
    assert len(client.payloads) == 1
    assert refined.source == "classifier"
    soft = refined.soft
    assert (soft.task, soft.complexity, soft.risk) == ("creative", 3, "low")
    assert refined.confidence >= chain.threshold
    assert not refined.ambiguous
    assert refined.hard == base.analyze(msg(VAGUE)).hard  # hard needs never change


def test_vision_task_is_kept_for_image_requests(base: Analyzer) -> None:
    image = {"type": "image_url", "image_url": {"url": "data:,"}}
    inp = AnalyzerInput([{"role": "user", "content": [image, {"type": "text", "text": VAGUE}]}])
    current = replace(base.analyze(inp), confidence=0.1)
    layer = TinyLlmClassifier("m", client=FakeClient(answer("chat", 2, "low")))
    refined = layer.refine(inp, current)
    assert refined is not None
    assert refined.soft.task == "vision"
    assert refined.hard.vision


def test_empty_prompt_does_not_call_model(base: Analyzer) -> None:
    client = FakeClient(answer())
    layer = TinyLlmClassifier("m", client=client)
    assert layer.refine(AnalyzerInput([]), base.analyze(AnalyzerInput([]))) is None
    assert client.payloads == []


def test_long_prompt_is_clipped_before_sending() -> None:
    assert len(prompt_text(msg("x" * 50000))) < 2100
