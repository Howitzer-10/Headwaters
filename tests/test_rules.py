import json
from pathlib import Path
from typing import Literal

import pytest
import yaml

from headwaters.core.rules import LocalTier, Rules, TiersRules
from headwaters.core.rules_io import (
    SCHEMA_RELATIVE_PATH,
    ConfigError,
    ensure_rules_file,
    load_rules,
    main,
    read_yaml_mapping,
    render_rules,
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
    ("written", "stored"),
    [("120", "120s"), ("0", "0s"), ("-1", "-1s"), ("30m", "30m"), ("1h30m", "1h30m")],
)
def test_numeric_keep_alive_gains_a_unit(tmp_path: Path, written: str, stored: str) -> None:
    """HDW-522: Ollama 400s a quoted bare number, so ``"120"`` is stored as ``"120s"``."""
    target = tmp_path / "rules.yaml"
    target.write_text(f'tiers:\n  local:\n    keep_alive: "{written}"\n', encoding="utf-8")
    assert load_rules(target).tiers.local.keep_alive == stored


def test_bare_numeric_keep_alive_is_rejected_with_the_fix(tmp_path: Path) -> None:
    """A YAML number never reaches Ollama; the error names the form that works (HDW-522)."""
    target = tmp_path / "rules.yaml"
    target.write_text("tiers:\n  local:\n    keep_alive: 120\n", encoding="utf-8")
    with pytest.raises(ConfigError) as caught:
        load_rules(target)
    assert "'120s'" in str(caught.value)


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
    text = target.read_text(encoding="utf-8")
    assert yaml.safe_load(text) is None, "a default is not a choice, so nothing is written"
    assert load_rules(target) == Rules()


def test_generated_file_pins_no_default(tmp_path: Path) -> None:
    """D21: a default written out is a default pinned, so a later release cannot change it."""
    target = tmp_path / "rules.yaml"
    ensure_rules_file(target)
    assert read_yaml_mapping(target) == {}, "the file chooses nothing, so it pins nothing"
    text = target.read_text(encoding="utf-8")
    assert "keep_warm_model: auto" not in text


def test_generated_file_still_names_the_fields_a_person_edits(tmp_path: Path) -> None:
    """A usable starting point: the reference names the settings, commented out."""
    target = tmp_path / "rules.yaml"
    ensure_rules_file(target)
    text = target.read_text(encoding="utf-8")
    for needle in ("#   default_scope:", "#       keep_warm_model:", "#     port: 4000"):
        assert needle in text, needle


def test_the_reference_writes_words_that_yaml_reads_as_booleans_quoted() -> None:
    """``off`` unquoted is YAML ``false``, and every field that accepts it is typed ``str``."""
    text = render_rules(Rules())
    assert '#       keep_warm_model: "off"' in text
    assert '#   advice: "off"' in text


class _NextRelease(Rules):
    """A later release whose default moved: the D21 shape, with ``advice`` instead."""

    advice: Literal["off", "inline", "header_only"] = "inline"


def test_a_changed_default_reaches_a_file_written_by_an_older_version(tmp_path: Path) -> None:
    target = tmp_path / "rules.yaml"
    ensure_rules_file(target)
    assert load_rules(target).advice == "off"
    updated = _NextRelease.model_validate(read_yaml_mapping(target))
    assert updated.advice == "inline"


def test_an_explicit_value_is_written_and_read_back(tmp_path: Path) -> None:
    chosen = Rules(tiers=TiersRules(local=LocalTier(keep_warm_model="auto")))
    text = render_rules(chosen)
    assert "keep_warm_model: auto" in text
    target = tmp_path / "rules.yaml"
    target.write_text(text, encoding="utf-8", newline="\n")
    assert read_yaml_mapping(target) == {"tiers": {"local": {"keep_warm_model": "auto"}}}
    loaded = load_rules(target)
    assert loaded.tiers.local.keep_warm_model == "auto"
    assert loaded == chosen


@pytest.mark.parametrize("written", ["off", "false", "no"])
def test_unquoted_yaml_false_disables_keep_warm(tmp_path: Path, written: str) -> None:
    """D21: YAML reads bare ``off``/``no``/``false`` as a boolean, not as the word."""
    target = tmp_path / "rules.yaml"
    target.write_text(f"tiers:\n  local:\n    keep_warm_model: {written}\n", encoding="utf-8")
    assert load_rules(target).tiers.local.keep_warm_model == "off"


@pytest.mark.parametrize("written", ["on", "true", "yes"])
def test_unquoted_yaml_true_means_keep_warm(tmp_path: Path, written: str) -> None:
    """``on`` means keep warm; ``auto`` says that without inventing a model name."""
    target = tmp_path / "rules.yaml"
    target.write_text(f"tiers:\n  local:\n    keep_warm_model: {written}\n", encoding="utf-8")
    assert load_rules(target).tiers.local.keep_warm_model == "auto"


@pytest.mark.parametrize("chosen", ["off", "auto", "qwen3:8b"])
def test_a_quoted_value_is_still_written_verbatim(tmp_path: Path, chosen: str) -> None:
    """The documented form (quoted) is untouched by the boolean mapping."""
    target = tmp_path / "rules.yaml"
    target.write_text(f'tiers:\n  local:\n    keep_warm_model: "{chosen}"\n', encoding="utf-8")
    assert load_rules(target).tiers.local.keep_warm_model == chosen


@pytest.mark.parametrize("chosen", ["off", "auto", "qwen3:8b"])
def test_a_yaml_round_trip_keeps_the_value(tmp_path: Path, chosen: str) -> None:
    """A dump-and-read cycle must not flip the default: ``off`` comes back unquoted."""
    rules = Rules(tiers=TiersRules(local=LocalTier(keep_warm_model=chosen)))
    target = tmp_path / "rules.yaml"
    target.write_text(yaml.safe_dump(rules.model_dump(), sort_keys=False), encoding="utf-8")
    assert load_rules(target).tiers.local.keep_warm_model == chosen


def test_a_non_text_non_boolean_keep_warm_model_still_fails(tmp_path: Path) -> None:
    """Tolerating YAML's booleans must not turn the field into a catch-all."""
    target = tmp_path / "rules.yaml"
    target.write_text("tiers:\n  local:\n    keep_warm_model: 3\n", encoding="utf-8")
    with pytest.raises(ConfigError) as caught:
        load_rules(target)
    assert any("keep_warm_model" in p for p in caught.value.problems)


def test_schema_still_declares_keep_warm_model_as_a_string() -> None:
    """``catalog/rules.schema.json`` is a contract: the loader tolerates, the schema does not."""
    field = json.loads(schema_json())["$defs"]["LocalTier"]["properties"]["keep_warm_model"]
    assert field["type"] == "string"
    assert field["default"] == "off"
