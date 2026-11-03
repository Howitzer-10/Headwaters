"""What an uninstall removes, and only after the user says so (HDW-405).

Deleting the runtime from inside the runtime is not possible: the uninstaller itself runs
``<root>\\runtime\\.venv\\Scripts\\python.exe``, and Windows will not remove a directory holding the
running image. So the plan is split by *owner*:

* the Inno uninstaller removes the scheduled task and ``runtime`` after this process exits;
* this process removes the optional parts (state, keys) while it still can read the keyring.

The default plan touches neither ``%LOCALAPPDATA%\\Headwaters`` nor ``%APPDATA%\\Headwaters``
beyond ``runtime``: state and keys go only when the user answered yes.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from headwaters.packaging.autostart import TASK_PATH
from headwaters.packaging.layout import Install, is_inside

OWNER_INSTALLER = "installer"
OWNER_PYTHON = "python"

KIND_TASK = "task"
KIND_TREE = "tree"
KIND_KEYS = "keys"


@dataclass(frozen=True)
class Options:
    """The three questions the uninstaller asks, as flags."""

    remove_task: bool = True
    remove_runtime: bool = True
    remove_state: bool = False
    remove_keys: bool = False


@dataclass(frozen=True)
class Step:
    """One unit of removal, owned by whoever can actually do it."""

    kind: str
    target: str
    why: str
    owner: str

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "target": self.target, "why": self.why, "owner": self.owner}


@dataclass(frozen=True)
class Plan:
    """What will go, in order, and what will stay."""

    steps: tuple[Step, ...]
    kept: tuple[str, ...] = ()

    @property
    def optional(self) -> tuple[Step, ...]:
        return tuple(step for step in self.steps if step.owner == OWNER_PYTHON)

    @property
    def deferred(self) -> tuple[Step, ...]:
        return tuple(step for step in self.steps if step.owner == OWNER_INSTALLER)

    def as_dict(self) -> dict[str, Any]:
        return {
            "steps": [step.as_dict() for step in self.steps],
            "kept": list(self.kept),
        }


def plan(install: Install, options: Options | None = None) -> Plan:
    """Build the removal plan; touches nothing."""
    opts = options or Options()
    steps: list[Step] = []
    kept: list[str] = []

    if opts.remove_task:
        steps.append(
            Step(
                kind=KIND_TASK,
                target=TASK_PATH,
                why="the at-logon task would restart a program that is gone",
                owner=OWNER_INSTALLER,
            )
        )

    if opts.remove_state:
        steps.append(
            Step(
                kind=KIND_TREE,
                target=str(install.local),
                why="install root: runtime, database, cache and logs",
                owner=OWNER_INSTALLER,
            )
        )
    elif opts.remove_runtime:
        steps.append(
            Step(
                kind=KIND_TREE,
                target=str(install.runtime),
                why="the private runtime and its virtualenv",
                owner=OWNER_INSTALLER,
            )
        )
        kept.extend((str(install.local), str(install.roaming)))

    if opts.remove_state:
        steps.append(
            Step(
                kind=KIND_TREE,
                target=str(install.roaming),
                why="rules and catalog overrides",
                owner=OWNER_PYTHON,
            )
        )

    if opts.remove_keys:
        steps.append(
            Step(
                kind=KIND_KEYS,
                target="every saved API key",
                why="provider credentials in the OS credential store",
                owner=OWNER_PYTHON,
            )
        )

    return Plan(tuple(steps), tuple(dict.fromkeys(kept)))


class UninstallError(RuntimeError):
    """A step pointed outside the two per-user roots, or no key remover was injected."""


def _guarded(install: Install, target: str) -> Path:
    path = Path(target)
    if not any(is_inside(path, root) for root in install.roots()):
        raise UninstallError(f"refusing to delete {target}: outside the install roots")
    return path


def apply_optional(
    install: Install,
    uninstall_plan: Plan,
    *,
    delete_tree: Callable[[Path], Any] = shutil.rmtree,
    delete_keys: Callable[[Sequence[str]], Any] | None = None,
    aliases: Callable[[], Sequence[str]] | None = None,
) -> list[str]:
    """Remove the steps this process owns (state dirs, keys). Installer steps are left alone."""
    removed: list[str] = []
    for step in uninstall_plan.optional:
        if step.kind == KIND_TREE:
            path = _guarded(install, step.target)
            if path.exists():
                delete_tree(path)
            removed.append(str(path))
        elif step.kind == KIND_KEYS:
            if delete_keys is None:
                raise UninstallError("no key remover injected")
            names = list(aliases() if aliases is not None else ())
            if names:
                delete_keys(names)
            removed.append(f"{len(names)} keys")
    return removed


def _store() -> Any:
    from headwaters.core.keys import KeyStore
    from headwaters.core.rules_io import load_rules

    return KeyStore(load_rules())


def saved_aliases(store: Any = None) -> list[str]:
    """Every alias in the credential store; a broken keyring reports none rather than failing."""
    try:
        return list((store or _store()).aliases())
    except Exception:
        return []


def remove_saved_keys(aliases: Sequence[str], store: Any = None) -> tuple[int, str | None]:
    """Remove ``aliases``; returns how many went and the first error, if any."""
    target = store or _store()
    gone = 0
    error: str | None = None
    for alias in aliases:
        try:
            if target.remove(alias):
                gone += 1
        except Exception as exc:
            error = str(exc)
    return gone, error


def main(
    argv: Sequence[str] | None = None,
    *,
    install: Install | None = None,
    store: Any = None,
    delete_tree: Callable[[Path], Any] = shutil.rmtree,
) -> int:
    """``python -m headwaters.packaging.uninstall --yes --remove-state --remove-keys``."""
    parser = argparse.ArgumentParser(prog="headwaters.packaging.uninstall", description=__doc__)
    parser.add_argument("--remove-state", action="store_true", help="also delete state and rules")
    parser.add_argument("--remove-keys", action="store_true", help="also delete saved API keys")
    parser.add_argument("--keep-runtime", action="store_true", help="leave the runtime alone")
    parser.add_argument("--yes", action="store_true", help="actually delete; without it, only plan")
    args = parser.parse_args(list(argv) if argv is not None else None)

    target = install or Install.default()
    options = Options(
        remove_runtime=not args.keep_runtime,
        remove_state=args.remove_state,
        remove_keys=args.remove_keys,
    )
    uninstall_plan = plan(target, options)
    for step in uninstall_plan.steps:
        print(f"{step.owner:9} {step.kind:5} {step.target}  ({step.why})")
    if not uninstall_plan.optional:
        return 0
    if not args.yes:
        print("nothing removed: rerun with --yes to apply")
        return 0

    try:
        removed = apply_optional(
            target,
            uninstall_plan,
            delete_tree=delete_tree,
            delete_keys=lambda names: remove_saved_keys(names, store)[0],
            aliases=lambda: saved_aliases(store),
        )
    except UninstallError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for entry in removed:
        print(f"removed {entry}")
    return 0


if __name__ == "__main__":  # pragma: no cover - the uninstaller entry point
    sys.exit(main())
