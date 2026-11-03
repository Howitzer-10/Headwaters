"""Versioned DDL for the shared SQLite database.

Never edit an applied entry: append a new one. Only metadata and token counts are stored.
"""

from __future__ import annotations

MIGRATIONS: tuple[tuple[int, str, str], ...] = (
    (
        1,
        "initial schema",
        """
        CREATE TABLE models (
            id TEXT PRIMARY KEY, tier TEXT NOT NULL, provider TEXT NOT NULL,
            modalities TEXT NOT NULL DEFAULT '[]',
            tools_ok INTEGER, json_ok INTEGER, ctx INTEGER,
            price_in REAL, price_out REAL, size_gb REAL,
            arch_json TEXT NOT NULL DEFAULT '{}',
            source TEXT NOT NULL DEFAULT '', last_seen REAL NOT NULL
        );
        CREATE TABLE scores (
            model_id TEXT NOT NULL, task TEXT NOT NULL,
            seed REAL, override REAL, learned_ema REAL,
            n_samples INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (model_id, task)
        );
        CREATE TABLE walls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT NOT NULL, kind TEXT NOT NULL,
            since REAL NOT NULL, until REAL,
            consecutive_count INTEGER NOT NULL DEFAULT 1, last_error TEXT,
            UNIQUE (subject, kind)
        );
        CREATE TABLE quotas (
            subject TEXT NOT NULL, window TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 0, "limit" INTEGER,
            soft_exhausted INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (subject, window)
        );
        CREATE TABLE sessions (
            session_key TEXT PRIMARY KEY, pinned_model TEXT, tier TEXT,
            created REAL NOT NULL, last_switch REAL, handoff_packet_id INTEGER
        );
        CREATE TABLE decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,
            surface TEXT NOT NULL, session TEXT,
            scope TEXT NOT NULL, strategy TEXT NOT NULL,
            locks_applied TEXT NOT NULL DEFAULT '[]',
            requirements_json TEXT NOT NULL DEFAULT '{}',
            chosen TEXT, alternatives TEXT NOT NULL DEFAULT '[]',
            floor REAL, score REAL, below_floor INTEGER NOT NULL DEFAULT 0,
            reasons TEXT NOT NULL DEFAULT '[]'
        );
        CREATE INDEX idx_decisions_ts ON decisions (ts);
        CREATE TABLE outcomes (
            decision_id INTEGER PRIMARY KEY REFERENCES decisions (id) ON DELETE CASCADE,
            status TEXT NOT NULL, latency_ms INTEGER,
            tokens_in INTEGER, tokens_out INTEGER,
            cost_usd REAL, counterfactual_usd REAL,
            verify_result TEXT, escalated_to TEXT, user_feedback TEXT
        );
        CREATE TABLE jobs (
            job_id TEXT PRIMARY KEY, status TEXT NOT NULL,
            progress REAL NOT NULL DEFAULT 0, result_ref TEXT,
            created REAL NOT NULL, updated REAL NOT NULL
        );
        CREATE TABLE handoffs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, session TEXT NOT NULL,
            packet_json TEXT NOT NULL, created REAL NOT NULL
        );
        CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """,
    ),
    (
        2,
        "model discovery columns",
        """
        ALTER TABLE models ADD COLUMN deprecated_by TEXT;
        ALTER TABLE models ADD COLUMN unscored INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE models ADD COLUMN retains_data INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE models ADD COLUMN warm INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE models ADD COLUMN size_vram_gb REAL;
        """,
    ),
    (
        3,
        "reasoning capability column",
        """
        ALTER TABLE models ADD COLUMN reasoning_ok INTEGER;
        """,
    ),
    (
        4,
        "job detail columns",
        """
        ALTER TABLE jobs ADD COLUMN tool TEXT;
        ALTER TABLE jobs ADD COLUMN message TEXT;
        ALTER TABLE jobs ADD COLUMN error_json TEXT;
        """,
    ),
    (
        5,
        "active locks and offload penalty on decisions",
        """
        ALTER TABLE decisions ADD COLUMN active_locks TEXT NOT NULL DEFAULT '[]';
        ALTER TABLE decisions ADD COLUMN offload_penalty REAL;
        """,
    ),
)
LATEST_VERSION = MIGRATIONS[-1][0]
