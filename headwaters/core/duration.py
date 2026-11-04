"""Duration text as Ollama's ``keep_alive`` takes it (``30m``, ``1h30m``, ``90``).

Its own module because both the rules validation (HDW-518) and the keep-warm tick need it, and
``rules`` may not import ``keepwarm`` (``keepwarm`` imports ``rules``).
"""

from __future__ import annotations

import re

__all__ = ["parse_duration_s"]

_UNIT_S = {"ns": 1e-9, "us": 1e-6, "µs": 1e-6, "ms": 1e-3, "s": 1.0, "m": 60.0, "h": 3600.0}
_PART = re.compile(r"(\d+(?:\.\d+)?)(ns|us|µs|ms|s|m|h)")


def parse_duration_s(text: str) -> float | None:
    """Seconds in an Ollama ``keep_alive`` value (``30m``, ``1h30m``, ``90``).

    ``None`` for a negative value (Ollama: keep loaded forever) and for text that is not a duration.
    """
    raw = text.strip().lower()
    if not raw:
        return None
    if raw.startswith("-"):
        return None
    try:
        return float(raw)  # a bare number is seconds
    except ValueError:
        pass
    total = 0.0
    position = 0
    for match in _PART.finditer(raw):
        if match.start() != position:
            return None
        total += float(match.group(1)) * _UNIT_S[match.group(2)]
        position = match.end()
    return total if position == len(raw) else None
