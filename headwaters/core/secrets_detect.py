"""Secret detection for routing (API keys, tokens, private keys).

Detection patterns and placeholder patterns are catalog data (``secret_patterns``,
``secret_placeholders`` and ``placeholder_patterns``), never code. A match whose text also matches
a placeholder pattern (``sk-xxxx``, ``YOUR_API_KEY``) is ignored.

Results carry the pattern name and the character span only. The matched text is never stored,
returned or logged, so a ``SecretMatch`` is safe to put in decision metadata.

The action to take on a hit (``local`` | ``refuse`` | ``off``) is applied by ``core.scope``.
"""

from __future__ import annotations

import base64
import binascii
import re
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from urllib.parse import unquote

from headwaters.core.catalog.schema import Catalog, NamedPattern
from headwaters.core.rules import SecretsRules


@dataclass(frozen=True)
class SecretMatch:
    """Where a secret-shaped string was found. Deliberately holds no text."""

    pattern: str
    start: int
    end: int


class SecretDetector:
    """Compiled catalog patterns. Build once per catalog, reuse for every request."""

    def __init__(
        self,
        secrets: Sequence[NamedPattern],
        placeholders: Sequence[NamedPattern] = (),
    ) -> None:
        self._secrets: list[tuple[str, re.Pattern[str]]] = [
            (p.name, re.compile(p.regex)) for p in secrets
        ]
        self._placeholders: list[re.Pattern[str]] = [re.compile(p.regex) for p in placeholders]

    @classmethod
    def from_catalog(cls, catalog: Catalog) -> SecretDetector:
        return cls(
            catalog.secret_patterns,
            [*catalog.secret_placeholders, *catalog.placeholder_patterns],
        )

    def _is_placeholder(self, matched: str) -> bool:
        return any(p.search(matched) for p in self._placeholders)

    def scan(self, text: str) -> list[SecretMatch]:
        """Every non-placeholder secret-shaped match, ordered by position."""
        found: list[SecretMatch] = []
        for name, pattern in self._secrets:
            for match in pattern.finditer(text):
                if match.end() == match.start():
                    continue
                if self._is_placeholder(match.group(0)):
                    continue
                found.append(SecretMatch(name, match.start(), match.end()))
        found.sort(key=lambda m: (m.start, m.end, m.pattern))
        return found

    def contains_secret(self, text: str) -> bool:
        return bool(self.scan(text))

    def scan_all(self, texts: Iterable[str]) -> list[SecretMatch]:
        """Scan several texts (e.g. every message). Spans are relative to their own text."""
        found: list[SecretMatch] = []
        for text in texts:
            found.extend(self.scan(text))
        return found

    def scan_groups(
        self, groups: Iterable[Sequence[str]], limits: ScanLimits | None = None
    ) -> list[SecretMatch]:
        """Scan each message's string leaves (HDW-142).

        Per group: every leaf alone; the leaves joined in order with no separator and with one
        space (only matches that cross a leaf boundary are added; spans may overlap a plain hit);
        and, one level deep, the percent-decoded and base64-decoded forms of each leaf. Spans of
        a decoded hit refer to the decoded text. Bounds come from ``limits`` (rules); once the
        time budget is spent only the plain per-leaf scan continues.
        """
        lim = limits or ScanLimits()
        deadline = time.monotonic() + lim.budget_ms / 1000.0
        found: list[SecretMatch] = []
        for group in groups:
            leaves = [t for t in group if t]
            for leaf in leaves:
                found.extend(self.scan(leaf))
            if lim.join and len(leaves) > 1 and time.monotonic() < deadline:
                for sep in ("", " "):
                    found.extend(self._scan_joined(leaves, sep, lim, deadline))
            if lim.decode:
                decoded_bytes = 0
                for leaf in leaves:
                    if time.monotonic() >= deadline or decoded_bytes > lim.max_decoded_bytes:
                        break
                    for text in _decoded_forms(leaf, lim):
                        decoded_bytes += len(text)
                        found.extend(self.scan(text))
        found.sort(key=lambda m: (m.start, m.end, m.pattern))
        return found

    def _scan_joined(
        self, leaves: Sequence[str], sep: str, lim: ScanLimits, deadline: float
    ) -> list[SecretMatch]:
        """Matches that cross a leaf boundary.

        For each boundary, up to ``join_span`` leaves on each side are joined (at most
        ``join_window_chars`` per side), so a neighbour touching the key does not hide it from
        the word-boundary anchors of the patterns. Only matches that cross the boundary count.
        """
        found: list[SecretMatch] = []
        for i in range(1, len(leaves)):
            if time.monotonic() >= deadline:
                break
            for back in range(1, min(lim.join_span, i) + 1):
                left = _tail(leaves[i - back : i], sep, lim.join_window_chars)
                for fwd in range(1, min(lim.join_span, len(leaves) - i) + 1):
                    right = _head(leaves[i : i + fwd], sep, lim.join_window_chars)
                    cut = len(left)
                    for m in self.scan(left + sep + right):
                        if m.start < cut < m.end and m not in found:
                            found.append(m)
        return found


@dataclass(frozen=True)
class ScanLimits:
    """Bounds for the joined and decoded scans; built from ``rules.secrets.scan_*``."""

    join: bool = True
    decode: bool = True
    join_window_chars: int = 2048
    join_span: int = 2
    base64_min_chars: int = 24
    base64_max_chars: int = 16384
    percent_max_chars: int = 262144
    max_candidates: int = 256
    max_decoded_bytes: int = 1048576
    budget_ms: int = 2000

    @classmethod
    def from_rules(cls, rules: SecretsRules) -> ScanLimits:
        return cls(
            join=rules.scan_join,
            decode=rules.scan_decode,
            join_window_chars=rules.scan_join_window_chars,
            join_span=rules.scan_join_span,
            base64_min_chars=rules.scan_base64_min_chars,
            base64_max_chars=rules.scan_base64_max_chars,
            percent_max_chars=rules.scan_percent_max_chars,
            max_candidates=rules.scan_max_candidates,
            max_decoded_bytes=rules.scan_max_decoded_bytes,
            budget_ms=rules.scan_budget_ms,
        )


def _tail(leaves: Sequence[str], sep: str, window: int) -> str:
    """Up to ``window`` chars at the end of the leaves joined by ``sep``."""
    parts: list[str] = []
    size = 0
    for leaf in reversed(leaves):
        parts.append(leaf[-(window - size) :] if size + len(leaf) > window else leaf)
        size += len(parts[-1]) + len(sep)
        if size >= window:
            break
    return sep.join(reversed(parts))


def _head(leaves: Sequence[str], sep: str, window: int) -> str:
    """Up to ``window`` chars at the start of the leaves joined by ``sep``."""
    parts: list[str] = []
    size = 0
    for leaf in leaves:
        parts.append(leaf[: window - size] if size + len(leaf) > window else leaf)
        size += len(parts[-1]) + len(sep)
        if size >= window:
            break
    return sep.join(parts)


_PERCENT = re.compile(r"%[0-9A-Fa-f]{2}")
_BASE64_RUN = re.compile(r"[A-Za-z0-9+/_-]{8,}={0,2}")


def _decoded_forms(leaf: str, lim: ScanLimits) -> list[str]:
    """Percent-decoded and base64-decoded texts of one leaf (one level; failures ignored)."""
    out: list[str] = []
    if len(leaf) <= lim.percent_max_chars and _PERCENT.search(leaf):
        decoded = unquote(leaf, errors="replace")
        if decoded != leaf:
            out.append(decoded)
    total = 0
    for count, run in enumerate(_BASE64_RUN.finditer(leaf)):
        if count >= lim.max_candidates:
            break
        blob = run.group(0).rstrip("=")
        if not lim.base64_min_chars <= len(blob) <= lim.base64_max_chars:
            continue
        total += len(blob) * 3 // 4
        if total > lim.max_decoded_bytes:
            break
        text = _b64_text(blob)
        if text:
            out.append(text)
    return out


def _b64_text(blob: str) -> str | None:
    normal = blob.replace("-", "+").replace("_", "/")
    if len(normal) % 4 == 1:
        return None
    normal += "=" * (-len(normal) % 4)
    try:
        return base64.b64decode(normal, validate=True).decode("utf-8")
    except (binascii.Error, ValueError):
        return None
