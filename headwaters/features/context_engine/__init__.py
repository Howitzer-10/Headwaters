"""HDW-601: the local code index ★ - symbols, references and imports, incrementally.

The index is built and kept here; nothing in this package answers a request yet. That is the
point of the split: HDW-601 stores the facts, HDW-602 turns them into context through the
``ContextPlugin`` interface that ``pack_files`` already satisfies. Registering the feature now
gives the status page and ``headwaters doctor`` the row, and gives ``features: {context_engine:
false}`` something to switch off before it can cost anything.

Everything stays local: the database is a SQLite file under the cache dir, the parsing is
tree-sitter on this machine, and no code path here opens a socket. The workspace root is the only
boundary (D9) - enumeration never widens it, and a symlink out of the tree is skipped rather than
followed.
"""

from __future__ import annotations

from headwaters.core.features import FeatureRegistry, FeatureSpec

FEATURE_ID = "context_engine"
TITLE = "Local code index"
DEFAULT = True

FEATURE_SPEC = FeatureSpec(id=FEATURE_ID, star=True, title=TITLE, default_enabled=DEFAULT)


def register(registry: FeatureRegistry) -> None:
    """Announce the feature. The plugin interface is HDW-602's to implement."""
    registry.register(FEATURE_SPEC, {})


__all__ = ["DEFAULT", "FEATURE_ID", "FEATURE_SPEC", "TITLE", "register"]
