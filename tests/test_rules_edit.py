"""HDW-118: targeted, validated, atomic edits of rules.yaml."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from headwaters.core.rules_edit import set_setting
from headwaters.core.rules_io import ConfigError, load_rules


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8", newline="\n")
    return path


def test_creates_the_default_file_on_first_use(tmp_path: Path) -> None:
    target = tmp_path / "rules.yaml"
    previous, new = set_setting("default_scope", "local", target)
    assert (previous, new) == ("auto", "local")
    assert load_rules(target).default_scope == "local"


def test_replaces_the_value_and_keeps_comment_and_neighbours(tmp_path: Path) -> None:
    target = _write(tmp_path / "rules.yaml", "# top\ndefault_scope: auto  # why\nairplane: true\n")
    set_setting("default_scope", "free", target)
    assert target.read_text(encoding="utf-8") == (
        "# top\ndefault_scope: free  # why\nairplane: true\n"
    )


def test_quoted_values_are_replaced(tmp_path: Path) -> None:
    target = _write(tmp_path / "rules.yaml", 'default_scope: "auto"\n')
    set_setting("default_scope", "cloud", target)
    assert load_rules(target).default_scope == "cloud"


def test_bool_settings_render_lowercase(tmp_path: Path) -> None:
    target = _write(tmp_path / "rules.yaml", "private_lock: false\n")
    assert set_setting("private_lock", True, target) == (False, True)
    assert target.read_text(encoding="utf-8") == "private_lock: true\n"


def test_appends_when_the_key_is_missing_even_without_trailing_newline(tmp_path: Path) -> None:
    target = _write(tmp_path / "rules.yaml", "version: 1")
    set_setting("private_lock", True, target)
    assert target.read_text(encoding="utf-8") == "version: 1\nprivate_lock: true\n"


def test_no_write_when_nothing_changes(tmp_path: Path) -> None:
    target = _write(tmp_path / "rules.yaml", "default_scope: auto\n")
    before = target.stat().st_mtime_ns
    set_setting("default_scope", "auto", target)
    assert target.stat().st_mtime_ns == before


def test_unknown_key_and_unsafe_value_are_refused(tmp_path: Path) -> None:
    target = _write(tmp_path / "rules.yaml", "version: 1\n")
    with pytest.raises(ValueError, match="cannot be edited"):
        set_setting("telemetry", True, target)
    with pytest.raises(ValueError, match="unsupported value"):
        set_setting("default_scope", "auto\nairplane: true", target)
    assert target.read_text(encoding="utf-8") == "version: 1\n"


def test_invalid_scope_value_is_rejected_and_file_untouched(tmp_path: Path) -> None:
    target = _write(tmp_path / "rules.yaml", "default_scope: auto\n")
    with pytest.raises(ConfigError, match="default_scope"):
        set_setting("default_scope", "nowhere", target)
    assert target.read_text(encoding="utf-8") == "default_scope: auto\n"


def test_unparseable_existing_value_is_not_guessed(tmp_path: Path) -> None:
    target = _write(tmp_path / "rules.yaml", "default_scope: [auto]\n")
    with pytest.raises(ConfigError):
        set_setting("default_scope", "free", target)
    assert target.read_text(encoding="utf-8") == "default_scope: [auto]\n"


def test_duplicate_key_is_refused(tmp_path: Path) -> None:
    target = _write(tmp_path / "rules.yaml", "default_scope: auto\ndefault_scope: free\n")
    with pytest.raises(ConfigError):
        set_setting("default_scope", "local", target)


def test_an_invalid_file_is_never_rewritten(tmp_path: Path) -> None:
    target = _write(tmp_path / "rules.yaml", "default_scope: auto\nairplane: maybe\n")
    with pytest.raises(ConfigError, match="airplane"):
        set_setting("default_scope", "local", target)
    assert "default_scope: auto" in target.read_text(encoding="utf-8")


def test_a_failed_write_leaves_no_temp_file_behind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = _write(tmp_path / "rules.yaml", "default_scope: auto\n")

    def boom(src: object, dst: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError, match="disk full"):
        set_setting("default_scope", "local", target)
    assert [p.name for p in tmp_path.iterdir()] == ["rules.yaml"]
    assert target.read_text(encoding="utf-8") == "default_scope: auto\n"
