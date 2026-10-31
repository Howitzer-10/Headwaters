from __future__ import annotations

import json
from pathlib import Path

import pytest

from headwaters.core.catalog.schema import NamedPattern
from headwaters.core.verify_basic import BasicVerifier, normalize_eol

# Test data only: the real patterns live in catalog/seed_catalog.json.
PATTERNS = [
    NamedPattern(name="todo-marker", regex=r"\bTODO\b"),
    NamedPattern(name="rest-of-code", regex=r"(?i)\.\.\.\s*rest of (the )?code"),
    NamedPattern(name="existing-code", regex=r"(?i)(//|#)\s*existing code"),
]


@pytest.fixture
def verifier() -> BasicVerifier:
    return BasicVerifier(PATTERNS)


def fenced(lang: str, body: str) -> str:
    return f"Here you go:\n```{lang}\n{body}\n```\n"


def codes(verifier: BasicVerifier, text: str) -> set[str]:
    return {r.code for r in verifier.verify_report(text).reasons}


@pytest.mark.parametrize(
    "body",
    ["def f():\n    # TODO implement", "def f():\n    ... rest of code", "x = 1\n# existing code"],
)
def test_placeholder_scan_fails(verifier: BasicVerifier, body: str) -> None:
    report = verifier.verify_report(fenced("python", body))
    assert "placeholder" in {r.code for r in report.reasons}
    assert "'" in report.reasons[0].message  # names the pattern, not the text


def test_placeholder_scan_ignores_prose_and_clean_code(verifier: BasicVerifier) -> None:
    assert verifier.verify_report("TODO: later, I will explain.").passed
    assert verifier.verify_report(fenced("python", "x = 1")).passed


def test_no_patterns_skips_the_scan() -> None:
    report = BasicVerifier().verify_report(fenced("python", "# TODO"))
    assert report.passed
    assert next(c for c in report.checks if c.name == "placeholders").status == "skipped"


def test_truncated_fence(verifier: BasicVerifier) -> None:
    report = verifier.verify_report("```python\ndef f():\n    return 1\n")
    assert [r.code for r in report.reasons] == ["truncated_fence"]
    assert report.reasons[0].line == 1


def test_longer_fence_and_tilde_fences(verifier: BasicVerifier) -> None:
    assert verifier.verify_report("````md\n```python\nx = 1\n```\n````\n").passed
    assert verifier.verify_report("~~~python\nx = 1\n~~~\n").passed
    assert codes(verifier, "~~~python\nx = 1\n```\n") == {"truncated_fence"}


def test_report_never_contains_candidate_text(verifier: BasicVerifier) -> None:
    secret = "sk-SUPERSECRETVALUE123"
    report = verifier.verify_report(fenced("python", f"x = '{secret}'\n# TODO"))
    assert not report.passed
    assert secret not in json.dumps(report.to_dict())


def test_crlf_candidate_is_normalized(verifier: BasicVerifier) -> None:
    text = fenced("python", "def f():\n    return 1").replace("\n", "\r\n")
    assert verifier.verify_report(text).passed
    assert normalize_eol("a\r\nb\rc\n") == "a\nb\nc\n"


def test_from_catalog_uses_seed_patterns() -> None:
    from headwaters.core.catalog.loader import SEED_RELATIVE_PATH, load_catalog

    root = Path(__file__).resolve().parents[1]
    seeded = BasicVerifier.from_catalog(load_catalog(root / SEED_RELATIVE_PATH))
    assert "placeholder" in codes(seeded, fenced("python", "key = 'YOUR_API_KEY'"))
    assert seeded.verify_report(fenced("py", "x = 1")).passed
