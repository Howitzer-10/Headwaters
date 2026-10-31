"""Feature registry, plugin interfaces and discovery."""

from __future__ import annotations

import pytest

from headwaters.core.features import STAR_LEGEND, FeatureRegistry, FeatureSpec
from headwaters.core.plugins import INTERFACES, PluginContractError, check_plugin
from headwaters.features._example import ExampleVerifier
from tests.contracts.harness import BEHAVIOUR


class _BadVersion:
    api_version = 999

    def verify(self, candidate: str) -> list[str]:
        return []


class _StrVersion:
    api_version = "1"

    def verify(self, candidate: str) -> list[str]:
        return []


def test_legend_text() -> None:
    assert STAR_LEGEND == "★ = free during beta; may become part of Headwaters Pro at 1.0"


def test_every_interface_has_a_contract_behaviour() -> None:
    assert set(INTERFACES) == set(BEHAVIOUR)


def test_spec_validation() -> None:
    with pytest.raises(ValueError, match="empty"):
        FeatureSpec(id="", star=False, title="x")
    with pytest.raises(ValueError, match="unknown interfaces"):
        FeatureSpec(id="a", star=False, title="x", interfaces=("NopePlugin",))
    assert FeatureSpec(id="a", star=True, title="Alpha").label == "★ Alpha"
    assert FeatureSpec(id="b", star=False, title="Beta").label == "Beta"


def test_enabled_defaults_and_flags() -> None:
    reg = FeatureRegistry()
    reg.register(FeatureSpec(id="on", star=True, title="On"))
    reg.register(FeatureSpec(id="off", star=True, title="Off", default_enabled=False))
    assert reg.enabled("on") is True
    assert reg.enabled("off") is False
    assert reg.enabled("missing") is False
    reg.set_flags({"on": False, "off": True})
    assert reg.enabled("on") is False
    assert reg.enabled("off") is True


def test_duplicate_registration_rejected() -> None:
    reg = FeatureRegistry()
    spec = FeatureSpec(id="a", star=False, title="A")
    reg.register(spec)
    with pytest.raises(ValueError, match="already registered"):
        reg.register(spec)


def test_undeclared_interface_rejected() -> None:
    reg = FeatureRegistry()
    spec = FeatureSpec(id="a", star=True, title="A", interfaces=("LedgerPlugin",))
    with pytest.raises(PluginContractError, match="does not declare"):
        reg.register(spec, {"VerifierPlugin": ExampleVerifier()})


def test_incompatible_plugins_rejected() -> None:
    with pytest.raises(PluginContractError, match="unknown plugin interface"):
        check_plugin("NopePlugin", object())
    with pytest.raises(PluginContractError, match="does not implement"):
        check_plugin("VerifierPlugin", object())
    with pytest.raises(PluginContractError, match="api_version 999"):
        check_plugin("VerifierPlugin", _BadVersion())
    with pytest.raises(PluginContractError, match="must be an int"):
        check_plugin("VerifierPlugin", _StrVersion())
