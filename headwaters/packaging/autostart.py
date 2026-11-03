"""The per-user "At log on" task that starts the daemon (HDW-405).

The task lives in the Task Scheduler root folder and runs as the signed-in user with
``LeastPrivilege``, so creating it needs no elevation and no ``HKLM``. It is registered from an
XML definition with ``schtasks /Create /XML`` because the useful settings (battery policy, no
execution time limit, ``IgnoreNew``) are not reachable through ``schtasks`` switches.

To keep it testable the module is split the usual way: :func:`task_xml` and the ``*_steps``
functions are pure data, :func:`apply` runs a list of steps with an injectable runner, and
``python -m headwaters.packaging.autostart --enable`` is the entry point the installer calls.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from headwaters.packaging.layout import Install

TASK_NAME = "Headwaters"
#: The root folder (not ``\Headwaters\...``): a standard user may create tasks there.
TASK_FOLDER = "\\"
TASK_PATH = TASK_FOLDER + TASK_NAME

TASK_NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"
DEFAULT_ARGUMENTS = ("start",)
DESCRIPTION = "Starts the Headwaters daemon when this user logs on."

SCHTASKS = "schtasks"

STATUS_ENABLED = "enabled"
STATUS_DISABLED = "disabled"
STATUS_ABSENT = "absent"
STATUS_UNKNOWN = "unknown"


def current_user(env: Mapping[str, str] | None = None) -> str:
    """``DOMAIN\\user`` from the environment, which is how the task names its principal."""
    source = os.environ if env is None else env
    name = (source.get("USERNAME") or "").strip()
    if not name:
        raise ValueError("USERNAME is not set; cannot name the task principal")
    domain = (source.get("USERDOMAIN") or "").strip()
    return f"{domain}\\{name}" if domain else name


def task_xml(
    *,
    user: str,
    command: Path | str,
    arguments: Sequence[str] = DEFAULT_ARGUMENTS,
    enabled: bool = True,
    description: str = DESCRIPTION,
) -> str:
    """The Task Scheduler XML for one user's logon trigger; valid input for ``schtasks /XML``."""
    flag = "true" if enabled else "false"
    who = escape(str(user), {'"': "&quot;"})
    return (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        f'<Task version="1.2" xmlns="{TASK_NS}">\n'
        "  <RegistrationInfo>\n"
        f"    <Description>{escape(description)}</Description>\n"
        f"    <URI>{escape(TASK_PATH)}</URI>\n"
        "  </RegistrationInfo>\n"
        "  <Triggers>\n"
        "    <LogonTrigger>\n"
        f"      <Enabled>{flag}</Enabled>\n"
        f"      <UserId>{who}</UserId>\n"
        "    </LogonTrigger>\n"
        "  </Triggers>\n"
        "  <Principals>\n"
        '    <Principal id="Author">\n'
        f"      <UserId>{who}</UserId>\n"
        "      <LogonType>InteractiveToken</LogonType>\n"
        "      <RunLevel>LeastPrivilege</RunLevel>\n"
        "    </Principal>\n"
        "  </Principals>\n"
        "  <Settings>\n"
        "    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n"
        "    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>\n"
        "    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>\n"
        "    <StartWhenAvailable>false</StartWhenAvailable>\n"
        "    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>\n"
        "    <AllowStartOnDemand>true</AllowStartOnDemand>\n"
        f"    <Enabled>{flag}</Enabled>\n"
        "    <Hidden>false</Hidden>\n"
        "    <RunOnlyIfIdle>false</RunOnlyIfIdle>\n"
        "    <WakeToRun>false</WakeToRun>\n"
        "    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>\n"
        "    <Priority>7</Priority>\n"
        "  </Settings>\n"
        '  <Actions Context="Author">\n'
        "    <Exec>\n"
        f"      <Command>{escape(str(command))}</Command>\n"
        f"      <Arguments>{escape(' '.join(arguments))}</Arguments>\n"
        "    </Exec>\n"
        "  </Actions>\n"
        "</Task>\n"
    )


def write_task_file(install: Install, text: str) -> Path:
    """Write the definition as UTF-16 (the encoding Task Scheduler documents) and return it."""
    install.runtime.mkdir(parents=True, exist_ok=True)
    path = install.autostart_xml
    path.write_text(text, encoding="utf-16")
    return path


@dataclass(frozen=True)
class TaskStep:
    """One ``schtasks`` call."""

    argv: tuple[str, ...]
    why: str

    def as_dict(self) -> dict[str, Any]:
        return {"argv": list(self.argv), "why": self.why}


def create_steps(xml_path: Path | str) -> list[TaskStep]:
    return [
        TaskStep(
            argv=(SCHTASKS, "/Create", "/TN", TASK_PATH, "/XML", str(xml_path), "/F"),
            why="register the at-logon task for this user",
        )
    ]


def enable_steps() -> list[TaskStep]:
    return [TaskStep(argv=(SCHTASKS, "/Change", "/TN", TASK_PATH, "/ENABLE"), why="turn it on")]


def disable_steps() -> list[TaskStep]:
    return [TaskStep(argv=(SCHTASKS, "/Change", "/TN", TASK_PATH, "/DISABLE"), why="turn it off")]


def delete_steps() -> list[TaskStep]:
    return [TaskStep(argv=(SCHTASKS, "/Delete", "/TN", TASK_PATH, "/F"), why="remove the task")]


def query_steps() -> list[TaskStep]:
    return [
        TaskStep(
            argv=(SCHTASKS, "/Query", "/TN", TASK_PATH, "/XML"),
            why="read the task definition back",
        )
    ]


Runner = Callable[..., Any]


def _run(argv: Sequence[str], *, runner: Runner | None = None) -> subprocess.CompletedProcess[str]:
    if runner is not None:
        return runner(list(argv))
    return subprocess.run(list(argv), capture_output=True, text=True, check=False)


def apply(steps: Sequence[TaskStep], *, runner: Runner | None = None) -> int:
    """Run the steps; the first non-zero exit stops the sequence and is returned."""
    for step in steps:
        completed = _run(step.argv, runner=runner)
        code = int(getattr(completed, "returncode", 0))
        if code != 0:
            return code
    return 0


def create(
    install: Install,
    *,
    user: str | None = None,
    enabled: bool = True,
    runner: Runner | None = None,
) -> tuple[list[TaskStep], int]:
    """Write the definition then register it. Returns the steps and the exit code."""
    who = user if user is not None else current_user()
    path = write_task_file(install, task_xml(user=who, command=install.cli, enabled=enabled))
    steps = create_steps(path)
    return steps, apply(steps, runner=runner)


def status(*, runner: Runner | None = None) -> str:
    """Read the task back: enabled, disabled by the toggle, absent or not readable."""
    completed = _run(query_steps()[0].argv, runner=runner)
    if int(getattr(completed, "returncode", 1)) != 0:
        return STATUS_ABSENT
    text = getattr(completed, "stdout", "") or ""
    if "<Task" not in text:
        return STATUS_UNKNOWN
    # Only the Settings block carries the on/off switch; the trigger says the same thing.
    settings = text.split("<Settings>", 1)[-1]
    if "<Enabled>false</Enabled>" in settings:
        return STATUS_DISABLED
    return STATUS_ENABLED


def main(argv: Sequence[str] | None = None, *, runner: Runner | None = None) -> int:
    """``--enable`` (default install), ``--disable``, ``--delete``, ``--status``."""
    parser = argparse.ArgumentParser(prog="headwaters.packaging.autostart", description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--enable", action="store_true", help="register or turn the task on")
    group.add_argument("--disable", action="store_true", help="turn the task off, keep it")
    group.add_argument("--delete", action="store_true", help="remove the task")
    group.add_argument("--status", action="store_true", help="print enabled/disabled/absent")
    parser.add_argument("--user", default=None, help="principle name, defaults to this user")
    parser.add_argument(
        "--dry-run", action="store_true", help="print the schtasks commands and do nothing"
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    install = Install.default()
    if args.status:
        print(status(runner=runner))
        return 0

    steps: list[TaskStep]
    if args.delete:
        steps = delete_steps()
    elif args.disable:
        steps = disable_steps()
    elif args.enable:
        who = args.user or current_user()
        path = write_task_file(install, task_xml(user=who, command=install.cli))
        steps = create_steps(path)
    else:
        print(f"autostart task {TASK_PATH}: {status(runner=runner)}")
        return 0

    for step in steps:
        print(subprocess.list2cmdline(list(step.argv)))
    if args.dry_run:
        return 0
    return apply(steps, runner=runner)


if __name__ == "__main__":  # pragma: no cover - the installer entry point
    sys.exit(main())
