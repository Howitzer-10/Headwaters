"""Ollama cloud-proxied ("remote") models are never local (HDW-121, D13, A-OLL-9).

The local Ollama server forwards models such as ``gpt-oss:120b-cloud`` to ollama.com, so a
loopback host does not prove a call stays on the machine. A model is remote when ``/api/tags``
or ``/api/show`` carries a non-empty ``remote_model`` / ``remote_host``, or when its name matches
a catalog ``ollama_remote_patterns`` regex (data, not code). Headwaters never routes to one.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from headwaters.core.catalog.schema import Catalog

__all__ = [
    "REMOTE_FIELDS",
    "RemotePatterns",
    "compile_remote_patterns",
    "has_remote_fields",
    "is_remote_name",
    "remote_patterns_from",
]

REMOTE_FIELDS = ("remote_model", "remote_host")
RemotePatterns = tuple[re.Pattern[str], ...]


def compile_remote_patterns(patterns: Iterable[str]) -> RemotePatterns:
    """Compile catalog regexes (already validated by the catalog schema)."""
    return tuple(re.compile(p) for p in patterns)


def remote_patterns_from(catalog: Catalog) -> RemotePatterns:
    return compile_remote_patterns(p.regex for p in catalog.ollama_remote_patterns)


def has_remote_fields(entry: Mapping[str, Any]) -> bool:
    """True when an ``/api/tags`` item or ``/api/show`` reply names a remote model or host."""
    return any(isinstance(entry.get(key), str) and bool(entry.get(key)) for key in REMOTE_FIELDS)


def is_remote_name(name: str, patterns: RemotePatterns) -> bool:
    """True when ``name`` matches any remote-model pattern."""
    return any(p.search(name) for p in patterns)
