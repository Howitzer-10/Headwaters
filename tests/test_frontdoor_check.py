"""HDW-204: ``headwaters_check`` verifies a diff or code through the registered verifiers."""

from __future__ import annotations

import asyncio
import subprocess
from collections.abc import Mapping
from pathlib import Path

import pytest

from headwaters.core.features import FeatureSpec
from headwaters.core.plugins import CURRENT_API_VERSION
from tests.frontdoor_support import DIFF_ANSWER, Rig, make_repo, structured, text_of

DIFF = DIFF_ANSWER.split("```diff\n")[1].split("```")[0]
APP = "def run():\n    return 1\n"
needs_git = pytest.mark.skipif(
    subprocess.run(["git", "--version"], capture_output=True, check=False).returncode != 0,
    reason="git is not installed",
)


def _check(rig: Rig, args: Mapping[str, object], cwd: Path | None = None) -> dict[str, object]:
    return structured(asyncio.run(rig.call("headwaters_check", args, cwd)))


@needs_git
def test_a_diff_that_applies_is_verified_and_relayed(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "p", {"app.py": APP})
    data = _check(Rig(tmp_path), {"diff": DIFF}, repo)
    assert data["ok"] is True
    assert data["verified"] is True
    assert data["verify_status"] == "pass"
    assert data["relay"] == "apply_diff"
    names = {c["name"]: c["status"] for c in data["checks"]}  # type: ignore[attr-defined]
    assert names["diff"] == "pass"


@needs_git
def test_a_diff_that_does_not_apply_fails(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "p", {"app.py": "def other():\n    pass\n"})
    data = _check(Rig(tmp_path), {"diff": DIFF}, repo)
    assert data["verified"] is False
    assert data["verify_status"] == "fail"
    assert any("diff_does_not_apply" in r for r in data["verify_reasons"])  # type: ignore[attr-defined]
    assert data["relay"] == "verbatim"


def test_a_diff_without_a_workspace_says_it_was_not_test_applied(tmp_path: Path) -> None:
    data = _check(Rig(tmp_path), {"diff": DIFF})
    assert data["relay"] == "verbatim"
    assert any("not test-applied" in n for n in data["notes"])  # type: ignore[attr-defined]


def test_good_code_passes_and_broken_code_fails(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    good = _check(rig, {"code": "def f():\n    return 1\n", "language": "python"})
    assert good["verified"] is True
    bad = _check(rig, {"code": "def f(:\n    return 1\n", "language": "python"})
    assert bad["verify_status"] == "fail"
    assert any("syntax_error" in r for r in bad["verify_reasons"])  # type: ignore[attr-defined]


def test_a_placeholder_is_caught_with_the_catalog_pattern(tmp_path: Path) -> None:
    data = _check(Rig(tmp_path), {"code": "KEY = 'your_api_key'\n", "language": "python"})
    assert any("placeholder" in r for r in data["verify_reasons"])  # type: ignore[attr-defined]


def test_nothing_checkable_is_unchecked_never_verified(tmp_path: Path) -> None:
    data = _check(Rig(tmp_path), {"code": "just some prose, no code at all"})
    assert data["verified"] is False
    assert data["verify_status"] == "unchecked"
    assert any("not verified" in n for n in data["notes"])  # type: ignore[attr-defined]


def test_a_registered_verifier_plugin_runs_and_can_fail_the_check(tmp_path: Path) -> None:
    class NoFoo:
        api_version = CURRENT_API_VERSION

        def verify(self, candidate: str) -> list[str]:
            return ["no_foo: the word foo is banned"] if "foo" in candidate else []

    rig = Rig(tmp_path)
    spec = FeatureSpec("nofoo", True, "No foo", ("VerifierPlugin",))
    rig.engine.registry.register(spec, {"VerifierPlugin": NoFoo()})
    ok = _check(rig, {"code": "x = 1\n", "language": "python"})
    assert ok["verified"] is True
    bad = _check(rig, {"code": "foo = 1\n", "language": "python"})
    assert bad["verify_status"] == "fail"
    assert "no_foo: the word foo is banned" in bad["verify_reasons"]  # type: ignore[operator]


@pytest.mark.parametrize(
    ("args", "code"),
    [
        ({}, "bad_arguments"),
        ({"diff": DIFF, "code": "x"}, "bad_arguments"),
        ({"diff": "   "}, "bad_arguments"),
        ({"diff": "not a diff at all"}, "not_a_diff"),
    ],
)
def test_bad_arguments_are_a_structured_error(
    tmp_path: Path, args: dict[str, object], code: str
) -> None:
    result = asyncio.run(Rig(tmp_path).call("headwaters_check", args))
    data = structured(result)
    assert result.is_error is True
    assert data["ok"] is False
    assert data["error"]["code"] == code  # type: ignore[index]
    assert data["error"]["fix"]  # type: ignore[index]
    assert "Fix:" in text_of(result)


def test_check_calls_no_model(tmp_path: Path) -> None:
    rig = Rig(tmp_path, cloud=True)
    _check(rig, {"code": "x = 1\n", "language": "python"})
    assert rig.backend.requests == []
    assert rig.cloud_calls == 0


def test_check_is_read_only_and_idempotent(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    args = {"code": "x = 1\n", "language": "python"}
    assert _check(rig, args) == _check(rig, args)
    assert len(rig.context.store) == 0  # nothing was stored


def test_an_oversized_text_is_refused(tmp_path: Path) -> None:
    from headwaters.mcp.tools.context import ToolSettings

    rig = Rig(tmp_path, settings=ToolSettings(max_check_chars=10))
    result = asyncio.run(rig.call("headwaters_check", {"code": "x" * 50}))
    assert structured(result)["error"]["code"] == "too_large"
