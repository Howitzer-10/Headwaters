"""Run the contract harness against every plugin of the fixture features."""

from __future__ import annotations

import pytest

from headwaters.core.features import FeatureRegistry, discover_features
from tests.contracts.harness import run_contract

_registry = FeatureRegistry()
discover_features(_registry, "tests.fixtures.features", include_private=True)
_CASES = [
    (iface, plugin)
    for spec in _registry.all()
    for iface in spec.interfaces
    for plugin in _registry.plugins_for(iface)
]


@pytest.mark.parametrize(("interface", "plugin"), _CASES)
def test_registered_plugin_meets_contract(interface: str, plugin: object) -> None:
    run_contract(interface, plugin)
