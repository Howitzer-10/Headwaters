"""HDW-511: the honest context error, and opt-in pruning so a local model can fit."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from headwaters.core.analyzer.requirements import (
    Analyzer,
    AnalyzerInput,
    Requirements,
)
from headwaters.core.policy.filters import Candidate
from headwaters.core.policy.tool_budget import PRUNE_KEY, tool_name
from headwaters.core.router_plan import RoutePlan
from headwaters.core.router_types import RouteRequest
from headwaters.core.rules import Rules, ToolsRules
from headwaters.core.scope import ScopeError
from tests.router_rig import Rig
from tests.router_support import MESSAGES, OK, candidate, registry_with, requirements
from tests.test_policy_scoring import fit

LOCAL_CTX = 65_536
TOOL_HEAVY = 57_794  # est_tokens of the recorded dogfood request
NEEDED = 69_353  # ceil(1.2 * 57,794): what a model must hold with ctx_headroom
FRONT_DOOR = ("do", "ask", "check", "think", "status", "scope", "more", "jobs")


def spec(name: str, size: int) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": "x" * size,
            "parameters": {"type": "object", "properties": {}},
        },
    }


def tools() -> tuple[dict[str, Any], ...]:
    """109 host-server schemas plus the 8 headwaters ones, like the recorded request."""
    host = tuple(spec(f"summer_tool_{n}", 1_200) for n in range(109))
    front = tuple(spec(f"headwaters_{name}", 60) for name in FRONT_DOOR)
    return host + front


def front_door_specs(found: tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    return [s for s in found if tool_name(s).startswith("headwaters_")]


def reqs_for(total: int) -> Requirements:
    base = requirements()
    return replace(base, hard=replace(base.hard, tools=True, min_ctx=total), est_tokens=total)


class ToolAnalyzer:
    """Requirements that fall once the heavy tool schemas are gone, like the real analyzer.

    ``heavy_while`` names the tool prefixes that keep the request heavy; ``None`` keeps it
    heavy for any tools at all, which is the "pruning cannot help" case.
    """

    def __init__(
        self, heavy: Requirements, light: Requirements, heavy_while: str | None = None
    ) -> None:
        self.heavy = heavy
        self.light = light
        self.heavy_while = heavy_while
        self.inputs: list[AnalyzerInput] = []
        self._real = Analyzer()

    def analyze(self, inp: AnalyzerInput) -> Requirements:
        self.inputs.append(inp)
        names = [tool_name(s) for s in inp.tools or ()]
        if not names:
            return self.light
        if self.heavy_while is None:
            return self.heavy
        return self.heavy if any(n.startswith(self.heavy_while) for n in names) else self.light

    def estimate_tokens(self, chunks: Iterable[str], code_extra_chars: int, images: int) -> int:
        return self._real.estimate_tokens(chunks, code_extra_chars, images)


def pool_for(reqs: Requirements) -> list[Candidate]:
    """A local model that only fits once the request is small enough for its window."""
    usable = reqs.hard.min_ctx <= 10_000
    local = candidate(
        "loc",
        "local",
        80.0,
        fit=fit("fits" if usable else "no_fit", "ok" if usable else "context_too_large"),
    )
    stripped = replace(local, row=replace(local.row, ctx=LOCAL_CTX))
    return [stripped, candidate("cloud", "paid", 85.0), candidate("free", "free_cloud", 82.0)]


def build(tmp_path: Path, *, prune: bool, analyzer: Any) -> Rig:
    rules = Rules().model_copy(update={"tools": ToolsRules(prune_to_fit=prune)})
    return Rig(
        tmp_path,
        {"loc": OK, "cloud": OK, "free": OK},
        [],
        rules=rules,
        analyzer=analyzer,
        candidates_for=pool_for,
        registry=registry_with(paid=True),
    )


def plan(rig: Rig, **kw: Any) -> RoutePlan:
    return asyncio.run(rig.router.plan(RouteRequest(messages=MESSAGES, **kw)))


def forwarded(rig: Rig, model: str) -> list[Mapping[str, Any]]:
    return list(rig.dispatch.backends[model].requests[0].tools or ())


def test_context_shortfall_is_explained_honestly(tmp_path: Path) -> None:
    rig = build(
        tmp_path,
        prune=False,
        analyzer=ToolAnalyzer(reqs_for(TOOL_HEAVY), reqs_for(1_000)),
    )
    with pytest.raises(ScopeError) as caught:
        plan(rig, tools=tools(), scope="local")
    message = caught.value.message
    assert caught.value.code == "local_unavailable"
    assert "no local model can" in message
    assert f"{TOOL_HEAVY:,} tokens" in message
    assert f"{NEEDED:,}" in message
    assert f"{LOCAL_CTX:,}" in message
    assert "summer" in message
    assert "headwaters" in message
    assert f"{PRUNE_KEY}: true" in message
    assert "headwaters/auto" in message
    assert "pull" not in message.lower()
    assert "pull" not in caught.value.fix_hint.lower()
    assert rig.dispatch.calls == []


def test_the_message_composition_adds_up_to_the_estimate(tmp_path: Path) -> None:
    rig = build(
        tmp_path,
        prune=False,
        analyzer=ToolAnalyzer(reqs_for(TOOL_HEAVY), reqs_for(1_000)),
    )
    with pytest.raises(ScopeError) as caught:
        plan(rig, tools=tools(), scope="local")
    found = re.search(
        r"tools ([\d,]+) tokens \(\d+ schemas\), message/system ([\d,]+), images ([\d,]+)",
        caught.value.message,
    )
    assert found is not None
    parts = [int(group.replace(",", "")) for group in found.groups()]
    assert sum(parts) == TOOL_HEAVY
    assert parts[0] > parts[1] > 0  # tool schemas dominate this request
    assert parts[2] == 0


def test_prune_off_forwards_every_tool_unchanged(tmp_path: Path) -> None:
    rig = build(tmp_path, prune=False, analyzer=ToolAnalyzer(reqs_for(2_000), reqs_for(1_000)))
    result = rig.run(tools=tools(), scope="local")
    assert result.decision.model == "loc"
    assert forwarded(rig, "loc") == list(tools())
    assert not any("pruned" in reason for reason in result.reasons)


def test_prune_on_drops_only_non_headwaters_namespaces(tmp_path: Path) -> None:
    analyzer = ToolAnalyzer(reqs_for(TOOL_HEAVY), reqs_for(2_000), heavy_while="summer")
    rig = build(tmp_path, prune=True, analyzer=analyzer)
    result = rig.run(tools=tools(), scope="local")
    assert result.decision.model == "loc"
    assert forwarded(rig, "loc") == front_door_specs(tools())
    assert any("tools pruned to fit a local model" in reason for reason in result.reasons)
    assert any("summer" in reason and "tokens" in reason for reason in result.reasons)
    # the pruned request is what the local model is asked to answer
    assert analyzer.inputs[-1].tools == tuple(front_door_specs(tools()))


def test_prune_on_shows_the_note_in_a_plan(tmp_path: Path) -> None:
    analyzer = ToolAnalyzer(reqs_for(TOOL_HEAVY), reqs_for(2_000), heavy_while="summer")
    rig = build(tmp_path, prune=True, analyzer=analyzer)
    found = plan(rig, tools=tools(), scope="local")
    assert found.decision.model == "loc"
    assert any("tools pruned to fit a local model" in reason for reason in found.decision.reasons)
    assert rig.dispatch.calls == []


def test_ambiguous_attribution_fails_closed(tmp_path: Path) -> None:
    rig = build(tmp_path, prune=True, analyzer=ToolAnalyzer(reqs_for(TOOL_HEAVY), reqs_for(1_000)))
    loose = (spec("alpha", 1_000), spec("beta", 1_000))
    with pytest.raises(ScopeError) as caught:
        plan(rig, tools=loose, scope="local")
    assert "could not be attributed to servers" in caught.value.message
    assert "prune-to-fit cannot act" in caught.value.message
    assert rig.dispatch.calls == []


def test_nothing_fits_even_after_pruning(tmp_path: Path) -> None:
    rig = build(tmp_path, prune=True, analyzer=ToolAnalyzer(reqs_for(TOOL_HEAVY), reqs_for(1_000)))
    request = RouteRequest(messages=MESSAGES, tools=tools(), scope="local")
    with pytest.raises(ScopeError) as caught:
        asyncio.run(rig.router.plan(request))
    assert f"{PRUNE_KEY} is already on and cannot help here" in caught.value.message
    assert len(request.tools or ()) == 117  # the caller's request is untouched
    assert rig.dispatch.calls == []


@pytest.mark.parametrize("scope_name", ["free", "cloud"])
def test_free_and_cloud_tiers_never_prune(tmp_path: Path, scope_name: str) -> None:
    analyzer = ToolAnalyzer(reqs_for(TOOL_HEAVY), reqs_for(1_000))
    rig = build(tmp_path, prune=True, analyzer=analyzer)
    result = rig.run(tools=tools(), scope=scope_name)  # type: ignore[arg-type]
    assert result.decision.tier != "local"
    assert forwarded(rig, result.decision.model) == list(tools())
    assert not any("pruned" in reason for reason in result.reasons)


def test_images_alone_over_the_window_are_not_pruned(tmp_path: Path) -> None:
    rig = build(tmp_path, prune=True, analyzer=ToolAnalyzer(reqs_for(200_000), reqs_for(2_000)))
    pictures = [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}] * 100
    request = RouteRequest(
        messages=({"role": "user", "content": pictures},), tools=tools(), scope="local"
    )
    with pytest.raises(ScopeError) as caught:
        asyncio.run(rig.router.plan(request))
    found = re.search(r"images ([\d,]+)", caught.value.message)
    assert found is not None
    assert int(found.group(1).replace(",", "")) > LOCAL_CTX
    assert f"{PRUNE_KEY} is already on and cannot help here" in caught.value.message
    assert len(request.tools or ()) == 117  # nothing was dropped
    assert rig.dispatch.calls == []
