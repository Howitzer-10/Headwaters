"""The virtual models ``GET /v1/models`` lists (HDW-301, v3.3 §B).

An id here is also a real name for ``core.scope.parse_model_name``; the rest is display metadata
for the model picker of an IDE. The two extras are listed only when ``picker.show_extras`` is on.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from headwaters.core.rules import Scope

__all__ = ["CORE", "EXTRAS", "VirtualModel", "card", "find", "listed"]

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


def card(model: VirtualModel) -> dict[str, Any]:
    """One ``GET /v1/models`` entry: the OpenAI fields plus the picker's label and blurb."""
    return {
        "id": model.id,
        "object": "model",
        "created": CREATED,
        "owned_by": OWNER,
        "name": model.name,
        "description": model.description,
    }
