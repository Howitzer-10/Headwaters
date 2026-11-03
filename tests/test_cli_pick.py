"""HDW-118: ``headwaters pick`` shows the reasons and the fallbacks, and is a pure dry run."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from headwaters.core.state import default_db_path, open_database
from tests.cli_support import (
    CODE_PROMPT,
    FAKE_KEY,
    add_model,
    gpu_state,
    home,
    invoke,
    invoke_json,
    seed_models,
    write_rules,
)


@pytest.fixture
def cli_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home(tmp_path, monkeypatch)
    seed_models(tmp_path)
    return tmp_path


def test_pick_shows_model_reasons_and_fallbacks(cli_home: Path) -> None:
    result = invoke("pick", CODE_PROMPT)
    assert result.exit_code == 0, result.output
    out = result.stdout
    assert "vendor/free:free" in out  # the pick
    assert "Why:" in out
    assert "floor" in out  # the reasons carry the quality floor
    assert "Fallbacks, in order" in out
    assert "qwen:14b" in out  # a fallback with its tier and availability
    assert "Availability" in out


def test_pick_json_has_reasons_fallbacks_and_scope(cli_home: Path) -> None:
    result, data = invoke_json("pick", CODE_PROMPT)
    assert result.exit_code == 0
    assert data["ok"] is True
    assert data["model"] == "vendor/free:free"
    assert data["tier"] == "free_cloud"
    assert data["reasons"], "the decision must explain itself"
    assert [f["model"] for f in data["fallbacks"]][:1] == ["qwen:14b"]
    assert data["fallbacks"][0]["status"] == "ok"
    assert data["scope"]["tiers"] == ["free_cloud", "local"]
    assert data["requirements"]["task"] == "code"
    assert "vendor/big" in {r["model"] for r in data["rejected"]}  # paid tiers are out of scope
    assert {"model", "reason"} <= set(data["rejected"][0])


def test_pick_explains_why_a_model_is_not_eligible(cli_home: Path) -> None:
    out = invoke("pick", CODE_PROMPT).stdout
    assert "Not eligible" in out
    assert "vendor/big" in out
    assert "scope" in out


def test_pick_scope_local_is_fail_closed(cli_home: Path) -> None:
    # The only local model is weaker than the code floor. Local scope never falls back to cloud:
    # it serves that local model flagged below the floor, with verification mandatory (HDW-123).
    add_model(open_database(), "qwen:14b", "local", 60.0, agentic=60.0)
    result, data = invoke_json("pick", CODE_PROMPT, "--scope", "local")
    assert result.exit_code == 0, data
    assert data["model"] == "qwen:14b"
    assert data["tier"] == "local"
    assert data["rung"] == "best_effort"
    assert data["below_floor"] is True
    assert data["verify_required"] is True
    assert data["fallbacks"] == []
    codes = {s["code"] for s in data["suggestions"]}
    assert {"pull_model", "split_task", "use_cloud"} <= codes
    rejected = {(r["model"], r["reason"]) for r in data["rejected"]}
    assert rejected == {("vendor/free:free", "scope"), ("vendor/big", "scope")}


def test_pick_scope_local_below_floor_shows_the_warning(cli_home: Path) -> None:
    add_model(open_database(), "qwen:14b", "local", 60.0, agentic=60.0)
    result = invoke("pick", CODE_PROMPT, "--scope", "local")
    assert result.exit_code == 0, result.output
    assert "Warning: no model in scope reaches the quality floor" in result.stdout
    assert "Below floor" in result.stdout
    assert "required" in result.stdout  # verification is mandatory
    assert "headwaters/cloud" in result.stdout  # the loosening hint


def test_pick_above_the_floor_shows_no_warning(cli_home: Path) -> None:
    assert "Warning:" not in invoke("pick", CODE_PROMPT).stdout


def test_pick_scope_local_with_no_usable_model_still_errors(cli_home: Path) -> None:
    # Below-floor serving needs a local model that passes the hard filter. With none usable
    # (the 32k local model cannot hold 100k tokens), local scope errors and never goes to cloud.
    result, data = invoke_json("pick", CODE_PROMPT, "--scope", "local", "--tokens", "100000")
    assert result.exit_code == 3
    assert data["error"]["code"] == "local_unavailable"


def test_warm_local_model_is_preferred_over_a_cold_stronger_one(cli_home: Path) -> None:
    # Loading a cold model would evict the warm one, so the warm bonus and eviction penalty keep
    # the warm model ahead even though the other scores higher (HDW-109 residency).
    add_model(open_database(), "strong:32b", "local", 95.0, agentic=95.0)
    _, data = invoke_json("pick", CODE_PROMPT, "--scope", "local")
    assert data["model"] == "qwen:14b"
    assert [f["model"] for f in data["fallbacks"]] == ["strong:32b"]


def test_pick_scope_local_uses_a_local_model_that_clears_the_floor(cli_home: Path) -> None:
    db = open_database()
    add_model(db, "qwen:14b", "local", 80.0, agentic=80.0)  # no longer warm
    add_model(db, "strong:32b", "local", 95.0, agentic=95.0)
    result, data = invoke_json("pick", CODE_PROMPT, "--scope", "local")
    assert result.exit_code == 0, data
    assert data["model"] == "strong:32b"
    assert data["tier"] == "local"
    assert data["scope"]["tiers"] == ["local"]
    assert data["below_floor"] is False
    assert {f["model"] for f in data["fallbacks"]} <= {"qwen:14b"}  # never a cloud fallback
    assert {r["model"] for r in data["rejected"]} == {"vendor/free:free", "vendor/big"}


def test_pick_bad_scope_is_a_usage_error(cli_home: Path) -> None:
    assert invoke("pick", "hi", "--scope", "everywhere").exit_code == 2


def test_private_lock_forces_local_and_blocks_cloud_request(cli_home: Path) -> None:
    assert invoke("private", "on").exit_code == 0
    _, data = invoke_json("pick", CODE_PROMPT)
    assert data["scope"]["tiers"] == ["local"]
    assert "private_lock" in data["scope"]["locks"]
    result, error = invoke_json("pick", CODE_PROMPT, "--scope", "cloud")
    assert result.exit_code == 3
    assert error["ok"] is False
    assert error["error"]["code"] == "private_lock_conflict"
    assert "headwaters private off" in error["error"]["fix"]


def test_private_lock_with_local_scope_still_shows_the_lock(cli_home: Path) -> None:
    """FU-128-3: a lock in force is shown even when it narrowed nothing."""
    assert invoke("private", "on").exit_code == 0
    result, data = invoke_json("pick", CODE_PROMPT, "--scope", "local")
    assert result.exit_code == 0, data
    assert data["scope"]["locks"] == []  # it narrowed nothing: local was asked for
    assert data["scope"]["active_locks"] == ["private_lock"]
    out = invoke("pick", CODE_PROMPT, "--scope", "local").stdout
    locks_line = next(line for line in out.splitlines() if "Locks" in line)
    assert "private_lock" in locks_line  # not "none"


def test_secret_in_prompt_forces_local(cli_home: Path) -> None:
    _, data = invoke_json("pick", f"{CODE_PROMPT}; my key is {FAKE_KEY}")
    assert data["scope"]["tiers"] == ["local"]
    assert any("secret detected" in r for r in data["reasons"])
    assert FAKE_KEY not in str(data), "a detected secret must never be echoed back"


def test_pick_never_echoes_the_prompt(cli_home: Path) -> None:
    marker = "ZEBRA-MARKER-7731"
    assert marker not in invoke("pick", f"{CODE_PROMPT} {marker}").output
    _, data = invoke_json("pick", f"{CODE_PROMPT} {marker}")
    assert marker not in str(data)


def test_pick_is_a_dry_run_that_writes_nothing(cli_home: Path) -> None:
    def counts() -> list[int]:
        with closing(sqlite3.connect(default_db_path())) as con:
            tables = ("decisions", "outcomes", "sessions")
            return [con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables]

    before = counts()
    assert invoke("pick", CODE_PROMPT).exit_code == 0
    assert invoke("pick", CODE_PROMPT, "--scope", "local").exit_code == 0
    assert counts() == before


def test_airplane_mode_limits_pick_to_local(cli_home: Path) -> None:
    write_rules(cli_home, "airplane: true\n")
    _, data = invoke_json("pick", CODE_PROMPT, "--scope", "free")
    assert data["model"] == "qwen:14b"
    assert data["fallbacks"] == []


def test_battery_suggestion_is_shown_for_a_local_pick(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home(tmp_path, monkeypatch, gpu_state(on_battery=True))
    db = seed_models(tmp_path)
    # Only a local model clears the floor here; on battery the CLI suggests cloud (and still
    # picks local: it never leaves the local tier on its own).
    add_model(db, "vendor/free:free", "free_cloud", 10.0, agentic=10.0)
    _, data = invoke_json("pick", CODE_PROMPT, "--scope", "free")
    assert data["model"] == "qwen:14b"
    assert data["suggestions"][0]["code"] == "use_cloud"
    assert "headwaters/cloud" in invoke("pick", CODE_PROMPT, "--scope", "local").stdout
