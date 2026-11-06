"""HDW-205: headwaters_status, and the schemas and annotations of the new tools."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from headwaters.core.features import FeatureSpec
from headwaters.core.plugins import CURRENT_API_VERSION
from headwaters.core.quotas import model_subject
from headwaters.core.rules import Rules
from headwaters.core.state_limits import record_wall
from headwaters.mcp.tools import descriptions
from tests.frontdoor_support import Rig, make_repo, structured, text_of

NEW_TOOLS = (
    "headwaters_think",
    "headwaters_status",
    "headwaters_scope",
    "headwaters_handoff",
    "headwaters_more",
    *descriptions.ADVANCED,
)


class Dashboard:
    api_version = CURRENT_API_VERSION

    def render_summary(self) -> Mapping[str, object]:
        return {"saved_usd": 1.25}


def _all_tools(tmp_path: Path) -> dict[str, Any]:
    async def go() -> dict[str, Any]:
        host: Any = {"headwaters/client": "x"}
        async with Rig(tmp_path).client() as client:
            await client.call_tool("headwaters_more", {}, meta=host)
            listed = await client.list_tools(meta=host, cache_mode="bypass")
            return {t.name: t for t in listed.tools}

    return asyncio.run(go())


def test_status_shows_scope_locks_walls_gpu_and_the_warm_model(tmp_path: Path) -> None:
    rig = Rig(tmp_path, rules=Rules(private_lock=True))
    record_wall(rig.db, model_subject("vendor/x"), "exhausted", until=time.time() + 120)
    result = asyncio.run(rig.call("headwaters_status", {}))
    data = structured(result)
    assert data["scope"]["effective"] == "local"
    assert "private_lock" in data["scope"]["locks"]
    assert data["walls"][0]["kind"] == "exhausted"
    assert 0 < data["walls"][0]["eta_s"] <= 120
    assert data["gpu"]["total_gb"] == 16.0
    assert [m["model"] for m in data["warm_models"]] == ["qwen:14b"]
    assert "Scope: local" in text_of(result)
    assert "Walls:" in text_of(result)


def test_status_shows_the_project_rule_from_the_callers_folder(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "proj", {".headwaters.yaml": "privacy: local\n"})
    rig = Rig(tmp_path)
    data = structured(asyncio.run(rig.call("headwaters_status", {}, repo)))
    assert data["scope"]["project_rule"] == "local"
    assert data["scope"]["effective"] == "local"  # the project rule tightens the default
    assert "project_rule" in data["scope"]["locks"]


def test_status_shows_no_savings_when_the_ledger_is_disabled(tmp_path: Path) -> None:
    """HDW-501: the ledger ships with the product, so no savings means the flag is off."""
    rig = Rig(tmp_path, rules=Rules(features={"ledger": False}))
    data = structured(asyncio.run(rig.call("headwaters_status", {})))
    assert data["savings"] is None


def test_status_shows_savings_when_a_ledger_dashboard_is_registered(tmp_path: Path) -> None:
    rig = Rig(tmp_path, rules=Rules(features={"ledger": False}))
    spec = FeatureSpec("stub_dashboard", True, "Stub", ("DashboardPlugin",))
    rig.engine.registry.register(spec, {"DashboardPlugin": Dashboard()})
    data = structured(asyncio.run(rig.call("headwaters_status", {})))
    assert data["savings"] == {"saved_usd": 1.25}


def test_status_without_an_engine_still_answers_from_state(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.context.engine = lambda: None
    data = structured(asyncio.run(rig.call("headwaters_status", {})))
    assert data["ok"] is True
    assert any("loading" in n for n in data["notes"])


@pytest.mark.parametrize("name", NEW_TOOLS)
def test_every_new_tool_has_an_output_schema_and_annotations(tmp_path: Path, name: str) -> None:
    tool = _all_tools(tmp_path)[name]
    assert tool.output_schema is not None
    assert tool.output_schema["type"] == "object"
    assert tool.annotations is not None
    assert tool.annotations.destructive_hint is False
    assert "Example" in tool.description or name == "headwaters_think"
    assert len(tool.description.split()) <= 120


def test_annotations_follow_the_b1_table_for_the_new_tools(tmp_path: Path) -> None:
    tools = _all_tools(tmp_path)
    read_only = (
        "headwaters_think",
        "headwaters_status",
        "headwaters_more",
        "headwaters_quota_status",
    )
    for name in read_only:
        assert tools[name].annotations.read_only_hint is True, name
    for name in ("headwaters_scope", "headwaters_handoff", "headwaters_pin_model"):
        assert tools[name].annotations.read_only_hint is False, name
    assert tools["headwaters_scope"].annotations.idempotent_hint is True


def test_error_results_still_match_the_output_schema(tmp_path: Path) -> None:
    rig = Rig(tmp_path, rules=Rules(default_scope="local"))
    result = asyncio.run(rig.call("headwaters_scope", {"scope": "auto"}))
    schema = _all_tools(tmp_path)["headwaters_scope"].output_schema
    jsonschema.validate(result.structured_content, schema)
    assert result.is_error is True
