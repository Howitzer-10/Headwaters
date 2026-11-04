"""HDW-519: free access is not a tier.

Two labels (``charges_money`` + ``free_access``) replace one ambiguous word, one formatter renders
them, ``free_limits`` becomes clauses with a metric/window/scope, and counting follows the clause.
No tier name is added or renamed: ``tier`` stays the derivation and the routing vocabulary.
"""

from __future__ import annotations

import random
import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from headwaters.core import state_limits
from headwaters.core.catalog import sources
from headwaters.core.catalog.loader import (
    SCHEMA_RELATIVE_PATH,
    SEED_RELATIVE_PATH,
    load_catalog,
)
from headwaters.core.catalog.loader import (
    main as loader_main,
)
from headwaters.core.catalog.schema import (
    FREE_TO_USE,
    RESERVED_PROVIDER_NAMES,
    SELLS_MARKED,
    SELLS_PLAN,
    SELLS_USAGE,
    Catalog,
    FreeLimit,
    LimitClause,
    LimitCondition,
    ProviderEntry,
    format_access,
)
from headwaters.core.keys import KeyStore
from headwaters.core.providers import Registry
from headwaters.core.quotas import (
    QuotaTracker,
    key_subject,
    model_subject,
    provider_subject,
    quota_window,
)
from headwaters.core.rules import Rules, WallsRules
from headwaters.core.state import Database, open_database
from tests.keys_support import FakeKeyring
from tests.test_discovery_sources import FakeHttp

REPO_ROOT = Path(__file__).resolve().parent.parent
SEED_PATH = REPO_ROOT / SEED_RELATIVE_PATH
CATALOG: Catalog = load_catalog(SEED_PATH)
REGISTRY = Registry.from_catalog(CATALOG)
NOW = 1_800_000_000.0  # a frozen instant, so window names are stable
NEW_ROWS = ("gemini", "groq", "cerebras", "cohere")
# What an un-migrated row derives from `tier` + `honor_free_markers`, per provider (D3).
TODAYS_LABELS = {
    "nvidia": (False, "all"),
    "huggingface": (True, "marked"),
    "mistral": (True, "none"),
    "pollinations": (True, "none"),
    "kilo": (True, "marked"),
    "opencode": (True, "none"),
    "opencode-go": (True, "none"),
}
TEXT = {"input_modalities": ["text"], "output_modalities": ["text"]}
LISTING: dict[str, Any] = {
    "data": [
        {"id": "acme-chat", **TEXT},
        {"id": "acme-free:free", **TEXT},
    ]
}


def provider(name: str) -> ProviderEntry:
    found = REGISTRY.get(name)
    assert found is not None, name
    return found


def seed_limit(name: str) -> FreeLimit:
    found = next((f for f in CATALOG.free_limits if f.provider == name), None)
    assert found is not None, name
    return found


def stripped(row: ProviderEntry) -> ProviderEntry:
    """The same row as a pre-change catalog wrote it: the HDW-519 fields absent (D2)."""
    data = row.model_dump()
    data.pop("charges_money")
    data.pop("free_access")
    data.pop("note", None)
    return ProviderEntry.model_validate(data)


def tiers(entry: ProviderEntry) -> list[str]:
    http = FakeHttp({("GET", entry.models_url): LISTING})
    return [m.tier for m in sources.registry_models(http, entry, "k-abcdef123456")]


def plain_row(**over: Any) -> ProviderEntry:
    base: dict[str, Any] = {
        "name": "acme",
        "title": "Acme",
        "env_var": "ACME_API_KEY",
        "base_url": "https://api.acme.test/v1",
        "tier": "free_cloud",
        "source_url": "https://acme.test/docs",
        "checked": "2026-11-03",
        **over,
    }
    return ProviderEntry.model_validate(base)


def fresh_db(tmp_path: Path, name: str = "state") -> Database:
    return open_database(tmp_path / f"{name}.db")


def tracker(db: Database, limits: list[FreeLimit], **kwargs: Any) -> QuotaTracker:
    walls = WallsRules(**kwargs.pop("walls", {}))
    return QuotaTracker(
        db,
        walls,
        limits,
        clock=lambda: NOW,
        rng=random.Random(7),
        facts=kwargs.pop("facts", None),
    )


# ------------------------------------------------------- compatibility (1-3)


def test_unmigrated_catalog_derives_todays_labels() -> None:
    for name, expected in TODAYS_LABELS.items():
        labels = stripped(provider(name)).labels()
        assert (labels.charges_money, labels.free_access) == expected, name


def test_unmigrated_catalog_model_tiers_are_unchanged() -> None:
    for name in TODAYS_LABELS:
        if name == "mistral":
            continue  # the owner-approved none -> plan migration: its own test asserts the change
        row = provider(name)
        assert tiers(stripped(row)) == tiers(row), name
    # Every other row keeps today's routing; mistral is the one deliberate behaviour change.
    mistral = stripped(provider("mistral"))
    assert tiers(mistral) == ["paid", "paid"]
    assert tiers(provider("mistral")) == ["free_cloud", "free_cloud"]


def test_old_build_rejects_new_catalog_fields() -> None:
    """Compatibility is one-directional: a pre-change build refuses a migrated catalog (D2)."""

    class OldProviderEntry(BaseModel):
        model_config = ConfigDict(extra="forbid")
        name: str
        tier: str

    data = provider("kilo").model_dump()
    with pytest.raises(ValidationError, match=r"charges_money|free_access|note"):
        OldProviderEntry.model_validate(data)
    assert ProviderEntry.model_validate(data).labels().free_access == "marked"


# ---------------------------------------------------------------- the split


def test_seed_rows_are_fully_migrated() -> None:
    for row in CATALOG.providers:
        assert row.charges_money is not None, row.name
        assert row.free_access is not None, row.name
        if row.free_access == "all":
            assert row.tier == "free_cloud", row.name
        if row.tier == "free_cloud":
            assert row.free_access in {"all", "plan"}, row.name


def test_kilo_states_both_facts() -> None:
    kilo = provider("kilo")
    assert (kilo.charges_money, kilo.free_access) == (True, "marked")
    label = format_access(kilo.charges_money, kilo.free_access)
    assert SELLS_USAGE in label
    assert "marked" in label


def test_free_tier_provider_routes_free() -> None:
    groq = provider("groq")  # charges_money True, free_access "plan", tier free_cloud
    assert (groq.charges_money, groq.free_access) == (True, "plan")
    assert tiers(groq) == ["free_cloud", "free_cloud"]


def test_two_labels_come_from_one_function() -> None:
    strings = (FREE_TO_USE, SELLS_USAGE, SELLS_MARKED, SELLS_PLAN)
    hits = {
        path.name
        for path in (REPO_ROOT / "headwaters").rglob("*.py")
        if any(text in path.read_text(encoding="utf-8") for text in strings)
    }
    assert hits == {"schema.py"}


# ------------------------------------------------------------ free_limits


def test_legacy_rpm_rpd_resolve_to_model_scope(tmp_path: Path) -> None:
    legacy = FreeLimit(provider="openrouter", rpm=10, rpd=50)
    assert [(c.metric, c.max, c.per, c.scope) for c in legacy.clauses()] == [
        ("requests", 10, "minute", "model"),
        ("requests", 50, "day", "model"),
    ]
    db = fresh_db(tmp_path)
    t = tracker(db, [legacy])
    t.count_request("m/a", "openrouter")
    t.count_request("m/b", "openrouter")
    _, minute = quota_window("requests", "minute", NOW)
    assert state_limits.get_quota(db, model_subject("m/a"), minute).count == 1
    assert state_limits.get_quota(db, model_subject("m/b"), minute).count == 1


DAY_CLAUSES = [
    LimitClause(metric="requests", max=50, per="day", scope="key"),
    LimitClause(
        metric="requests",
        max=1000,
        per="day",
        scope="key",
        condition=LimitCondition(fact="account_topup_usd_total", op=">=", value=10),
    ),
]


def test_topup_condition_selects_the_permissive_clause(tmp_path: Path) -> None:
    row = seed_limit("openrouter")
    plain = {(c.max, c.per) for c in row.clauses(None)}
    topped = {(c.max, c.per) for c in row.clauses({"account_topup_usd_total": 25.0})}
    assert (1000.0, "day") not in plain
    assert (50.0, "day") in plain
    assert (1000.0, "day") in topped

    asks = 60
    without = tracker(
        fresh_db(tmp_path, "without"),
        [FreeLimit(provider="openrouter", limits=DAY_CLAUSES)],
        walls={"soft_exhaust_pct": 100},
    )
    with_fact = tracker(
        fresh_db(tmp_path, "with"),
        [FreeLimit(provider="openrouter", limits=DAY_CLAUSES)],
        walls={"soft_exhaust_pct": 100},
        facts={"account_topup_usd_total": 10.0},
    )
    for _ in range(asks):
        without.count_request("m/a", "openrouter")
        with_fact.count_request("m/a", "openrouter")
    assert without.availability("m/a", "openrouter").status == "soft"  # 50/day governs
    assert with_fact.availability("m/a", "openrouter").status == "ok"  # 1000/day governs


def test_conditional_never_restricts(tmp_path: Path) -> None:
    row = FreeLimit(provider="openrouter", limits=DAY_CLAUSES)
    below = tracker(
        fresh_db(tmp_path, "below"),
        [row],
        walls={"soft_exhaust_pct": 100},
        facts={"account_topup_usd_total": 5.0},
    )
    for _ in range(55):
        below.count_request("m/a", "openrouter")
    assert below.availability("m/a", "openrouter").status == "soft"  # the day still caps at 50
    unknown = tracker(
        fresh_db(tmp_path, "unknown"), [row], walls={"soft_exhaust_pct": 100}, facts=None
    )
    for _ in range(55):
        unknown.count_request("m/a", "openrouter")
    assert unknown.availability("m/a", "openrouter").status == "soft"


def test_organisation_scope_shares_one_subject(tmp_path: Path) -> None:
    db = fresh_db(tmp_path)
    t = tracker(db, [seed_limit("groq")])
    t.count_request("groq/llama-a", "groq")
    t.count_request("groq/llama-b", "groq")
    _, minute = quota_window("requests", "minute", NOW)
    shared = state_limits.get_quota(db, provider_subject("groq"), minute)
    assert shared is not None
    assert shared.count == 2
    assert state_limits.get_quota(db, model_subject("groq/llama-a"), minute) is None


def test_neurons_clause_creates_no_quota(tmp_path: Path) -> None:
    db = fresh_db(tmp_path)
    t = tracker(db, [seed_limit("cloudflare")], walls={"soft_exhaust_pct": 1})
    for _ in range(20):
        t.count_request("cloudflare/@cf/model", "cloudflare")
    assert t.availability("cloudflare/@cf/model", "cloudflare").status == "ok"
    _, day = quota_window("neurons", "day", NOW)
    assert state_limits.get_quota(db, provider_subject("cloudflare"), day) is None
    _, minute = quota_window("requests", "minute", NOW)
    assert state_limits.get_quota(db, model_subject("cloudflare/@cf/model"), minute) is None


def test_monthly_token_clause_parses_and_is_inert(tmp_path: Path) -> None:
    row = seed_limit("mistral")
    again = FreeLimit.model_validate(row.model_dump())
    assert again == row
    monthly = [c for c in again.clauses() if c.per == "month"]
    assert [(c.metric, c.max, c.scope, c.tokens) for c in monthly] == [
        ("tokens", 1_000_000_000, "key", "input+output")
    ]
    db = fresh_db(tmp_path)
    t = tracker(db, [again], walls={"soft_exhaust_pct": 1})
    for _ in range(5):
        t.count_request("mistral/mistral-small-latest", "mistral")
    assert t.availability("mistral/mistral-small-latest", "mistral").status == "ok"
    _, month = quota_window("tokens", "month", NOW)
    assert state_limits.get_quota(db, key_subject("default"), month) is None


def test_licence_is_documentation_only() -> None:
    row = seed_limit("cohere")
    assert row.licence == "non_commercial_only"
    plain = FreeLimit.model_validate({**row.model_dump(), "licence": "unstated"})
    assert [(c.metric, c.max, c.scope) for c in plain.clauses()] == [
        (c.metric, c.max, c.scope) for c in row.clauses()
    ]
    readers = {
        path.name
        for path in (REPO_ROOT / "headwaters").rglob("*.py")
        if ".licence" in path.read_text(encoding="utf-8")
    }
    assert readers == set()  # nothing reads it: it is a note to a person, not a gate


# ------------------------------------------------------------------ rows


def test_new_schema_fields_are_in_the_generated_schema() -> None:
    assert loader_main(["--check", str(REPO_ROOT / SCHEMA_RELATIVE_PATH)]) == 0
    generated = (REPO_ROOT / SCHEMA_RELATIVE_PATH).read_text(encoding="utf-8")
    for field in ("free_access", "charges_money", "limits", "licence"):
        assert f'"{field}"' in generated, field


def test_new_provider_rows_validate_and_are_registered() -> None:
    names = [p.name for p in REGISTRY]
    infos = KeyStore(Rules(), env={}, backend=FakeKeyring()).providers()
    for name in NEW_ROWS:
        assert names.count(name) == 1, name
        assert [i.name for i in infos].count(name) == 1, name
        assert name not in RESERVED_PROVIDER_NAMES, name
        row = provider(name)
        assert row.verify is False, name  # an unverified row says why it is unverified (D4)
        assert row.note, name
        assert row.charges_money is True, name
        assert row.free_access == "plan", name


def test_cloudflare_base_url_vars_or_row_absent() -> None:
    # No half-built row: the account id cannot be templated into the base url (deviation 2).
    assert REGISTRY.get("cloudflare") is None
    assert "base_url_vars" not in ProviderEntry.model_fields
    assert [c.metric for c in seed_limit("cloudflare").clauses()] == ["neurons"]


KEY_SHAPED = (
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{35}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{36,}"),
    re.compile(r"xox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)


def test_no_api_key_is_written_into_the_catalog() -> None:
    for path in (SEED_PATH, REPO_ROOT / SCHEMA_RELATIVE_PATH):
        text = path.read_text(encoding="utf-8")
        assert [p.pattern for p in KEY_SHAPED if p.search(text)] == [], path.name
