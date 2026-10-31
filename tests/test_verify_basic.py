from __future__ import annotations

import pytest

from headwaters.core.catalog.schema import NamedPattern
from headwaters.core.plugins import check_plugin
from headwaters.core.verify_basic import BasicVerifier

ALIASES = {"py": "python", "ts": "typescript", "js": "javascript", "rs": "rust", "c++": "cpp"}


@pytest.fixture
def verifier() -> BasicVerifier:
    # Test data only: the real patterns live in catalog/seed_catalog.json.
    patterns = [NamedPattern(name="todo-marker", regex=r"\bTODO\b")]
    return BasicVerifier(patterns, fence_aliases=ALIASES)


def fenced(lang: str, body: str) -> str:
    return f"Here you go:\n```{lang}\n{body}\n```\n"


def test_is_a_verifier_plugin(verifier: BasicVerifier) -> None:
    check_plugin("VerifierPlugin", verifier)
    assert verifier.verify("plain prose") == []
    assert verifier.verify(fenced("python", "def f(:")) != []


GOOD = {
    "python": "def f(x):\n    return x + 1",
    "javascript": "const f = (x) => x + 1;",
    "typescript": "const f = (x: number): number => x + 1;",
    "go": "package main\n\nfunc f(x int) int { return x + 1 }",
    "rust": "fn f(x: i32) -> i32 { x + 1 }",
    "java": "class A { int f(int x) { return x + 1; } }",
    "csharp": "class A { int F(int x) { return x + 1; } }",
    "c": "int f(int x) { return x + 1; }",
    "cpp": "int f(int x) { return x + 1; }",
    "json": '{"a": [1, 2]}',
    "yaml": "a:\n  - 1\n  - 2",
}
BAD = {
    "python": "def f(:\n    return",
    "javascript": "const f = (x => {;",
    "typescript": "const f = (x: number: number => ;",
    "go": "package main\n\nfunc f( { ",
    "rust": "fn f( -> { let = ;",
    "java": "class A { int f( { }",
    "csharp": "class A { int F( { }",
    "c": "int f(int x { return ;",
    "cpp": "int f(int x { return ;",
    "json": '{"a": [1, 2,]}',
    "yaml": "a: [1, 2\nb: {",
}


@pytest.mark.parametrize("lang", sorted(GOOD))
def test_parse_passes_valid_code(verifier: BasicVerifier, lang: str) -> None:
    report = verifier.verify_report(fenced(lang, GOOD[lang]))
    assert report.passed, report.reasons


@pytest.mark.parametrize("lang", sorted(BAD))
def test_parse_fails_broken_code(verifier: BasicVerifier, lang: str) -> None:
    report = verifier.verify_report(fenced(lang, BAD[lang]))
    assert not report.passed
    assert report.reasons[0].code in {"syntax_error", "json_invalid"}
    assert report.reasons[0].line is not None


def test_fence_alias_and_line_number(verifier: BasicVerifier) -> None:
    report = verifier.verify_report(fenced("py", "x = 1\ny = (\n"))
    assert [r.code for r in report.reasons] == ["syntax_error"]
    assert report.reasons[0].line is not None


def test_python_uses_ast_for_semantic_syntax_errors(verifier: BasicVerifier) -> None:
    # tree-sitter accepts this call; ast.parse rejects it.
    report = verifier.verify_report(fenced("python", "f(a=1, 2)"))
    assert [r.code for r in report.reasons] == ["syntax_error"]


def test_unknown_language_is_not_parsed(verifier: BasicVerifier) -> None:
    assert verifier.verify_report(fenced("brainfuck", "+++[->+<")).passed


def test_unfenced_code_with_language_hint(verifier: BasicVerifier) -> None:
    broken = verifier.verify_report("def f(:\n", language="python")
    assert [r.code for r in broken.reasons] == ["syntax_error"]
    assert verifier.verify_report("def f(): pass", language="python").passed


def test_indented_python_snippet(verifier: BasicVerifier) -> None:
    assert verifier.verify_report(fenced("python", "    x = 1\n    y = 2")).passed
