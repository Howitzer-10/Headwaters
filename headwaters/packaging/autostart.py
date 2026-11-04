"""The per-user "At log on" task that starts the daemon, and its watchdog (HDW-405, HDW-521).

The tasks live in the Task Scheduler root folder and run as the signed-in user with
``LeastPrivilege``, so creating them needs no elevation and no ``HKLM``. They are registered from
an XML definition with ``schtasks /Create /XML`` because the useful settings (battery policy, no
execution time limit, ``IgnoreNew``) are not reachable through ``schtasks`` switches.

Two tasks, one action each:

* ``Headwaters`` (at logon) runs ``headwaters start``: the daemon is there when the user logs on.
  A logon is also the one moment a deliberate stop is forgotten: ``start`` clears the hold file.
* ``Headwaters Watchdog`` (every five minutes) runs ``headwaters watchdog``: it starts the daemon
  when it has died and leaves it alone when it is running or was stopped on purpose. Repeating the
  daemon's own idempotent start is the cheapest watchdog there is: no new long-lived process, no
  service, no admin, and ``IgnoreNew`` plus the five-minute interval bound a crash loop.

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
WATCHDOG_TASK_NAME = "Headwaters Watchdog"
#: The root folder (not ``\Headwaters\...``): a standard user may create tasks there.
TASK_FOLDER = "\\"
TASK_PATH = TASK_FOLDER + TASK_NAME
WATCHDOG_TASK_PATH = TASK_FOLDER + WATCHDOG_TASK_NAME

TASK_NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"
DEFAULT_ARGUMENTS = ("start",)
DESCRIPTION = "Starts the Headwaters daemon when this user logs on."

#: The watchdog runs the daemon's own idempotent ``start`` through ``headwaters watchdog``. The
#: interval is also the crash-loop bound: a daemon that dies at once is retried once per interval,
#: never in a tight loop, and ``IgnoreNew`` keeps one tick from overlapping the next.
WATCHDOG_ARGUMENTS = ("watchdog",)
WATCHDOG_INTERVAL_MINUTES = 5
#: A fixed boundary keeps the runs on :00, :05, :10... and leaves the XML deterministic.
WATCHDOG_START_BOUNDARY = "2026-01-01T00:00:00"
WATCHDOG_TIME_LIMIT = "PT2M"
WATCHDOG_DESCRIPTION = (
    "Starts the Headwaters daemon again when it has died (every "
    f"{WATCHDOG_INTERVAL_MINUTES} minutes), unless it was stopped on purpose."
)
SCHEDULE_LOGON = "logon"
SCHEDULE_PERIODIC = "periodic"

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


def _triggers(*, who: str, flag: str, schedule: str) -> str:
    """The ``<Triggers>`` block for the at-logon task or the repeating watchdog task."""
    if schedule == SCHEDULE_PERIODIC:
        return (
            "  <Triggers>\n"
            "    <TimeTrigger>\n"
            "      <Repetition>\n"
            f"        <Interval>PT{WATCHDOG_INTERVAL_MINUTES}M</Interval>\n"
            "      </Repetition>\n"
            f"      <StartBoundary>{WATCHDOG_START_BOUNDARY}</StartBoundary>\n"
            f"      <Enabled>{flag}</Enabled>\n"
            "    </TimeTrigger>\n"
            "  </Triggers>\n"
        )
    if schedule != SCHEDULE_LOGON:
        raise ValueError(
            f"unknown schedule {schedule!r}; expected {SCHEDULE_LOGON!r} or {SCHEDULE_PERIODIC!r}"
        )
    return (
        "  <Triggers>\n"
        "    <LogonTrigger>\n"
        f"      <Enabled>{flag}</Enabled>\n"
        f"      <UserId>{who}</UserId>\n"
        "    </LogonTrigger>\n"
        "  </Triggers>\n"
    )


def task_xml(
    *,
    user: str,
    command: Path | str,
    arguments: Sequence[str] = DEFAULT_ARGUMENTS,
    enabled: bool = True,
    description: str = DESCRIPTION,
    name: str = TASK_NAME,
    schedule: str = SCHEDULE_LOGON,
    start_when_available: bool = False,
    execution_time_limit: str = "PT0S",
) -> str:
    """The Task Scheduler XML for one user's task; valid input for ``schtasks /XML``.

    ``schedule`` is ``logon`` (the default) or ``periodic`` (the watchdog, every
    :data:`WATCHDOG_INTERVAL_MINUTES` minutes).
    """
    flag = "true" if enabled else "false"
    who = escape(str(user), {'"': "&quot;"})
    start_available = "true" if start_when_available else "false"
    return (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        f'<Task version="1.2" xmlns="{TASK_NS}">\n'
        "  <RegistrationInfo>\n"
        f"    <Description>{escape(description)}</Description>\n"
        f"    <URI>{escape(TASK_FOLDER + name)}</URI>\n"
        "  </RegistrationInfo>\n"
        + _triggers(who=who, flag=flag, schedule=schedule)
        + "  <Principals>\n"
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
        f"    <StartWhenAvailable>{start_available}</StartWhenAvailable>\n"
        "    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>\n"
        "    <AllowStartOnDemand>true</AllowStartOnDemand>\n"
        f"    <Enabled>{flag}</Enabled>\n"
        "    <Hidden>false</Hidden>\n"
        "    <RunOnlyIfIdle>false</RunOnlyIfIdle>\n"
        "    <WakeToRun>false</WakeToRun>\n"
        f"    <ExecutionTimeLimit>{escape(execution_time_limit)}</ExecutionTimeLimit>\n"
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


def watchdog_task_xml(*, user: str, command: Path | str, enabled: bool = True) -> str:
    """The repeating task that restarts a dead daemon and respects a deliberate stop."""
    return task_xml(
        user=user,
        command=command,
        name=WATCHDOG_TASK_NAME,
        arguments=WATCHDOG_ARGUMENTS,
        enabled=enabled,
        description=WATCHDOG_DESCRIPTION,
        schedule=SCHEDULE_PERIODIC,
        start_when_available=True,
        execution_time_limit=WATCHDOG_TIME_LIMIT,
    )


def write_task_file(install: Install, text: str, *, path: Path | None = None) -> Path:
    """Write a definition as UTF-16 (the encoding Task Scheduler documents) and return it."""
    install.runtime.mkdir(parents=True, exist_ok=True)
    target = install.autostart_xml if path is None else path
    target.write_text(text, encoding="utf-16")
    return target


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


def watchdog_create_steps(xml_path: Path | str) -> list[TaskStep]:
    return [
        TaskStep(
            argv=(SCHTASKS, "/Create", "/TN", WATCHDOG_TASK_PATH, "/XML", str(xml_path), "/F"),
            why=f"register the {WATCHDOG_INTERVAL_MINUTES}-minute watchdog task for this user",
        )
    ]


def enable_steps() -> list[TaskStep]:
    return [
        TaskStep(argv=(SCHTASKS, "/Change", "/TN", TASK_PATH, "/ENABLE"), why="turn it on"),
        TaskStep(
            argv=(SCHTASKS, "/Change", "/TN", WATCHDOG_TASK_PATH, "/ENABLE"),
            why="turn the watchdog on",
        ),
    ]


def disable_steps() -> list[TaskStep]:
    return [
        TaskStep(argv=(SCHTASKS, "/Change", "/TN", TASK_PATH, "/DISABLE"), why="turn it off"),
        TaskStep(
            argv=(SCHTASKS, "/Change", "/TN", WATCHDOG_TASK_PATH, "/DISABLE"),
            why="turn the watchdog off",
        ),
    ]


def delete_steps() -> list[TaskStep]:
    return [
        TaskStep(argv=(SCHTASKS, "/Delete", "/TN", TASK_PATH, "/F"), why="remove the task"),
        TaskStep(
            argv=(SCHTASKS, "/Delete", "/TN", WATCHDOG_TASK_PATH, "/F"),
            why="remove the watchdog",
        ),
    ]


def query_steps() -> list[TaskStep]:
    return [TaskStep(argv=_query_argv(TASK_NAME), why="read the task definition back")]


def _query_argv(name: str) -> tuple[str, ...]:
    return (SCHTASKS, "/Query", "/TN", TASK_FOLDER + name, "/XML")


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
    """Write both definitions, register both tasks, logon task first. Returns steps and code."""
    steps = install_steps(install, user=user, enabled=enabled)
    return steps, apply(steps, runner=runner)


def install_steps(
    install: Install, *, user: str | None = None, enabled: bool = True
) -> list[TaskStep]:
    """Write the two definitions into the install and return the steps that register them."""
    who = user if user is not None else current_user()
    logon_xml = write_task_file(install, task_xml(user=who, command=install.cli, enabled=enabled))
    watchdog_xml = write_task_file(
        install,
        watchdog_task_xml(user=who, command=install.cli, enabled=enabled),
        path=install.watchdog_xml,
    )
    return [*create_steps(logon_xml), *watchdog_create_steps(watchdog_xml)]


def status(*, runner: Runner | None = None, name: str = TASK_NAME) -> str:
    """Read a task back: enabled, disabled by the toggle, absent or not readable."""
    completed = _run(_query_argv(name), runner=runner)
    if int(getattr(completed, "returncode", 1)) != 0:
        return STATUS_ABSENT
    text = getattr(completed, "stdout", "") or ""
    # A task that was never created is reported on stdout with a zero exit code, not as an error.
    if "exist" in text.lower():
        return STATUS_ABSENT
    if "<Task" not in text:
        return STATUS_UNKNOWN
    # Only the Settings block carries the on/off switch; the trigger says the same thing.
    settings = text.split("<Settings>", 1)[-1]
    if "<Enabled>false</Enabled>" in settings:
        return STATUS_DISABLED
    return STATUS_ENABLED


def main(argv: Sequence[str] | None = None, *, runner: Runner | None = None) -> int:
    """``--enable`` (default install), ``--disable``, ``--delete``, ``--status``.

    ``--watchdog`` narrows ``--status`` to the watchdog task; the enable/disable/delete
    operations always cover both tasks, because half a self-healing install is not one.
    """
    parser = argparse.ArgumentParser(prog="headwaters.packaging.autostart", description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--enable", action="store_true", help="register or turn the tasks on")
    group.add_argument("--disable", action="store_true", help="turn the tasks off, keep them")
    group.add_argument("--delete", action="store_true", help="remove the tasks")
    group.add_argument("--status", action="store_true", help="print enabled/disabled/absent")
    parser.add_argument("--user", default=None, help="principle name, defaults to this user")
    parser.add_argument(
        "--watchdog", action="store_true", help="with --status, report the watchdog task"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="print the schtasks commands and do nothing"
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    install = Install.default()
    if args.status:
        name = WATCHDOG_TASK_NAME if args.watchdog else TASK_NAME
        print(status(runner=runner, name=name))
        return 0

    steps: list[TaskStep]
    if args.delete:
        steps = delete_steps()
    elif args.disable:
        steps = disable_steps()
    elif args.enable:
        steps = install_steps(install, user=args.user)
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
