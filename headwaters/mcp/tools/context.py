"""What the front-door tools need from the daemon (HDW-204).

The daemon builds one ``ToolContext`` and hands it to ``build_endpoint``. The engine is read per
call (``engine()``), because the daemon swaps in a new one when the catalog changes, and the rules
are read per call so a Private lock set after start is seen at once (HDW-131).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from headwaters.core.engine import Engine
from headwaters.core.rules import Rules
from headwaters.core.rules_edit import set_setting
from headwaters.core.state import Database
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
    max_handoff_turns: int = 6  # recent turns kept verbatim in a handoff packet
    max_handoff_chars: int = 4_000  # per turn, goal and last error


@dataclass
class ToolContext:
    engine: Callable[[], Engine | None]
    rules: Callable[[], Rules]
    store: ResultStore = field(default_factory=ResultStore)
    settings: ToolSettings = field(default_factory=ToolSettings)
    # The configured workspace root(s). No rules key names them yet (FU-203-2), so the daemon
    # passes none and the shim's working directory is the boundary.
    configured_roots: Callable[[], Sequence[str]] = field(default=lambda: ())
    # The shared SQLite state (walls, jobs, handoffs, feedback). Without it the tools that need it
    # answer ``no_state``.
    db: Database | None = None
    # ``headwaters_scope`` writes rules.yaml through ``edit_rules`` (comments kept), then asks the
    # daemon to re-read it at once, so the next call and ``status`` see the change.
    edit_rules: Callable[[str, bool | str], tuple[Any, Any]] = field(default=set_setting)
    reload_rules: Callable[[], object] = field(default=lambda: None)
    # Advanced tools enabled per host (``headwaters_more``); in memory, one set per daemon.
    enabled_hosts: set[str] = field(default_factory=set)

    def state(self) -> Database:
        if self.db is None:
            raise ToolFailure(
                "no_state", "Headwaters has no state database here.", "Run `headwaters doctor`."
            )
        return self.db


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
