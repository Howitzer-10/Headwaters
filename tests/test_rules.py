import json
from pathlib import Path

import pytest
import yaml

from headwaters.core.rules import Rules
from headwaters.core.rules_io import (
    SCHEMA_RELATIVE_PATH,
    ConfigError,
    ensure_rules_file,
    load_rules,
    main,
    schema_json,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_defaults_match_design() -> None:
    r = Rules()
    assert (r.default_scope, r.strategy, r.verify, r.advice) == (
        "auto",
        "cost_saver",
        "agentic",
        "off",
    )
    assert r.gateway.port == 4000
    assert r.dashboard.port == 4001
    assert r.mcp.async_threshold_s == 20
    assert r.walls.cascade_if_wait_over_s == 8
    assert r.walls.soft_exhaust_pct == 90
    assert r.floors.agentic_bonus == 15
    assert r.floors.high_risk_bonus == 10
    assert r.floors.optimistic_margin == 8
    assert r.tiers.local.reserve_gb is None
    assert r.tiers.local.on_battery == "prefer_cloud"
    assert r.tiers.free_cloud.ranking == "catalog"
    assert r.telemetry is False
    assert r.secrets.action == "local"


def test_first_run_writes_defaults_and_roundtrips(tmp_path: Path) -> None:
    target = tmp_path / "cfg" / "rules.yaml"
    ensure_rules_file(target)
    assert target.is_file()
    assert load_rules(target) == Rules()


def test_ensure_never_overwrites(tmp_path: Path) -> None:
    target = tmp_path / "rules.yaml"
    target.write_text("strategy: quality_first\n", encoding="utf-8")
    ensure_rules_file(target)
    assert load_rules(target).strategy == "quality_first"


def test_empty_file_is_defaults(tmp_path: Path) -> None:
    target = tmp_path / "rules.yaml"
    target.write_text("", encoding="utf-8")
    assert load_rules(target) == Rules()


def test_partial_file_overrides_only_named_fields(tmp_path: Path) -> None:
    target = tmp_path / "rules.yaml"
    target.write_text("tiers:\n  local:\n    reserve_gb: 2.0\n", encoding="utf-8")
    r = load_rules(target)
    assert r.tiers.local.reserve_gb == 2.0
    assert r.tiers.local.keep_alive == "30m"


@pytest.mark.parametrize(
    ("text", "needle"),
    [
        ("default_scope: everywhere\n", "default_scope"),
        ("gateway:\n  port: 99999\n", "gateway.port"),
        ("telemetry: true\n", "telemetry"),
        ("strategyy: cost_saver\n", "strategyy"),
        ("secrets:\n  action: shout\n", "secrets.action"),
        ("budget:\n  paid_daily_usd: -1\n", "budget.paid_daily_usd"),
        ("version: 2\n", "version"),
    ],
)
def test_invalid_values_name_the_field(tmp_path: Path, text: str, needle: str) -> None:
    target = tmp_path / "rules.yaml"
    target.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError) as info:
        load_rules(target)
    assert any(needle in p for p in info.value.problems)
    assert str(target) in str(info.value)


def test_yaml_syntax_error_has_line(tmp_path: Path) -> None:
    target = tmp_path / "rules.yaml"
    target.write_text("strategy: [unclosed\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="YAML syntax error at line"):
        load_rules(target)


def test_non_mapping_and_missing_file(tmp_path: Path) -> None:
    target = tmp_path / "rules.yaml"
    target.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="mapping"):
        load_rules(target)
    with pytest.raises(ConfigError, match="cannot read"):
        load_rules(tmp_path / "missing.yaml")


def test_schema_is_valid_json_and_mentions_fields() -> None:
    schema = json.loads(schema_json())
    assert schema["$schema"].startswith("https://json-schema.org")
    for key in ("default_scope", "tiers", "floors", "hosts", "advice", "features"):
        assert key in schema["properties"]


def test_committed_schema_is_current() -> None:
    committed = (REPO_ROOT / SCHEMA_RELATIVE_PATH).read_bytes().decode("utf-8")
    assert committed.replace("\r\n", "\n") == schema_json()


def test_schema_cli_write_and_check(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "s.json"
    assert main(["--check", str(out)]) == 1
    assert main(["--write", str(out)]) == 0
    assert main(["--check", str(out)]) == 0
    out.write_text("{}\n", encoding="utf-8")
    assert main(["--check", str(out)]) == 1
    assert main([]) == 0
    assert json.loads(capsys.readouterr().out)["title"] == "Headwaters rules.yaml"


def test_default_file_content_is_plain_yaml(tmp_path: Path) -> None:
    target = tmp_path / "rules.yaml"
    ensure_rules_file(target)
    assert yaml.safe_load(target.read_text(encoding="utf-8"))["version"] == 1
