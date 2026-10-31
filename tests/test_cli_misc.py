"""HDW-118: ``version``, ``features``, ``catalog`` and the shared JSON/exit-code conventions."""

from __future__ import annotations

from pathlib import Path

import pytest

import headwaters
from headwaters.core.features import STAR_LEGEND
from tests.cli_support import home, invoke, invoke_json, write_rules


@pytest.fixture
def cli_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return home(tmp_path, monkeypatch)


def test_version_command_and_json(cli_home: Path) -> None:
    assert invoke("version").stdout.strip() == headwaters.__version__
    _, data = invoke_json("version")
    assert data == {"ok": True, "version": headwaters.__version__}


def test_features_lists_core_and_star_with_legend(cli_home: Path) -> None:
    result = invoke("features")
    assert result.exit_code == 0
    assert "Core (free forever)" in result.stdout
    assert "Free and local scopes, Private mode" in result.stdout
    assert STAR_LEGEND in result.stdout
    assert "_example" not in result.stdout  # internal example feature stays hidden


def test_features_json_separates_core_from_star(cli_home: Path) -> None:
    _, data = invoke_json("features")
    assert data["legend"] == STAR_LEGEND
    assert {f["id"] for f in data["core"]} >= {"gateway", "mcp", "failover", "verify_basic"}
    assert all(f["enabled"] for f in data["core"])  # core features cannot be switched off
    assert data["star"] == []  # no real star feature is installed yet


def test_features_respects_user_flags(cli_home: Path) -> None:
    # The internal example feature is hidden, so check the flag through the JSON of a real run:
    # switching a feature off in rules.yaml must be reflected, never crash the command.
    write_rules(cli_home, "features:\n  _example: false\n  something_else: false\n")
    result, data = invoke_json("features")
    assert result.exit_code == 0
    assert data["star"] == []
    assert all(f["enabled"] for f in data["core"])


def test_catalog_status_plain_and_json(cli_home: Path) -> None:
    out = invoke("catalog", "status").stdout
    assert "Active catalog: seed" in out
    assert "Feed URL: (not configured)" in out
    _, data = invoke_json("catalog", "status")
    assert data["source"] == "seed"
    assert data["url"] is None
    assert data["updates"] == "catalog.url is not configured"


def test_catalog_update_blocked_is_reported_as_json(cli_home: Path) -> None:
    write_rules(cli_home, "airplane: true\n")
    result, data = invoke_json("catalog", "update")
    assert result.exit_code == 0
    assert data["ok"] is True
    assert "airplane" in data["message"]


def test_invalid_rules_is_exit_2_for_every_command(cli_home: Path) -> None:
    write_rules(cli_home, "airplane: maybe\n")
    for args in (
        ["status"],
        ["models", "list"],
        ["catalog", "status"],
        ["features"],
        ["pick", "x"],
    ):
        result = invoke(*args)
        assert result.exit_code == 2, args
        assert "airplane" in result.output


def test_help_lists_every_command(cli_home: Path) -> None:
    out = invoke("--help").stdout
    for name in ("version", "pick", "status", "models", "scope", "private", "features", "catalog"):
        assert name in out


def test_json_output_is_pure_json_on_stdout(cli_home: Path) -> None:
    import json

    for args in (["status"], ["models", "list"], ["features"], ["scope"], ["private"]):
        result = invoke(*args, "--json")
        assert result.exit_code == 0, args
        json.loads(result.stdout)  # nothing but the document
