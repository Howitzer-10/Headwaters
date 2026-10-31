"""Secret detection for routing (API keys, tokens, private keys).

Detection patterns and placeholder patterns are catalog data (``secret_patterns``,
``secret_placeholders`` and ``placeholder_patterns``), never code. A match whose text also matches
a placeholder pattern (``sk-xxxx``, ``YOUR_API_KEY``) is ignored.

Results carry the pattern name and the character span only. The matched text is never stored,
returned or logged, so a ``SecretMatch`` is safe to put in decision metadata.

The action to take on a hit (``local`` | ``refuse`` | ``off``) is applied by ``core.scope``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from headwaters.core.catalog.schema import Catalog, NamedPattern


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
