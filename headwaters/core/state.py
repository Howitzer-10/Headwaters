"""Shared SQLite state (gateway, MCP daemon and CLI all open the same database).

WAL journal, ``busy_timeout``, one short-lived connection per operation, and writes inside
``BEGIN IMMEDIATE`` so concurrent processes queue instead of failing. Migrations are
versioned and idempotent. Typed repository functions live in ``state_models``,
``state_decisions``, ``state_limits`` and ``state_misc``.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from headwaters.core import paths
from headwaters.core.state_schema import LATEST_VERSION, MIGRATIONS

DB_FILE_NAME = "headwaters.db"
BUSY_TIMEOUT_MS = 30_000

__all__ = ["LATEST_VERSION", "Database", "default_db_path", "dumps", "open_database", "scrub"]

# JSON keys that could carry prompt/output text: stripped before anything is stored.
_FORBIDDEN_KEYS = frozenset({"prompt", "content", "messages", "output", "text"})


def scrub(value: Any) -> Any:
    """Drop keys that could carry prompt/output text, recursively."""
    if isinstance(value, Mapping):
        return {str(k): scrub(v) for k, v in value.items() if str(k).lower() not in _FORBIDDEN_KEYS}
    if isinstance(value, list | tuple | set | frozenset):
        return [scrub(v) for v in value]
    return value


def dumps(value: Any) -> str:
    return json.dumps(scrub(value), ensure_ascii=False, sort_keys=True, default=str)


def default_db_path() -> Path:
    return paths.state_dir() / DB_FILE_NAME


def _split_statements(ddl: str) -> list[str]:
    # The DDL contains no semicolons inside literals, so a plain split is sufficient.
    return [s.strip() for s in ddl.split(";") if s.strip()]


class Database:
    """Handle to the shared database. Cheap to create; opens a connection per operation."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_db_path()

    def _open(self) -> sqlite3.Connection:
        safe = paths.long_safe(self.path)
        safe.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(safe), timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        self._enable_wal(conn)
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @staticmethod
    def _enable_wal(conn: sqlite3.Connection) -> None:
        """Switch to WAL, retrying while another process holds the lock.

        Changing a fresh database to WAL needs an exclusive lock and SQLite does not apply
        ``busy_timeout`` to that switch, so two first-run processes can get "database is
        locked" at once (seen on Linux CI). Retry until the same deadline instead.
        """
        deadline = time.monotonic() + BUSY_TIMEOUT_MS / 1000
        while True:
            try:
                conn.execute("PRAGMA journal_mode = WAL")
                return
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc) or time.monotonic() >= deadline:
                    raise
                time.sleep(0.01)

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        conn = self._open()
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        """One short write transaction; takes the write lock up front (waits for it)."""
        conn = self._open()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        finally:
            conn.close()

    def migrate(self) -> list[int]:
        """Apply pending migrations; safe to call repeatedly and from many processes.

        Returns the versions applied by this call.
        """
        applied: list[int] = []
        with self.write() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version INTEGER PRIMARY KEY, description TEXT NOT NULL, applied_at REAL NOT NULL)"
            )
            # Checked inside the write lock, so a racing process cannot apply a version twice.
            done = {r["version"] for r in conn.execute("SELECT version FROM schema_migrations")}
            for version, description, ddl in MIGRATIONS:
                if version in done:
                    continue
                for statement in _split_statements(ddl):
                    conn.execute(statement)
                conn.execute(
                    "INSERT INTO schema_migrations (version, description, applied_at)"
                    " VALUES (?, ?, ?)",
                    (version, description, time.time()),
                )
                applied.append(version)
        return applied

    def schema_version(self) -> int:
        with self.read() as conn:
            try:
                row = conn.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
            except sqlite3.OperationalError:
                return 0
        return int(row["v"] or 0)


def open_database(path: Path | None = None) -> Database:
    """Create the handle and make sure the schema is current."""
    db = Database(path)
    db.migrate()
    return db
