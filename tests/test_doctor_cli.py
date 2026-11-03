"""HDW-402: ``headwaters doctor`` through the real Typer app, with every external probe faked.

Nothing here reaches Ollama, a provider, the network or the Windows Credential Manager: the doctor
module's probes are monkeypatched and the key store is ``FakeKeyring``-backed.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from headwaters.cli import cmd_doctor
from headwaters.control import doctor
from headwaters.control.doctor import OK, Check, Report
from headwaters.core.keys import KeyStore
from headwaters.core.rules import Rules
from tests.cli_support import home, invoke, invoke_json
from tests.keys_support import SECRET, FakeKeyring

TOKEN = "daemon-token-5f3a9c1e7b2d"


@pytest.fixture
def cli_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home(tmp_path, monkeypatch)
    keyring = FakeKeyring()
    monkeypatch.setattr(doctor, "read_ollama_version", lambda host, http=None: ("0.40.13", ""))
    monkeypatch.setattr(
        doctor, "read_ollama_env", lambda **kw: dict.fromkeys(doctor.OLLAMA_ENV_VARS, "")
    )
    monkeypatch.setattr(doctor, "read_token", lambda: TOKEN)
    for module in (doctor, cmd_doctor):
        monkeypatch.setattr(
            module, "KeyStore", lambda rules: KeyStore(rules, env={}, backend=keyring)
        )
    monkeypatch.setattr(cmd_doctor, "read_token", lambda: TOKEN)
    return tmp_path


def test_doctor_prints_a_table_and_a_fix_list(cli_home: Path) -> None:
    result = invoke("doctor")
    assert result.exit_code == 0, result.output
    assert "Ollama" in result.output
    assert "Daemon" in result.output
    assert "Fix list:" in result.output


def test_doctor_json_is_the_report(cli_home: Path) -> None:
    result, payload = invoke_json("doctor")
    assert result.exit_code == 0, result.output
    assert payload["ok"] is True
    assert payload["host"] is None
    ids = [row["id"] for row in payload["checks"]]
    assert ids[:3] == ["environment", "ollama", "ollama_env"]
    assert "daemon_token" in ids
    assert payload["fixes"]


def test_an_unknown_host_is_a_config_error(cli_home: Path) -> None:
    result = invoke("doctor", "--host", "vim")
    assert result.exit_code == 2
    assert "unknown host 'vim'" in result.output
    assert "claude-code" in result.output


def test_an_unknown_host_is_a_config_error_in_json(cli_home: Path) -> None:
    result, payload = invoke_json("doctor", "--host", "vim")
    assert result.exit_code == 2
    assert payload["error"]["code"] == "unknown_host"


def test_a_host_probe_that_passes_adds_three_rows(
    cli_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [
        {"name": "round_trip", "ok": True, "detail": "9 tools listed"},
        {"name": "tool_call", "ok": True, "detail": "headwaters_status answered"},
        {"name": "scope", "ok": True, "detail": "effective scope auto"},
    ]
    monkeypatch.setattr(doctor, "host_probe", lambda host: {"ok": True, "checks": rows})
    result, payload = invoke_json("doctor", "--host", "claude-code")
    assert result.exit_code == 0, result.output
    assert payload["host"] == "claude-code"
    assert [row["id"] for row in payload["checks"]][-3:] == [
        "host_round_trip",
        "host_tool_call",
        "host_scope",
    ]


def test_a_host_probe_that_fails_exits_one(cli_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor, "host_probe", lambda host: {"ok": False, "error": "no daemon"})
    result = invoke("doctor", "--host", "vscode")
    assert result.exit_code == 1
    assert "Host round trip" in result.output


def test_bundle_writes_a_redacted_zip_and_names_it(
    cli_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    leaked = f"pid 42 holds {SECRET}"
    report = Report((Check("keys", "API keys", OK, leaked),), host=None)
    monkeypatch.setattr(cmd_doctor, "run_doctor", lambda rules, **kw: report)
    result = invoke("doctor", "--bundle")
    assert result.exit_code == 0, result.output
    line = next(row for row in result.output.splitlines() if row.startswith("Bundle: "))
    target = Path(line.split("Bundle: ", 1)[1].strip())
    assert target.is_file()
    with zipfile.ZipFile(target) as archive:
        assert sorted(archive.namelist()) == [
            "environment.json",
            "manifest.json",
            "report.json",
            "report.txt",
        ]
        text = "\n".join(archive.read(name).decode("utf-8") for name in archive.namelist())
    assert SECRET not in text
    assert "<redacted>" in text


def test_bundle_is_named_in_json_too(cli_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        cmd_doctor, "run_doctor", lambda rules, **kw: Report((Check("x", "X", OK),))
    )
    result, payload = invoke_json("doctor", "--bundle")
    assert result.exit_code == 0, result.output
    assert Path(payload["bundle"]).is_file()


def test_bundle_lives_under_the_state_dir(cli_home: Path) -> None:
    from headwaters.core import paths

    _, payload = invoke_json("doctor", "--bundle")
    assert paths.state_dir().is_dir()
    assert Path(payload["bundle"]).parent == paths.state_dir() / "diagnostics"


def test_the_secret_list_carries_the_token_and_every_stored_key(cli_home: Path) -> None:
    store = cmd_doctor.KeyStore(Rules())
    store.save("work", SECRET, provider="openrouter")
    assert set(cmd_doctor._secrets(Rules())) == {TOKEN, SECRET}


def test_a_broken_store_still_yields_the_token(
    cli_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.keys_support import BrokenKeyring

    monkeypatch.setattr(
        cmd_doctor, "KeyStore", lambda rules: KeyStore(rules, env={}, backend=BrokenKeyring())
    )
    assert cmd_doctor._secrets(Rules()) == [TOKEN]


def test_doctor_is_registered_on_the_app() -> None:
    from headwaters.cli.main import app

    names = {command.name for command in app.registered_commands}
    assert "doctor" in names
