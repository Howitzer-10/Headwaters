"""What the front-door tools need from the daemon (HDW-204).

The daemon builds one ``ToolContext`` and hands it to ``build_endpoint``. The engine is read per
call (``engine()``), because the daemon swaps in a new one when the catalog changes, and the rules
are read per call so a Private lock set after start is seen at once (HDW-131).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from headwaters.core.engine import Engine
from headwaters.core.rules import Rules
from headwaters.mcp.tools.schemas import FileNote
from headwaters.mcp.tools.store import ResultStore

__all__ = ["ToolContext", "ToolFailure", "ToolSettings"]


@dataclass(frozen=True)
class ToolSettings:
    """First-draft bounds (see docs/ASSUMPTIONS.md). None of them is a model, a price or a
    provider limit."""

    pack_budget_tokens: int = 16_000  # files packed into one request
    answer_max_chars: int = 6_000  # the answer kept inline; the rest is at result_uri
    max_paths: int = 100
    max_images: int = 4
    max_image_bytes: int = 8 * 1024 * 1024
    max_check_chars: int = 1_000_000


@dataclass
class ToolContext:
    engine: Callable[[], Engine | None]
    rules: Callable[[], Rules]
    store: ResultStore = field(default_factory=ResultStore)
    settings: ToolSettings = field(default_factory=ToolSettings)
    # The configured workspace root(s). No rules key names them yet (FU-203-2), so the daemon
    # passes none and the shim's working directory is the boundary.
    configured_roots: Callable[[], Sequence[str]] = field(default=lambda: ())


class ToolFailure(Exception):
    """A request the tool refuses or cannot serve. ``code`` is stable and safe to show."""

    def __init__(
        self,
        code: str,
        message: str,
        fix: str = "",
        *,
        suggestions: Sequence[tuple[str, str]] = (),
        files: Sequence[FileNote] = (),
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.fix = fix
        self.suggestions = tuple(suggestions)
        self.files = tuple(files)
