"""The virtual models ``GET /v1/models`` lists (HDW-301, v3.3 §B).

An id here is also a real name for ``core.scope.parse_model_name``; the rest is display metadata
for the model picker of an IDE. The two extras are listed only when ``picker.show_extras`` is on.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from headwaters.core.catalog.schema import Catalog, ModelEntry
from headwaters.core.rules import Rules, Scope
from headwaters.core.scope import SCOPE_TIERS, TierName

__all__ = ["CORE", "EXTRAS", "Declared", "VirtualModel", "card", "declared", "find", "listed"]

CREATED = 0  # a virtual model has no creation time; OpenAI clients only pass it along
OWNER = "headwaters"


@dataclass(frozen=True)
class VirtualModel:
    id: str
    name: str
    description: str
    scope: Scope
    extras: bool = False


CORE: tuple[VirtualModel, ...] = (
    VirtualModel(
        "headwaters/auto",
        "Headwaters Auto",
        "Your own keys, free cloud and local: the best model that can serve this request.",
        "auto",
    ),
    VirtualModel(
        "headwaters/free",
        "Headwaters Free ($0)",
        "Free cloud and local models only; never spends money.",
        "free",
    ),
    VirtualModel(
        "headwaters/local",
        "Headwaters Local (Private)",
        "Ollama only; the request never leaves this machine.",
        "local",
    ),
    VirtualModel(
        "headwaters/cloud",
        "Headwaters Cloud",
        "Paid and free cloud models; local models are skipped.",
        "cloud",
    ),
)

EXTRAS: tuple[VirtualModel, ...] = (
    VirtualModel(
        "headwaters/quality",
        "Headwaters Quality First",
        "Like auto, but the best allowed model leads instead of the cheapest that clears it.",
        "auto",
        True,
    ),
    VirtualModel(
        "headwaters/duck",
        "Headwaters Duck",
        "Rubber-duck mode: the cheapest fast model that can help you think.",
        "auto",
        True,
    ),
)


def listed(show_extras: bool) -> tuple[VirtualModel, ...]:
    """The picker entries the config allows, core models first."""
    return CORE + EXTRAS if show_extras else CORE


def find(model_id: str, show_extras: bool) -> VirtualModel | None:
    return next((model for model in listed(show_extras) if model.id == model_id), None)


def card(model: VirtualModel, floor: Declared) -> dict[str, Any]:
    """One ``GET /v1/models`` entry: the OpenAI fields plus the picker's label and blurb.

    ``capabilities`` is the declared floor from ``declared`` (HDW-506), never a per-model claim.
    """
    return {
        "id": model.id,
        "object": "model",
        "created": CREATED,
        "owned_by": OWNER,
        "name": model.name,
        "description": model.description,
        "capabilities": floor.as_dict(),
    }


# --- declared capabilities (HDW-506) -------------------------------------------------------
#
# A virtual model is a routing alias: which real model answers changes per request, so the alias
# has no true context window, tool support or vision support of its own. The IDE's model picker
# still needs numbers, so this declares a floor instead of a fact: the smallest window, and the
# capabilities that hold for *every* eligible catalog entry, over the entries the alias's scope
# can route to. Every number comes from the catalog and the live rules; nothing is a per-model
# guess. ``basis`` says which of the two computations produced it.

_TIER_OF_HINT: dict[str, TierName] = {"paid": "paid", "free": "free_cloud", "local": "local"}
BASIS_CATALOG = "catalog_floor"  # at least one eligible catalog entry
BASIS_UNKNOWN = "unknown_defaults"  # no eligible entry: the catalog's own unknown-model values


@dataclass(frozen=True)
class Declared:
    """A conservative, data-derived floor for one routing alias (see ``declared``)."""

    max_input_tokens: int
    max_output_tokens: int
    image_input: bool
    tool_calling: bool
    basis: str
    entries: int  # how many catalog entries the floor was taken over; 0 with BASIS_UNKNOWN

    def as_dict(self) -> dict[str, Any]:
        return {
            "max_input_tokens": self.max_input_tokens,
            "max_output_tokens": self.max_output_tokens,
            "image_input": self.image_input,
            "tool_calling": self.tool_calling,
            "basis": self.basis,
            "entries": self.entries,
        }


def _eligible(scope: Scope, catalog: Catalog) -> list[ModelEntry]:
    """The catalog entries ``scope`` can route to: at least one tier hint inside the scope.

    An entry with no tier hint is left out: unknown tier is not evidence of eligibility. The
    paid-scopes ★ feature only ever removes entries here, so ignoring it can only lower the floor.
    """
    tiers = SCOPE_TIERS[scope]
    return [
        entry
        for entry in catalog.models
        if any(_TIER_OF_HINT.get(hint) in tiers for hint in entry.tier_hints)
    ]


def _ctx(entry: ModelEntry, catalog: Catalog) -> int:
    return entry.ctx if entry.ctx is not None else catalog.unknown_model_defaults.ctx


def _tools(entry: ModelEntry, catalog: Catalog) -> bool:
    return entry.tools_ok if entry.tools_ok is not None else catalog.unknown_model_defaults.tools_ok


def declared(scope: Scope, catalog: Catalog, rules: Rules) -> Declared:
    """The declared floor for an alias whose scope is ``scope`` (HDW-506).

    ``max_input_tokens`` is the smallest eligible context divided by ``scoring.ctx_headroom``,
    the same headroom the policy engine demands before it will route a request to a model.
    ``max_output_tokens`` is ``scoring.assumed_output_tokens``, capped to what the smallest
    eligible window still has room for. Both are capped at 1 or more because the IDE rejects
    anything smaller.
    """
    eligible = _eligible(scope, catalog)
    floor_ctx = min((_ctx(entry, catalog) for entry in eligible), default=None)
    if floor_ctx is None:
        floor_ctx = catalog.unknown_model_defaults.ctx

    headroom = max(1.0, rules.scoring.ctx_headroom)
    max_input = max(1, min(int(floor_ctx / headroom), floor_ctx - 1))
    room = max(1, floor_ctx - max_input)
    max_output = max(1, min(rules.scoring.assumed_output_tokens, room))

    return Declared(
        max_input_tokens=max_input,
        max_output_tokens=max_output,
        image_input=bool(eligible) and all("image" in entry.modalities for entry in eligible),
        tool_calling=bool(eligible) and all(_tools(entry, catalog) for entry in eligible),
        basis=BASIS_CATALOG if eligible else BASIS_UNKNOWN,
        entries=len(eligible),
    )
