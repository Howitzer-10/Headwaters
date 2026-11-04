"""HDW-511: tool attribution and the composition arithmetic behind the context message."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from typing import Any

import pytest

from headwaters.core.analyzer.requirements import AnalyzerInput
from headwaters.core.policy.tool_budget import (
    PRUNE_KEY,
    AmbiguousTools,
    Composition,
    attribute,
    diagnose,
    measure,
    tool_name,
)
from tests.router_support import requirements


class Chars:
    """A token estimator with no model behind it: four characters to the token."""

    def estimate_tokens(self, chunks: Any, code_extra_chars: int, images: int) -> int:
        return sum(len(chunk) for chunk in chunks) // 4 + code_extra_chars // 4 + images * 500


def spec(name: str, size: int) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": "x" * size,
            "parameters": {"type": "object", "properties": {}},
        },
    }


def test_tool_name_reads_openai_and_anthropic_shapes() -> None:
    assert tool_name(spec("summer_add", 0)) == "summer_add"
    assert tool_name({"name": "summer_add"}) == "summer_add"
    assert tool_name({}) == ""


def test_attribution_splits_on_a_double_underscore() -> None:
    found = attribute(["summer__add_node", "headwaters__do"])
    assert found == {"summer__add_node": "summer", "headwaters__do": "headwaters"}


def test_attribution_reads_the_leading_segment_of_flattened_names() -> None:
    found = attribute(["summer_add_node", "summer_write_file", "headwaters_do", "headwaters_scope"])
    assert found["summer_add_node"] == found["summer_write_file"] == "summer"
    assert found["headwaters_do"] == found["headwaters_scope"] == "headwaters"


def test_attribution_of_one_server_reports_its_shared_prefix() -> None:
    found = attribute(["summer_add_node", "summer_write_file"])
    assert set(found.values()) == {"summer"}


def test_attribution_refuses_a_name_with_no_prefix() -> None:
    with pytest.raises(AmbiguousTools):
        attribute(["summer_add_node", "summer_write_file", "status"])


def test_attribution_refuses_a_lone_tool_among_groups() -> None:
    with pytest.raises(AmbiguousTools):
        attribute(["summer_add", "summer_write", "headwaters_do", "headwaters_scope", "solo_tool"])


def test_measure_splits_the_estimate_into_parts_that_add_up() -> None:
    tools = (
        spec("summer_add_node", 4_000),
        spec("summer_write_file", 4_000),
        spec("headwaters_do", 200),
        spec("headwaters_scope", 200),
    )
    inp = AnalyzerInput(messages=[{"role": "user", "content": "hi"}], tools=tools)
    found = measure(Chars(), inp, replace(requirements(), est_tokens=1_000))
    assert found is not None
    assert (found.total, found.images) == (1_000, 0)
    assert found.tools == found.total - found.messages - found.images
    assert found.tool_count == 4
    assert sum(tokens for _, tokens, _ in found.namespaces) == found.tools
    assert [(name, count) for name, _, count in found.namespaces] == [
        ("summer", 2),
        ("headwaters", 2),
    ]
    assert next(name for name, _ in found.largest_tools) == "summer_add_node"
    assert not found.ambiguous


def test_measure_reports_an_unattributable_tools_array_without_guessing() -> None:
    inp = AnalyzerInput(messages=[{"role": "user", "content": "hi"}], tools=[spec("alpha", 10)])
    found = measure(Chars(), inp, replace(requirements(), est_tokens=1_000))
    assert found is not None
    assert found.ambiguous
    assert found.namespaces == ()
    assert found.tool_count == 1


def composition() -> Composition:
    return Composition(
        total=57_794,
        messages=13_518,
        tools=44_271,
        images=0,
        tool_count=117,
        namespaces=(("summer", 44_271, 109), ("headwaters", 2_423, 8)),
        largest_tools=(("summer_capture_gameplay", 900),),
        ambiguous=False,
    )


def test_diagnosis_states_the_composition_and_never_suggests_a_pull() -> None:
    found = diagnose(
        composition(),
        needed=69_353,
        headroom=1.2,
        local_ctx=[65_536],
        prune_enabled=False,
        widen=True,
    )
    assert "57,794 tokens" in found.message
    assert "69,353" in found.message
    assert "65,536" in found.message
    assert "tools 44,271 tokens (117 schemas)" in found.message
    assert "message/system 13,518" in found.message
    assert "images 0" in found.message
    assert "summer 44,271 (109 tools)" in found.message
    assert f"{PRUNE_KEY}: true" in found.message
    assert "headwaters/auto" in found.message
    assert "pull" not in found.message.lower()
    assert "pull" not in found.fix_hint.lower()
    assert "headwaters/auto" in found.fix_hint


def test_diagnosis_does_not_offer_a_wider_scope_when_a_lock_keeps_it_local() -> None:
    found = diagnose(
        composition(),
        needed=69_353,
        headroom=1.2,
        local_ctx=[65_536],
        prune_enabled=True,
        widen=False,
    )
    assert "locked to local" in found.message
    assert "headwaters/auto" not in found.message
    assert PRUNE_KEY in found.message  # already on: say so instead of pretending it is off


def test_diagnosis_says_why_pruning_cannot_act_on_unattributable_tools() -> None:
    found = diagnose(
        Composition(
            total=1_000,
            messages=100,
            tools=900,
            images=0,
            tool_count=2,
            namespaces=(),
            largest_tools=(("alpha", 500), ("beta", 400)),
            ambiguous=True,
        ),
        needed=1_200,
        headroom=1.2,
        local_ctx=(),
        prune_enabled=True,
        widen=True,
    )
    assert "could not be attributed to servers" in found.message
    assert "prune-to-fit cannot act" in found.message
    assert "alpha 500" in found.message


def test_measure_returns_none_without_an_estimator() -> None:
    class Bare:
        def analyze(self, inp: AnalyzerInput) -> object:  # pragma: no cover - never called
            raise AssertionError

    tools: Sequence[Any] = [spec("summer_add", 10)]
    inp = AnalyzerInput(messages=[{"role": "user", "content": "hi"}], tools=tools)
    assert measure(Bare(), inp, requirements()) is None
