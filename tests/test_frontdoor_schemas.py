"""HDW-204: the tools' schemas, annotations and descriptions, seen through a real MCP client."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from headwaters.mcp.tools import descriptions
from tests.frontdoor_support import CODE_TASK, Rig, structured, text_of

TOOLS = ("headwaters_do", "headwaters_ask", "headwaters_check")


def _listed(tmp_path: Path) -> dict[str, Any]:
    async def go() -> dict[str, Any]:
        async with Rig(tmp_path).client() as client:
            return {t.name: t for t in (await client.list_tools()).tools}

    return asyncio.run(go())


def test_the_three_front_door_tools_are_listed(tmp_path: Path) -> None:
    assert sorted(_listed(tmp_path)) == sorted(TOOLS)


@pytest.mark.parametrize("name", TOOLS)
def test_every_tool_declares_an_output_schema_and_annotations(tmp_path: Path, name: str) -> None:
    tool = _listed(tmp_path)[name]
    assert tool.output_schema is not None
    assert tool.output_schema["type"] == "object"
    assert tool.annotations is not None
    assert tool.annotations.destructive_hint is False
    assert tool.description


def test_annotations_follow_the_b1_table(tmp_path: Path) -> None:
    tools = _listed(tmp_path)
    assert tools["headwaters_do"].annotations.read_only_hint is False
    assert tools["headwaters_do"].annotations.idempotent_hint is False
    for name in ("headwaters_ask", "headwaters_check"):
        assert tools[name].annotations.read_only_hint is True
        assert tools[name].annotations.idempotent_hint is True


def test_do_output_schema_has_every_field_the_ticket_names(tmp_path: Path) -> None:
    props = _listed(tmp_path)["headwaters_do"].output_schema["properties"]
    wanted = {
        "answer", "diffs", "commands", "model", "tier", "scope", "verified", "verify_reasons",
        "below_floor", "reasons", "relay", "result_uri", "suggestions",
    }  # fmt: skip
    assert wanted <= set(props)
    assert props["relay"]["enum"] == ["verbatim", "apply_diff", "summarize_ok"]


def test_do_input_schema_matches_the_ticket_and_accepts_async(tmp_path: Path) -> None:
    schema = _listed(tmp_path)["headwaters_do"].input_schema
    assert schema["required"] == ["task"]
    assert {"task", "paths", "images", "scope", "want", "async"} <= set(schema["properties"])
    assert schema["properties"]["want"]["enum"] == ["answer", "diff", "plan"]


def test_descriptions_are_short_and_example_led() -> None:
    for text in (descriptions.DO, descriptions.ASK, descriptions.CHECK):
        assert len(text.split()) <= 120
        assert "Example:" in text
    assert "relay" in descriptions.DO
    assert "do NOT paste" in descriptions.DO


def test_a_result_validates_against_its_own_output_schema(tmp_path: Path) -> None:
    rig = Rig(tmp_path)

    async def go() -> tuple[dict[str, Any] | None, dict[str, Any]]:
        async with rig.client() as client:
            tool = next(t for t in (await client.list_tools()).tools if t.name == "headwaters_do")
            result = await client.call_tool("headwaters_do", {"task": CODE_TASK})
            return tool.output_schema, structured(result)

    schema, data = asyncio.run(go())
    assert schema is not None
    jsonschema.validate(data, schema)


def test_the_text_rendering_comes_with_the_structured_content(tmp_path: Path) -> None:
    result = asyncio.run(Rig(tmp_path).call("headwaters_do", {"task": CODE_TASK}))
    assert result.is_error is False
    text = text_of(result)
    assert "qwen:14b" in text
    assert "relay:" in text
    assert len(text) < 2000


def test_a_bad_argument_is_reported_by_the_sdk_not_as_a_crash(tmp_path: Path) -> None:
    result = asyncio.run(Rig(tmp_path).call("headwaters_do", {"task": "x", "want": "poem"}))
    assert result.is_error is True
    assert "want" in text_of(result)
