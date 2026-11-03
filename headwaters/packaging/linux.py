"""The Linux target (HDW-406): a systemd user unit, its installer and the payload script.

A Linux install is the same shape as the Windows one -- a per-user root holding ``runtime/`` and a
virtualenv built by ``uv`` from the frozen lockfile -- with two differences that matter:

* the service is a *user* unit (``systemctl --user``), so nothing here needs root and no file
  leaves ``$HOME``;
* the launcher is ``runtime/.venv/bin/python`` rather than ``Scripts\\python.exe``.

``ExecStart`` runs ``python -m headwaters.daemon``, which is the foreground daemon, not
``headwaters start`` (that one spawns a detached child and exits, so a ``Type=simple`` unit would
consider the service dead the moment it returned).

Nothing here touches systemd unless the caller asks for it: every plan is a list of argv tuples
and every side effect is injectable, so the target is fully testable on a machine without
systemd (this repository's CI runs on Windows too). The doctor's ``install`` check is left
alone; see ``docs/reports/HDW-406.md`` for the follow-up.
"""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from headwaters.core import paths
from headwaters.packaging.layout import (
    CATALOG_DIR_NAME,
    LOCK_FILE_NAME,
    PROJECT_FILE_NAME,
    RUNTIME_DIR_NAME,
    VENV_DIR_NAME,
    is_inside,
)
from headwaters.packaging.uninstall import remove_saved_keys, saved_aliases

#: The unit name systemd uses for ``systemctl --user ...``.
SERVICE_NAME = "headwaters.service"

#: ``--path`` overrides the app root; the systemd unit still goes to the user unit directory.
ROOT_ENV = "HEADWATERS_ROOT"
UV_ENV = "HEADWATERS_UV"

SYSTEMCTL = "systemctl"
USER_FLAG = "--user"

DESCRIPTION = "Headwaters: local-first LLM router (daemon, gateway and MCP endpoint)"
DOCUMENTATION = "https://github.com/Howitzer-10/Headwaters"

DAEMON_ARGS = ("-m", "headwaters.daemon")

#: The daemon's own exit codes (``headwaters/daemon/__main__.py``): 2 is an unreadable
#: ``rules.yaml``, 3 means another daemon already holds the lock. Both are configuration states,
#: so restarting on them would only fail again in a loop.
PREVENT_RESTART_ON = (2, 3)

UV_NAME = "uv"
BIN_DIR_NAME = "bin"
UNIT_DIR_PARTS = ("systemd", "user")
INSTALL_SCRIPT_NAME = "install.sh"

STATUS_ENABLED = "enabled"
STATUS_DISABLED = "disabled"
STATUS_ABSENT = "absent"
STATUS_UNKNOWN = "unknown"

DISABLED_ANSWERS = ("disabled", "masked", "masked-runtime", "linked", "indirect", "static")

OWNER_PYTHON = "python"
OWNER_CALLER = "caller"

KIND_UNIT = "unit"
KIND_TREE = "tree"
KIND_KEYS = "keys"

Runner = Callable[..., Any]


class LinuxError(RuntimeError):
    """A removal pointed outside the roots this install owns."""


@dataclass(frozen=True)
class LinuxInstall:
    """The trees a Linux install owns.

    ``root`` is ``$XDG_DATA_HOME/headwaters`` (``~/.local/share/headwaters``) and holds
    ``runtime/`` only. ``config_home`` is ``$XDG_CONFIG_HOME`` (``~/.config``) and holds the
    systemd user unit under ``systemd/user``. ``state`` is the daemon's own state directory from
    :mod:`headwaters.core.paths`, so ``HEADWATERS_HOME`` still moves it.
    """

    root: Path
    config_home: Path
    state: Path

    @classmethod
    def default(cls, env: Mapping[str, str] | None = None) -> LinuxInstall:
        """The real per-user layout, from the XDG variables the service will also see."""
        source = os.environ if env is None else env
        home = Path(source.get("HOME") or Path.home())
        data = _xdg(source, "XDG_DATA_HOME", home / ".local" / "share")
        config = _xdg(source, "XDG_CONFIG_HOME", home / ".config")
        override = (source.get(ROOT_ENV) or "").strip()
        root = Path(override) if override else data / "headwaters"
        return cls(root=root, config_home=config, state=paths.state_dir())

    @classmethod
    def at(
        cls,
        root: Path | str,
        *,
        config_home: Path | str | None = None,
        state: Path | str | None = None,
    ) -> LinuxInstall:
        """A self-contained layout under ``root``; what the tests drive."""
        base = Path(root)
        return cls(
            root=base,
            config_home=Path(config_home) if config_home is not None else base / "config",
            state=Path(state) if state is not None else base / "state",
        )

    @property
    def runtime(self) -> Path:
        return self.root / RUNTIME_DIR_NAME

    @property
    def venv(self) -> Path:
        return self.runtime / VENV_DIR_NAME

    @property
    def bin(self) -> Path:
        return self.venv / BIN_DIR_NAME

    @property
    def python(self) -> Path:
        return self.bin / "python"

    @property
    def cli(self) -> Path:
        return self.bin / "headwaters"

    @property
    def mcp(self) -> Path:
        return self.bin / "headwaters-mcp"

    @property
    def uv(self) -> Path:
        return self.runtime / UV_NAME

    @property
    def lock(self) -> Path:
        return self.runtime / LOCK_FILE_NAME

    @property
    def project(self) -> Path:
        return self.runtime / PROJECT_FILE_NAME

    @property
    def catalog(self) -> Path:
        return self.runtime / CATALOG_DIR_NAME

    @property
    def unit_dir(self) -> Path:
        return self.config_home.joinpath(*UNIT_DIR_PARTS)

    @property
    def unit(self) -> Path:
        return self.unit_dir / SERVICE_NAME

    def roots(self) -> tuple[Path, ...]:
        """Every tree this install may delete from; the guard for ``--uninstall``."""
        return (self.root, self.state)


def _xdg(source: Mapping[str, str], name: str, fallback: Path) -> Path:
    value = (source.get(name) or "").strip()
    return Path(value) if value else fallback


def _quote(argument: str) -> str:
    """Quote an ``ExecStart`` word the way systemd wants it, but only when it needs it."""
    if not any(character in argument for character in ' \t"\\'):
        return argument
    escaped = argument.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def exec_start(install: LinuxInstall) -> str:
    """The one command the unit runs: the private interpreter, in the foreground."""
    return " ".join(_quote(part) for part in (str(install.python), *DAEMON_ARGS))


def unit_text(install: LinuxInstall) -> str:
    """The unit file as text: pure, so the tests can assert every directive."""
    prevent = " ".join(str(code) for code in PREVENT_RESTART_ON)
    lines = [
        "[Unit]",
        f"Description={DESCRIPTION}",
        f"Documentation={DOCUMENTATION}",
        "# The daemon only needs loopback, but the catalog refresh reaches the network.",
        "After=network-online.target",
        "Wants=network-online.target",
        "",
        "[Service]",
        "# ``simple`` and ``exec`` differ only in when the service counts as started; ``simple``",
        "# is what every distribution ships in its examples and needs no sd_notify support.",
        "Type=simple",
        f"ExecStart={exec_start(install)}",
        f"WorkingDirectory={_quote(str(install.root))}",
        "Environment=PYTHONUNBUFFERED=1",
        "Environment=PYTHONUTF8=1",
        "Restart=on-failure",
        "RestartSec=5",
        f"RestartPreventExitStatus={prevent}",
        "TimeoutStopSec=30",
        "# SIGTERM to the main process first, then SIGKILL to whatever it left behind.",
        "KillMode=mixed",
        "SyslogIdentifier=headwaters",
        "",
        "[Install]",
        "WantedBy=default.target",
        "",
    ]
    return "\n".join(lines)


def write_unit(install: LinuxInstall) -> Path:
    """Write the unit into the user unit directory; the only writer in this module."""
    install.unit_dir.mkdir(parents=True, exist_ok=True)
    path = install.unit
    path.write_text(unit_text(install), encoding="utf-8", newline="\n")
    return path


@dataclass(frozen=True)
class Step:
    """One command to run, with the reason a human reading the log cares about."""

    argv: tuple[str, ...]
    why: str = ""

    def command_line(self) -> str:
        return shlex.join(self.argv)

    def as_dict(self) -> dict[str, Any]:
        return {"argv": list(self.argv), "why": self.why}


def install_steps() -> list[Step]:
    """Pick up the new unit, then start it now and at every login."""
    return [
        Step((SYSTEMCTL, USER_FLAG, "daemon-reload"), "read the new unit file"),
        Step(
            (SYSTEMCTL, USER_FLAG, "enable", "--now", SERVICE_NAME),
            "start it now and start it again at every login",
        ),
    ]


def uninstall_steps() -> list[Step]:
    """Stop it and unhook it from login before the unit file goes away."""
    return [
        Step(
            (SYSTEMCTL, USER_FLAG, "disable", "--now", SERVICE_NAME),
            "stop it and unhook it from login",
        ),
        Step((SYSTEMCTL, USER_FLAG, "daemon-reload"), "forget the removed unit file"),
        Step(
            (SYSTEMCTL, USER_FLAG, "reset-failed", SERVICE_NAME),
            "clear the recorded failure state",
        ),
    ]


def status_steps() -> list[Step]:
    return [
        Step((SYSTEMCTL, USER_FLAG, "is-enabled", SERVICE_NAME), "ask systemd about the unit"),
    ]


def _run(argv: Sequence[str], *, runner: Runner | None = None) -> Any:
    if runner is not None:
        return runner(list(argv))
    return subprocess.run(list(argv), capture_output=True, text=True, check=False)


def apply(steps: Sequence[Step], *, runner: Runner | None = None) -> int:
    """Run the steps in order and stop at the first non-zero exit code."""
    for step in steps:
        code = int(getattr(_run(step.argv, runner=runner), "returncode", 0))
        if code != 0:
            print(f"error: {step.command_line()} failed with {code}", file=sys.stderr)
            return code
    return 0


def classify_status(code: int, output: str) -> str:
    """Map ``systemctl --user is-enabled`` to one of the four states this module reports."""
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    answer = lines[0] if lines else ""
    if answer == "not-found":
        return STATUS_ABSENT
    if answer.startswith("enabled"):
        return STATUS_ENABLED
    if answer in DISABLED_ANSWERS:
        return STATUS_DISABLED
    if not answer and code != 0:
        return STATUS_ABSENT
    return STATUS_UNKNOWN


def status(*, runner: Runner | None = None) -> str:
    """Whether the unit is enabled, without changing anything."""
    step = status_steps()[0]
    completed = _run(step.argv, runner=runner)
    return classify_status(
        int(getattr(completed, "returncode", 0)), str(getattr(completed, "stdout", "") or "")
    )


@dataclass(frozen=True)
class UnitCheck:
    """What the install script verifies after ``uv sync`` and ``systemctl enable``."""

    ok: bool
    missing: tuple[Path, ...]

    def detail(self) -> str:
        if self.ok:
            return "the private runtime, both launchers and the unit file are present"
        return "missing: " + ", ".join(str(path) for path in self.missing)


def verify(install: LinuxInstall) -> UnitCheck:
    """Check the pieces an install must have produced; touches nothing."""
    wanted = (
        install.uv,
        install.lock,
        install.project,
        install.catalog,
        install.cli,
        install.mcp,
        install.python,
        install.unit,
    )
    missing = tuple(path for path in wanted if not path.exists())
    return UnitCheck(ok=not missing, missing=missing)


@dataclass(frozen=True)
class Removal:
    """One unit of removal, owned by whoever can actually do it."""

    kind: str
    target: Path | None
    why: str
    owner: str

    def describe(self) -> str:
        subject = str(self.target) if self.target is not None else "(keyring)"
        verb = "remove" if self.owner == OWNER_PYTHON else "keep"
        return f"{verb:6} {subject}  # {self.why}"


@dataclass(frozen=True)
class RemovalPlan:
    """What ``--uninstall`` removes, in order, and what it can only tell you to remove."""

    steps: tuple[Removal, ...]

    @property
    def owned(self) -> tuple[Removal, ...]:
        return tuple(step for step in self.steps if step.owner == OWNER_PYTHON)

    @property
    def deferred(self) -> tuple[Removal, ...]:
        return tuple(step for step in self.steps if step.owner == OWNER_CALLER)

    def describe(self) -> list[str]:
        return [step.describe() for step in self.steps]


def removal_plan(
    install: LinuxInstall,
    *,
    remove_state: bool = False,
    remove_keys: bool = False,
) -> RemovalPlan:
    """Build the removal plan; touches nothing.

    ``runtime`` is always in the plan but always owned by the caller: the uninstaller runs from
    ``runtime/.venv/bin/python``, so it can unlink its own files and still cannot be trusted to
    delete the tree it is executing out of.
    """
    steps = [Removal(KIND_UNIT, install.unit, "the systemd user unit file", OWNER_PYTHON)]
    if remove_state:
        steps.append(
            Removal(KIND_TREE, install.state, "the database, the log and the cache", OWNER_PYTHON)
        )
    if remove_keys:
        steps.append(
            Removal(KIND_KEYS, None, "the provider keys in the login keyring", OWNER_PYTHON)
        )
    steps.append(
        Removal(
            KIND_TREE,
            install.runtime,
            "the private runtime, including the interpreter running this",
            OWNER_CALLER,
        )
    )
    return RemovalPlan(tuple(steps))


def _guarded(install: LinuxInstall, target: Path) -> Path:
    if not any(is_inside(target, root) for root in install.roots()):
        raise LinuxError(f"refusing to delete {target}: outside the install roots")
    return target


@dataclass(frozen=True)
class RemovalResult:
    """What actually went; ``error`` carries the first keyring failure, if any."""

    removed: tuple[str, ...] = ()
    keys_removed: int = 0
    error: str | None = None


def apply_removal(
    install: LinuxInstall,
    uninstall_plan: RemovalPlan,
    *,
    delete_tree: Callable[[Path], Any] = shutil.rmtree,
    alias_source: Callable[[], Sequence[str]] = saved_aliases,
    key_remover: Callable[[Sequence[str]], tuple[int, str | None]] = remove_saved_keys,
) -> RemovalResult:
    """Remove the parts this process owns; the caller-owned runtime is left alone."""
    removed: list[str] = []
    keys_removed = 0
    error: str | None = None
    for step in uninstall_plan.owned:
        if step.kind == KIND_UNIT and step.target is not None:
            if step.target.exists():
                step.target.unlink()
                removed.append(str(step.target))
        elif step.kind == KIND_TREE and step.target is not None:
            target = _guarded(install, step.target)
            if target.exists():
                delete_tree(target)
                removed.append(str(target))
        elif step.kind == KIND_KEYS:
            count, failure = key_remover(list(alias_source()))
            keys_removed += count
            error = error or failure
    return RemovalResult(removed=tuple(removed), keys_removed=keys_removed, error=error)


def render_install_script() -> str:
    """The ``install.sh`` shipped in the payload; compared byte for byte by the tests."""
    lines = [
        "#!/usr/bin/env bash",
        "# Generated by headwaters/packaging/linux.py (HDW-406). Do not edit by hand:",
        "# tests/test_packaging_linux.py compares this file with the generator.",
        "#",
        "# Installs Headwaters into a per-user prefix and registers the systemd user unit",
        "# headwaters.service. No root, no system-wide file, no Python of your own: uv builds a",
        "# private virtualenv from the frozen lockfile.",
        "#",
        "#   ./install.sh                          install (or repair) and start",
        "#   HEADWATERS_ROOT=~/hw ./install.sh     install somewhere else",
        "#",
        "# Stop and remove the unit with:",
        "#   <root>/runtime/.venv/bin/python -m headwaters.packaging.linux --uninstall --yes",
        "set -euo pipefail",
        "",
        'HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"',
        'SOURCE="$HERE/runtime"',
        'ROOT="${HEADWATERS_ROOT:-${XDG_DATA_HOME:-$HOME/.local/share}/headwaters}"',
        'UV="${HEADWATERS_UV:-uv}"',
        "",
        "fail() {",
        '  echo "error: $*" >&2',
        "  exit 1",
        "}",
        "",
        '[ -d "$SOURCE" ] || fail "runtime/ is missing next to install.sh; unpack the payload"',
        'command -v "$UV" >/dev/null 2>&1 || fail \\',
        '  "uv is not on PATH; install it with: curl -LsSf https://astral.sh/uv/install.sh | sh"',
        "",
        'echo "Installing Headwaters into $ROOT"',
        'mkdir -p "$ROOT/runtime"',
        'cp -R "$SOURCE"/. "$ROOT/runtime"/',
        "",
        'echo "Building the private runtime with $("$UV" --version)"',
        '"$UV" sync --frozen --no-dev --python 3.11 --project "$ROOT/runtime"',
        "",
        '"$ROOT/runtime/.venv/bin/python" -m headwaters.packaging.linux \\',
        '  --install --yes --path "$ROOT"',
        "",
        "echo",
        'echo "Installed. Check it with:"',
        'echo "  systemctl --user status headwaters.service"',
        'echo "  $ROOT/runtime/.venv/bin/headwaters doctor"',
        'echo "To keep it running while you are logged out: loginctl enable-linger $USER"',
        "",
    ]
    return "\n".join(lines)


def _target(path: str | None) -> LinuxInstall:
    """``--path`` moves the app root only; the unit still belongs to the user's config."""
    default = LinuxInstall.default()
    if not path:
        return default
    return LinuxInstall.at(path, config_home=default.config_home, state=default.state)


def print_plan(steps: Sequence[Step]) -> None:
    for step in steps:
        print(f"  {step.command_line()}")
        if step.why:
            print(f"    {step.why}")


def main(
    argv: Sequence[str] | None = None,
    *,
    install: LinuxInstall | None = None,
    runner: Runner | None = None,
    alias_source: Callable[[], Sequence[str]] = saved_aliases,
    key_remover: Callable[[Sequence[str]], tuple[int, str | None]] = remove_saved_keys,
) -> int:
    """Install, uninstall or report the user unit. Writes or runs nothing without ``--yes``."""
    parser = argparse.ArgumentParser(prog="headwaters.packaging.linux", description=DESCRIPTION)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--install", action="store_true", help="write the unit and start it")
    action.add_argument("--uninstall", action="store_true", help="stop it and remove the unit")
    action.add_argument("--status", action="store_true", help="print enabled/disabled/absent")
    parser.add_argument("--path", default=None, help=f"the app root (env {ROOT_ENV} also works)")
    parser.add_argument("--dry-run", action="store_true", help="print the plan, touch nothing")
    parser.add_argument("--yes", action="store_true", help="apply the plan (this is the consent)")
    parser.add_argument(
        "--remove-state", action="store_true", help="uninstall also deletes the database and logs"
    )
    parser.add_argument(
        "--remove-keys", action="store_true", help="uninstall also deletes the stored provider keys"
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    target = install if install is not None else _target(args.path)
    if args.status or not (args.install or args.uninstall):
        answer = status(runner=runner)
        print(f"{SERVICE_NAME}: {answer}")
        print(f"unit file: {target.unit} ({'present' if target.unit.exists() else 'absent'})")
        return 0

    if args.uninstall:
        return _uninstall(
            target,
            args,
            runner=runner,
            alias_source=alias_source,
            key_remover=key_remover,
        )
    return _install(target, args, runner=runner)


def _install(target: LinuxInstall, args: argparse.Namespace, *, runner: Runner | None) -> int:
    steps = install_steps()
    print(f"Headwaters installs {SERVICE_NAME} for this user:")
    print(unit_text(target))
    print(f"write  {target.unit}")
    print_plan(steps)
    if args.dry_run:
        return 0
    if not args.yes:
        print("nothing written: rerun with --yes to install")
        return 0
    path = write_unit(target)
    print(f"wrote {path}")
    code = apply(steps, runner=runner)
    if code != 0:
        print(
            "error: `systemctl --user` failed; a user session (or `loginctl enable-linger`) "
            "is required",
            file=sys.stderr,
        )
        return code
    check = verify(target)
    print(check.detail())
    return 0 if check.ok else 1


def _uninstall(
    target: LinuxInstall,
    args: argparse.Namespace,
    *,
    runner: Runner | None,
    alias_source: Callable[[], Sequence[str]],
    key_remover: Callable[[Sequence[str]], tuple[int, str | None]],
) -> int:
    steps = uninstall_steps()
    uninstall_plan = removal_plan(
        target, remove_state=args.remove_state, remove_keys=args.remove_keys
    )
    print_plan(steps)
    for line in uninstall_plan.describe():
        print(f"  {line}")
    if args.dry_run:
        return 0
    if not args.yes:
        print("nothing removed: rerun with --yes to uninstall")
        return 0
    code = apply(steps, runner=runner)
    if code != 0:
        print("warning: systemctl --user did not report success; removing the files anyway")
    result = apply_removal(
        target, uninstall_plan, alias_source=alias_source, key_remover=key_remover
    )
    for path in result.removed:
        print(f"removed {path}")
    if result.keys_removed:
        print(f"removed {result.keys_removed} stored key(s)")
    if result.error:
        print(f"warning: keyring: {result.error}", file=sys.stderr)
    for step in uninstall_plan.deferred:
        print(f"still there: {step.target}")
        print(f"  rm -rf {shlex.quote(str(step.target))}")
    return 0


if __name__ == "__main__":  # pragma: no cover - release entry point
    sys.exit(main())
