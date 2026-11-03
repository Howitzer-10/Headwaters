"""The shipped artifacts (HDW-405): the Inno script, the itch manifest and the butler script.

Nothing here compiles Inno Setup, calls butler or runs a setup: the tests read the committed
files and the Python that generates them, so they hold on any platform and in CI.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest
import typer.main

from headwaters.cli.main import app
from headwaters.installer.hosts import host_ids
from headwaters.packaging import build, itch
from headwaters.packaging.layout import Install

REPO = Path(__file__).resolve().parent.parent
ISS = REPO / "packaging" / "innosetup" / "headwaters.iss"
MANIFEST = REPO / "packaging" / "itch" / ".itch.toml"
BUTLER = REPO / "packaging" / "butler" / "push_windows_beta.ps1"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _sections(text: str) -> dict[str, str]:
    """Split an Inno script into ``[Section]`` bodies; comments and blanks are kept."""
    sections: dict[str, list[str]] = {}
    current = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            current = stripped
            sections[current] = []
        elif current:
            sections[current].append(line)
    return {name: "\n".join(body) for name, body in sections.items()}


def _groups() -> dict[str, list[str]]:
    """Subcommand name -> the long options it accepts, read off the real Typer app."""
    group = typer.main.get_command(app)
    commands = getattr(group, "commands", {})
    return {
        name: [opt for param in command.params for opt in getattr(param, "opts", [])]
        for name, command in commands.items()
    }


def test_the_committed_manifest_is_what_the_generator_renders() -> None:
    assert _text(MANIFEST).replace("\r\n", "\n") == itch.render_manifest()


def test_the_manifest_parses_and_every_action_names_an_executable_that_ships() -> None:
    data = tomllib.loads(_text(MANIFEST))

    entries = data["actions"]
    assert len(entries) == 6
    assert [entry["name"] for entry in entries][:2] == ["Open status page", "Doctor"]
    for entry in entries:
        assert entry["platform"] == itch.PLATFORM
        assert entry["path"] == Install.at(Path(".")).uv.as_posix()
        assert entry["args"][:4] == ["run", "--frozen", "--no-dev", "--project"]
        assert entry["args"][4] == "runtime"


def test_every_action_runs_a_command_and_options_the_cli_really_has() -> None:
    groups = _groups()

    for entry in itch.manifest_actions(MANIFEST.parent):
        args = list(entry["args"])
        command = args[args.index("headwaters") + 1]
        assert command in groups, f"{entry['name']} runs unknown command {command!r}"
        for flag in args[args.index("headwaters") + 2 :]:
            if not flag.startswith("-"):
                continue  # an option value, e.g. the host id after --host
            assert flag in groups[command], f"{entry['name']}: {command} has no {flag}"


def test_the_install_actions_cover_exactly_the_hosts_the_installer_knows() -> None:
    assert tuple(host for host, _ in itch.INSTALL_HOSTS) == host_ids()
    named = [entry["name"] for entry in itch.manifest_actions(MANIFEST.parent)]
    for _host, label in itch.INSTALL_HOSTS:
        assert f"Install for {label}" in named


def test_the_itch_install_is_recognised_so_self_update_stays_out_of_the_way(
    tmp_path: Path,
) -> None:
    (tmp_path / itch.MARKER_NAME).write_text(_text(MANIFEST), encoding="utf-8")

    assert itch.read_manifest(tmp_path)["actions"]
    assert itch.manifest_actions(tmp_path)[0]["name"] == "Open status page"


def test_the_installer_needs_no_administrator() -> None:
    sections = _sections(_text(ISS))

    assert "PrivilegesRequired=lowest" in sections["[Setup]"]
    assert "DefaultDirName={localappdata}\\Headwaters" in sections["[Setup]"]
    assert "HKEY" not in _text(ISS)  # no [Registry] section, nothing machine wide
    assert "[Registry]" not in sections


def test_the_installer_ships_the_runtime_and_never_a_frozen_python() -> None:
    text = _text(ISS)
    sections = _sections(text)

    assert 'DestDir: "{app}\\runtime"' in sections["[Files]"]
    assert "uv.exe" in sections["[Run]"]
    assert "sync --frozen --no-dev" in sections["[Run]"]
    assert "--python 3.11" in sections["[Run]"]
    lowered = text.lower()
    for forbidden in ("pyinstaller", "python-3", "python3.dll", "SignTool="):
        assert forbidden.lower() not in lowered


def test_the_autostart_step_only_runs_when_the_box_was_ticked() -> None:
    run = _sections(_text(ISS))["[Run]"]

    assert "headwaters.packaging.autostart --enable" in run
    line = next(entry for entry in run.splitlines() if "autostart --enable" in entry)
    assert "Tasks: autostart" in line


def test_the_uninstaller_takes_the_task_and_the_runtime_but_keeps_state(
    tmp_path: Path,
) -> None:
    sections = _sections(_text(ISS))

    assert (
        'schtasks.exe"; Parameters: "/Delete /TN ""Headwaters"" /F"' in sections["[UninstallRun]"]
    )
    delete = sections["[UninstallDelete]"]
    assert 'Type: filesandordirs; Name: "{app}\\runtime"' in delete
    assert 'Type: filesandordirs; Name: "{app}"' not in delete  # {app} is also the state dir
    assert "CurUninstallStepChanged" in sections["[Code]"]
    assert "--remove-state --remove-keys" in sections["[Code]"]
    assert "ask" in sections["[Code]"].lower()


def test_the_butler_script_is_a_hand_run_release_step() -> None:
    script = _text(BUTLER)
    live = "\n".join(
        line for line in script.splitlines() if line.strip() and not line.strip().startswith("#")
    )

    assert "butler push" in live
    assert ":$Channel" in live
    assert '"windows-beta"' in live
    assert ".itch.toml" in live
    assert "pytest" not in live


def test_no_test_or_workflow_ever_pushes_a_build() -> None:
    # Built at runtime so this file does not match its own scan.
    needle = "butler" + " push"
    script_name = "push_windows" + "_beta"
    for folder in (REPO / "tests", REPO / ".github" / "workflows"):
        if not folder.exists():
            continue
        for path in folder.rglob("*"):
            if path == Path(__file__) or not path.is_file():
                continue
            if path.suffix not in {".py", ".yml", ".yaml"}:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            assert needle not in text, path
            assert script_name not in text, path


def test_signing_is_skipped_with_a_message_when_no_certificate_is_supplied() -> None:
    plan = build.signing_plan({}, [Path("Setup.exe")])

    assert not plan.enabled
    assert build.CERT_ENV in plan.reason
    assert plan.redacted() == ""


def test_signing_never_prints_the_certificate_password() -> None:
    env = {
        build.CERT_ENV: "C:\\certs\\owner.pfx",
        build.CERT_PASSWORD_ENV: "hunter2-should-never-appear",
        build.SIGNTOOL_ENV: "signtool.exe",
    }

    plan = build.signing_plan(env, [Path("dist\\HeadwatersSetup-1.2.3.exe")])

    assert plan.enabled
    assert plan.argv[:2] == ("signtool.exe", "sign")
    assert "C:\\certs\\owner.pfx" in plan.argv
    assert "/fd" in plan.argv
    assert plan.password_index is not None
    assert plan.argv[plan.password_index] == "hunter2-should-never-appear"
    assert "hunter2-should-never-appear" not in plan.redacted()
    assert "C:\\certs\\owner.pfx" in plan.redacted()
    assert "hunter2" not in str(plan.as_dict())


def test_the_iscc_call_passes_the_version_and_paths_as_defines() -> None:
    command = build.iscc_command(
        Path("ISCC.exe"),
        ISS,
        version="1.2.3",
        payload_dir=Path("build/windows/payload"),
        output_dir=Path("dist"),
    )

    assert command[0] == "ISCC.exe"
    assert "/DAppVersion=1.2.3" in command
    assert any(arg.startswith("/DSourceDir=") for arg in command)
    assert any(arg.startswith("/DOutputDir=") for arg in command)
    assert command[-1] == str(ISS)


def test_the_payload_is_the_lockfile_the_project_the_uv_binary_and_the_source(
    tmp_path: Path,
) -> None:
    uv = tmp_path / "uv.exe"
    uv.write_text("", encoding="utf-8")

    steps = build.payload_steps(REPO, tmp_path / "payload", uv=uv)

    targets = {step.target.name for step in steps}
    assert targets == {"pyproject.toml", "uv.lock", "uv.exe", "headwaters", "catalog"}
    assert {step.kind for step in steps} == {"file", "tree"}
    assert all(step.source is not None and step.source.exists() for step in steps)


def test_find_uv_prefers_the_environment_and_can_report_none(tmp_path: Path) -> None:
    uv = tmp_path / "uv.exe"
    uv.write_text("", encoding="utf-8")

    assert build.find_uv({build.UV_ENV: str(uv)}, which=lambda name: None) == uv
    assert build.find_uv({}, which=lambda name: None) is None
    assert build.find_uv({}, which=lambda name: "C:\\bin\\uv.exe") == Path("C:\\bin\\uv.exe")


def test_find_iscc_prefers_the_environment(tmp_path: Path) -> None:
    iscc = tmp_path / "ISCC.exe"
    iscc.write_text("", encoding="utf-8")

    assert build.find_iscc({build.ISCC_ENV: str(iscc)}, is_file=lambda p: True) == iscc
    assert build.find_iscc({}, is_file=lambda p: False) is None


def test_the_itch_payload_is_the_runtime_plus_the_marker(tmp_path: Path) -> None:
    steps = build.itch_steps(tmp_path / "payload", tmp_path / "itch")

    assert [step.target.name for step in steps] == ["runtime", ".itch.toml"]
    assert steps[1].text == itch.render_manifest()


def test_butler_command_names_the_channel() -> None:
    assert build.butler_command("howitzer-10", "headwaters", Path("build/windows/itch")) == [
        "butler",
        "push",
        str(Path("build/windows/itch")),
        "howitzer-10/headwaters:windows-beta",
    ]


def test_a_dry_run_build_writes_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    uv = tmp_path / "uv.exe"
    uv.write_text("", encoding="utf-8")

    with pytest.MonkeyPatch.context() as patch:
        patch.setenv(build.UV_ENV, str(uv))
        code = build.main(["--dry-run", "--repo-root", str(tmp_path)])

    assert code == 0
    assert not (tmp_path / "build").exists()
    output = capsys.readouterr().out
    assert "runtime" in output
    assert "sign:" in output
