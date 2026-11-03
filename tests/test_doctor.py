"""HDW-402: every ``headwaters doctor`` check, with injected probes (no daemon, GPU or network).

The checks take their probes as arguments (``Doctor`` fields), which is why nothing here needs a
running Ollama, a real GPU, a live daemon or the credential store: each row is exercised against
the smallest fake that produces it.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from headwaters.control import doctor as doc
from headwaters.control import keys as key_checks
from headwaters.control.doctor import (
    FAIL,
    HOSTS,
    OK,
    SKIP,
    WARN,
    Doctor,
    parse_version,
    read_ollama_env,
    render_text,
    run_doctor,
)
from headwaters.control.keys import PingResult
from headwaters.core.catalog import updater as catalog_updater
from headwaters.core.catalog.http import SourceError
from headwaters.core.catalog.updater import CatalogStatus
from headwaters.core.daemon_client import DaemonError
from headwaters.core.daemon_info import DaemonInfo, DaemonPorts
from headwaters.core.hardware.state import GpuBusy
from headwaters.core.hardware.sysmem import SYSMEM_HINT
from headwaters.core.keys import KeyStore
from headwaters.core.rules import Rules
from tests.cli_support import gpu_state, home
from tests.keys_support import SECRET, FakeKeyring

INFO = DaemonInfo(4321, 1.0, "9.9.9", DaemonPorts(4002, 4000, 4001))


@pytest.fixture
def cli_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    return home(tmp_path, monkeypatch)


class FakeHttp:
    def __init__(self, body: Any = None, *, error: Exception | None = None) -> None:
        self.body = body
        self.error = error
        self.urls: list[str] = []

    def request_json(self, method: str, url: str, **_kwargs: Any) -> Any:
        self.urls.append(url)
        if self.error is not None:
            raise self.error
        return self.body


def _doctor(**fields: Any) -> Doctor:
    base: dict[str, Any] = {
        "env": {},
        "excluded": [],
        "port_state": lambda port: "free",
        "git_config": lambda key: None,
        "hardware": gpu_state(),
    }
    base.update(fields)
    rules = base.pop("rules", None) or Rules()
    return Doctor(rules, **base)


class FakeClient:
    def __init__(self, info: DaemonInfo = INFO, *, error: Exception | None = None) -> None:
        self.info = info
        self.error = error

    def status(self) -> dict[str, Any]:
        if self.error is not None:
            raise self.error
        return {"ok": True, "pid": self.info.pid, "uptime_s": 61}


def _store() -> KeyStore:
    return KeyStore(Rules(), env={}, backend=FakeKeyring())


def _ping(*rows: PingResult) -> Any:
    return lambda store, rules: list(rows)


def test_environment_row_names_the_versions(cli_home: Path) -> None:
    check = _doctor()._environment()
    assert check.status == OK
    assert "Python" in check.detail
    assert check.data["headwaters"] == doc.headwaters.__version__


def test_ollama_reports_the_version_it_answers(cli_home: Path) -> None:
    check = _doctor(http=FakeHttp({"version": "0.40.13"}))._ollama()
    assert (check.status, check.id) == (OK, "ollama")
    assert "0.40.13" in check.detail


def test_ollama_warns_below_the_supported_floor(cli_home: Path) -> None:
    check = _doctor(http=FakeHttp({"version": "0.39.9"}))._ollama()
    assert check.status == WARN
    assert "older than 0.40.0" in check.detail
    assert check.fix is not None


def test_ollama_warns_when_nothing_answers(cli_home: Path) -> None:
    check = _doctor(http=FakeHttp(error=SourceError("connection refused")))._ollama()
    assert check.status == WARN
    assert "connection refused" in check.detail
    assert "Install Ollama" in (check.fix or "")


def test_ollama_version_rejects_a_reply_without_one(cli_home: Path) -> None:
    assert doc.read_ollama_version("http://x", FakeHttp({})) == (None, "the reply has no version")
    assert parse_version("0.40.13") == (0, 40, 13)
    assert parse_version("v1.2.3-rc1") == (1, 2, 3)
    assert parse_version("nightly") is None


def test_ollama_env_is_ok_when_every_value_is_set(cli_home: Path) -> None:
    check = _doctor(
        env={
            "OLLAMA_FLASH_ATTENTION": "1",
            "OLLAMA_KV_CACHE_TYPE": "q8_0",
            "OLLAMA_MAX_LOADED_MODELS": "1",
        }
    )._ollama_env()
    assert check.status == OK


def test_ollama_env_warns_with_setx_and_a_restart(cli_home: Path) -> None:
    check = _doctor(env={"OLLAMA_MAX_LOADED_MODELS": "3"})._ollama_env()
    assert check.status == WARN
    assert "setx OLLAMA_FLASH_ATTENTION 1" in (check.fix or "")
    assert "restart the Ollama tray app" in (check.fix or "")
    assert "OLLAMA_MAX_LOADED_MODELS is more than 1" in check.detail


def test_read_ollama_env_prefers_the_process_over_the_registry(cli_home: Path) -> None:
    values = read_ollama_env(
        source={"OLLAMA_FLASH_ATTENTION": "0"},
        platform="win32",
        registry={"OLLAMA_FLASH_ATTENTION": "1", "OLLAMA_KV_CACHE_TYPE": "q8_0"},
    )
    assert values["OLLAMA_FLASH_ATTENTION"] == "0"
    assert values["OLLAMA_KV_CACHE_TYPE"] == "q8_0"
    assert values["OLLAMA_NO_CLOUD"] == ""


def test_private_mode_is_skipped_when_it_is_off(cli_home: Path) -> None:
    check = _doctor()._private_mode()
    assert check.status == SKIP
    assert "off" in check.detail


def test_private_mode_hints_at_ollama_no_cloud_and_a_restart(cli_home: Path) -> None:
    """FU-121-1: Private Mode must say Ollama can still reach the cloud, and how to stop it."""
    check = _doctor(rules=_private())._private_mode()
    assert check.status == WARN
    fix = check.fix or ""
    assert "OLLAMA_NO_CLOUD=1" in fix
    assert "server.json" in fix
    assert "restart" in fix.lower()


def _private() -> Rules:
    return Rules(default_scope="local")


def test_private_mode_accepts_the_environment_variable(cli_home: Path) -> None:
    check = _doctor(rules=_private(), env={"OLLAMA_NO_CLOUD": "1"})._private_mode()
    assert check.status == OK
    assert "OLLAMA_NO_CLOUD=1" in check.detail


def test_private_mode_accepts_server_json(cli_home: Path, tmp_path: Path) -> None:
    path = tmp_path / ".ollama" / "server.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"disable_ollama_cloud": true}', encoding="utf-8")
    check = _doctor(rules=_private())._private_mode()
    assert check.status == OK
    assert "disable_ollama_cloud" in check.detail


def test_gpu_is_skipped_without_a_snapshot(cli_home: Path) -> None:
    check = _doctor(hardware=None)._gpu()
    assert check.status == SKIP


def test_gpu_warns_when_there_is_no_nvidia_card(cli_home: Path) -> None:
    check = _doctor(hardware=replace(gpu_state(), gpu=None))._gpu()
    assert check.status == WARN
    assert "no NVIDIA GPU" in check.detail


def test_gpu_warns_while_another_app_holds_it(cli_home: Path) -> None:
    busy = GpuBusy(True, 9000.0, 90.0, "game.exe", "a game is rendering")
    check = _doctor(hardware=replace(gpu_state(), gpu_busy=busy))._gpu()
    assert check.status == WARN
    assert "a game is rendering" in check.detail
    assert check.fix is not None


def test_gpu_points_at_the_sysmem_setting(cli_home: Path) -> None:
    state = replace(gpu_state(), notes=("sysmem fallback seen on 2 runs",))
    check = _doctor(hardware=state)._gpu()
    assert check.status == WARN
    assert check.fix == SYSMEM_HINT


def test_ports_are_ok_when_all_three_are_free(cli_home: Path) -> None:
    check = _doctor()._ports()
    assert check.status == OK
    assert set(check.data) == {"api", "gateway", "dashboard"}


def test_ports_warn_about_a_windows_excluded_range(cli_home: Path) -> None:
    check = _doctor(excluded=[(4000, 4010)], port_state=lambda port: "free")._ports()
    assert check.status == WARN
    assert "netsh int ipv4 show excludedportrange protocol=tcp" in (check.fix or "")


def test_ports_warn_when_something_else_holds_one(cli_home: Path) -> None:
    check = _doctor(port_state=lambda port: "busy")._ports()
    assert check.status == WARN
    assert "is busy" in check.detail


def test_ports_held_by_the_running_daemon_are_not_a_warning(cli_home: Path) -> None:
    doctor = _doctor(find=lambda: FakeClient(), port_state=lambda port: "busy")
    doctor._client = FakeClient()  # type: ignore[assignment]
    check = doctor._ports()
    assert check.status == OK


def test_daemon_warns_when_it_is_not_running(cli_home: Path) -> None:
    check = _doctor(find=lambda: None)._daemon()
    assert check.status == WARN
    assert "not running" in check.detail
    assert check.fix == "headwaters start"


def test_daemon_offers_force_stop_for_a_stale_file(
    cli_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FU-201-10: a daemon.json that outlives its daemon has a way out."""
    monkeypatch.setattr(doc, "read_info", lambda: INFO)
    check = _doctor(find=lambda: None)._daemon()
    assert check.status == WARN
    assert str(INFO.pid) in check.detail
    assert check.fix == "headwaters stop --force"


def test_daemon_reports_a_healthy_client(cli_home: Path) -> None:
    check = _doctor(find=lambda: FakeClient())._daemon()
    assert check.status == OK
    assert f"pid {INFO.pid}" in check.detail
    assert "up 61 s" in check.detail


def test_daemon_offers_force_stop_when_the_api_stops_answering(cli_home: Path) -> None:
    client = FakeClient(error=DaemonError("timed out"))
    check = _doctor(find=lambda: client)._daemon()
    assert check.status == WARN
    assert "timed out" in check.detail
    assert check.fix == "headwaters stop --force"


def test_token_warns_when_it_has_not_been_written_yet(
    cli_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(doc, "read_token", lambda: None)
    check = _doctor()._token()
    assert check.status == WARN
    assert check.fix == "headwaters start"


def test_token_states_the_windows_acl_caveat(
    cli_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FU-201-5: daemon.token relies on the profile folder, with no explicit ACL."""
    monkeypatch.setattr(doc, "read_token", lambda: "tok")
    check = _doctor(platform="win32")._token()
    assert check.status == OK
    assert "daemon.token is present" in check.detail
    assert "no explicit ACL" in check.detail
    assert "another process of the same user can read it" in check.detail
    assert check.data["windows_acl"] == "per-user profile folder only"


def test_token_omits_the_windows_caveat_elsewhere(
    cli_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(doc, "read_token", lambda: "tok")
    assert "ACL" not in _doctor(platform="linux")._token().detail


def test_keys_are_skipped_when_none_are_saved(cli_home: Path) -> None:
    check = _doctor(store=_store())._keys()
    assert check.status == SKIP
    assert "no keys saved" in check.detail


def test_keys_are_skipped_while_every_provider_is_blocked(cli_home: Path) -> None:
    store = _store()
    store.save("work", SECRET, provider="openrouter")
    check = _doctor(
        rules=Rules(airplane=True),
        store=store,
        ping=_ping(PingResult("work", "openrouter", key_checks.BLOCKED, "airplane mode")),
    )._keys()
    assert check.status == SKIP
    assert "none checked" in check.detail


def test_keys_warn_when_a_provider_rejects_one(cli_home: Path) -> None:
    store = _store()
    store.save("work", SECRET, provider="openrouter")
    check = _doctor(
        store=store,
        ping=_ping(PingResult("work", "openrouter", key_checks.UNAUTHORIZED, "HTTP 401")),
    )._keys()
    assert check.status == WARN
    assert "work unauthorized" in check.detail
    assert "headwaters keys set" in (check.fix or "")


def test_keys_warn_when_a_provider_cannot_be_reached(cli_home: Path) -> None:
    store = _store()
    store.save("work", SECRET, provider="openrouter")
    check = _doctor(
        store=store,
        ping=_ping(PingResult("work", "openrouter", key_checks.UNREACHABLE, "timed out")),
    )._keys()
    assert check.status == WARN
    assert "airplane mode" in (check.fix or "")


def test_keys_are_ok_when_every_provider_answers(cli_home: Path) -> None:
    store = _store()
    store.save("work", SECRET, provider="openrouter")
    check = _doctor(
        store=store, ping=_ping(PingResult("work", "openrouter", key_checks.OK, "HTTP 200"))
    )._keys()
    assert check.status == OK
    assert SECRET not in check.detail


def test_keys_warn_when_the_store_cannot_be_read(cli_home: Path) -> None:
    class Broken:
        def entries(self) -> list[Any]:
            raise OSError("the credential store is locked")

    check = _doctor(store=Broken())._keys()
    assert check.status == WARN
    assert "cannot be read" in check.detail


def _catalog(**fields: Any) -> CatalogStatus:
    base: dict[str, Any] = {
        "source": "seed",
        "catalog_version": "2026.01.1",
        "etag": None,
        "fetched_at": None,
        "url": None,
        "update_blocked": None,
    }
    base.update(fields)
    return CatalogStatus(**base)


def test_catalog_is_ok_with_the_seed_catalog(cli_home: Path) -> None:
    check = _doctor(catalog_status=_catalog())._catalog()
    assert check.status == OK
    assert "seed catalog" in check.detail


def test_catalog_warns_when_it_is_a_week_stale(cli_home: Path) -> None:
    check = _doctor(
        catalog_status=_catalog(fetched_at=1_000.0, etag="abc"), now=1_000.0 + 8 * 86400
    )._catalog()
    assert check.status == WARN
    assert "8.0 days ago" in check.detail
    assert check.fix == "headwaters catalog update"


def test_catalog_does_not_warn_when_updates_are_off(cli_home: Path) -> None:
    check = _doctor(
        catalog_status=_catalog(fetched_at=1_000.0, update_blocked="airplane mode"),
        now=1_000.0 + 30 * 86400,
    )._catalog()
    assert check.status == OK
    assert "updates off (airplane mode)" in check.detail


def test_git_warns_about_autocrlf_true(cli_home: Path) -> None:
    check = _doctor(git_config=lambda key: "true")._git()
    assert check.status == WARN
    assert "core.autocrlf=true" in check.detail
    assert "input" in (check.fix or "")


def test_git_accepts_unset_and_input(cli_home: Path) -> None:
    assert _doctor()._git().status == OK
    assert _doctor(git_config=lambda key: "input")._git().status == OK


def test_mcp_launcher_names_a_command(cli_home: Path) -> None:
    check = _doctor()._mcp_launch()
    assert check.status == OK
    assert check.data["command"]


def _probe(rows: list[dict[str, Any]]) -> Any:
    return lambda host: {"ok": all(row["ok"] for row in rows), "checks": rows}


def test_host_checks_pass_when_the_probe_does(cli_home: Path) -> None:
    doctor = _doctor(
        host="claude-code",
        probe=_probe(
            [
                {"name": "round_trip", "ok": True, "detail": "9 tools listed"},
                {"name": "tool_call", "ok": True, "detail": "headwaters_status answered"},
                {"name": "scope", "ok": True, "detail": "effective scope auto"},
            ]
        ),
    )
    checks = doctor._host_checks()
    assert [check.status for check in checks] == [OK, OK, OK]
    assert checks[0].detail == "claude-code: 9 tools listed"


def test_host_checks_fail_as_a_group_when_the_probe_cannot_run(cli_home: Path) -> None:
    doctor = _doctor(host="vscode", probe=lambda host: {"ok": False, "error": "no daemon"})
    checks = doctor._host_checks()
    assert [check.status for check in checks] == [FAIL, FAIL, FAIL]
    assert all("no daemon" in check.detail for check in checks)
    assert all(check.fix == doc.HOST_FIX for check in checks)


def test_a_failed_host_check_offers_the_host_fix(cli_home: Path) -> None:
    doctor = _doctor(
        host="cursor",
        probe=_probe(
            [
                {"name": "round_trip", "ok": True, "detail": "9 tools listed"},
                {"name": "tool_call", "ok": False, "detail": "headwaters_status returned an error"},
                {"name": "scope", "ok": True, "detail": "effective scope auto"},
            ]
        ),
    )
    checks = doctor._host_checks()
    assert [check.status for check in checks] == [OK, FAIL, OK]
    assert checks[1].fix == doc.HOST_FIX


def test_run_covers_every_check_and_adds_the_host_rows(cli_home: Path) -> None:
    report = run_doctor(
        Rules(),
        find=lambda: None,
        env={},
        excluded=[],
        port_state=lambda port: "free",
        git_config=lambda key: None,
        hardware=gpu_state(),
        catalog_status=_catalog(),
        store=_store(),
        http=FakeHttp({"version": "0.40.13"}),
        platform="win32",
    )
    assert [check.id for check in report.checks] == [
        "environment",
        "ollama",
        "ollama_env",
        "private_mode",
        "gpu",
        "ports",
        "daemon",
        "daemon_token",
        "keys",
        "catalog",
        "git",
        "mcp_launch",
        "host_configs",
    ]
    assert report.ok  # warnings do not fail it
    payload = report.as_dict()
    assert payload["host"] is None
    assert len(payload["checks"]) == len(report.checks)
    assert render_text(report).startswith("Headwaters doctor ")
    assert "Fix list:" in render_text(report)


def test_a_failed_check_makes_the_report_not_ok() -> None:
    from headwaters.control.doctor import Check, Report

    report = Report((Check("host_scope", "Host scope test", FAIL, "no", fix="fix it"),))
    assert not report.ok
    assert report.fixes == (("Host scope test", "fix it"),)


def test_fixes_are_deduplicated_in_order() -> None:
    from headwaters.control.doctor import Check, Report

    report = Report(
        (
            Check("a", "A", WARN, "x", fix="same"),
            Check("b", "B", WARN, "y", fix="same"),
            Check("c", "C", WARN, "z", fix="other"),
        )
    )
    assert [fix for _title, fix in report.fixes] == ["same", "other"]


def test_every_host_name_is_selectable() -> None:
    assert "claude-code" in HOSTS
    assert "claude-desktop" in HOSTS
    assert "all" in HOSTS


def test_host_probe_reads_the_last_json_line(
    cli_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Done:
        returncode = 0
        stdout = 'warning\n{"ok": true, "checks": []}\n'
        stderr = ""

    monkeypatch.setattr(doc.subprocess, "run", lambda *a, **kw: Done())
    assert doc.host_probe("vscode") == {"ok": True, "checks": []}


def test_host_probe_reports_when_nothing_was_printed(
    cli_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Done:
        returncode = 1
        stdout = ""
        stderr = "ModuleNotFoundError: mcp\n"

    monkeypatch.setattr(doc.subprocess, "run", lambda *a, **kw: Done())
    payload = doc.host_probe("vscode")
    assert payload["ok"] is False
    assert "ModuleNotFoundError" in payload["error"]


def test_catalog_updater_is_the_default_source(
    cli_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(catalog_updater, "status", lambda rules, **kw: _catalog(fetched_at=1.0))
    check = _doctor(now=2.0)._catalog()
    assert check.status == OK
