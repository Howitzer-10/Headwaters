"""Discovery of ★ features and entry points; core must not import them."""

from __future__ import annotations

import importlib.metadata
import subprocess
import sys
from pathlib import Path

import pytest

from headwaters.core import features
from headwaters.core.features import (
    FeatureRegistry,
    FeatureSpec,
    discover_entry_points,
    discover_features,
)
from headwaters.core.plugins import PluginContractError
from headwaters.features._example import ExampleLedger
from tests.contracts.harness import run_contract


def test_plugins_for_respects_flags() -> None:
    reg = FeatureRegistry()
    discover_features(reg)
    assert len(reg.plugins_for("VerifierPlugin")) == 1
    reg.set_flags({"_example": False})
    assert reg.plugins_for("VerifierPlugin") == []
    with pytest.raises(ValueError, match="unknown plugin interface"):
        reg.plugins_for("NopePlugin")


def test_example_found_by_discovery_and_passes_contracts() -> None:
    reg = FeatureRegistry()
    assert discover_features(reg) == ["headwaters.features._example"]
    spec = reg.get("_example")
    assert spec is not None
    assert spec.star
    assert reg.enabled("_example")  # every star feature is on by default in the beta
    for interface in spec.interfaces:
        for plugin in reg.plugins_for(interface):
            run_contract(interface, plugin)
    reg.clear()
    assert reg.all() == []


def test_ledger_example_records() -> None:
    ledger = ExampleLedger()
    run_contract("LedgerPlugin", ledger)
    assert ledger.events == [{"route": "local", "wall": None}]


def test_module_level_registry() -> None:
    reg = features.get_registry()
    reg.clear()
    try:
        features.register(FeatureSpec(id="m", star=False, title="M"))
        assert features.enabled("m")
    finally:
        reg.clear()


def test_discover_skips_packages_without_register(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pkg = tmp_path / "fakefeatures"
    (pkg / "plain").mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "plain" / "__init__.py").write_text("")
    (pkg / "module_only.py").write_text("")
    monkeypatch.syspath_prepend(str(tmp_path))
    assert discover_features(FeatureRegistry(), "fakefeatures") == []


def test_discover_non_package_returns_empty() -> None:
    assert discover_features(FeatureRegistry(), "headwaters.core.paths") == []


class _FakeEntryPoint:
    def __init__(self, name: str, target: object) -> None:
        self.name = name
        self._target = target

    def load(self) -> object:
        return self._target


def _hook(reg: FeatureRegistry) -> None:
    reg.register(FeatureSpec(id="ep", star=True, title="EP"))


class _ModuleLike:
    register = staticmethod(_hook)


def test_entry_point_callable_and_module(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        importlib.metadata, "entry_points", lambda group: [_FakeEntryPoint("a", _hook)]
    )
    reg = FeatureRegistry()
    assert discover_entry_points(reg) == ["a"]
    assert reg.get("ep") is not None

    monkeypatch.setattr(
        importlib.metadata, "entry_points", lambda group: [_FakeEntryPoint("m", _ModuleLike)]
    )
    assert discover_entry_points(FeatureRegistry()) == ["m"]


def test_entry_point_without_register_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        importlib.metadata, "entry_points", lambda group: [_FakeEntryPoint("b", object())]
    )
    with pytest.raises(PluginContractError, match="no register"):
        discover_entry_points(FeatureRegistry())


def test_core_does_not_import_features() -> None:
    """Importing the core registry must not pull in any headwaters.features module."""
    code = (
        "import sys, headwaters.core.features, headwaters.core.plugins;"
        "bad=[m for m in sys.modules if m.startswith('headwaters.features.')];"
        "sys.exit(1 if bad else 0)"
    )
    result = subprocess.run([sys.executable, "-c", code], check=False)
    assert result.returncode == 0
