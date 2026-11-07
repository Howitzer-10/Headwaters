"""HDW-506: the declared capability floor on the ``/v1/models`` cards.

A Headwaters virtual model is a routing alias, so it has no true context window or capability of
its own. These tests pin the rule that keeps the cards honest: every number is the most
conservative value over the catalog entries the alias's scope can route to, and the card says
which computation produced it. Nothing here asserts a value for a named real model.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
from starlette.types import ASGIApp

from headwaters.core.catalog.loader import parse_catalog
from headwaters.core.catalog.schema import Catalog
from headwaters.core.rules import PickerRules, Rules, ScoringRules
from headwaters.gateway.models import BASIS_CATALOG, BASIS_UNKNOWN, declared
from headwaters.gateway.openai_routes import build_gateway_app
from tests.rebuild_support import TOKEN

BASE = "http://127.0.0.1:4000"


def catalog_of(*models: dict[str, Any], **defaults: Any) -> Catalog:
    """A tiny catalog: only the fields the floor reads, plus the unknown-model defaults."""
    body = {
        "schema_version": 1,
        "catalog_version": "test",
        "unknown_model_defaults": {"scores": {}, "tools_ok": False, "json_ok": False, "ctx": 8192}
        | defaults,
        "models": list(models),
    }
    return parse_catalog(json.dumps(body), Path("test-catalog.json"))


def entry(model_id: str, tier: str, **fields: Any) -> dict[str, Any]:
    return {"id": model_id, "provider": "test", "tier_hints": [tier]} | fields


def app_for(catalog: Catalog, rules: Rules | None = None) -> ASGIApp:
    live = rules if rules is not None else Rules()
    return build_gateway_app(
        token=TOKEN, router=lambda: None, rules=lambda: live, catalog=lambda: catalog
    )


async def cards(app: ASGIApp) -> list[dict[str, Any]]:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
        response = await client.get("/v1/models", headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 200
    return response.json()["data"]


def test_the_floor_is_the_smallest_eligible_window_less_the_headroom() -> None:
    catalog = catalog_of(
        entry("big", "paid", ctx=200_000, tools_ok=True, modalities=["text", "image"]),
        entry("small", "paid", ctx=32_000, tools_ok=True, modalities=["text", "image"]),
    )
    floor = declared("cloud", catalog, Rules())
    # 32_000 / 1.2 = 26_666.6, truncated: the headroom the policy engine itself demands.
    assert floor.max_input_tokens == 26_666
    assert floor.max_output_tokens == 1000  # rules.scoring.assumed_output_tokens
    assert floor.basis == BASIS_CATALOG
    assert floor.entries == 2


def test_entries_outside_the_scope_do_not_lower_the_floor() -> None:
    catalog = catalog_of(
        entry("cloud-only", "paid", ctx=200_000, tools_ok=True),
        entry("tiny-local", "local", ctx=2_048, tools_ok=False),
    )
    assert declared("cloud", catalog, Rules()).max_input_tokens == 166_666
    assert declared("local", catalog, Rules()).max_input_tokens == 1_706
    assert declared("local", catalog, Rules()).entries == 1


def test_capabilities_are_claimed_only_when_every_eligible_entry_has_them() -> None:
    mixed = catalog_of(
        entry("a", "free", ctx=64_000, tools_ok=True, modalities=["text", "image"]),
        entry("b", "free", ctx=64_000, tools_ok=False, modalities=["text"]),
    )
    floor = declared("free", mixed, Rules())
    assert (floor.tool_calling, floor.image_input) == (False, False)

    agreed = catalog_of(
        entry("a", "free", ctx=64_000, tools_ok=True, modalities=["text", "image"]),
        entry("b", "free", ctx=64_000, tools_ok=True, modalities=["text", "image"]),
    )
    floor = declared("free", agreed, Rules())
    assert (floor.tool_calling, floor.image_input) == (True, True)


def test_a_missing_field_falls_back_to_the_unknown_model_defaults() -> None:
    catalog = catalog_of(entry("vague", "local"))  # no ctx, no tools_ok, no modalities
    floor = declared("local", catalog, Rules())
    assert floor.max_input_tokens == 6_826  # unknown_model_defaults.ctx 8192 / 1.2
    assert (floor.tool_calling, floor.image_input) == (False, False)


def test_no_eligible_entry_declares_the_unknown_model_defaults_themselves() -> None:
    catalog = catalog_of(entry("cloud-only", "paid", ctx=200_000, tools_ok=True))
    floor = declared("local", catalog, Rules())
    assert floor.basis == BASIS_UNKNOWN
    assert floor.entries == 0
    assert floor.max_input_tokens == 6_826
    assert (floor.tool_calling, floor.image_input) == (False, False)


def test_an_untiered_entry_is_not_evidence_of_eligibility() -> None:
    catalog = catalog_of({"id": "no-tier", "provider": "test", "ctx": 4_096, "tools_ok": True})
    assert declared("auto", catalog, Rules()).entries == 0


def test_the_headroom_and_output_assumption_come_from_the_rules() -> None:
    catalog = catalog_of(entry("a", "paid", ctx=10_000, tools_ok=True))
    rules = Rules(scoring=ScoringRules(ctx_headroom=2.0, assumed_output_tokens=400))
    floor = declared("cloud", catalog, rules)
    assert floor.max_input_tokens == 5_000
    assert floor.max_output_tokens == 400


def test_the_output_ceiling_cannot_exceed_what_the_window_has_left() -> None:
    catalog = catalog_of(entry("a", "paid", ctx=1_000, tools_ok=True))
    rules = Rules(scoring=ScoringRules(ctx_headroom=4.0, assumed_output_tokens=5_000))
    floor = declared("cloud", catalog, rules)
    assert floor.max_input_tokens == 250
    assert floor.max_output_tokens == 750


def test_a_zero_output_assumption_still_declares_one_token() -> None:
    catalog = catalog_of(entry("a", "paid", ctx=10_000, tools_ok=True))
    floor = declared("cloud", catalog, Rules(scoring=ScoringRules(assumed_output_tokens=0)))
    assert floor.max_output_tokens == 1


def test_every_card_carries_its_floor_and_the_extras_do_too() -> None:
    catalog = catalog_of(
        entry("paid-model", "paid", ctx=200_000, tools_ok=True, modalities=["text", "image"]),
        entry("free-model", "free", ctx=32_000, tools_ok=False, modalities=["text"]),
        entry("local-model", "local", ctx=8_192, tools_ok=False, modalities=["text"]),
    )
    rules = Rules(picker=PickerRules(show_extras=True))
    listed_cards = asyncio.run(cards(app_for(catalog, rules)))
    by_id = {card["id"]: card for card in listed_cards}

    assert list(by_id) == [
        "headwaters/auto",
        "headwaters/free",
        "headwaters/local",
        "headwaters/cloud",
        "headwaters/quality",
        "headwaters/duck",
    ]
    everything = by_id["headwaters/auto"]["capabilities"]
    assert everything == {
        "max_input_tokens": 6_826,  # the local entry is the smallest window in auto scope
        "max_output_tokens": 1_000,
        "image_input": False,  # not every eligible entry takes images
        "tool_calling": False,
        "basis": BASIS_CATALOG,
        "entries": 3,
    }
    assert by_id["headwaters/free"]["capabilities"]["entries"] == 2
    assert by_id["headwaters/local"]["capabilities"]["entries"] == 1
    assert by_id["headwaters/quality"]["capabilities"] == everything  # auto scope as well
    # The cloud scope never sees the local entry, so its floor is the free entry.
    assert by_id["headwaters/cloud"]["capabilities"]["max_input_tokens"] == 26_666


def test_the_single_model_route_carries_the_same_floor() -> None:
    catalog = catalog_of(entry("local-model", "local", ctx=8_192, tools_ok=True))

    async def go() -> dict[str, Any]:
        transport = httpx.ASGITransport(app=app_for(catalog))
        async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
            response = await client.get(
                "/v1/models/headwaters/auto", headers={"Authorization": f"Bearer {TOKEN}"}
            )
        assert response.status_code == 200
        return response.json()

    body = asyncio.run(go())
    assert body["capabilities"] == {
        "max_input_tokens": 6_826,
        "max_output_tokens": 1_000,
        "image_input": False,
        "tool_calling": True,
        "basis": BASIS_CATALOG,
        "entries": 1,
    }


def test_the_cards_are_still_guarded() -> None:
    catalog = catalog_of(entry("local-model", "local", ctx=8_192))

    async def go() -> int:
        transport = httpx.ASGITransport(app=app_for(catalog))
        async with httpx.AsyncClient(transport=transport, base_url=BASE) as client:
            response = await client.get("/v1/models")
        return response.status_code

    assert asyncio.run(go()) == 401
