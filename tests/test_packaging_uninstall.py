"""The uninstaller plan (HDW-405): task and runtime always, state and keys only on consent.

Everything runs against ``tmp_path``. The task and the runtime are *never* removed by this
module (the uninstaller owns them), so the tests assert on the plan and on the optional steps.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from headwaters.packaging import uninstall
from headwaters.packaging.layout import Install
from headwaters.packaging.uninstall import (
    KIND_KEYS,
    KIND_TASK,
    KIND_TREE,
    OWNER_INSTALLER,
    OWNER_PYTHON,
    Options,
    Plan,
    Step,
    UninstallError,
    apply_optional,
    plan,
)


class FakeStore:
    """A keyring stand-in: records removals and never touches the real credential store."""

    def __init__(self, aliases: list[str] | None = None, broken: bool = False) -> None:
        self._aliases = list(aliases or [])
        self.broken = broken
        self.removed: list[str] = []

    def aliases(self) -> list[str]:
        if self.broken:
            raise RuntimeError("keyring is not available")
        return list(self._aliases)

    def remove(self, alias: str) -> bool:
        self.removed.append(alias)
        return alias in self._aliases


def _install(tmp_path: Path) -> Install:
    return Install.at(tmp_path / "Headwaters")


def _populate(install: Install) -> None:
    (install.runtime / ".venv" / "Scripts").mkdir(parents=True)
    (install.local / "headwaters.db").write_text("", encoding="utf-8")
    install.roaming.mkdir(parents=True, exist_ok=True)
    (install.roaming / "rules.yaml").write_text("", encoding="utf-8")


def test_the_default_plan_removes_the_task_and_the_runtime_only(tmp_path: Path) -> None:
    install = _install(tmp_path)

    result = plan(install)

    assert [(step.kind, step.owner) for step in result.steps] == [
        (KIND_TASK, OWNER_INSTALLER),
        (KIND_TREE, OWNER_INSTALLER),
    ]
    assert result.steps[1].target == str(install.runtime)
    assert str(install.local) in result.kept
    assert str(install.roaming) in result.kept
    assert result.optional == ()


def test_state_and_keys_are_opt_in(tmp_path: Path) -> None:
    install = _install(tmp_path)

    result = plan(install, Options(remove_state=True, remove_keys=True))

    kinds = [step.kind for step in result.steps]
    assert kinds == [KIND_TASK, KIND_TREE, KIND_TREE, KIND_KEYS]
    assert [step.owner for step in result.steps] == [
        OWNER_INSTALLER,
        OWNER_INSTALLER,
        OWNER_PYTHON,
        OWNER_PYTHON,
    ]
    assert [step.target for step in result.steps][1] == str(install.local)
    assert [step.target for step in result.steps][2] == str(install.roaming)
    assert result.kept == ()


def test_removing_the_state_root_covers_the_runtime(tmp_path: Path) -> None:
    install = _install(tmp_path)

    targets = [step.target for step in plan(install, Options(remove_state=True)).steps]

    assert str(install.local) in targets
    assert str(install.runtime) not in targets


def test_apply_optional_removes_the_consented_trees(tmp_path: Path) -> None:
    install = _install(tmp_path)
    _populate(install)

    removed = apply_optional(install, plan(install, Options(remove_state=True)))

    assert not install.roaming.exists()
    assert install.local.exists()  # the runtime and the database stay until the installer goes
    assert removed == [str(install.roaming)]


def test_apply_optional_never_removes_installer_steps(tmp_path: Path) -> None:
    install = _install(tmp_path)
    _populate(install)
    deleted: list[Path] = []

    apply_optional(install, plan(install), delete_tree=lambda path: deleted.append(path))

    assert deleted == []


def test_apply_optional_removes_every_saved_key(tmp_path: Path) -> None:
    install = _install(tmp_path)
    store = FakeStore(["openai", "anthropic"])

    removed = apply_optional(
        install,
        plan(install, Options(remove_keys=True)),
        delete_keys=lambda names: uninstall.remove_saved_keys(names, store),
        aliases=store.aliases,
    )

    assert removed == ["2 keys"]
    assert store.removed == ["openai", "anthropic"]


def test_apply_optional_refuses_a_target_outside_the_install_roots(tmp_path: Path) -> None:
    install = _install(tmp_path)
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    tampered = Plan((Step(KIND_TREE, str(outside), "wrong", OWNER_PYTHON),))

    with pytest.raises(UninstallError):
        apply_optional(install, tampered)

    assert outside.exists()


def test_apply_optional_needs_a_key_remover(tmp_path: Path) -> None:
    install = _install(tmp_path)

    with pytest.raises(UninstallError):
        apply_optional(install, plan(install, Options(remove_keys=True)))


def test_a_broken_keyring_reports_no_aliases() -> None:
    assert uninstall.saved_aliases(FakeStore(["openai"], broken=True)) == []
    assert uninstall.saved_aliases(FakeStore(["openai"])) == ["openai"]


def test_remove_saved_keys_counts_the_successes() -> None:
    store = FakeStore(["openai", "anthropic"])

    gone, error = uninstall.remove_saved_keys(["openai", "missing"], store)

    assert (gone, error) == (1, None)
    assert store.removed == ["openai", "missing"]


def test_main_only_plans_without_yes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    install = _install(tmp_path)
    _populate(install)
    store = FakeStore(["openai"])

    assert uninstall.main(["--remove-state", "--remove-keys"], install=install, store=store) == 0

    assert install.roaming.exists()
    assert store.removed == []
    assert "nothing removed" in capsys.readouterr().out


def test_main_applies_the_optional_steps_with_yes(tmp_path: Path) -> None:
    install = _install(tmp_path)
    _populate(install)
    store = FakeStore(["openai", "anthropic"])

    code = uninstall.main(
        ["--yes", "--remove-state", "--remove-keys"], install=install, store=store
    )

    assert code == 0
    assert not install.roaming.exists()
    assert install.local.exists()
    assert store.removed == ["openai", "anthropic"]


def test_main_without_the_optional_flags_removes_nothing(tmp_path: Path) -> None:
    install = _install(tmp_path)
    _populate(install)

    assert uninstall.main(["--yes"], install=install) == 0

    assert install.runtime.exists()
    assert install.roaming.exists()
    assert (install.local / "headwaters.db").exists()
