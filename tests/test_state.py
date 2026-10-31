from pathlib import Path

import pytest

from headwaters.core.state import LATEST_VERSION, Database, open_database, scrub
from headwaters.core.state_limits import (
    active_walls,
    add_quota_usage,
    clear_wall,
    get_quota,
    record_wall,
    reset_quota_window,
    set_quota_limit,
)
from headwaters.core.state_misc import kv_get
from headwaters.core.state_models import (
    ModelRow,
    ScoreRow,
    get_model,
    get_score,
    list_models,
    set_score,
    upsert_model,
)


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return open_database(tmp_path / "state" / "test.db")


EXPECTED_TABLES = {
    "models",
    "scores",
    "walls",
    "quotas",
    "sessions",
    "decisions",
    "outcomes",
    "jobs",
    "handoffs",
    "kv",
    "schema_migrations",
}


def test_schema_and_pragmas(db: Database) -> None:
    with db.read() as c:
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert tables >= EXPECTED_TABLES
        assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert c.execute("PRAGMA busy_timeout").fetchone()[0] >= 5000
        cols = {r["name"] for r in c.execute("PRAGMA table_info(decisions)")}
        assert {"scope", "strategy", "locks_applied"} <= cols
        ocols = {r["name"] for r in c.execute("PRAGMA table_info(outcomes)")}
        assert {"tokens_in", "cost_usd", "counterfactual_usd", "verify_result"} <= ocols


def test_migrations_are_idempotent(tmp_path: Path) -> None:
    d = Database(tmp_path / "m.db")
    assert d.schema_version() == 0
    assert d.migrate() == list(range(1, LATEST_VERSION + 1))
    assert d.migrate() == []
    assert d.schema_version() == LATEST_VERSION
    upsert_model(d, ModelRow("m", "local", "ollama", ["text"]))
    assert d.migrate() == []  # existing data untouched
    assert get_model(d, "m") is not None
    with d.read() as c:
        assert c.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == LATEST_VERSION


def test_failed_write_rolls_back(db: Database) -> None:
    def failing_write() -> None:
        with db.write() as c:
            c.execute("INSERT INTO kv (key, value) VALUES ('a', '1')")
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        failing_write()
    assert kv_get(db, "a") is None


def test_models_and_scores(db: Database) -> None:
    upsert_model(
        db, ModelRow("qwen", "local", "ollama", ["text"], tools_ok=True, ctx=8192, arch={"l": 1})
    )
    upsert_model(db, ModelRow("qwen", "local", "ollama", ["text", "vision"], json_ok=False))
    m = get_model(db, "qwen")
    assert m is not None
    assert (m.modalities, m.tools_ok, m.json_ok, m.ctx) == (["text", "vision"], None, False, None)
    upsert_model(db, ModelRow("gpt", "paid", "openai", ["text"]))
    assert [x.id for x in list_models(db)] == ["gpt", "qwen"]
    assert [x.id for x in list_models(db, "local")] == ["qwen"]
    assert get_model(db, "none") is None

    set_score(db, ScoreRow("qwen", "code", seed=70.0))
    set_score(db, ScoreRow("qwen", "code", seed=70.0, learned_ema=72.5, n_samples=3))
    assert get_score(db, "qwen", "code") == ScoreRow("qwen", "code", 70.0, None, 72.5, 3)
    assert get_score(db, "qwen", "chat") is None


def test_walls(db: Database) -> None:
    w = record_wall(db, "prov", "rate_limit", until=100.0, error="429")
    assert w.consecutive_count == 1
    w = record_wall(db, "prov", "rate_limit", until=500.0, error="429 again")
    assert (w.consecutive_count, w.until, w.last_error) == (2, 500.0, "429 again")
    record_wall(db, "prov", "auth", until=None)
    assert {x.kind for x in active_walls(db, now=200.0)} == {"rate_limit", "auth"}
    assert {x.kind for x in active_walls(db, now=600.0)} == {"auth"}  # hard wall stays
    assert clear_wall(db, "prov", "auth") == 1
    assert clear_wall(db, "prov") == 1
    assert active_walls(db) == []


def test_quotas(db: Database) -> None:
    assert get_quota(db, "m", "day") is None
    assert add_quota_usage(db, "m", "day").count == 1
    assert add_quota_usage(db, "m", "day", 4).count == 5
    set_quota_limit(db, "m", "day", limit=50, soft_exhausted=True)
    q = get_quota(db, "m", "day")
    assert q is not None
    assert (q.count, q.limit, q.soft_exhausted) == (5, 50, True)
    assert reset_quota_window(db, "day") == 1
    q = get_quota(db, "m", "day")
    assert q is not None
    assert (q.count, q.soft_exhausted, q.limit) == (0, False, 50)


def test_scrub_removes_text_keys_recursively() -> None:
    data = {"a": 1, "Prompt": "x", "n": {"content": "y", "ok": [{"text": "z", "k": 2}]}}
    assert scrub(data) == {"a": 1, "n": {"ok": [{"k": 2}]}}


def test_no_text_columns_for_prompts(db: Database) -> None:
    with db.read() as c:
        for table in EXPECTED_TABLES:
            for col in c.execute(f"PRAGMA table_info({table})"):
                assert col["name"] not in {"prompt", "output", "content", "messages", "text"}
