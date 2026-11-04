"""The at-logon task definition and the schtasks plans (HDW-405).

No test registers a task: every call goes through an injected runner that only records argv, and
the definition lands in ``tmp_path``.
"""

from __future__ import annotations

import subprocess
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from headwaters.packaging import autostart
from headwaters.packaging.layout import Install

NS = f"{{{autostart.TASK_NS}}}"


def _install(tmp_path: Path) -> Install:
    return Install.at(tmp_path / "Headwaters")


def fake_runner(code: int = 0, stdout: str = "", calls: list[list[str]] | None = None) -> Any:
    def run(argv: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if calls is not None:
            calls.append(list(argv))
        return subprocess.CompletedProcess(list(argv), code, stdout, "")

    return run


def _settings_xml(enabled: bool) -> str:
    return (
        f'<Task xmlns="{autostart.TASK_NS}"><Settings>'
        f"<Enabled>{'true' if enabled else 'false'}</Enabled></Settings></Task>"
    )


def test_the_task_runs_the_cli_as_the_user_with_least_privilege(tmp_path: Path) -> None:
    install = _install(tmp_path)

    root = ET.fromstring(autostart.task_xml(user="WS\\ana", command=install.cli))

    principal = root.find(f"{NS}Principals/{NS}Principal")
    assert principal is not None
    assert principal.findtext(f"{NS}UserId") == "WS\\ana"
    assert principal.findtext(f"{NS}LogonType") == "InteractiveToken"
    assert principal.findtext(f"{NS}RunLevel") == "LeastPrivilege"
    trigger = root.find(f"{NS}Triggers/{NS}LogonTrigger")
    assert trigger is not None
    assert trigger.findtext(f"{NS}UserId") == "WS\\ana"
    assert trigger.findtext(f"{NS}Enabled") == "true"
    action = root.find(f"{NS}Actions/{NS}Exec")
    assert action is not None
    assert action.findtext(f"{NS}Command") == str(install.cli)
    assert action.findtext(f"{NS}Arguments") == "start"
    settings = root.find(f"{NS}Settings")
    assert settings is not None
    assert settings.findtext(f"{NS}MultipleInstancesPolicy") == "IgnoreNew"
    assert settings.findtext(f"{NS}ExecutionTimeLimit") == "PT0S"
    assert root.findtext(f"{NS}RegistrationInfo/{NS}URI") == "\\Headwaters"


def test_a_disabled_task_keeps_its_definition_but_not_its_trigger(tmp_path: Path) -> None:
    install = _install(tmp_path)

    root = ET.fromstring(autostart.task_xml(user="ana", command=install.cli, enabled=False))

    assert root.findtext(f"{NS}Triggers/{NS}LogonTrigger/{NS}Enabled") == "false"
    assert root.findtext(f"{NS}Settings/{NS}Enabled") == "false"


def test_the_definition_is_written_as_utf16_and_parses_back(tmp_path: Path) -> None:
    install = _install(tmp_path)

    path = autostart.write_task_file(install, autostart.task_xml(user="ana", command=install.cli))

    assert path == install.autostart_xml
    assert path.read_bytes().startswith(b"\xff\xfe")  # UTF-16 little endian with a BOM
    assert ET.fromstring(path.read_text(encoding="utf-16")).tag == f"{NS}Task"


def test_create_registers_the_written_definition_once(tmp_path: Path) -> None:
    install = _install(tmp_path)
    calls: list[list[str]] = []

    steps = autostart.create_steps(install.autostart_xml)

    assert autostart.apply(steps, runner=fake_runner(calls=calls)) == 0
    assert calls == [
        [
            "schtasks",
            "/Create",
            "/TN",
            "\\Headwaters",
            "/XML",
            str(install.autostart_xml),
            "/F",
        ]
    ]


def test_the_toggle_uses_change_on_the_same_task_name() -> None:
    enable = autostart.enable_steps()[0].argv
    disable = autostart.disable_steps()[0].argv

    assert enable == ("schtasks", "/Change", "/TN", "\\Headwaters", "/ENABLE")
    assert disable == ("schtasks", "/Change", "/TN", "\\Headwaters", "/DISABLE")


def test_delete_removes_the_task_without_a_prompt() -> None:
    assert autostart.delete_steps()[0].argv == (
        "schtasks",
        "/Delete",
        "/TN",
        "\\Headwaters",
        "/F",
    )


@pytest.mark.parametrize(
    ("code", "stdout", "expected"),
    [
        (0, _settings_xml(True), autostart.STATUS_ENABLED),
        (0, _settings_xml(False), autostart.STATUS_DISABLED),
        (1, "", autostart.STATUS_ABSENT),
        (0, "not xml", autostart.STATUS_UNKNOWN),
    ],
)
def test_status_reads_the_settings_block(code: int, stdout: str, expected: str) -> None:
    assert autostart.status(runner=fake_runner(code, stdout)) == expected


def test_current_user_comes_from_the_environment() -> None:
    assert autostart.current_user({"USERNAME": "ana", "USERDOMAIN": "WS"}) == "WS\\ana"
    assert autostart.current_user({"USERNAME": "ana"}) == "ana"
    with pytest.raises(ValueError, match="USERNAME"):
        autostart.current_user({})


def test_apply_stops_at_the_first_failure() -> None:
    calls: list[list[str]] = []
    steps = [*autostart.enable_steps(), *autostart.delete_steps()]

    assert autostart.apply(steps, runner=fake_runner(5, calls=calls)) == 5
    assert len(calls) == 1


def test_main_enable_writes_the_definition_into_the_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install = _install(tmp_path)
    monkeypatch.setattr(autostart.Install, "default", staticmethod(lambda: install))
    monkeypatch.setenv("USERNAME", "ana")
    monkeypatch.setenv("USERDOMAIN", "WS")
    calls: list[list[str]] = []

    assert autostart.main(["--enable"], runner=fake_runner(calls=calls)) == 0

    assert install.autostart_xml.exists()
    assert "WS\\ana" in install.autostart_xml.read_text(encoding="utf-16")
    assert calls[0][:4] == ["schtasks", "/Create", "/TN", "\\Headwaters"]


def test_main_dry_run_prints_the_commands_and_touches_the_scheduler(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    install = _install(tmp_path)
    monkeypatch.setattr(autostart.Install, "default", staticmethod(lambda: install))
    monkeypatch.setenv("USERNAME", "ana")
    calls: list[list[str]] = []

    assert autostart.main(["--enable", "--dry-run"], runner=fake_runner(calls=calls)) == 0

    assert calls == []
    assert "schtasks /Create" in capsys.readouterr().out


def test_main_status_prints_the_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(autostart.Install, "default", staticmethod(lambda: _install(tmp_path)))

    assert autostart.main(["--status"], runner=fake_runner(1, "")) == 0
    assert capsys.readouterr().out.strip() == autostart.STATUS_ABSENT


# ------------------------------------ the repeating watchdog task (HDW-521)


def test_the_watchdog_task_repeats_every_five_minutes_and_runs_the_watchdog(
    tmp_path: Path,
) -> None:
    install = _install(tmp_path)

    root = ET.fromstring(autostart.watchdog_task_xml(user="WS\\ana", command=install.cli))

    assert root.findtext(f"{NS}RegistrationInfo/{NS}URI") == "\\Headwaters Watchdog"
    repetition = root.findtext(f"{NS}Triggers/{NS}TimeTrigger/{NS}Repetition/{NS}Interval")
    assert repetition == "PT5M"
    assert root.find(f"{NS}Triggers/{NS}LogonTrigger") is None
    action = root.find(f"{NS}Actions/{NS}Exec")
    assert action is not None
    assert action.findtext(f"{NS}Command") == str(install.cli)
    assert action.findtext(f"{NS}Arguments") == "watchdog"
    settings = root.find(f"{NS}Settings")
    assert settings is not None
    assert settings.findtext(f"{NS}MultipleInstancesPolicy") == "IgnoreNew"
    assert settings.findtext(f"{NS}ExecutionTimeLimit") == "PT2M"
    assert settings.findtext(f"{NS}StartWhenAvailable") == "true"
    assert settings.findtext(f"{NS}DisallowStartIfOnBatteries") == "false"
    principal = root.find(f"{NS}Principals/{NS}Principal")
    assert principal is not None
    assert principal.findtext(f"{NS}RunLevel") == "LeastPrivilege"
    assert principal.findtext(f"{NS}LogonType") == "InteractiveToken"


def test_an_unknown_schedule_is_a_programming_error(tmp_path: Path) -> None:
    install = _install(tmp_path)

    with pytest.raises(ValueError, match="unknown schedule"):
        autostart.task_xml(user="ana", command=install.cli, schedule="weekly")


def test_the_watchdog_is_its_own_task(tmp_path: Path) -> None:
    steps = autostart.watchdog_create_steps(tmp_path / "watchdog.xml")

    assert [list(step.argv) for step in steps] == [
        [
            "schtasks",
            "/Create",
            "/TN",
            "\\Headwaters Watchdog",
            "/XML",
            str(tmp_path / "watchdog.xml"),
            "/F",
        ]
    ]


def test_install_registers_the_logon_task_then_the_watchdog(tmp_path: Path) -> None:
    install = _install(tmp_path)
    calls: list[list[str]] = []

    steps = autostart.install_steps(install, user="WS\\ana")

    assert [step.argv[3] for step in steps] == ["\\Headwaters", "\\Headwaters Watchdog"]
    assert install.autostart_xml.exists()
    assert install.watchdog_xml.exists()
    assert autostart.apply(steps, runner=fake_runner(calls=calls)) == 0
    assert [call[3] for call in calls] == ["\\Headwaters", "\\Headwaters Watchdog"]


def test_delete_and_the_toggle_cover_both_tasks() -> None:
    assert [step.argv[3] for step in autostart.delete_steps()] == [
        "\\Headwaters",
        "\\Headwaters Watchdog",
    ]
    assert [step.argv[3] for step in autostart.enable_steps()] == [
        "\\Headwaters",
        "\\Headwaters Watchdog",
    ]
    assert [step.argv[3] for step in autostart.disable_steps()] == [
        "\\Headwaters",
        "\\Headwaters Watchdog",
    ]


def test_status_can_be_asked_about_the_watchdog() -> None:
    calls: list[list[str]] = []

    assert (
        autostart.status(
            runner=fake_runner(0, _settings_xml(True), calls), name="Headwaters Watchdog"
        )
        == autostart.STATUS_ENABLED
    )
    assert calls[0][3] == "\\Headwaters Watchdog"


def test_a_task_that_was_never_created_reads_as_absent() -> None:
    missing = (
        'ERROR: The system cannot find the file specified.\nThe task "Headwaters" does not exist.'
    )

    assert autostart.status(runner=fake_runner(0, missing)) == autostart.STATUS_ABSENT
