"""HDW-514: one canonical set of plain provider names, shared by the CLI and the dashboard.

A person types ``nvidia``, never an environment-variable name, and no human-readable output prints
one. ``--json`` keeps ``env_var`` as a machine contract.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from typer.testing import Result

from headwaters.cli import cmd_keys
from headwaters.cli.main import app
from headwaters.core.keys import KeyStore
from headwaters.core.rules import Rules
from tests.cli_support import FAKE_KEY, home, runner
from tests.keys_support import FakeKeyring

CANONICAL = [
    "anthropic",
    "openai",
    "openrouter",
    "nvidia",
    "huggingface",
    "mistral",
    "pollinations",
    "opencode",
    "kilo",
    "ollama",
]
ENV_NAME = re.compile(r"[A-Z][A-Z0-9]*_(API_KEY|TOKEN)\b")


def run(*args: str, stdin: str | None = None) -> Result:
    return runner.invoke(app, list(args), input=stdin)


def stored(fake: FakeKeyring) -> dict[str, dict[str, Any]]:
    raw = fake.get_password("headwaters", "__aliases__")
    return json.loads(raw) if raw else {}


@pytest.fixture
def backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeKeyring:
    home(tmp_path, monkeypatch)
    for var in (
        "OPENROUTER_API_KEY",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "NVIDIA_API_KEY",
        "HF_TOKEN",
        "MISTRAL_API_KEY",
        "POLLINATIONS_API_KEY",
        "KILO_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)
    fake = FakeKeyring()
    monkeypatch.setattr(cmd_keys, "key_store", lambda rules: KeyStore(rules, backend=fake))
    return fake


def test_providers_is_the_one_canonical_list_without_env_names() -> None:
    store = KeyStore(Rules(), env={}, backend=FakeKeyring())
    infos = store.providers()
    assert [p.name for p in infos][:3] == CANONICAL[:3]
    assert sorted(p.name for p in infos) == sorted(CANONICAL)
    by_name = {p.name: p for p in infos}
    assert (by_name["nvidia"].key_required, by_name["nvidia"].tier) == (True, "free_cloud")
    assert (by_name["kilo"].key_required, by_name["kilo"].tier) == (False, "paid")
    assert (by_name["mistral"].key_required, by_name["mistral"].tier) == (True, "paid")
    assert (by_name["ollama"].key_required, by_name["ollama"].tier) == (False, "local")
    assert not any(hasattr(p, "env_var") for p in infos)


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("nvidia", "nvidia"),
        ("NVIDIA", "nvidia"),
        ("NVIDIA_API_KEY", "nvidia"),
        ("hf", "huggingface"),
        ("HF_TOKEN", "huggingface"),
        ("OPENROUTER_API_KEY", "openrouter"),
        ("or", "openrouter"),
        ("work", None),
    ],
)
def test_names_normalise_to_the_canonical_provider(typed: str, expected: str | None) -> None:
    store = KeyStore(Rules(), env={}, backend=FakeKeyring())
    assert store.provider_name(typed) == expected


def test_set_prints_only_the_plain_name_and_never_a_variable(backend: FakeKeyring) -> None:
    result = run("keys", "set", "nvidia", "--stdin", stdin=f"{FAKE_KEY}\n")
    assert result.exit_code == 0, result.output
    assert result.output.startswith("Saved nvidia (")
    assert "used instead" in result.output
    assert not ENV_NAME.search(result.output)
    assert "$" not in result.output
    assert backend.get_password("headwaters", "nvidia") == FAKE_KEY


def test_set_json_keeps_env_var_as_a_machine_contract(backend: FakeKeyring) -> None:
    result = run("keys", "set", "nvidia", "--stdin", "--json", stdin=f"{FAKE_KEY}\n")
    payload = json.loads(result.stdout)
    assert (payload["alias"], payload["provider"], payload["env_var"]) == (
        "nvidia",
        "nvidia",
        "NVIDIA_API_KEY",
    )


@pytest.mark.parametrize(
    ("typed", "provider"),
    [("NVIDIA_API_KEY", "nvidia"), ("hf", "huggingface"), ("HF_TOKEN", "huggingface")],
)
def test_an_env_spelling_or_short_form_becomes_the_provider_not_a_second_alias(
    backend: FakeKeyring, typed: str, provider: str
) -> None:
    result = run("keys", "set", typed, "--stdin", stdin=f"{FAKE_KEY}\n")
    assert result.exit_code == 0, result.output
    assert set(stored(backend)) == {provider}
    assert backend.get_password("headwaters", provider) == FAKE_KEY
    assert not ENV_NAME.search(result.output)


def test_an_unknown_name_without_provider_stores_nothing_and_lists_the_known_ones(
    backend: FakeKeyring,
) -> None:
    result = run("keys", "set", "nvidiaa", "--stdin", stdin=f"{FAKE_KEY}\n")
    assert result.exit_code != 0
    assert backend.values == {}
    for name in CANONICAL:
        assert name in result.output
    assert not ENV_NAME.search(result.output)


def test_unknown_name_in_json_mode_is_a_structured_error(backend: FakeKeyring) -> None:
    result = run("keys", "set", "nope", "--stdin", "--json", stdin=f"{FAKE_KEY}\n")
    payload = json.loads(result.stdout)
    assert payload["error"]["code"] == "unknown_provider"
    assert backend.values == {}


def test_custom_aliases_still_work_and_allow_several_keys_per_provider(
    backend: FakeKeyring,
) -> None:
    assert (
        run("keys", "set", "work", "-p", "nvidia", "--stdin", stdin="k-work-1234567\n").exit_code
        == 0
    )
    assert (
        run(
            "keys", "set", "home", "-p", "NVIDIA_API_KEY", "--stdin", stdin="k-home-1234567\n"
        ).exit_code
        == 0
    )
    assert run("keys", "set", "work", "--stdin", stdin="k-work-7654321\n").exit_code == 0
    index = stored(backend)
    assert {alias: spec["provider"] for alias, spec in index.items()} == {
        "work": "nvidia",
        "home": "nvidia",
    }
    assert backend.get_password("headwaters", "work") == "k-work-7654321"


def test_listing_testing_and_removing_accept_the_short_form(backend: FakeKeyring) -> None:
    run("keys", "set", "huggingface", "--stdin", stdin=f"{FAKE_KEY}\n")
    listing = run("keys", "list")
    assert "huggingface" in listing.output
    assert not ENV_NAME.search(listing.output)
    assert run("keys", "remove", "hf").exit_code == 0
    assert backend.get_password("headwaters", "huggingface") is None


def test_help_and_hints_use_plain_names_only(backend: FakeKeyring) -> None:
    for args in (
        ["keys", "set", "--help"],
        ["keys", "list", "--help"],
        ["keys", "remove", "--help"],
        ["keys", "test", "--help"],
    ):
        result = run(*args)
        assert result.exit_code == 0
        assert not ENV_NAME.search(result.output), args
    assert "headwaters keys set nvidia" in run("keys", "set", "--help").output
    assert "headwaters keys set nvidia" in run("keys", "test").output
