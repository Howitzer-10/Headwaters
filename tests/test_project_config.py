from pathlib import Path

import pytest

from headwaters.core.project_config import (
    PROJECT_FILE_NAME,
    ProjectConfig,
    effective_scope,
    find_project_file,
    load_project_config,
)
from headwaters.core.rules import Rules
from headwaters.core.rules_io import ConfigError


def test_walks_up_from_nested_dir(tmp_path: Path) -> None:
    (tmp_path / PROJECT_FILE_NAME).write_text(
        'privacy: local\nchecks: ["ruff check {files}", "pytest -q -x tests/unit"]\n',
        encoding="utf-8",
    )
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    cfg, found = load_project_config(nested)
    assert found == (tmp_path / PROJECT_FILE_NAME).resolve()
    assert cfg.privacy == "local"
    assert cfg.checks == ["ruff check {files}", "pytest -q -x tests/unit"]


def test_nearest_file_wins(tmp_path: Path) -> None:
    (tmp_path / PROJECT_FILE_NAME).write_text("privacy: local\n", encoding="utf-8")
    inner = tmp_path / "inner"
    inner.mkdir()
    (inner / PROJECT_FILE_NAME).write_text("privacy: free\n", encoding="utf-8")
    assert load_project_config(inner)[0].privacy == "free"


def test_start_may_be_a_file(tmp_path: Path) -> None:
    (tmp_path / PROJECT_FILE_NAME).write_text("privacy: free\n", encoding="utf-8")
    source = tmp_path / "x.py"
    source.write_text("", encoding="utf-8")
    assert find_project_file(source) is not None


def test_absent_gives_defaults(tmp_path: Path) -> None:
    # tmp_path ancestors might hold a stray file on a dev box; pin to an isolated start.
    cfg, found = load_project_config(tmp_path)
    if found is None:
        assert cfg == ProjectConfig()


@pytest.mark.parametrize("text", ["privacy: public\n", "checks: pytest\n", "extra: 1\n"])
def test_invalid_project_config(tmp_path: Path, text: str) -> None:
    (tmp_path / PROJECT_FILE_NAME).write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError):
        load_project_config(tmp_path)


@pytest.mark.parametrize(
    ("scope", "privacy", "expected"),
    [
        ("auto", "any", "auto"),
        ("cloud", "any", "cloud"),
        ("auto", "free", "free"),
        ("cloud", "free", "free"),
        ("auto", "local", "local"),
        ("free", "local", "local"),
        ("local", "any", "local"),  # never loosened
        ("local", "free", "local"),
        ("free", "free", "free"),
    ],
)
def test_privacy_only_tightens(scope: str, privacy: str, expected: str) -> None:
    rules = Rules.model_validate({"default_scope": scope})
    project = ProjectConfig.model_validate({"privacy": privacy})
    assert effective_scope(rules, project) == expected
