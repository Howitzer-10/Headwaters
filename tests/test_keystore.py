"""HDW-401: the credential store (keyring first, environment fallback) and its masking.

Every store here is built over ``tests.keys_support.FakeKeyring``: the suite never reads or writes
the real Windows Credential Manager.
"""

from __future__ import annotations

from typing import Any

import pytest

from headwaters.core.keys import KeyStore, KeyStoreError, mask
from headwaters.core.rules import OpenAICompatEndpoint, Rules
from tests.keys_support import ENV_SECRET, SECRET, BrokenKeyring, FakeKeyring


def _rules(*endpoints: OpenAICompatEndpoint) -> Rules:
    return Rules.model_validate(
        {"models": {"openai_compatible": [e.model_dump() for e in endpoints]}}
    )


def _endpoint(name: str = "myfree", var: str = "MYFREE_KEY") -> OpenAICompatEndpoint:
    return OpenAICompatEndpoint(name=name, base_url="https://api.example.com/v1", api_key_env=var)


def test_a_saved_key_is_what_the_executor_gets_and_its_alias_is_the_wall_subject() -> None:
    store = KeyStore(Rules(), env={"OPENROUTER_API_KEY": ENV_SECRET}, backend=FakeKeyring())
    saved = store.save("work", SECRET, provider="openrouter")
    assert saved.masked != SECRET
    assert SECRET not in saved.masked
    assert store.api_key_for("openrouter") == SECRET  # the store wins over the variable
    assert store.key_alias_for("openrouter") == "work"
    assert store.secret_for("work") == SECRET
    assert store.provider_for("work") == "openrouter"
    assert [(r.alias, r.source) for r in store.entries() if r.alias == "work"] == [
        ("work", "credential store")
    ]


def test_the_environment_variable_is_the_fallback_and_keeps_the_provider_alias() -> None:
    store = KeyStore(Rules(), env={"OPENROUTER_API_KEY": ENV_SECRET}, backend=FakeKeyring())
    assert store.api_key_for("openrouter") == ENV_SECRET
    assert store.key_alias_for("openrouter") == "openrouter"
    assert store.secret_for("openrouter") == ENV_SECRET
    sources = {r.alias: r.source for r in store.entries()}
    assert sources["openrouter"] == "environment"
    assert sources["anthropic"] == "not set"


def test_a_user_endpoint_reads_its_own_variable_and_is_saved_under_its_name() -> None:
    rules = _rules(_endpoint())
    store = KeyStore(rules, env={"MYFREE_KEY": ENV_SECRET}, backend=FakeKeyring())
    assert store.api_key_for("myfree") == ENV_SECRET
    assert store.key_alias_for("myfree") == "myfree"
    store.save("myfree", SECRET)
    assert store.api_key_for("myfree") == SECRET
    assert store.provider_for("myfree") == "myfree"


def test_a_custom_alias_for_an_endpoint_looks_the_key_up_by_its_provider() -> None:
    store = KeyStore(_rules(_endpoint()), env={}, backend=FakeKeyring())
    store.save("personal", SECRET, provider="myfree")
    assert store.api_key_for("myfree") == SECRET
    assert store.key_alias_for("myfree") == "personal"


def test_no_key_means_no_alias_so_the_wall_stays_provider_scoped() -> None:
    store = KeyStore(Rules(), env={}, backend=FakeKeyring())
    assert store.api_key_for("openrouter") is None
    assert store.key_alias_for("openrouter") is None
    assert store.secret_for("work") is None
    assert store.provider_for("work") is None
    assert {r.alias: r.masked for r in store.entries()}["openrouter"] == ""


def test_mask_shows_the_ends_of_a_long_key_and_never_the_key() -> None:
    assert mask(SECRET) == f"{SECRET[:4]}...{SECRET[-4:]}"
    assert mask(SECRET) != SECRET
    assert mask("short") == "*****"
    assert mask(None) == ""
    assert mask("") == ""


def test_removing_a_key_deletes_the_secret_and_the_index_entry() -> None:
    backend = FakeKeyring()
    store = KeyStore(Rules(), env={"OPENROUTER_API_KEY": ENV_SECRET}, backend=backend)
    store.save("work", SECRET, provider="openrouter")
    assert store.remove("work") is True
    assert store.api_key_for("openrouter") == ENV_SECRET  # the variable is used again
    assert SECRET not in backend.payload()
    assert store.remove("work") is False


def test_the_index_survives_a_new_store_and_holds_no_secret() -> None:
    backend = FakeKeyring()
    KeyStore(Rules(), env={}, backend=backend).save("work", SECRET, provider="openrouter")
    reopened = KeyStore(Rules(), env={}, backend=backend)
    assert reopened.api_key_for("openrouter") == SECRET
    assert reopened.key_alias_for("openrouter") == "work"
    index = backend.get_password("headwaters", "__aliases__") or ""
    assert "work" in index
    assert SECRET not in index


def test_a_bad_alias_or_an_empty_key_is_refused() -> None:
    store = KeyStore(Rules(), env={}, backend=FakeKeyring())
    with pytest.raises(KeyStoreError):
        store.save("two words", SECRET)
    with pytest.raises(KeyStoreError):
        store.save("work", "   ")
    with pytest.raises(KeyStoreError):
        store.save("work", "has a space")


def test_a_broken_store_falls_back_to_the_environment_and_refuses_a_write() -> None:
    store = KeyStore(Rules(), env={"OPENROUTER_API_KEY": ENV_SECRET}, backend=BrokenKeyring())
    assert store.api_key_for("openrouter") == ENV_SECRET
    assert store.key_alias_for("openrouter") == "openrouter"
    with pytest.raises(KeyStoreError):
        store.save("work", SECRET)


def test_nothing_a_caller_can_print_contains_the_secret() -> None:
    store = KeyStore(Rules(), env={}, backend=FakeKeyring())
    store.save("work", SECRET, provider="openrouter")
    printed: list[str] = [repr(store)]
    printed.extend(str(value) for value in store.entries())
    printed.extend(str(row) for row in store.entries())
    assert not any(SECRET in text for text in printed)


def test_the_cache_is_cleared_when_a_key_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    store = KeyStore(Rules(), env={}, backend=FakeKeyring())
    assert store.api_key_for("openrouter") is None  # a miss is cached too
    store.save("openrouter", SECRET)
    assert store.api_key_for("openrouter") == SECRET
    assert store.remove("openrouter") is True
    assert store.api_key_for("openrouter") is None


def test_a_second_alias_can_hold_a_second_key_for_the_same_provider() -> None:
    store = KeyStore(Rules(), env={}, backend=FakeKeyring())
    store.save("work", SECRET, provider="openrouter")
    store.save("personal", ENV_SECRET, provider="openrouter")
    # The first alias in sorted order wins, so the choice is stable across calls and restarts.
    assert store.key_alias_for("openrouter") == "personal"
    assert store.secret_for("work") == SECRET
    rows: list[dict[str, Any]] = [
        {"alias": r.alias, "masked": r.masked}
        for r in store.entries()
        if r.provider == "openrouter"
    ]
    assert {row["alias"] for row in rows} == {"work", "personal", "openrouter"}
    assert all(SECRET not in str(row["masked"]) for row in rows)
