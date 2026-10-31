"""Wall classifier (HDW-110, v3 section 4.4): turn a failed provider response into a ``WallEvent``.

``classify(status, headers, body, provider)`` decides which kind of wall a response is
(``rate_limit``, ``exhausted``, ``overloaded``, ``context`` or ``other``), how long to wait
(``retry_after`` seconds / ``reset_at`` epoch seconds) and how far it reaches (``scope``:
``model``, ``provider`` or ``key``).

Status codes, body regexes, kinds and scopes are DATA from the catalog (``wall_patterns``), never
code. Only the wire format of the reset hints (``Retry-After``, ``x-ratelimit-reset*`` and friends,
Gemini ``RetryInfo``) is parsed here.

Privacy: the body is only matched, never stored or logged; the event carries no body text.
"""

from __future__ import annotations

import json
import math
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

from headwaters.core.catalog.schema import Catalog, WallKind, WallPattern, WallScope

ANY_PROVIDER = "*"
OTHER: WallKind = "other"
_DEFAULT_SCOPE: WallScope = "model"
_BODY_SCAN_CHARS = 32_768  # bodies are matched on their first 32K characters only
_EPOCH_MS_MIN = 1e11  # bare numbers above this are epoch milliseconds
_EPOCH_S_MIN = 1e9  # bare numbers above this (and below the ms limit) are epoch seconds
_DURATION_PART = re.compile(r"(\d+(?:\.\d+)?)(ms|h|m|s)")
_DURATION_UNITS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}

__all__ = ["ANY_PROVIDER", "WallClassifier", "WallEvent", "classify", "parse_wait"]


@dataclass(frozen=True, slots=True)
class WallEvent:
    """Result of classifying one response. ``kind == "other"`` means: not a wall."""

    kind: WallKind
    scope: WallScope
    retry_after: float | None = None  # seconds to wait from ``now``
    reset_at: float | None = None  # epoch seconds when the wall is expected to lift
    status: int | None = None
    provider: str = ""
    pattern_index: int | None = None  # index into the catalog's wall_patterns, None if no match

    @property
    def is_wall(self) -> bool:
        """True for kinds that mark a wall; ``context`` re-routes and ``other`` surfaces."""
        return self.kind in ("rate_limit", "exhausted", "overloaded")


def _to_text(body: object) -> str:
    if body is None:
        return ""
    if isinstance(body, bytes | bytearray):
        text = bytes(body[: _BODY_SCAN_CHARS * 4]).decode("utf-8", errors="replace")
    elif isinstance(body, str):
        text = body
    else:
        try:
            text = json.dumps(body, ensure_ascii=False)
        except (TypeError, ValueError):
            text = str(body)
    return text[:_BODY_SCAN_CHARS]


def _parse_duration(value: str) -> float | None:
    """``"30"``, ``"1.5s"``, ``"6m0s"``, ``"20ms"`` -> seconds (None if not a duration)."""
    text = value.strip().lower()
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        pass
    else:
        return number if math.isfinite(number) and number >= 0 else None
    total = 0.0
    position = 0
    for match in _DURATION_PART.finditer(text):
        if match.start() != position:
            return None
        total += float(match.group(1)) * _DURATION_UNITS[match.group(2)]
        position = match.end()
    return total if position == len(text) else None


def _parse_timestamp(value: str) -> float | None:
    """RFC 3339 / ISO 8601 -> epoch seconds."""
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


def parse_wait(value: str, now: float, *, reset_header: bool = False) -> float | None:
    """Parse one wait hint into an absolute epoch time.

    Accepts a number of seconds, a Go-style duration, an RFC 3339 timestamp or (for
    ``Retry-After``) an HTTP date. With ``reset_header=True`` a bare number may also be an epoch
    in seconds or milliseconds (OpenRouter sends epoch ms), decided by magnitude.
    """
    text = value.strip()
    if not text:
        return None
    duration = _parse_duration(text)
    if duration is not None:
        if reset_header and duration >= _EPOCH_MS_MIN:
            return duration / 1000.0
        if reset_header and duration >= _EPOCH_S_MIN:
            return duration
        return now + duration
    stamp = _parse_timestamp(text)
    if stamp is not None:
        return stamp
    try:
        return parsedate_to_datetime(text).timestamp()
    except (TypeError, ValueError, IndexError):
        return None


def _lowered(headers: Mapping[str, str] | None) -> dict[str, str]:
    if not headers:
        return {}
    return {str(k).lower(): str(v) for k, v in headers.items()}


def _header_reset(headers: dict[str, str], now: float) -> float | None:
    """Absolute reset time from headers, or None.

    ``Retry-After`` (and ``retry-after-ms``) win because they say when to retry. Otherwise the
    latest of the ``*ratelimit*reset*`` headers is used: several limits (requests, tokens) can
    reset at different times and we do not know which one was hit, so waiting for the last one is
    the safe reading (a long wait only makes the router cascade sooner).
    """
    retry_after = headers.get("retry-after")
    if retry_after is not None:
        parsed = parse_wait(retry_after, now)
        if parsed is not None:
            return parsed
    retry_ms = headers.get("retry-after-ms")
    if retry_ms is not None:
        millis = _parse_duration(retry_ms)
        if millis is not None:
            return now + millis / 1000.0
    candidates = [
        parsed
        for name, value in headers.items()
        if "ratelimit" in name and "reset" in name
        for parsed in [parse_wait(value, now, reset_header=True)]
        if parsed is not None
    ]
    return max(candidates) if candidates else None


def _find_retry_delay(node: Any) -> str | None:
    """Gemini ``google.rpc.RetryInfo``: ``error.details[].retryDelay`` (e.g. ``"30s"``)."""
    if isinstance(node, dict):
        delay = node.get("retryDelay")
        if isinstance(delay, str):
            return delay
        for value in node.values():
            found = _find_retry_delay(value)
            if found is not None:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _find_retry_delay(item)
            if found is not None:
                return found
    return None


def _body_reset(text: str, now: float) -> float | None:
    if "retryDelay" not in text:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    delay = _find_retry_delay(data)
    return None if delay is None else parse_wait(delay, now)


class WallClassifier:
    """Classify responses against a list of catalog ``WallPattern``s.

    A pattern matches when its provider is the response's provider (case-insensitive) or ``*``,
    its ``status_codes`` (if any) include the status, and its ``body_regex`` (if any) is found in
    the body. When several match, patterns with a body regex beat status-only ones, then
    provider-specific beats ``*``, then catalog order decides.
    """

    def __init__(self, patterns: Sequence[WallPattern]) -> None:
        self._patterns = list(patterns)
        self._regex = [
            re.compile(p.body_regex) if p.body_regex is not None else None for p in self._patterns
        ]

    def _best(self, status: int, provider: str, text: str) -> int | None:
        best: tuple[int, int, int] | None = None
        best_index: int | None = None
        wanted = provider.lower()
        for index, pattern in enumerate(self._patterns):
            name = pattern.provider.lower()
            if name not in (wanted, ANY_PROVIDER):
                continue
            if pattern.status_codes and status not in pattern.status_codes:
                continue
            regex = self._regex[index]
            if regex is not None and regex.search(text) is None:
                continue
            rank = (int(regex is not None), int(name != ANY_PROVIDER), -index)
            if best is None or rank > best:
                best, best_index = rank, index
        return best_index

    def classify(
        self,
        status: int,
        headers: Mapping[str, str] | None,
        body: object,
        provider: str,
        *,
        now: float | None = None,
    ) -> WallEvent:
        """Classify one response. ``now`` is injectable for tests (epoch seconds)."""
        moment = time.time() if now is None else now
        text = _to_text(body)
        index = self._best(status, provider, text)
        if index is None:
            return WallEvent(kind=OTHER, scope=_DEFAULT_SCOPE, status=status, provider=provider)
        pattern = self._patterns[index]
        reset_at: float | None = None
        if pattern.kind in ("rate_limit", "exhausted", "overloaded"):
            reset_at = _header_reset(_lowered(headers), moment)
            if reset_at is None:
                reset_at = _body_reset(text, moment)
        retry_after = None if reset_at is None else max(0.0, reset_at - moment)
        return WallEvent(
            kind=pattern.kind,
            scope=pattern.scope,
            retry_after=retry_after,
            reset_at=reset_at,
            status=status,
            provider=provider,
            pattern_index=index,
        )


def classify(
    status: int,
    headers: Mapping[str, str] | None,
    body: object,
    provider: str,
    *,
    catalog: Catalog | None = None,
    patterns: Sequence[WallPattern] | None = None,
    now: float | None = None,
) -> WallEvent:
    """``classify(status, headers, body, provider) -> WallEvent`` using catalog wall patterns.

    Give either ``catalog`` or ``patterns``. For many calls build one ``WallClassifier`` instead.
    """
    if patterns is None:
        if catalog is None:
            raise ValueError("classify needs a catalog or patterns")
        patterns = catalog.wall_patterns
    return WallClassifier(patterns).classify(status, headers, body, provider, now=now)
