"""Feature registry.

Features (core and ★) describe themselves with a ``FeatureSpec`` and register plugin
implementations here. Core never imports ``headwaters.features.*``: ★ packages are found at
runtime by name (``discover_features``) or through Python entry points (``headwaters.plugins``).

A ★ feature package exposes ``register(registry)`` which calls ``registry.register(spec, ...)``.

Runtime flags (the ``features:`` map from rules) are for user choice, never licensing. During the
beta every feature is on unless the user switches it off.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import pkgutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from headwaters.core.plugins import INTERFACES, PluginContractError, check_plugin

STAR_LEGEND = "★ = free during beta; may become part of Headwaters Pro at 1.0"
FEATURES_PACKAGE = "headwaters.features"
ENTRY_POINT_GROUP = "headwaters.plugins"


@dataclass(frozen=True)
class FeatureSpec:
    id: str
    star: bool
    title: str
    interfaces: tuple[str, ...] = ()
    default_enabled: bool = True

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("FeatureSpec.id must not be empty")
        unknown = [name for name in self.interfaces if name not in INTERFACES]
        if unknown:
            raise ValueError(f"FeatureSpec {self.id!r} lists unknown interfaces: {unknown}")

    @property
    def label(self) -> str:
        """Title with the ★ marker for display."""
        return f"★ {self.title}" if self.star else self.title


class FeatureRegistry:
    def __init__(self, flags: Mapping[str, bool] | None = None) -> None:
        self._specs: dict[str, FeatureSpec] = {}
        self._plugins: dict[str, list[tuple[str, object]]] = {}
        self._flags: dict[str, bool] = dict(flags or {})

    def register(self, spec: FeatureSpec, plugins: Mapping[str, object] | None = None) -> None:
        """Register a feature and the plugin instance it provides for each interface.

        ``plugins`` maps interface name to implementation; every key must be listed in
        ``spec.interfaces`` and the implementation must pass the contract check.
        """
        if spec.id in self._specs:
            raise ValueError(f"feature already registered: {spec.id!r}")
        provided = dict(plugins or {})
        for interface, plugin in provided.items():
            if interface not in spec.interfaces:
                raise PluginContractError(
                    f"feature {spec.id!r} provides {interface} but does not declare it"
                )
            check_plugin(interface, plugin)
        self._specs[spec.id] = spec
        self._plugins[spec.id] = list(provided.items())

    def set_flags(self, flags: Mapping[str, bool]) -> None:
        """Replace the runtime flags (the ``features:`` map from rules)."""
        self._flags = {str(k): bool(v) for k, v in flags.items()}

    def enabled(self, feature_id: str) -> bool:
        """True when the feature is registered and on. An explicit flag wins over the default."""
        spec = self._specs.get(feature_id)
        if spec is None:
            return False
        return self._flags.get(feature_id, spec.default_enabled)

    def get(self, feature_id: str) -> FeatureSpec | None:
        return self._specs.get(feature_id)

    def all(self) -> list[FeatureSpec]:
        return sorted(self._specs.values(), key=lambda s: s.id)

    def plugins_for(self, interface: str) -> list[object]:
        """Plugins implementing ``interface`` from enabled features, ordered by feature id."""
        if interface not in INTERFACES:
            raise ValueError(f"unknown plugin interface: {interface!r}")
        found: list[object] = []
        for spec in self.all():
            if not self.enabled(spec.id):
                continue
            found.extend(p for name, p in self._plugins[spec.id] if name == interface)
        return found

    def clear(self) -> None:
        self._specs.clear()
        self._plugins.clear()


def discover_features(registry: FeatureRegistry, package: str = FEATURES_PACKAGE) -> list[str]:
    """Import each subpackage of ``package`` by name and call its ``register(registry)``.

    Returns the module names that registered. Subpackages without ``register`` are skipped.
    Imports are dynamic on purpose: core has no static dependency on ★ code.
    """
    root = importlib.import_module(package)
    search_path = getattr(root, "__path__", None)
    if search_path is None:
        return []
    registered: list[str] = []
    for info in sorted(pkgutil.iter_modules(search_path), key=lambda m: m.name):
        if not info.ispkg:
            continue
        module = importlib.import_module(f"{package}.{info.name}")
        hook = getattr(module, "register", None)
        if callable(hook):
            hook(registry)
            registered.append(module.__name__)
    return registered


def discover_entry_points(registry: FeatureRegistry, group: str = ENTRY_POINT_GROUP) -> list[str]:
    """Load optional split-out plugins. Each entry point is a ``register`` callable (or a module
    exposing one)."""
    registered: list[str] = []
    for entry_point in sorted(importlib.metadata.entry_points(group=group), key=lambda e: e.name):
        loaded = entry_point.load()
        hook: Callable[[FeatureRegistry], None] | None = getattr(loaded, "register", None) or (
            loaded if callable(loaded) else None
        )
        if hook is None:
            raise PluginContractError(f"entry point {entry_point.name!r} has no register()")
        hook(registry)
        registered.append(entry_point.name)
    return registered


_default = FeatureRegistry()


def get_registry() -> FeatureRegistry:
    """The process-wide registry."""
    return _default


def register(spec: FeatureSpec, plugins: Mapping[str, object] | None = None) -> None:
    _default.register(spec, plugins)


def enabled(feature_id: str) -> bool:
    return _default.enabled(feature_id)
