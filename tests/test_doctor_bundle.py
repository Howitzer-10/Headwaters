"""HDW-402 / FU-401-1: the ``--bundle`` zip holds no key, no prompt and no foreign path.

HDW-401 added the keyring-backed store, so the bundle is the one artefact that could leak a key in
a bug report: these tests open the zip and assert, member by member, that it cannot.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from headwaters.control.bundle import BUNDLE_FORMAT, PATH_MARK, REDACTED, build_bundle, redact
from headwaters.control.doctor import FAIL, OK, Check, Report
from headwaters.core import paths
from tests.cli_support import home
from tests.keys_support import OTHER_SECRET, SECRET

MEMBERS = ["report.json", "report.txt", "environment.json", "manifest.json"]


@pytest.fixture
def cli_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return home(tmp_path, monkeypatch)


def _report(*checks: Check) -> Report:
    return Report(checks or (Check("environment", "Environment", OK, "all good"),), host="vscode")


def _read(target: Path) -> dict[str, str]:
    with zipfile.ZipFile(target) as archive:
        return {name: archive.read(name).decode("utf-8") for name in archive.namelist()}


def test_the_bundle_holds_exactly_the_four_allowlisted_members(
    cli_home: Path, tmp_path: Path
) -> None:
    target = build_bundle(_report(), directory=tmp_path / "out")
    with zipfile.ZipFile(target) as archive:
        assert sorted(archive.namelist()) == sorted(MEMBERS)


def test_known_secrets_never_reach_a_member(cli_home: Path, tmp_path: Path) -> None:
    check = Check("daemon", "Daemon", OK, f"pid 42 using {SECRET} and {OTHER_SECRET}")
    target = build_bundle(_report(check), secrets=[SECRET, OTHER_SECRET], directory=tmp_path)
    text = "\n".join(_read(target).values())
    assert SECRET not in text
    assert OTHER_SECRET not in text
    assert REDACTED in text


def test_key_shaped_strings_are_scrubbed_even_when_unlisted(cli_home: Path, tmp_path: Path) -> None:
    leaked = "sk-live-9f8e7d6c5b4a39281706f5e4d3c2b1a0"
    check = Check("keys", "API keys", OK, f"provider echoed {leaked}")
    target = build_bundle(_report(check), directory=tmp_path)
    text = "\n".join(_read(target).values())
    assert leaked not in text
    assert "sk-live" not in text


def test_bearer_and_header_shapes_are_scrubbed(cli_home: Path, tmp_path: Path) -> None:
    detail = "Authorization: Bearer abcdef1234567890 x-api-key: 0123456789abcdef"
    target = build_bundle(_report(Check("keys", "API keys", OK, detail)), directory=tmp_path)
    text = "\n".join(_read(target).values())
    assert "abcdef1234567890" not in text
    assert "0123456789abcdef" not in text


def test_a_path_outside_the_app_dirs_becomes_a_marker(cli_home: Path, tmp_path: Path) -> None:
    detail = r"Ollama is installed in C:\Users\someone\AppData\Local\Programs\Ollama\app.exe"
    target = build_bundle(_report(Check("ollama", "Ollama", FAIL, detail)), directory=tmp_path)
    text = "\n".join(_read(target).values())
    assert "someone" not in text
    assert PATH_MARK in text


def test_a_path_inside_the_app_dirs_is_kept(cli_home: Path, tmp_path: Path) -> None:
    state = str(paths.state_dir() / "daemon.json")
    target = build_bundle(_report(Check("daemon", "Daemon", OK, state)), directory=tmp_path)
    text = "\n".join(_read(target).values())
    assert state.replace("\\", "\\\\") in text or state in text


def test_the_report_json_is_the_whole_report(cli_home: Path, tmp_path: Path) -> None:
    report = _report(
        Check("environment", "Environment", OK, "fine"),
        Check("host_scope", "Host scope test", FAIL, "no scope", fix="fix it"),
    )
    members = _read(build_bundle(report, directory=tmp_path))
    payload = json.loads(members["report.json"])
    assert payload["host"] == "vscode"
    assert [row["id"] for row in payload["checks"]] == ["environment", "host_scope"]
    assert payload["fixes"] == [{"check": "Host scope test", "fix": "fix it"}]
    assert "[fail]" in members["report.txt"]


def test_the_manifest_names_the_members_and_the_format(cli_home: Path, tmp_path: Path) -> None:
    members = _read(build_bundle(_report(), directory=tmp_path))
    manifest = json.loads(members["manifest.json"])
    assert manifest["format"] == BUNDLE_FORMAT
    assert manifest["members"] == MEMBERS
    assert manifest["host"] == "vscode"
    assert "no prompt text" in manifest["redaction"]


def test_the_environment_member_is_the_injected_one(cli_home: Path, tmp_path: Path) -> None:
    supplied = {"ollama": {"OLLAMA_FLASH_ATTENTION": "1"}, "app_dirs": {"state": "x"}}
    members = _read(
        build_bundle(_report(), directory=tmp_path, environment=supplied, roots=[tmp_path])
    )
    assert json.loads(members["environment.json"]) == supplied


def test_nothing_but_the_four_members_is_written(cli_home: Path, tmp_path: Path) -> None:
    target = build_bundle(_report(), directory=tmp_path)
    assert [item.name for item in target.parent.iterdir()] == [target.name]
    assert target.suffix == ".zip"


def test_redact_leaves_ordinary_text_alone(cli_home: Path) -> None:
    assert redact("9 tools listed", secrets=["abc"]) == "9 tools listed"
    assert redact("token abcdef", secrets=["abcdef"]) == f"token {REDACTED}"


def test_redact_does_not_touch_urls(cli_home: Path) -> None:
    assert (
        redact("https://api.anthropic.com/v1/messages") == "https://api.anthropic.com/v1/messages"
    )
