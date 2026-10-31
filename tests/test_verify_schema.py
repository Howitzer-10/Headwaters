from __future__ import annotations

from typing import Any

import pytest

from headwaters.core.verify_basic import BasicVerifier

SCHEMA = {
    "type": "object",
    "required": ["name", "n"],
    "properties": {"name": {"type": "string"}, "n": {"type": "integer"}},
}
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "parameters": {
                "type": "object",
                "required": ["path"],
                "properties": {"path": {"type": "string"}},
                "additionalProperties": False,
            },
        },
    },
    {"name": "noop", "input_schema": {"type": "object"}},
]


def codes(**kw: Any) -> list[str]:
    text = kw.pop("text", "")
    return [r.code for r in BasicVerifier().verify_report(text, **kw).reasons]


def test_json_schema_pass() -> None:
    assert codes(text='{"name": "a", "n": 1}', json_schema=SCHEMA) == []
    assert codes(text='```json\n{"name": "a", "n": 1}\n```', json_schema=SCHEMA) == []


def test_json_schema_violation_reports_path_not_value() -> None:
    report = BasicVerifier().verify_report('{"name": "a", "n": "secret-x"}', json_schema=SCHEMA)
    assert [r.code for r in report.reasons] == ["json_schema_violation"]
    assert "n:" in report.reasons[0].message
    assert "secret-x" not in report.reasons[0].message


def test_json_schema_missing_invalid_and_bad_schema() -> None:
    assert codes(text="just words", json_schema=SCHEMA) == ["json_not_found"]
    assert codes(text='{"name": ', json_schema=SCHEMA) == ["json_invalid"]
    assert codes(text="{}", json_schema={"type": 12}) == ["json_schema_invalid"]


def test_fenced_json_syntax_error_reported_once() -> None:
    assert codes(text='```json\n{"name": }\n```', json_schema=SCHEMA) == ["json_invalid"]


def test_tool_calls_valid_openai_and_anthropic() -> None:
    calls = [
        {"function": {"name": "read_file", "arguments": '{"path": "a.py"}'}},
        {"name": "noop", "input": {}},
    ]
    assert codes(tool_calls=calls, tools=TOOLS) == []


@pytest.mark.parametrize(
    ("call", "expected"),
    [
        ({"function": {"name": "delete_all", "arguments": "{}"}}, "tool_unknown"),
        ({"function": {"name": "read_file", "arguments": "{oops"}}, "tool_args_invalid_json"),
        ({"function": {"name": "read_file", "arguments": "[1]"}}, "tool_args_not_object"),
        ({"function": {"name": "read_file", "arguments": "{}"}}, "tool_args_schema"),
        ({"function": {"name": "read_file", "arguments": '{"path": 3}'}}, "tool_args_schema"),
        (
            {"function": {"name": "read_file", "arguments": '{"path": "a", "x": 1}'}},
            "tool_args_schema",
        ),
        ({"function": {"arguments": "{}"}}, "tool_name_missing"),
    ],
)
def test_tool_calls_invalid(call: dict[str, Any], expected: str) -> None:
    assert codes(tool_calls=[call], tools=TOOLS) == [expected]


def test_tool_calls_without_tool_list_only_check_arguments() -> None:
    assert codes(tool_calls=[{"function": {"name": "any", "arguments": '{"a": 1}'}}]) == []
    assert codes(tool_calls=[{"function": {"name": "any", "arguments": "{"}}]) == [
        "tool_args_invalid_json"
    ]


def test_invalid_tool_schema_is_skipped_not_failed() -> None:
    tools = [{"name": "t", "input_schema": {"type": 12}}]
    report = BasicVerifier().verify_report("", tool_calls=[{"name": "t", "input": {}}], tools=tools)
    assert report.passed
    assert next(c for c in report.checks if c.name == "tool_calls").detail
