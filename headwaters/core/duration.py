"""Duration text as Ollama's ``keep_alive`` takes it (``30m``, ``1h30m``, ``120s``).

Its own module because the rules validation (HDW-518), the keep-warm tick and the executor payload
all need it, and ``rules`` may not import ``keepwarm`` (``keepwarm`` imports ``rules``).
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

__all__ = ["normalize_keep_alive", "parse_duration_s"]

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


def normalize_keep_alive(text: str) -> str:
    """``text`` as a Go duration string, so Ollama never gets a quoted bare number (HDW-522).

    Ollama takes a duration string (``"30m"``) or an unquoted number of seconds (``120``), and
    rejects a quoted bare number with HTTP 400 (``time: missing unit in duration "120"``). A bare
    number is therefore rewritten to ``"120s"``; anything with a unit, or text that is not a
    number, is returned unchanged so existing values keep their exact spelling.
    """
    compact = text.strip()
    try:
        seconds = Decimal(compact)
    except InvalidOperation:
        return text
    if not seconds.is_finite() or len(compact) > 32:
        return text
    return f"{seconds:f}s"
