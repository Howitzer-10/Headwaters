"""HDW-118: ``headwaters status``: scope, locks, project rule, walls with ETAs, warm model."""

from __future__ import annotations

from pathlib import Path

import pytest

from headwaters.core.state import open_database
from headwaters.core.state_limits import record_wall
from tests.cli_support import home, invoke, invoke_json, seed_models, write_rules

FAR_FUTURE = 4_102_444_800.0  # 2100-01-01


@pytest.fixture
def cli_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return home(tmp_path, monkeypatch)


def test_project_rule_tightens_scope(cli_home: Path) -> None:
    (cli_home / ".headwaters.yaml").write_text("privacy: local\n", encoding="utf-8")
    _, status = invoke_json("status")
    assert status["project_rule"] == "local"
    assert status["effective"]["tiers"] == ["local"]
    assert "project_rule" in status["locks"]
    assert status["project_file"].endswith(".headwaters.yaml")


def test_invalid_project_file_is_a_config_error(cli_home: Path) -> None:
    (cli_home / ".headwaters.yaml").write_text("privacy: nobody\n", encoding="utf-8")
    result, data = invoke_json("status")
    assert result.exit_code == 2
    assert data["error"]["code"] == "invalid_project_config"


def test_status_text_lists_scope_locks_walls_and_warm_model(cli_home: Path) -> None:
    out = invoke("status").stdout
    for label in (
        "Default scope",
        "Fallback order",
        "Effective scope",
        "Private lock",
        "Locks in force",
        "Catalog",
    ):
        assert label in out
    assert "Walls: none" in out
    assert "Warm model: none" in out


def test_status_shows_the_fallback_order(cli_home: Path) -> None:
    assert invoke_json("status")[1]["tier_order"] == "interleave"
    write_rules(
        cli_home,
        "default_scope: cloud\nrouter:\n  tier_order: descend\n  tier_direction: local_first\n",
    )
    assert "descend (local_first)" in invoke("status").stdout
    _, data = invoke_json("status")
    assert (data["tier_order"], data["tier_direction"]) == ("descend", "local_first")


def test_status_unsatisfiable_scope_shows_the_fix(cli_home: Path) -> None:
    write_rules(cli_home, "private_lock: true\ndefault_scope: cloud\n")
    out = invoke("status").stdout
    assert "Effective scope" in out
    assert "headwaters private off" in out
    _, data = invoke_json("status")
    assert data["scope_error"]["code"] == "private_lock_conflict"
    assert data["effective"] is None


def test_status_shows_walls_with_eta_and_kind(cli_home: Path) -> None:
    db = open_database()
    record_wall(db, "model:vendor/free:free", "rate_limit", until=FAR_FUTURE)
    record_wall(db, "provider:openrouter:paid", "exhausted", until=None)
    _, data = invoke_json("status")
    walls = {w["subject"]: w for w in data["walls"]}
    assert walls["model:vendor/free:free"]["eta_s"] > 0
    assert walls["provider:openrouter:paid"]["eta_s"] is None
    out = invoke("status").stdout
    assert "rate limited" in out
    assert "out of credit" in out
    # FU-122-4: readable labels, and no "after a balance check" for a wall with no end time
    # (every wall gets one per HDW-122), so an end time that is not known says so.
    assert walls["provider:openrouter:paid"]["label"] == "OpenRouter: paid credits"
    assert walls["model:vendor/free:free"]["label"] == "Model vendor/free:free"
    assert "OpenRouter: paid credits" in out
    assert "after a balance check" not in out
    assert "unknown" in out


def test_status_locks_in_force_is_the_decisions_active_locks(cli_home: Path) -> None:
    """FU-128-1: every lock in force, including one that narrowed nothing."""
    write_rules(cli_home, "private_lock: true\ndefault_scope: local\n")
    _, data = invoke_json("status")
    assert data["locks"] == ["private_lock"]
    assert "private_lock" in invoke("status").stdout


def test_status_ignores_walls_that_have_expired(cli_home: Path) -> None:
    record_wall(open_database(), "model:old", "rate_limit", until=1.0)
    _, data = invoke_json("status")
    assert data["walls"] == []


def test_status_shows_the_warm_model(cli_home: Path) -> None:
    seed_models(cli_home)
    _, data = invoke_json("status")
    assert data["warm_models"] == [{"model": "qwen:14b", "vram_gb": 8.0}]
    assert "qwen:14b (8.0 GB on the GPU)" in invoke("status").stdout


def test_status_airplane_mode_is_a_lock(cli_home: Path) -> None:
    write_rules(cli_home, "airplane: true\n")
    _, data = invoke_json("status")
    assert data["airplane"] is True
    assert "airplane" in data["locks"]
