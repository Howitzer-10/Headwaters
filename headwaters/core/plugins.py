"""Versioned plugin interfaces.

Every plugin carries an ``api_version`` attribute. Core checks it at registration time, so an
incompatible plugin is refused instead of failing later. Interfaces are ``runtime_checkable``
Protocols, so ``isinstance`` verifies that the required members exist (it does not check
signatures; the contract tests in ``tests/contracts`` exercise behaviour).

Interfaces are versioned together: bump ``CURRENT_API_VERSION`` only for a breaking change and
keep ``SUPPORTED_API_VERSIONS`` listing every version core still accepts.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol, runtime_checkable

CURRENT_API_VERSION = 1
SUPPORTED_API_VERSIONS: frozenset[int] = frozenset({CURRENT_API_VERSION})


@runtime_checkable
class SurfacePlugin(Protocol):
    """A user-facing surface (tray, status widget, wizard)."""

    api_version: int
    surface_id: str

    def start(self) -> None: ...

    def stop(self) -> None: ...


@runtime_checkable
class VerifierPlugin(Protocol):
    """Checks a candidate answer; returns a list of problems (empty means it passed)."""

    api_version: int

    def verify(self, candidate: str) -> list[str]: ...


@runtime_checkable
class ContextPlugin(Protocol):
    """Builds extra repo context for a request within a token budget."""

    api_version: int

    def build_context(self, query: str, budget_tokens: int) -> str: ...


@runtime_checkable
class BridgePlugin(Protocol):
    """Integration with a host application (installer or hook support)."""

    api_version: int
    host_id: str

    def supports(self, host_id: str) -> bool: ...


@runtime_checkable
class DashboardPlugin(Protocol):
    """Contributes a section to the dashboard / status page."""

    api_version: int

    def render_summary(self) -> dict[str, object]: ...


@runtime_checkable
class LedgerPlugin(Protocol):
    """Records decision metadata (never prompts, outputs or keys)."""

    api_version: int

    def record(self, event: Mapping[str, object]) -> None: ...


@runtime_checkable
class ClassifierLayerPlugin(Protocol):
    """An extra classifier layer; returns a score per label."""

    api_version: int

    def classify(self, features: Mapping[str, float]) -> dict[str, float]: ...


@runtime_checkable
class EscalationPlugin(Protocol):
    """Decides whether a failed attempt should escalate to a stronger candidate."""

    api_version: int

    def should_escalate(self, attempt: int, problems: Sequence[str]) -> bool: ...


INTERFACES: dict[str, type] = {
    "SurfacePlugin": SurfacePlugin,
    "VerifierPlugin": VerifierPlugin,
    "ContextPlugin": ContextPlugin,
    "BridgePlugin": BridgePlugin,
    "DashboardPlugin": DashboardPlugin,
    "LedgerPlugin": LedgerPlugin,
    "ClassifierLayerPlugin": ClassifierLayerPlugin,
    "EscalationPlugin": EscalationPlugin,
}


class PluginContractError(ValueError):
    """A plugin does not satisfy its declared interface."""


def check_plugin(interface: str, plugin: object) -> None:
    """Raise ``PluginContractError`` unless ``plugin`` satisfies ``interface`` at a supported
    API version."""
    protocol = INTERFACES.get(interface)
    if protocol is None:
        raise PluginContractError(f"unknown plugin interface: {interface!r}")
    if not isinstance(plugin, protocol):
        raise PluginContractError(f"{type(plugin).__name__} does not implement {interface}")
    version = getattr(plugin, "api_version", None)
    if not isinstance(version, int) or isinstance(version, bool):
        raise PluginContractError(f"{type(plugin).__name__}.api_version must be an int")
    if version not in SUPPORTED_API_VERSIONS:
        raise PluginContractError(
            f"{type(plugin).__name__} targets api_version {version}; "
            f"supported: {sorted(SUPPORTED_API_VERSIONS)}"
        )
