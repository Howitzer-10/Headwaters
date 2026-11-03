"""HDW-406: the systemd *user* unit, its plan and the payload script -- without systemd.

The repository's CI has no systemd and the owner's box is Windows, so nothing here may start,
stop or enable a service. Everything is driven through the ``runner`` seam and through
``tmp_path``:

* the unit file is pure text, so the directives are asserted after parsing it;
* every side effect (the unit file, the state tree, the runtime) lives under ``tmp_path``;
* the one thing that cannot be faked -- that ``ExecStart`` really starts a daemon in the
  foreground -- is proven by running the exact ``DAEMON_ARGS`` argv as a real process.
"""

from __future__ import annotations

import configparser
import os
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from headwaters.core import paths
from headwaters.core.daemon_client import find_running
from headwaters.core.daemon_control import stop_daemon
from headwaters.packaging import build, linux

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "packaging" / "linux" / "install.sh"
BUTLER = REPO / "packaging" / "butler" / "push_linux_beta.ps1"


def unescape(word: str) -> str:
    """Read one quoted unit word back: systemd applies C-style escapes to ``\\\\`` and ``\\"``."""
    if not (word.startswith('"') and word.endswith('"')):
        return word
    return word[1:-1].replace("\\\\", "\\").replace('\\"', '"')


def exec_start_words(install: linux.LinuxInstall) -> list[str]:
    """The words systemd will hand to ``execve`` for this unit's ``ExecStart``."""
    return [unescape(word) for word in linux.exec_start(install).split(" ")]


def fake_runner(code: Any = 0, *, stdout: str = "", calls: list[list[str]] | None = None) -> Any:
    """A ``systemctl`` stand-in: records argv, never spawns anything."""

    def run(argv: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
        if calls is not None:
            calls.append(list(argv))
        returncode = code(argv) if callable(code) else code
        return subprocess.CompletedProcess(argv, returncode, stdout=stdout, stderr="")

    return run


def _make_runtime(install: linux.LinuxInstall) -> None:
    """The files a completed ``uv sync`` leaves behind, so ``verify`` can pass."""
    for path in (
        install.uv,
        install.lock,
        install.project,
        install.catalog,
        install.cli,
        install.mcp,
        install.python,
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")


def test_the_default_layout_follows_the_xdg_variables() -> None:
    install = linux.LinuxInstall.default({"HOME": "/home/ana"})

    assert install.root == Path("/home/ana/.local/share/headwaters")
    assert install.runtime == install.root / "runtime"
    assert install.python == install.root / "runtime" / ".venv" / "bin" / "python"
    assert install.unit == Path("/home/ana/.config/systemd/user/headwaters.service")
    assert install.state == paths.state_dir()


def test_the_xdg_variables_and_the_root_override_win() -> None:
    install = linux.LinuxInstall.default(
        {
            "HOME": "/home/ana",
            "XDG_DATA_HOME": "/data",
            "XDG_CONFIG_HOME": "/conf",
        }
    )
    assert install.root == Path("/data/headwaters")
    assert install.unit == Path("/conf/systemd/user/headwaters.service")

    moved = linux.LinuxInstall.default({"HOME": "/home/ana", linux.ROOT_ENV: "/opt/hw"})
    assert moved.root == Path("/opt/hw")


def test_at_keeps_every_path_under_the_given_root(tmp_path: Path) -> None:
    install = linux.LinuxInstall.at(tmp_path)

    assert install.roots() == (tmp_path, tmp_path / "state")
    assert install.unit == tmp_path / "config" / "systemd" / "user" / "headwaters.service"
    assert tmp_path in install.python.parents


def test_the_unit_runs_one_foreground_daemon(tmp_path: Path) -> None:
    install = linux.LinuxInstall.at(tmp_path)
    text = linux.unit_text(install)
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.read_string(text)

    assert text.count("ExecStart=") == 1
    assert set(parser.sections()) == {"Unit", "Service", "Install"}
    assert parser["Service"]["Type"] == "simple"
    assert exec_start_words(install) == [str(install.python), *linux.DAEMON_ARGS]
    assert unescape(parser["Service"]["WorkingDirectory"]) == str(install.root)
    assert parser["Install"]["WantedBy"] == "default.target"
    assert parser["Unit"]["After"] == "network-online.target"


def test_the_unit_restarts_crashes_but_not_a_refused_configuration(tmp_path: Path) -> None:
    text = linux.unit_text(linux.LinuxInstall.at(tmp_path))
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.read_string(text)

    assert parser["Service"]["Restart"] == "on-failure"
    assert parser["Service"]["RestartSec"] == "5"
    # 2 is an unreadable rules.yaml, 3 is another daemon holding the lock: both would loop.
    assert parser["Service"]["RestartPreventExitStatus"] == "2 3"
    assert parser["Service"]["KillMode"] == "mixed"
    assert parser["Service"]["TimeoutStopSec"] == "30"


def test_a_root_with_a_space_is_quoted_for_systemd(tmp_path: Path) -> None:
    install = linux.LinuxInstall.at(tmp_path / "a na")
    line = next(
        entry for entry in linux.unit_text(install).splitlines() if entry.startswith("ExecStart=")
    )

    assert line.startswith('ExecStart="')
    assert line.endswith('" -m headwaters.daemon')


def test_write_unit_creates_the_user_unit_directory(tmp_path: Path) -> None:
    install = linux.LinuxInstall.at(tmp_path)

    path = linux.write_unit(install)

    assert path == install.unit
    assert path.parent == tmp_path / "config" / "systemd" / "user"
    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" not in raw
    assert raw.endswith(b"\n")
    assert not raw.endswith(b"\n\n")
    assert raw.decode("utf-8") == linux.unit_text(install)


def test_the_install_plan_reloads_then_starts_at_every_login() -> None:
    assert [step.argv for step in linux.install_steps()] == [
        ("systemctl", "--user", "daemon-reload"),
        ("systemctl", "--user", "enable", "--now", "headwaters.service"),
    ]


def test_the_uninstall_plan_stops_before_it_forgets_the_unit() -> None:
    assert [step.argv for step in linux.uninstall_steps()] == [
        ("systemctl", "--user", "disable", "--now", "headwaters.service"),
        ("systemctl", "--user", "daemon-reload"),
        ("systemctl", "--user", "reset-failed", "headwaters.service"),
    ]


def test_apply_stops_at_the_first_non_zero_exit() -> None:
    calls: list[list[str]] = []
    codes = iter([0, 1, 0])

    code = linux.apply(
        [*linux.install_steps(), *linux.uninstall_steps()],
        runner=fake_runner(lambda argv: next(codes), calls=calls),
    )

    assert code == 1
    assert len(calls) == 2


@pytest.mark.parametrize(
    ("code", "output", "expected"),
    [
        (0, "enabled\n", linux.STATUS_ENABLED),
        (0, "enabled-runtime\n", linux.STATUS_ENABLED),
        (1, "disabled\n", linux.STATUS_DISABLED),
        (1, "masked\n", linux.STATUS_DISABLED),
        (1, "not-found\n", linux.STATUS_ABSENT),
        (1, "", linux.STATUS_ABSENT),
        (0, "", linux.STATUS_UNKNOWN),
        (1, "something new\n", linux.STATUS_UNKNOWN),
    ],
)
def test_status_maps_what_systemctl_answers(code: int, output: str, expected: str) -> None:
    assert linux.classify_status(code, output) == expected


def test_status_asks_about_the_user_unit() -> None:
    calls: list[list[str]] = []

    answer = linux.status(runner=fake_runner(0, stdout="enabled\n", calls=calls))

    assert answer == linux.STATUS_ENABLED
    assert calls == [["systemctl", "--user", "is-enabled", "headwaters.service"]]


def test_verify_names_every_missing_piece(tmp_path: Path) -> None:
    install = linux.LinuxInstall.at(tmp_path)

    check = linux.verify(install)

    assert not check.ok
    assert install.python in check.missing
    assert install.unit in check.missing
    assert check.detail().startswith("missing: ")

    _make_runtime(install)
    linux.write_unit(install)

    assert linux.verify(install).ok
    assert linux.verify(install).missing == ()
    assert linux.verify(install).detail().startswith("the private runtime")


def test_install_without_yes_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    install = linux.LinuxInstall.at(tmp_path)
    calls: list[list[str]] = []

    code = linux.main(["--install"], install=install, runner=fake_runner(0, calls=calls))

    assert code == 0
    assert calls == []
    assert not install.unit.exists()
    assert "--yes" in capsys.readouterr().out


def test_a_dry_run_install_writes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    install = linux.LinuxInstall.at(tmp_path)
    calls: list[list[str]] = []

    code = linux.main(
        ["--install", "--yes", "--dry-run"], install=install, runner=fake_runner(0, calls=calls)
    )

    assert code == 0
    assert calls == []
    assert not install.unit.exists()
    assert "ExecStart=" in capsys.readouterr().out


def test_install_yes_writes_the_unit_and_starts_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    install = linux.LinuxInstall.at(tmp_path)
    _make_runtime(install)
    calls: list[list[str]] = []

    code = linux.main(["--install", "--yes"], install=install, runner=fake_runner(0, calls=calls))

    assert code == 0
    assert install.unit.exists()
    assert calls == [list(step.argv) for step in linux.install_steps()]
    assert "the private runtime" in capsys.readouterr().out


def test_a_failed_systemctl_reports_the_code_and_still_writes_the_unit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    install = linux.LinuxInstall.at(tmp_path)

    code = linux.main(["--install", "--yes"], install=install, runner=fake_runner(1))

    assert code == 1
    assert install.unit.exists()
    assert "loginctl enable-linger" in capsys.readouterr().err


def test_uninstall_stops_the_unit_and_keeps_the_state_by_default(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    install = linux.LinuxInstall.at(tmp_path)
    linux.write_unit(install)
    install.state.mkdir(parents=True)
    (install.state / "headwaters.sqlite3").write_text("", encoding="utf-8")
    calls: list[list[str]] = []

    code = linux.main(
        ["--uninstall", "--yes"],
        install=install,
        runner=fake_runner(0, calls=calls),
        alias_source=lambda: [],
        key_remover=lambda aliases: (0, None),
    )

    assert code == 0
    assert calls == [list(step.argv) for step in linux.uninstall_steps()]
    assert not install.unit.exists()
    assert (install.state / "headwaters.sqlite3").exists()
    out = capsys.readouterr().out
    assert "rm -rf" in out
    assert str(install.runtime) in out


def test_uninstall_can_take_the_state_and_the_keys(tmp_path: Path) -> None:
    install = linux.LinuxInstall.at(tmp_path)
    linux.write_unit(install)
    install.state.mkdir(parents=True)
    gone: list[str] = []

    def remove_keys(aliases: Sequence[str]) -> tuple[int, str | None]:
        gone.extend(aliases)
        return len(aliases), None

    code = linux.main(
        ["--uninstall", "--yes", "--remove-state", "--remove-keys"],
        install=install,
        runner=fake_runner(0),
        alias_source=lambda: ["openai", "anthropic"],
        key_remover=remove_keys,
    )

    assert code == 0
    assert not install.state.exists()
    assert gone == ["openai", "anthropic"]


def test_uninstall_without_yes_changes_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    install = linux.LinuxInstall.at(tmp_path)
    linux.write_unit(install)
    calls: list[list[str]] = []

    code = linux.main(["--uninstall"], install=install, runner=fake_runner(0, calls=calls))

    assert code == 0
    assert calls == []
    assert install.unit.exists()
    assert "--yes" in capsys.readouterr().out


def test_a_removal_outside_the_roots_is_refused(tmp_path: Path) -> None:
    install = linux.LinuxInstall.at(tmp_path / "app")
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    plan = linux.RemovalPlan(
        (linux.Removal(linux.KIND_TREE, outside, "a trap", linux.OWNER_PYTHON),)
    )

    with pytest.raises(linux.LinuxError):
        linux.apply_removal(install, plan)

    assert outside.exists()


def test_the_committed_install_script_is_what_the_generator_renders() -> None:
    assert SCRIPT.read_text(encoding="utf-8").replace("\r\n", "\n") == linux.render_install_script()


def test_the_install_script_needs_no_root_and_no_pip() -> None:
    script = linux.render_install_script()

    assert script.startswith("#!/usr/bin/env bash\n")
    assert script.endswith("\n")
    assert not script.endswith("\n\n")
    assert "set -euo pipefail" in script
    assert "sudo" not in script
    assert "pip install" not in script
    assert '"$UV" sync --frozen --no-dev --python 3.11' in script
    assert "-m headwaters.packaging.linux" in script
    assert "loginctl enable-linger" in script


def test_the_linux_payload_is_the_runtime_plus_the_script(tmp_path: Path) -> None:
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "pyproject.toml").write_text("", encoding="utf-8")

    steps = build.linux_steps(payload, tmp_path / "itch")
    assert [step.target.name for step in steps] == ["runtime", "install.sh"]

    build.apply(steps)

    assert (tmp_path / "itch" / "install.sh").read_text(encoding="utf-8") == (
        linux.render_install_script()
    )
    assert (tmp_path / "itch" / "runtime" / "pyproject.toml").exists()


def test_the_linux_target_stages_without_a_compiler(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    uv = tmp_path / "uv"
    uv.write_text("", encoding="utf-8")
    for name in ("pyproject.toml", "uv.lock"):
        (tmp_path / name).write_text("", encoding="utf-8")
    (tmp_path / "headwaters").mkdir()
    (tmp_path / "headwaters" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "catalog").mkdir()

    with patch.dict(os.environ, {build.UV_ENV: str(uv)}, clear=False):
        code = build.main(["--target", "linux", "--repo-root", str(tmp_path)])

    stage = tmp_path / "build" / "linux" / "itch"
    assert code == 0
    assert (stage / "install.sh").exists()
    assert (stage / "runtime" / "uv").exists()
    assert (stage / "runtime" / "uv.lock").exists()
    assert "linux-beta" in capsys.readouterr().out


def test_a_dry_run_linux_build_writes_nothing(tmp_path: Path) -> None:
    uv = tmp_path / "uv"
    uv.write_text("", encoding="utf-8")

    with patch.dict(os.environ, {build.UV_ENV: str(uv)}, clear=False):
        code = build.main(["--target", "linux", "--dry-run", "--repo-root", str(tmp_path)])

    assert code == 0
    assert not (tmp_path / "build").exists()


def test_the_butler_command_names_the_linux_channel() -> None:
    argv = build.butler_command(
        "howitzer-10", "headwaters", Path("build/linux/itch"), channel=build.CHANNEL_LINUX
    )

    assert argv[0] == "butler"
    assert argv[1] == "push"
    assert argv[-1] == "howitzer-10/headwaters:linux-beta"


def test_the_linux_butler_script_is_a_hand_run_release_step() -> None:
    script = BUTLER.read_text(encoding="utf-8")
    live = "\n".join(
        line for line in script.splitlines() if line.strip() and not line.strip().startswith("#")
    )

    assert "install.sh" in live
    assert ":$Channel" in live
    assert '"linux-beta"' in script
    assert "runtime/uv" in live
    assert "pytest" not in live
    assert "push_linux" + "_beta" not in (REPO / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )


@pytest.mark.slow
def test_the_unit_command_line_really_runs_the_daemon(live_home: Path) -> None:
    """``ExecStart`` is ``python -m headwaters.daemon``: start that argv and watch it serve.

    The proof is causality, not process identity: an isolated home has no daemon, this argv
    makes one appear, and stopping it makes it disappear. The recorded pid is deliberately not
    compared with ``process.pid`` -- a Windows venv launcher hands off to another interpreter,
    and systemd on Linux does not care which pid a launcher picks either.
    """
    assert find_running() is None, "the isolated home already had a daemon"
    process = subprocess.Popen(
        [sys.executable, *linux.DAEMON_ARGS],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.monotonic() + 60.0
        client = None
        while time.monotonic() < deadline:
            client = find_running()
            if client is not None:
                break
            if process.poll() is not None:
                output = (process.stdout.read() if process.stdout else "")[-2000:]
                pytest.fail(f"the daemon exited early with {process.returncode}: {output}")
            time.sleep(0.25)

        assert client is not None, "the daemon never recorded a running instance"
        assert client.info is not None
        assert client.health()["ok"] is True
        assert client.health()["pid"] == client.info.pid
        assert client.info.pid > 0

        stopped = stop_daemon(timeout_s=30)
        assert stopped.ok, stopped.message
        assert process.wait(timeout=30) == 0
        assert find_running() is None
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=30)
