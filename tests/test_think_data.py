"""HDW-140 (D12): the ``reasoning_ok`` capability: discovery, storage, migration 3, hard filter."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from headwaters.core.analyzer.requirements import HardRequirements
from headwaters.core.catalog import sources
from headwaters.core.policy.filters import Candidate, hard_filter
from headwaters.core.state import LATEST_VERSION, Database, open_database
from headwaters.core.state_models import ModelRow, get_model, upsert_model
from headwaters.core.state_schema import MIGRATIONS
from tests.test_discovery_sources import OLLAMA, FakeHttp, ollama_routes
from tests.test_policy_scoring import RULES, cand, hard, scope


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return open_database(tmp_path / "state.db")


def with_flag(flag: bool | None) -> Candidate:
    base = cand("m")
    return replace(base, row=replace(base.row, reasoning_ok=flag))


def test_hard_filter_keeps_models_that_can_reason() -> None:
    needs = HardRequirements(False, False, False, 1000, reasoning=True)
    assert [c.id for c in hard_filter([with_flag(True)], needs, scope(), RULES).kept] == ["m"]


@pytest.mark.parametrize("flag", [False, None])
def test_hard_filter_rejects_models_that_cannot_or_may_not_reason(flag: bool | None) -> None:
    needs = HardRequirements(False, False, False, 1000, reasoning=True)
    res = hard_filter([with_flag(flag)], needs, scope(), RULES)
    assert res.kept == ()
    assert res.rejected[0].reason == "no_reasoning"


def test_hard_filter_ignores_the_flag_when_reasoning_is_not_required() -> None:
    assert hard_filter([with_flag(False)], hard(), scope(), RULES).kept


def test_ollama_thinking_capability_is_read() -> None:
    shows = {
        "qwen:14b": {"capabilities": ["completion", "tools", "thinking"]},
        "vis:latest": {"capabilities": ["completion"]},
        "embed:1": {"capabilities": ["embedding"]},
    }

    class Http(FakeHttp):
        def request_json(self, method: str, url: str, **kw: Any) -> Any:
            if url.endswith("/api/show"):
                return shows[kw["body"]["model"]]
            return super().request_json(method, url, **kw)

    models, _ = sources.ollama_models(Http(ollama_routes()), OLLAMA)
    by_id = {m.id: m for m in models}
    assert by_id["qwen:14b"].reasoning_ok is True
    assert by_id["vis:latest"].reasoning_ok is False


def test_ollama_without_capabilities_leaves_reasoning_unknown() -> None:
    routes: dict[tuple[str, str], Any] = {
        ("GET", f"{OLLAMA}/api/tags"): {"models": [{"name": "a:1", "size": 1}]},
        ("POST", f"{OLLAMA}/api/show"): {},
        ("GET", f"{OLLAMA}/api/ps"): {"models": []},
    }
    models, _ = sources.ollama_models(FakeHttp(routes), OLLAMA)
    assert models[0].reasoning_ok is None


def test_openrouter_reasoning_parameter_is_read() -> None:
    payload = {
        "data": [
            {
                "id": "vendor/deep:free",
                "architecture": {"output_modalities": ["text"]},
                "supported_parameters": ["tools", "reasoning", "include_reasoning"],
            },
            {
                "id": "vendor/plain",
                "architecture": {"output_modalities": ["text"]},
                "supported_parameters": ["tools"],
            },
        ]
    }
    http = FakeHttp({("GET", f"{sources.OPENROUTER_BASE}/models"): payload})
    by_id = {m.id: m for m in sources.openrouter_models(http)}
    assert by_id["vendor/deep:free"].reasoning_ok is True
    assert by_id["vendor/plain"].reasoning_ok is False


def test_reasoning_ok_round_trips_and_defaults_to_unknown(db: Database) -> None:
    upsert_model(db, ModelRow("a", "local", "ollama", ["text"], reasoning_ok=True))
    upsert_model(db, ModelRow("b", "local", "ollama", ["text"]))
    a, b = get_model(db, "a"), get_model(db, "b")
    assert a is not None
    assert a.reasoning_ok is True
    assert b is not None
    assert b.reasoning_ok is None


def test_migration_3_adds_the_column_to_an_existing_database(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    db = Database(path)
    with db.write() as c:  # a version-2 database: migrations 1 and 2 applied by hand
        c.execute(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY,"
            " description TEXT NOT NULL, applied_at REAL NOT NULL)"
        )
        for version, description, ddl in MIGRATIONS[:2]:
            for statement in [s.strip() for s in ddl.split(";") if s.strip()]:
                c.execute(statement)
            c.execute("INSERT INTO schema_migrations VALUES (?, ?, 0)", (version, description))
        c.execute("INSERT INTO models (id, tier, provider, last_seen) VALUES ('m','local','o',1)")
    assert db.schema_version() == 2
    assert db.migrate() == list(range(3, LATEST_VERSION + 1))
    model = get_model(db, "m")
    assert model is not None
    assert model.reasoning_ok is None
