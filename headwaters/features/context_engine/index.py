"""Incremental symbol index over a workspace root (HDW-601).

What it answers, without the model seeing a file: where a name is defined, which files call it,
what a file imports, and a one-line placeholder summary per file. Rows live in one SQLite file
per workspace under the cache dir, so the answer survives restarts and a lost cache is just a
rebuild.

Incremental by three routes, cheapest first:

* ``update()`` with no scope re-walks the root, skips every file whose ``size`` and ``mtime_ns``
  still match its row, and forgets rows whose file is gone;
* ``update(paths)`` re-indexes exactly the paths it is given (a watcher event, an editor save);
* ``sync_git()`` compares the recorded commit with the checked-out one and re-indexes only the
  files that differ, which is the cheap answer to a branch switch.

Enumeration goes through the same :class:`IgnoreMatcher` the packer uses, with the catalog's
secret patterns, so an index can never hold content (or a name) from an ignored file. Symlinks are
skipped: following one could leave the workspace (D9). Files above ``max_file_bytes`` are skipped
for the same reason the packer skips them.
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
import threading
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from headwaters.core import paths
from headwaters.core.catalog.updater import load_active
from headwaters.core.workspace_files import DEFAULT_MAX_FILE_BYTES
from headwaters.core.workspace_ignore import IgnoreMatcher

from . import gitstate
from .extract import ExtractResult, extract, language_for
from .store import SCHEMA_SQL, SCHEMA_VERSION

INDEX_DIR_NAME = "index"
_SLUG = re.compile(r"[^A-Za-z0-9._-]+")
DROP_SQL = """
DROP TABLE IF EXISTS imports;
DROP TABLE IF EXISTS refs;
DROP TABLE IF EXISTS symbols;
DROP TABLE IF EXISTS files;
DROP TABLE IF EXISTS meta;
"""


def default_ignore_patterns() -> tuple[str, ...]:
    """The catalog's secret/env patterns. Empty when the catalog cannot be read, never an error:
    a missing catalog must not stop the index from building."""
    try:
        catalog, _source = load_active()
    except (OSError, ValueError):
        return ()
    return tuple(catalog.default_ignore_patterns)


def index_path(root: Path, *, cache_dir: Path | None = None) -> Path:
    """Where ``root``'s index lives: one file per workspace, keyed by the root's identity."""
    digest = hashlib.sha256(str(root).casefold().encode("utf-8")).hexdigest()[:16]
    slug = _SLUG.sub("-", root.name).strip("-")[:40] or "workspace"
    base = paths.get_paths().cache if cache_dir is None else cache_dir
    return base / INDEX_DIR_NAME / f"{slug}-{digest}.sqlite3"


@dataclass(frozen=True)
class SymbolRecord:
    rel: str
    name: str
    kind: str
    line: int
    end_line: int
    signature: str = ""


@dataclass(frozen=True)
class ImportRecord:
    rel: str
    module: str
    line: int


@dataclass(frozen=True)
class FileRecord:
    rel: str
    language: str
    size: int
    lines: int
    symbols: int
    references: int
    imports: int
    summary: str
    sha256: str = ""


@dataclass(frozen=True)
class IndexStats:
    """One build or refresh, in counts. ``seconds`` is measured, not estimated."""

    files: int = 0
    symbols: int = 0
    references: int = 0
    imports: int = 0
    refreshed: int = 0
    unchanged: int = 0
    removed: int = 0
    seconds: float = 0.0


class SymbolIndex:
    """The index for one workspace root. Safe to use from several threads (one lock, one file)."""

    def __init__(
        self,
        root: Path,
        db_path: Path | None = None,
        *,
        default_ignore: Sequence[str] | None = None,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
        use_git_excludes: bool = True,
    ) -> None:
        self.root = Path(root)
        self.db_path = index_path(self.root) if db_path is None else Path(db_path)
        self._max_bytes = max_file_bytes
        self._use_git_excludes = use_git_excludes
        self._defaults: tuple[str, ...] = (
            default_ignore_patterns() if default_ignore is None else tuple(default_ignore)
        )
        self._lock = threading.RLock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection = self._connect()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if version != SCHEMA_VERSION:
            conn.executescript(DROP_SQL)
            conn.executescript(SCHEMA_SQL)
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            conn.commit()
        return conn

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> SymbolIndex:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def matcher(self) -> IgnoreMatcher:
        """The ignore rules this index enumerates with (catalog secrets plus the repo's own)."""
        return IgnoreMatcher(
            self.root, self._defaults, use_git_excludes=self._use_git_excludes
        )

    # -- enumeration ------------------------------------------------------------------

    def scan(self, matcher: IgnoreMatcher | None = None) -> list[str]:
        """Every indexable source file under the root, as root-relative ``/`` paths."""
        rules = self.matcher() if matcher is None else matcher
        found: list[str] = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            base = Path(dirpath)
            relative = os.path.relpath(dirpath, self.root).replace(os.sep, "/")
            prefix = "" if relative == "." else relative + "/"
            dirnames[:] = sorted(
                name
                for name in dirnames
                if not rules.is_ignored(prefix + name, is_dir=True)
                and not (base / name).is_symlink()
            )
            for name in sorted(filenames):
                if language_for(name) is None or (base / name).is_symlink():
                    continue
                if rules.is_ignored(prefix + name):
                    continue
                found.append(prefix + name)
        found.sort()
        return found

    # -- building ---------------------------------------------------------------------

    def update(
        self,
        scope: Sequence[str] | None = None,
        *,
        force: bool = False,
        remove: Iterable[str] = (),
    ) -> IndexStats:
        """Index the whole root (``scope=None``) or just ``scope`` plus ``remove``."""
        started = time.monotonic()
        rules = self.matcher()
        refreshed = unchanged = removed = 0
        if scope is None:
            wanted = self.scan(rules)
            gone = sorted(set(self._known_paths()) - set(wanted))
            targets: list[str] = wanted
        else:
            targets = [
                rel
                for rel in (self._normalise(item) for item in scope)
                if rel is not None and language_for(rel) and not rules.is_ignored(rel)
            ]
            gone = [rel for rel in (self._normalise(item) for item in remove) if rel is not None]
        with self._lock, self._conn:
            for rel in targets:
                outcome = self._index_one(rel, force=force)
                if outcome == "refreshed":
                    refreshed += 1
                elif outcome == "unchanged":
                    unchanged += 1
                elif outcome == "removed":
                    removed += 1
            for rel in gone:
                removed += self._forget(rel)
            if scope is None:
                self._set_meta("head", gitstate.head_commit(self.root) or "")
        return self._stats(started, refreshed, unchanged, removed)

    def sync_git(self) -> IndexStats | None:
        """Catch up with a checkout, pull or branch switch. ``None`` when nothing moved."""
        started = time.monotonic()
        recorded = self.head()
        current = gitstate.head_commit(self.root)
        if not current or current == recorded:
            return None
        if recorded is None:
            return self.update()
        changes = gitstate.changed_between(self.root, recorded, current)
        if changes is None:
            return self.update()
        changed, removed = changes
        if not changed and not removed:
            with self._lock, self._conn:
                self._set_meta("head", current)
            return self._stats(started, 0, 0, 0)
        stats = self.update(changed, force=True, remove=removed)
        with self._lock, self._conn:
            self._set_meta("head", current)
        return stats

    def _index_one(self, rel: str, *, force: bool) -> str:
        target = paths.long_safe(self.root / rel)
        try:
            stat = target.stat()
        except OSError:
            return "removed" if self._forget(rel) else "ignored"
        if not target.is_file() or stat.st_size > self._max_bytes:
            self._forget(rel)
            return "removed"
        row = self._row(rel)
        if (
            not force
            and row is not None
            and row["size"] == stat.st_size
            and row["mtime_ns"] == stat.st_mtime_ns
        ):
            return "unchanged"
        try:
            data = target.read_bytes()
        except OSError:
            self._forget(rel)
            return "removed"
        language = language_for(rel)
        if language is None:
            return "ignored"
        result = extract(language, data)
        self._store(rel, language, stat.st_size, stat.st_mtime_ns, data, result)
        return "refreshed"

    def _store(
        self,
        rel: str,
        language: str,
        size: int,
        mtime_ns: int,
        data: bytes,
        result: ExtractResult,
    ) -> None:
        digest = hashlib.sha256(data).hexdigest()
        lines = data.count(b"\n") + 1 if data else 0
        self._conn.execute(
            """
            INSERT INTO files (rel, language, size, mtime_ns, sha256, lines,
                               symbols_count, refs_count, imports_count, summary)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(rel) DO UPDATE SET
                language=excluded.language, size=excluded.size, mtime_ns=excluded.mtime_ns,
                sha256=excluded.sha256, lines=excluded.lines,
                symbols_count=excluded.symbols_count, refs_count=excluded.refs_count,
                imports_count=excluded.imports_count, summary=excluded.summary
            """,
            (
                rel,
                language,
                size,
                mtime_ns,
                digest,
                lines,
                len(result.definitions),
                len(result.references),
                len(result.imports),
                result.summary,
            ),
        )
        row = self._row(rel)
        if row is None:  # pragma: no cover - the upsert above just wrote it
            return
        file_id = int(row["id"])
        for table in ("symbols", "refs", "imports"):
            self._conn.execute(f"DELETE FROM {table} WHERE file_id = ?", (file_id,))
        self._conn.executemany(
            "INSERT INTO symbols (file_id, name, kind, line, end_line, signature)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            [
                (file_id, item.name, item.kind, item.line, item.end_line, item.signature)
                for item in result.definitions
            ],
        )
        self._conn.executemany(
            "INSERT INTO refs (file_id, name, line) VALUES (?, ?, ?)",
            [(file_id, item.name, item.line) for item in result.references],
        )
        self._conn.executemany(
            "INSERT INTO imports (file_id, module, line) VALUES (?, ?, ?)",
            [(file_id, item.module, item.line) for item in result.imports],
        )

    def _forget(self, rel: str) -> int:
        cursor = self._conn.execute("DELETE FROM files WHERE rel = ?", (rel,))
        return max(cursor.rowcount, 0)

    def forget(self, rel: str) -> bool:
        """Drop one file from the index (deleted or ignored). Returns whether it was there."""
        with self._lock, self._conn:
            return self._forget(rel) > 0

    # -- queries ----------------------------------------------------------------------

    def files(self) -> list[FileRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT rel, language, size, lines, symbols_count, refs_count, imports_count,"
                " summary, sha256 FROM files ORDER BY rel"
            ).fetchall()
        return [
            FileRecord(
                rel=row["rel"],
                language=row["language"],
                size=row["size"],
                lines=row["lines"],
                symbols=row["symbols_count"],
                references=row["refs_count"],
                imports=row["imports_count"],
                summary=row["summary"],
                sha256=row["sha256"],
            )
            for row in rows
        ]

    def symbols_in(self, rel: str) -> list[SymbolRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT f.rel, s.name, s.kind, s.line, s.end_line, s.signature"
                " FROM symbols s JOIN files f ON f.id = s.file_id"
                " WHERE f.rel = ? ORDER BY s.line, s.name",
                (rel,),
            ).fetchall()
        return [self._symbol(row) for row in rows]

    def search(self, name: str, *, limit: int = 20, prefix: bool = False) -> list[SymbolRecord]:
        """Definitions of ``name`` (or of every name starting with it when ``prefix``)."""
        clause = "s.name LIKE ? ESCAPE '\\'" if prefix else "s.name = ?"
        escaped = str(name).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        value = f"{escaped}%" if prefix else name
        with self._lock:
            rows = self._conn.execute(
                "SELECT f.rel, s.name, s.kind, s.line, s.end_line, s.signature"
                " FROM symbols s JOIN files f ON f.id = s.file_id"
                f" WHERE {clause} ORDER BY f.rel, s.line LIMIT ?",
                (value if prefix else name, limit),
            ).fetchall()
        return [self._symbol(row) for row in rows]

    def references_to(self, name: str, *, limit: int = 50) -> list[tuple[str, int]]:
        """``(rel, line)`` of each call site of ``name``."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT f.rel, r.line FROM refs r JOIN files f ON f.id = r.file_id"
                " WHERE r.name = ? ORDER BY f.rel, r.line LIMIT ?",
                (name, limit),
            ).fetchall()
        return [(row["rel"], row["line"]) for row in rows]

    def imports_of(self, rel: str) -> list[ImportRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT f.rel, i.module, i.line FROM imports i JOIN files f ON f.id = i.file_id"
                " WHERE f.rel = ? ORDER BY i.line, i.module",
                (rel,),
            ).fetchall()
        return [ImportRecord(row["rel"], row["module"], row["line"]) for row in rows]

    def summary(self, rel: str) -> str:
        with self._lock:
            row = self._row(rel)
        return "" if row is None else str(row["summary"])

    def head(self) -> str | None:
        value = self._get_meta("head")
        return value or None

    def _symbol(self, row: sqlite3.Row) -> SymbolRecord:
        return SymbolRecord(
            rel=row["rel"],
            name=row["name"],
            kind=row["kind"],
            line=row["line"],
            end_line=row["end_line"],
            signature=row["signature"],
        )

    # -- internals --------------------------------------------------------------------

    def _row(self, rel: str) -> sqlite3.Row | None:
        row: sqlite3.Row | None = self._conn.execute(
            "SELECT * FROM files WHERE rel = ?", (rel,)
        ).fetchone()
        return row

    def _known_paths(self) -> list[str]:
        return [row["rel"] for row in self._conn.execute("SELECT rel FROM files").fetchall()]

    def _normalise(self, item: str) -> str | None:
        text = str(item).strip().replace("\\", "/")
        if not text:
            return None
        candidate = Path(text)
        if candidate.is_absolute():
            try:
                text = candidate.relative_to(self.root).as_posix()
            except ValueError:
                try:
                    text = candidate.resolve().relative_to(self.root.resolve()).as_posix()
                except (OSError, ValueError):
                    return None
        while text.startswith("./"):
            text = text[2:]
        parts = [part for part in text.split("/") if part not in ("", ".")]
        if not parts or ".." in parts:
            return None
        return "/".join(parts)

    def _stats(self, started: float, refreshed: int, unchanged: int, removed: int) -> IndexStats:
        totals = self._totals()
        return IndexStats(
            files=totals["files"],
            symbols=totals["symbols"],
            references=totals["references"],
            imports=totals["imports"],
            refreshed=refreshed,
            unchanged=unchanged,
            removed=removed,
            seconds=round(time.monotonic() - started, 3),
        )

    def _totals(self) -> dict[str, int]:
        with self._lock:
            files = self._conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
            symbols = self._conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
            references = self._conn.execute("SELECT COUNT(*) FROM refs").fetchone()[0]
            imports = self._conn.execute("SELECT COUNT(*) FROM imports").fetchone()[0]
        return {
            "files": int(files),
            "symbols": int(symbols),
            "references": int(references),
            "imports": int(imports),
        }

    def _get_meta(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row["value"])

    def _set_meta(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def open_index(root: Path, **kwargs: Any) -> SymbolIndex:
    """Open (or create) the index for ``root``. ``kwargs`` go to :class:`SymbolIndex`."""
    return SymbolIndex(root, **kwargs)
