"""JSON Schema and tool-call argument validation for verification (HDW-116).

Violations are reported by path and keyword only; the offending data is never echoed.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from headwaters.core.verify_fences import Block
from headwaters.core.verify_types import Collector

MAX_SCHEMA_ERRORS = 5


def _validate(
    out: Collector, check: str, code: str, schema: Mapping[str, Any], data: Any, prefix: str = ""
) -> None:
    errors = sorted(
        Draft202012Validator(schema).iter_errors(data),
        key=lambda e: [str(p) for p in e.absolute_path],
    )
    for error in errors[:MAX_SCHEMA_ERRORS]:
        path = "/".join(str(p) for p in error.absolute_path) or "(root)"
        out.fail(check, code, f"{prefix}{path}: failed '{error.validator}' constraint")


def check_json_schema(
    out: Collector, text: str, blocks: Sequence[Block], schema: Mapping[str, Any]
) -> None:
    """Validate the JSON in the candidate (first closed json fence, else the whole text)."""
    out.ran("json")
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        out.fail("json", "json_schema_invalid", f"the supplied schema is invalid: {exc.message}")
        return
    raw = next((b.body for b in blocks if b.closed and b.language == "json"), None)
    if raw is None:
        stripped = text.strip()
        if not stripped.startswith(("{", "[")):
            out.fail("json", "json_not_found", "no JSON found in the output")
            return
        raw = stripped
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        if not out.has_code("json_invalid"):
            out.fail("json", "json_invalid", exc.msg, exc.lineno)
        return
    _validate(out, "json", "json_schema_violation", schema, data)


def _tool_schemas(tools: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    schemas: dict[str, Mapping[str, Any]] = {}
    for tool in tools:
        function = tool.get("function")
        spec = function if isinstance(function, Mapping) else tool
        name = spec.get("name")
        if isinstance(name, str):
            schemas[name] = spec.get("parameters") or spec.get("input_schema") or {}
    return schemas


def check_tool_calls(
    out: Collector,
    tool_calls: Sequence[Mapping[str, Any]],
    tools: Sequence[Mapping[str, Any]] | None,
) -> None:
    """Valid tool name (when ``tools`` is given), arguments are JSON and match the tool schema.

    Accepts OpenAI (``function.name/arguments`` string) and Anthropic (``name/input``) shapes.
    """
    out.ran("tool_calls")
    schemas = _tool_schemas(tools) if tools is not None else {}
    for index, call in enumerate(tool_calls):
        function = call.get("function")
        spec = function if isinstance(function, Mapping) else call
        name = spec.get("name")
        label = f"tool call {index}"
        if not isinstance(name, str) or not name:
            out.fail("tool_calls", "tool_name_missing", f"{label} has no tool name")
            continue
        if tools is not None and name not in schemas:
            out.fail("tool_calls", "tool_unknown", f"{label}: unknown tool {name!r}")
            continue
        arguments = spec.get("arguments", spec.get("input", {}))
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments) if arguments.strip() else {}
            except json.JSONDecodeError as exc:
                out.fail(
                    "tool_calls",
                    "tool_args_invalid_json",
                    f"{label} ({name}): arguments are not valid JSON: {exc.msg}",
                )
                continue
        if not isinstance(arguments, Mapping):
            out.fail(
                "tool_calls",
                "tool_args_not_object",
                f"{label} ({name}): arguments must be an object",
            )
            continue
        schema = schemas.get(name)
        if not schema:
            continue
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError:
            out.skip("tool_calls", f"{name}: tool schema is invalid, arguments not checked")
            continue
        _validate(out, "tool_calls", "tool_args_schema", schema, arguments, f"{label} ({name}) ")
