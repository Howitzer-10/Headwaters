"""HDW-601: the local symbol index - build, incremental update, queries, one DB per workspace."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from headwaters.core.catalog.schema import Catalog
from headwaters.features.context_engine.index import (
    SymbolIndex,
    default_ignore_patterns,
    index_path,
)

_SEED = Path(__file__).resolve().parents[1] / "catalog" / "seed_catalog.json"

PY = (
    '"""Tally module."""\n'
    "import os\n"
    "from pathlib import Path\n"
    "\n"
    "\n"
    "class Tally:\n"
    "    def add(self, value):\n"
    "        return stamp(value)\n"
    "\n"
    "\n"
    "def stamp(value):\n"
    "    return os.path.basename(str(Path(value)))\n"
)
JS = (
    "// Widget module.\n"
    "import { stamp } from './py.js';\n"
    "export function draw(n) {\n"
    "  return stamp(n);\n"
    "}\n"
)
GO = (
    "package main\n"
    "\n"
    'import "fmt"\n'
    "\n"
    "func main() {\n"
    '\tfmt.Println(stamp(1))\n'
    "}\n"
    "\n"
    "func stamp(n int) int { return n }\n"
)


def _defaults() -> list[str]:
    return Catalog.model_validate(
        json.loads(_SEED.read_text(encoding="utf-8"))
    ).default_ignore_patterns


def _put(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    _put(root / "py.py", PY)
    _put(root / "web" / "widget.js", JS)
    _put(root / "cmd" / "main.go", GO)
    _put(root / "README.md", "# not source\n")
    return root


@pytest.fixture
def index(ws: Path, tmp_path: Path):
    with SymbolIndex(ws, tmp_path / "index.sqlite3", default_ignore=()) as build:
        yield build


def test_build_indexes_every_language_it_knows(index: SymbolIndex) -> None:
    stats = index.update()
    assert stats.files == 3
    assert stats.refreshed == 3
    assert stats.unchanged == 0
    assert stats.removed == 0
    assert index.scan() == ["cmd/main.go", "py.py", "web/widget.js"]
    assert [(f.rel, f.language) for f in index.files()] == [
        ("cmd/main.go", "go"),
        ("py.py", "python"),
        ("web/widget.js", "javascript"),
    ]
    assert stats.symbols == 6  # Tally, add, stamp, main, stamp, draw
    assert stats.imports == 4  # os, pathlib, fmt, ./py.js
    assert stats.references >= 6
    assert stats.seconds >= 0.0


def test_definitions_are_queryable_by_name_and_by_file(index: SymbolIndex) -> None:
    index.update()
    stamp = index.search("stamp")
    assert [(s.rel, s.kind) for s in stamp] == [
        ("cmd/main.go", "function"),
        ("py.py", "function"),
    ]
    assert [s.kind for s in index.symbols_in("py.py")] == ["class", "method", "function"]
    assert index.search("nope") == []
    assert [s.rel for s in index.search("st", prefix=True)] == ["cmd/main.go", "py.py"]
    assert len(index.search("stamp", limit=1)) == 1


def test_references_and_imports_are_queryable(index: SymbolIndex) -> None:
    index.update()
    assert sorted(index.references_to("stamp")) == [
        ("cmd/main.go", 6),
        ("py.py", 8),
        ("web/widget.js", 4),
    ]
    assert index.references_to("nope") == []
    assert [i.module for i in index.imports_of("py.py")] == ["os", "pathlib"]
    assert [i.module for i in index.imports_of("cmd/main.go")] == ["fmt"]
    assert index.imports_of("README.md") == []


def test_summary_is_the_first_comment_line(index: SymbolIndex) -> None:
    index.update()
    assert index.summary("py.py") == "Tally module."
    assert index.summary("web/widget.js") == "Widget module."
    assert index.summary("README.md") == ""


def test_second_build_reads_nothing_again(index: SymbolIndex) -> None:
    index.update()
    stats = index.update()
    assert (stats.refreshed, stats.unchanged, stats.removed) == (0, 3, 0)
    assert stats.files == 3


def test_editing_one_file_refreshes_only_that_file(index: SymbolIndex, ws: Path) -> None:
    index.update()
    _put(ws / "py.py", PY.replace("def stamp(", "def stamped("))
    stats = index.update()
    assert (stats.refreshed, stats.unchanged) == (1, 2)
    assert [s.rel for s in index.search("stamp")] == ["cmd/main.go"]
    assert [s.rel for s in index.search("stamped")] == ["py.py"]


def test_delete_forgets_the_file_and_its_symbols(index: SymbolIndex, ws: Path) -> None:
    index.update()
    os.unlink(ws / "web" / "widget.js")
    stats = index.update()
    assert stats.removed == 1
    assert stats.files == 2
    assert index.search("draw") == []
    assert index.references_to("stamp") == [("cmd/main.go", 6), ("py.py", 8)]


def test_scoped_update_takes_relative_absolute_and_windows_separators(
    index: SymbolIndex, ws: Path
) -> None:
    index.update()
    _put(ws / "cmd" / "main.go", GO.replace("func stamp(", "func stampAgain("))
    relative = os.path.join("cmd", "main.go")
    stats = index.update([str(ws / "cmd" / "main.go"), "./py.py", relative])
    assert stats.refreshed == 1
    assert [s.rel for s in index.search("stampAgain")] == ["cmd/main.go"]


def test_scoped_update_ignores_paths_outside_or_unknown(index: SymbolIndex, tmp_path: Path) -> None:
    index.update()
    stats = index.update([str(tmp_path / "other.py"), "../escape.py", "notes.md", ""])
    assert (stats.refreshed, stats.unchanged) == (0, 0)
    assert stats.files == 3


def test_force_reindexes_an_unchanged_file(index: SymbolIndex) -> None:
    index.update()
    assert index.update(["py.py"], force=True).refreshed == 1


def test_ignore_rules_keep_secrets_and_ignored_dirs_out(
    ws: Path, tmp_path: Path
) -> None:
    _put(ws / ".env", "SECRET=1\n")
    _put(ws / "certs" / "server.pem", "-----BEGIN-----\n")
    _put(ws / "node_modules" / "dep.js", "export const x = 1;\n")
    _put(ws / ".gitignore", "build/\nnode_modules/\n")
    _put(ws / "build" / "out.js", "export const y = 2;\n")
    _put(ws / ".headwatersignore", "web/\n")
    with SymbolIndex(ws, tmp_path / "i.sqlite3", default_ignore=_defaults()) as build:
        stats = build.update()
        assert [f.rel for f in build.files()] == ["cmd/main.go", "py.py"]
        assert stats.files == 2
        # a watcher event naming an ignored path must not smuggle it in
        assert build.update([".env", "node_modules/dep.js", "web/widget.js"]).files == 2
        assert [f.rel for f in build.files()] == ["cmd/main.go", "py.py"]


def test_default_ignore_patterns_come_from_the_catalog() -> None:
    patterns = default_ignore_patterns()
    assert ".env" in patterns
    assert any("pem" in pattern for pattern in patterns)


def test_oversized_file_is_skipped(ws: Path, tmp_path: Path) -> None:
    with SymbolIndex(ws, tmp_path / "i.sqlite3", default_ignore=(), max_file_bytes=10) as build:
        stats = build.update()
        assert "py.py" not in [f.rel for f in build.files()]
        assert stats.files == 0


def test_file_with_undecodable_bytes_is_indexed_without_error(
    index: SymbolIndex, ws: Path
) -> None:
    (ws / "weird.py").write_bytes(b"\xff\xfe\x00def broken(:\n")
    stats = index.update(["weird.py"])
    assert stats.refreshed == 1
    assert [f.rel for f in index.files() if f.rel == "weird.py"] == ["weird.py"]


def test_forget_reports_whether_the_row_existed(index: SymbolIndex) -> None:
    index.update()
    assert index.forget("py.py") is True
    assert index.forget("py.py") is False
    assert index.search("Tally") == []


def test_remove_list_forgets_deleted_paths(index: SymbolIndex) -> None:
    index.update()
    stats = index.update([], remove=["web/widget.js"])
    assert stats.removed == 1
    assert stats.files == 2


def test_index_is_one_sqlite_file_per_workspace(ws: Path, tmp_path: Path) -> None:
    other = tmp_path / "other"
    other.mkdir()
    first = index_path(ws, cache_dir=tmp_path / "cache")
    assert first == index_path(ws, cache_dir=tmp_path / "cache")
    assert first != index_path(other, cache_dir=tmp_path / "cache")
    assert first.parent.name == "index"
    assert first.suffix == ".sqlite3"
    assert first.name.startswith("ws-")


def test_index_defaults_to_the_cache_dir(
    ws: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HEADWATERS_HOME", str(tmp_path / "home"))
    path = index_path(ws)
    assert path.parent.parent == tmp_path / "home" / "cache"


def test_reopening_an_index_keeps_its_rows(ws: Path, tmp_path: Path) -> None:
    db = tmp_path / "i.sqlite3"
    with SymbolIndex(ws, db, default_ignore=()) as first:
        first.update()
    with SymbolIndex(ws, db, default_ignore=()) as second:
        assert len(second.files()) == 3
        assert second.update().unchanged == 3


def test_a_symlinked_source_file_is_not_followed(ws: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.py"
    _put(outside, "def secret():\n    return 1\n")
    link = ws / "linked.py"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):  # pragma: no cover - needs a privilege on Windows
        pytest.skip("symlinks are not available")
    with SymbolIndex(ws, tmp_path / "i.sqlite3", default_ignore=()) as build:
        build.update()
        assert "linked.py" not in build.scan()
        assert build.search("secret") == []
