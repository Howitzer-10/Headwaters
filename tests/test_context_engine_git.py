"""HDW-601: git-aware invalidation - a branch switch re-indexes only what moved."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from headwaters.features.context_engine import gitstate
from headwaters.features.context_engine.index import SymbolIndex

_GIT = ["-c", "user.name=Headwaters Test", "-c", "user.email=test@example.invalid"]


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(root), *_GIT, *args],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    if _git(root, "init", "-q").returncode != 0:  # pragma: no cover - git is a test dependency
        pytest.skip("git is not available")
    _put(root / "one.py", "def one():\n    return 1\n")
    _put(root / "pkg" / "two.py", "def two():\n    return 2\n")
    _put(root / "old.py", "def old():\n    return 3\n")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "first")
    return root


def _put(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")


def test_head_commit_matches_git(repo: Path) -> None:
    expected = _git(repo, "rev-parse", "HEAD").stdout.strip()
    assert gitstate.head_commit(repo) == expected


def test_head_commit_is_none_outside_a_repository(tmp_path: Path) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    assert gitstate.head_commit(plain) is None


def test_changed_between_names_edits_adds_and_removals(repo: Path) -> None:
    first = gitstate.head_commit(repo)
    _put(repo / "one.py", "def one():\n    return 10\n")
    _put(repo / "three.py", "def three():\n    return 3\n")
    (repo / "old.py").unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "second")
    second = gitstate.head_commit(repo)
    assert first
    assert second
    assert first != second
    changed, removed = gitstate.changed_between(repo, first, second) or ([], [])
    assert sorted(changed) == ["one.py", "three.py"]
    assert removed == ["old.py"]


def test_changed_between_returns_none_when_git_cannot_answer(repo: Path) -> None:
    assert gitstate.changed_between(repo, "0" * 40, "1" * 40) is None


def test_full_build_records_the_head(repo: Path) -> None:
    with SymbolIndex(repo, repo.parent / "i.sqlite3", default_ignore=()) as index:
        index.update()
        assert index.head() == gitstate.head_commit(repo)
        assert [f.rel for f in index.files()] == ["old.py", "one.py", "pkg/two.py"]


def test_branch_switch_reindexes_only_the_files_that_moved(repo: Path) -> None:
    _git(repo, "checkout", "-q", "-b", "feature")
    _put(repo / "one.py", "def one():\n    return 99\n")
    _put(repo / "feature.py", "def feature():\n    return 4\n")
    (repo / "old.py").unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "feature")
    _git(repo, "checkout", "-q", "-")
    with SymbolIndex(repo, repo.parent / "i.sqlite3", default_ignore=()) as index:
        index.update()
        _git(repo, "checkout", "-q", "feature")
        stats = index.sync_git()
        assert stats is not None
        assert (stats.refreshed, stats.removed) == (2, 1)
        assert [f.rel for f in index.files()] == ["feature.py", "one.py", "pkg/two.py"]
        assert [s.rel for s in index.search("feature")] == ["feature.py"]
        assert index.head() == gitstate.head_commit(repo)
        assert index.sync_git() is None


def test_sync_git_builds_when_nothing_was_recorded(repo: Path) -> None:
    with SymbolIndex(repo, repo.parent / "i.sqlite3", default_ignore=()) as index:
        stats = index.sync_git()
        assert stats is not None
        assert stats.files == 3
        assert index.head() == gitstate.head_commit(repo)


def test_sync_git_sees_no_change_between_two_commits_touching_nothing(repo: Path) -> None:
    with SymbolIndex(repo, repo.parent / "i.sqlite3", default_ignore=()) as index:
        index.update()
        empty = _git(repo, "commit", "-q", "--allow-empty", "-m", "empty")
        assert empty.returncode == 0
        stats = index.sync_git()
        assert stats is not None
        assert (stats.refreshed, stats.unchanged, stats.removed) == (0, 0, 0)
        assert index.head() == gitstate.head_commit(repo)
