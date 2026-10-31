"""HDW-118: ``headwaters scope`` and ``private`` (and the rules.yaml edit under them)."""

from __future__ import annotations

from pathlib import Path

import pytest

from headwaters.core.rules_io import load_rules
from tests.cli_support import home, invoke, invoke_json, write_rules


@pytest.fixture
def cli_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return home(tmp_path, monkeypatch)


def _rules_file(root: Path) -> Path:
    return root / "config" / "rules.yaml"


def test_scope_shows_default_and_effective(cli_home: Path) -> None:
    result, data = invoke_json("scope")
    assert result.exit_code == 0
    assert data["default_scope"] == "auto"
    # Without the paid-scopes feature, auto behaves like free (paid tiers are excluded).
    assert data["effective"]["scope"] == "free"
    assert data["changed"] is False
    assert "Default scope: auto" in invoke("scope").stdout


@pytest.mark.parametrize("scope", ["auto", "free", "local", "cloud"])
def test_scope_sets_each_scope(cli_home: Path, scope: str) -> None:
    result, data = invoke_json("scope", scope)
    assert result.exit_code == 0, result.output
    assert data["default_scope"] == scope
    assert load_rules(_rules_file(cli_home)).default_scope == scope


def test_scope_reports_change_and_no_change(cli_home: Path) -> None:
    assert "auto -> local" in invoke("scope", "local").stdout
    assert "already local" in invoke("scope", "local").stdout
    _, data = invoke_json("scope", "free")
    assert (data["previous"], data["changed"]) == ("local", True)


def test_scope_rejects_unknown_value_without_touching_the_file(cli_home: Path) -> None:
    invoke("scope", "free")
    before = _rules_file(cli_home).read_bytes()
    assert invoke("scope", "everywhere").exit_code == 2
    assert _rules_file(cli_home).read_bytes() == before


def test_scope_edit_keeps_comments_and_other_settings(cli_home: Path) -> None:
    write_rules(
        cli_home,
        "# my notes\nversion: 1\ndefault_scope: auto  # keep me\nstrategy: quality_first\n",
    )
    assert invoke("scope", "local").exit_code == 0
    text = _rules_file(cli_home).read_text(encoding="utf-8")
    assert "# my notes" in text
    assert "default_scope: local  # keep me" in text
    assert "strategy: quality_first" in text


def test_scope_edit_keeps_crlf_line_endings(cli_home: Path) -> None:
    path = _rules_file(cli_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"version: 1\r\ndefault_scope: auto\r\nprivate_lock: false\r\n")
    assert invoke("scope", "free").exit_code == 0
    raw = path.read_bytes()
    assert b"default_scope: free\r\n" in raw
    assert b"\n" not in raw.replace(b"\r\n", b"")


def test_scope_adds_the_key_when_the_file_does_not_have_it(cli_home: Path) -> None:
    write_rules(cli_home, "version: 1\n")
    assert invoke("scope", "local").exit_code == 0
    assert load_rules(_rules_file(cli_home)).default_scope == "local"


def test_invalid_rules_file_is_reported_and_left_alone(cli_home: Path) -> None:
    write_rules(cli_home, "default_scope: auto\nairplane: maybe\n")
    before = _rules_file(cli_home).read_bytes()
    result = invoke("scope", "local")
    assert result.exit_code == 2
    assert "airplane" in result.output
    assert _rules_file(cli_home).read_bytes() == before


def test_private_on_off_and_effective_scope(cli_home: Path) -> None:
    result, data = invoke_json("private", "on")
    assert result.exit_code == 0
    assert data["private_lock"] is True
    assert data["effective"]["tiers"] == ["local"]
    assert "private_lock" in data["effective"]["locks"]
    assert load_rules(_rules_file(cli_home)).private_lock is True
    off, after = invoke_json("private", "off")
    assert off.exit_code == 0
    assert after["private_lock"] is False
    assert after["effective"]["locks"] == []


def test_private_text_output_says_nothing_leaves(cli_home: Path) -> None:
    out = invoke("private", "on").stdout
    assert "Private lock: off -> on" in out
    assert "no cloud client is created" in out
    assert "already on" in invoke("private", "on").stdout
    assert "Private lock: on" in invoke("private").stdout


def test_private_rejects_other_words(cli_home: Path) -> None:
    assert invoke("private", "maybe").exit_code == 2


def test_lock_narrows_a_broad_default_scope_to_local(cli_home: Path) -> None:
    invoke("private", "on")
    out = invoke("scope", "auto").stdout
    assert "tighter setting always wins" in out
    _, status = invoke_json("status")
    assert status["effective"]["tiers"] == ["local"]
    assert status["default_scope"] == "auto"


def test_lock_is_never_loosened_by_a_cloud_default_scope(cli_home: Path) -> None:
    # Scopes that cannot overlap are an error with a fix, never a silent loosening of the lock.
    invoke("private", "on")
    result, data = invoke_json("scope", "cloud")
    assert result.exit_code == 0  # the setting itself was saved
    assert data["effective"]["scope"] is None
    assert data["effective"]["error"]["code"] == "private_lock_conflict"
    assert "headwaters private off" in invoke("scope").stdout
    _, status = invoke_json("status")
    assert status["effective"] is None
    assert status["private_lock"] is True
