"""The provider registry (HDW-514): cloud providers are catalog data, not code.

``catalog.providers`` lists every OpenAI-compatible cloud provider Headwaters can call with one
key: its env var, base URL, key-check URL and tier. The key store, ``headwaters keys test``,
discovery and the executor all read it through ``Registry``, so adding a provider is a catalog
entry plus a test, with no ``rules.yaml`` edit.

Nothing here talks to the network. A broken or missing catalog gives an empty registry: routing
to the built-in providers must never depend on it.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from headwaters.core.catalog.schema import Catalog, ProviderEntry

__all__ = ["Registry", "default_registry"]


class Registry:
    """Providers by name. Immutable after construction; validation lives in ``Catalog``."""

    def __init__(self, entries: Iterable[ProviderEntry] = ()) -> None:
        self._by_name = {entry.name: entry for entry in entries}

    @classmethod
    def from_catalog(cls, catalog: Catalog) -> Registry:
        return cls(catalog.providers)

    def get(self, name: str) -> ProviderEntry | None:
        return self._by_name.get(name)

    def env_var(self, name: str) -> str | None:
        entry = self._by_name.get(name)
        return None if entry is None else entry.env_var

    def __iter__(self) -> Iterator[ProviderEntry]:
        return iter(self._by_name.values())

    def __len__(self) -> int:
        return len(self._by_name)

    def __contains__(self, name: object) -> bool:
        return name in self._by_name


def default_registry() -> Registry:
    """The registry of the best catalog available offline (signed cache, else the seed)."""
    from headwaters.core.catalog import updater

    try:
        catalog, _source = updater.load_active()
    except Exception:  # an unreadable catalog must not break key handling or routing
        return Registry()
    return Registry.from_catalog(catalog)
