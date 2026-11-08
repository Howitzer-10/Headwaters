"""HDW-601: the file watcher - events become pending paths, one drain becomes one update."""

from __future__ import annotations

from pathlib import Path

import pytest

from headwaters.features.context_engine.index import SymbolIndex
from headwaters.features.context_engine.watch import IndexWatcher


@pytest.fixture
def watcher(tmp_path: Path):
    ws = tmp_path / "ws"
    (ws / "pkg").mkdir(parents=True)
    (ws / "py.py").write_text("def one():\n    return 1\n", encoding="utf-8")
    (ws / "pkg" / "web.js").write_text("export function two() {}\n", encoding="utf-8")
    with SymbolIndex(ws, tmp_path / "i.sqlite3", default_ignore=()) as index:
        index.update()
        yield IndexWatcher(index), ws


def test_nothing_pending_drains_to_none(watcher) -> None:
    watch, _ws = watcher
    assert watch.pending() == []
    assert watch.drain() is None


def test_absolute_and_relative_paths_both_queue(watcher) -> None:
    watch, ws = watcher
    watch.note(str(ws / "py.py"))
    watch.note("pkg/web.js")
    watch.note("./py.py")
    assert watch.pending() == ["pkg/web.js", "py.py"]


def test_paths_outside_the_workspace_are_dropped(watcher, tmp_path: Path) -> None:
    watch, _ws = watcher
    watch.note(str(tmp_path / "elsewhere.py"))
    watch.note("../escape.py")
    watch.note("")
    assert watch.pending() == []


def test_drain_reindexes_and_empties_the_queue(watcher) -> None:
    watch, ws = watcher
    (ws / "py.py").write_text(
        "def one():\n    return 1\n\n\ndef three():\n    return 3\n", encoding="utf-8"
    )
    watch.note(str(ws / "py.py"))
    stats = watch.drain()
    assert stats is not None
    assert stats.refreshed == 1
    assert [s.rel for s in watch.index.search("three")] == ["py.py"]
    assert watch.pending() == []
    assert watch.drain() is None


def test_drain_of_a_deleted_file_forgets_it(watcher) -> None:
    watch, ws = watcher
    (ws / "pkg" / "web.js").unlink()
    watch.note(str(ws / "pkg" / "web.js"))
    stats = watch.drain()
    assert stats is not None
    assert stats.removed == 1
    assert [f.rel for f in watch.index.files()] == ["py.py"]


def test_non_source_and_ignored_paths_are_harmless(watcher) -> None:
    watch, ws = watcher
    (ws / "notes.md").write_text("# notes\n", encoding="utf-8")
    watch.note(str(ws / "notes.md"))
    stats = watch.drain()
    assert stats is not None
    assert (stats.refreshed, stats.files) == (0, 2)


def test_stop_without_start_is_a_no_op(watcher) -> None:
    watch, _ws = watcher
    assert watch.running is False
    watch.stop()
    assert watch.running is False
