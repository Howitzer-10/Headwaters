"""The symbol index's SQLite schema (HDW-601).

One database per workspace, under the cache directory, so two workspaces never see each other's
symbols and deleting the cache rebuilds everything (D9: the workspace boundary is the root - the
index never widens it).

Every child row hangs off ``files`` with ``ON DELETE CASCADE``, so "this file changed" is one
delete plus one insert no matter how many symbols, references or imports the file had. ``files``
carries the identity a rebuild compares: size and ``mtime_ns`` decide whether a file can be
skipped, and ``sha256`` records what was indexed (useful when a checkout restores an old file
with a restored mtime).

``refs`` and ``imports`` are pluralised away from SQL keywords on purpose. ``summary`` is the
file-summary placeholder described in ``extract``.
"""

from __future__ import annotations

SCHEMA_VERSION = 1

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY,
    rel TEXT NOT NULL UNIQUE,
    language TEXT NOT NULL,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    lines INTEGER NOT NULL DEFAULT 0,
    symbols_count INTEGER NOT NULL DEFAULT 0,
    refs_count INTEGER NOT NULL DEFAULT 0,
    imports_count INTEGER NOT NULL DEFAULT 0,
    summary TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS files_language ON files(language);

CREATE TABLE IF NOT EXISTS symbols (
    id INTEGER PRIMARY KEY,
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    signature TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS symbols_name ON symbols(name);
CREATE INDEX IF NOT EXISTS symbols_file ON symbols(file_id);
CREATE INDEX IF NOT EXISTS symbols_kind ON symbols(kind);

CREATE TABLE IF NOT EXISTS refs (
    id INTEGER PRIMARY KEY,
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    line INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS refs_name ON refs(name);
CREATE INDEX IF NOT EXISTS refs_file ON refs(file_id);

CREATE TABLE IF NOT EXISTS imports (
    id INTEGER PRIMARY KEY,
    file_id INTEGER NOT NULL REFERENCES files(id) ON DELETE CASCADE,
    module TEXT NOT NULL,
    line INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS imports_module ON imports(module);
CREATE INDEX IF NOT EXISTS imports_file ON imports(file_id);
"""

#: Tables a query may read, with the column that answers "which file is this from".
READABLE_TABLES = ("files", "symbols", "refs", "imports")
