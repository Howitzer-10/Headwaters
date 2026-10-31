"""Fixture prompts for HDW-106, part B: JSON, chat, reasoning, duck, sizing."""

from __future__ import annotations

from headwaters.core.analyzer import AnalyzerInput
from tests.analyzer_fixtures_a import Fixture, case, user

FIXTURES_B: list[Fixture] = [
    (
        "json_object_format",
        AnalyzerInput(
            [user("Extract the name and date from: 'Bob, 2026-01-02'")],
            response_format={"type": "json_object"},
        ),
        case(json=True, task="extract"),
    ),
    (
        "json_schema_format",
        AnalyzerInput(
            [user("parse this invoice")],
            response_format={"type": "json_schema", "json_schema": {"name": "inv", "schema": {}}},
        ),
        case(json=True, task="extract"),
    ),
    (
        "json_text_format_is_not_json",
        AnalyzerInput([user("hello")], response_format={"type": "text"}),
        case(json=False),
    ),
    (
        "json_extract_no_format",
        AnalyzerInput([user("Extract all emails from this text and parse them")]),
        case(json=False, task="extract"),
    ),
    (
        "chat_greeting",
        AnalyzerInput([user("hi, how are you today?")]),
        case(
            task="chat",
            agentic=False,
            tools=False,
            vision=False,
            json=False,
            risk="low",
            complexity=1,
            confidence_ok=True,
        ),
    ),
    (
        "chat_summarize",
        AnalyzerInput([user("Summarize this article in three bullets: lorem ipsum")]),
        case(task="summarize"),
    ),
    (
        "chat_creative",
        AnalyzerInput([user("write a poem about autumn")]),
        case(task="creative"),
    ),
    (
        "chat_long_vague_is_ambiguous",
        AnalyzerInput([user("I was thinking about stuff and things around the house. " * 40)]),
        case(task="chat", confidence_ok=False),
    ),
    (
        "reasoning_proof",
        AnalyzerInput([user("Prove that the square root of 2 is irrational")]),
        case(task="reasoning", reasoning_hint=True),
    ),
    (
        "reasoning_architecture",
        AnalyzerInput([user("Design an architecture for a multi-tenant billing system")]),
        case(reasoning_hint=True, min_complexity=2),
    ),
    (
        "reasoning_security_audit",
        AnalyzerInput([user("do a security audit of our login flow")]),
        case(reasoning_hint=True, risk="medium"),
    ),
    (
        "risk_high",
        AnalyzerInput([user("delete the production credentials table in the migration")]),
        case(risk="high"),
    ),
    (
        "duck_help_me_think",
        AnalyzerInput([user("help me think through why my cache keeps going stale")]),
        case(duck_hint=True),
    ),
    (
        "duck_why_does",
        AnalyzerInput([user("why does my loop print twice?")]),
        case(duck_hint=True, reasoning_hint=False),
    ),
    (
        "duck_explain_my_bug",
        AnalyzerInput([user("explain my bug:\n```js\nlet x = [] + {}\n```")]),
        case(duck_hint=True, languages=("javascript",), task="code"),
    ),
    (
        "no_duck_plain_task",
        AnalyzerInput([user("write a function that adds two numbers")]),
        case(duck_hint=False),
    ),
    (
        "min_ctx_includes_max_output",
        AnalyzerInput([user("hello")], max_output_tokens=4000),
        case(min_ctx_at_least=4000),
    ),
    (
        "huge_prompt_complexity_up",
        AnalyzerInput([user("lorem ipsum dolor " * 3000)]),
        case(min_complexity=3),
    ),
]
