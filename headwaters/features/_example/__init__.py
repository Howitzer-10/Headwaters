"""Dummy ★ feature used to prove registration and the contract harness. Not a real feature."""

from __future__ import annotations

from collections.abc import Mapping

from headwaters.core.features import FeatureRegistry, FeatureSpec
from headwaters.core.plugins import CURRENT_API_VERSION

FEATURE = FeatureSpec(
    id="_example",
    star=True,
    title="Example feature",
    interfaces=("VerifierPlugin", "LedgerPlugin"),
)


class ExampleVerifier:
    api_version = CURRENT_API_VERSION

    def verify(self, candidate: str) -> list[str]:
        return [] if candidate.strip() else ["empty answer"]


class ExampleLedger:
    api_version = CURRENT_API_VERSION

    def __init__(self) -> None:
        self.events: list[Mapping[str, object]] = []

    def record(self, event: Mapping[str, object]) -> None:
        self.events.append(dict(event))


def register(registry: FeatureRegistry) -> None:
    registry.register(
        FEATURE,
        {"VerifierPlugin": ExampleVerifier(), "LedgerPlugin": ExampleLedger()},
    )
