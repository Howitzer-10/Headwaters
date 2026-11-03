"""Alias table and explicit-model semantics shared by both gateway faces (HDW-304, v3 §5.1).

An IDE sends whatever model names its own picker lists. ``rules.yaml`` maps the ones the user
cares about (``models.aliases``); a name with no alias that is not a picker model is a request
for that exact model, so it passes through as an explicit pick. The hard filter, the scope and
the walls still apply to it, and a failed explicit model fails over to the next allowed model --
unless the client sends ``x-headwaters-pin: true``, which asks for that model or nothing.

Both faces call ``requested`` so the alias table and the header mean the same thing on
``/v1/chat/completions`` and ``/v1/messages``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from headwaters.core.rules import Rules
from headwaters.core.scope import ParsedModel, parse_model_name

__all__ = ["PIN_HEADER", "Requested", "requested", "resolve_alias"]

PIN_HEADER = "x-headwaters-pin"
_TRUE = frozenset({"1", "true", "yes"})


@dataclass(frozen=True)
class Requested:
    """What one client asked for, after the alias table and the picker syntax.

    ``pin`` is the explicit model to pass through (``None`` for a picker model); ``strict`` is
    true only when the client also pinned it, which turns failover off.
    """

    model: str
    parsed: ParsedModel | None
    pin: str | None
    strict: bool


def requested(model: str, headers: Mapping[str, str], rules: Rules) -> Requested:
    """Resolve ``model``, then read the picker syntax. Raises ``ScopeError`` for a bad name."""
    target = resolve_alias(model, rules.models.aliases)
    parsed = parse_model_name(target)
    pin = None if parsed is not None else target
    # A picker model already names the route the user asked for, so the header is ignored there
    # rather than refused: an IDE may set it globally.
    return Requested(target, parsed, pin, pin is not None and _wants_pin(headers))


def resolve_alias(model: str, aliases: Mapping[str, str]) -> str:
    """The alias target, else ``model`` unchanged. Exact match first, then case-insensitive."""
    text = model.strip()
    if text in aliases:
        return aliases[text].strip()
    folded = text.lower()
    for name, target in aliases.items():
        if name.lower() == folded:
            return target.strip()
    return text


def _wants_pin(headers: Mapping[str, str]) -> bool:
    value = next((v for k, v in headers.items() if k.lower() == PIN_HEADER), "")
    return value.strip().lower() in _TRUE
