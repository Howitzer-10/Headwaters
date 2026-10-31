"""HDW-140 (D12): ``task_hint`` and ``reasoning`` on ``RouteRequest`` (pure helpers)."""

from __future__ import annotations

from dataclasses import replace

import pytest

from headwaters.core.executor.types import ExecOptions
from headwaters.core.router_prep import apply_request_hints, executor_options
from headwaters.core.router_types import RouteRequest
from tests.router_support import MESSAGES, requirements


def test_task_hint_forces_the_task_only() -> None:
    reqs = requirements("code")
    out = apply_request_hints(reqs, RouteRequest(MESSAGES, task_hint="reasoning"))
    assert out.soft.task == "reasoning"
    assert out.hard == reqs.hard  # the hard filter is untouched
    assert out.est_tokens == reqs.est_tokens


def test_unknown_task_hint_is_refused() -> None:
    with pytest.raises(ValueError, match="task_hint"):
        apply_request_hints(requirements(), RouteRequest(MESSAGES, task_hint="poetry"))


@pytest.mark.parametrize("value", [True, "high"])
def test_reasoning_on_adds_the_hard_requirement(value: bool | str) -> None:
    out = apply_request_hints(requirements(), RouteRequest(MESSAGES, reasoning=value))
    assert out.hard.reasoning is True
    assert out.soft.task == "code"


@pytest.mark.parametrize("value", [None, False])
def test_reasoning_off_or_unset_adds_no_requirement(value: bool | None) -> None:
    reqs = requirements()
    assert apply_request_hints(reqs, RouteRequest(MESSAGES, reasoning=value)) is reqs


def test_reasoning_maps_to_exec_options() -> None:
    assert executor_options(RouteRequest(MESSAGES, reasoning=True)).reasoning is True
    assert executor_options(RouteRequest(MESSAGES, reasoning="low")).reasoning == "low"
    assert executor_options(RouteRequest(MESSAGES)).reasoning is None


def test_explicit_options_keep_their_reasoning_and_gain_the_request_value() -> None:
    explicit = ExecOptions(reasoning="high", max_tokens=5)
    assert executor_options(RouteRequest(MESSAGES, options=explicit, reasoning=True)) is explicit
    bare = ExecOptions(max_tokens=5)
    merged = executor_options(RouteRequest(MESSAGES, options=bare, reasoning=True))
    assert merged == replace(bare, reasoning=True)


def test_options_reasoning_on_also_requires_a_capable_model() -> None:
    request = RouteRequest(MESSAGES, options=ExecOptions(reasoning=True))
    assert apply_request_hints(requirements(), request).hard.reasoning is True
