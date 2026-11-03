"""HDW-401: ``headwaters keys set|list|remove|test`` over an in-memory credential store.

``cli_support.home`` points ``HEADWATERS_HOME`` at ``tmp_path``; ``cmd_keys.key_store`` is replaced
with one over ``FakeKeyring``, so no test reads or writes the Windows Credential Manager.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, ClassVar

import pytest
from typer.testing import Result

from headwaters.cli import cmd_keys
from headwaters.cli.main import app
from headwaters.control import keys as control_keys
from headwaters.core.catalog.http import SourceError
from headwaters.core.keys import KeyStore
from tests.cli_support import FAKE_KEY, home, runner, write_rules
from tests.keys_support import FakeKeyring


class FakeHttp:
    """A metadata endpoint that either answers or fails; counts what was sent."""

    calls: ClassVar[int] = 0
    urls: ClassVar[list[str]] = []
    headers: ClassVar[list[dict[str, str]]] = []
    error: ClassVar[str | None] = None

    def __init__(self, timeout: float = 0.0) -> None:
        pass

    def request_json(
        self, method: str, url: str, headers: dict[str, str] | None = None, **kw: Any
    ) -> Any:
        FakeHttp.calls += 1
        FakeHttp.urls.append(url)
        FakeHttp.headers.append(dict(headers or {}))
        if FakeHttp.error is not None:
            raise SourceError(FakeHttp.error)
        return {"data": []}


def run(*args: str, stdin: str | None = None) -> Result:
    return runner.invoke(app, list(args), input=stdin)


def run_json(*args: str, stdin: str | None = None) -> tuple[Result, dict[str, Any]]:
    result = runner.invoke(app, [*args, "--json"], input=stdin)
    return result, json.loads(result.stdout)


@pytest.fixture
def backend(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeKeyring:
    home(tmp_path, monkeypatch)
    for name in ("OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    fake = FakeKeyring()
    monkeypatch.setattr(cmd_keys, "key_store", lambda rules: KeyStore(rules, backend=fake))
    monkeypatch.setattr(control_keys, "UrllibHttp", FakeHttp)
    FakeHttp.calls = 0
    FakeHttp.urls = []
    FakeHttp.headers = []
    FakeHttp.error = None
    return fake


def test_set_reads_the_key_from_stdin_and_prints_only_the_masked_value(
    backend: FakeKeyring,
) -> None:
    result = run("keys", "set", "openrouter", "--stdin", stdin=f"{FAKE_KEY}\n")
    assert result.exit_code == 0, result.output
    assert FAKE_KEY not in result.output
    assert f"{FAKE_KEY[:4]}...{FAKE_KEY[-4:]}" in result.output
    assert backend.get_password("headwaters", "openrouter") == FAKE_KEY


def test_set_with_json_reports_the_metadata_and_never_the_key(backend: FakeKeyring) -> None:
    result, payload = run_json(
        "keys", "set", "work", "--provider", "openrouter", "--stdin", stdin=f"{FAKE_KEY}\n"
    )
    assert result.exit_code == 0, result.output
    assert payload["alias"] == "work"
    assert payload["provider"] == "openrouter"
    assert payload["env_var"] == "OPENROUTER_API_KEY"
    assert payload["key"] == f"{FAKE_KEY[:4]}...{FAKE_KEY[-4:]}"
    assert FAKE_KEY not in result.stdout


def test_list_masks_every_key_and_says_where_it_comes_from(backend: FakeKeyring) -> None:
    run("keys", "set", "work", "--provider", "openrouter", "--stdin", stdin=f"{FAKE_KEY}\n")
    result, payload = run_json("keys", "list")
    assert result.exit_code == 0, result.output
    rows = {row["alias"]: row for row in payload["keys"]}
    assert rows["work"]["source"] == "credential store"
    assert rows["work"]["provider"] == "openrouter"
    assert rows["openrouter"]["source"] == "not set"
    assert all(FAKE_KEY not in row["key"] for row in payload["keys"])
    assert FAKE_KEY not in result.stdout

    text = run("keys", "list")
    assert text.exit_code == 0
    assert FAKE_KEY not in text.output
    assert f"{FAKE_KEY[:4]}...{FAKE_KEY[-4:]}" in text.output


def test_a_key_that_is_only_in_the_environment_says_so(
    backend: FakeKeyring, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", FAKE_KEY)
    _result, payload = run_json("keys", "list")
    rows = {row["alias"]: row for row in payload["keys"]}
    assert rows["openrouter"]["source"] == "environment"
    assert FAKE_KEY not in json.dumps(payload)


def test_remove_reports_a_missing_key_and_deletes_a_present_one(backend: FakeKeyring) -> None:
    missing = run("keys", "remove", "nope")
    assert missing.exit_code == 1
    assert "no stored key named nope" in missing.output

    run("keys", "set", "work", "--provider", "openrouter", "--stdin", stdin=f"{FAKE_KEY}\n")
    removed = run("keys", "remove", "work")
    assert removed.exit_code == 0, removed.output
    assert backend.get_password("headwaters", "work") is None


def test_test_sends_the_stored_key_to_the_providers_metadata_endpoint(
    backend: FakeKeyring,
) -> None:
    run("keys", "set", "openrouter", "--stdin", stdin=f"{FAKE_KEY}\n")
    result = run("keys", "test", "openrouter")
    assert result.exit_code == 0, result.output
    assert "ok" in result.output
    assert FAKE_KEY not in result.output
    assert FakeHttp.urls == ["https://openrouter.ai/api/v1/key"]
    assert FakeHttp.headers[0]["Authorization"] == f"Bearer {FAKE_KEY}"


def test_test_reports_a_rejected_key_and_exits_non_zero(backend: FakeKeyring) -> None:
    run("keys", "set", "openrouter", "--stdin", stdin=f"{FAKE_KEY}\n")
    FakeHttp.error = "HTTP 401 from openrouter.ai"
    result = run("keys", "test")
    assert result.exit_code == 1
    assert "unauthorized" in result.output
    assert FAKE_KEY not in result.output


def test_test_sends_nothing_while_the_private_lock_is_on(
    backend: FakeKeyring, tmp_path: Path
) -> None:
    run("keys", "set", "openrouter", "--stdin", stdin=f"{FAKE_KEY}\n")
    write_rules(tmp_path, "private_lock: true\n")
    result = run("keys", "test")
    assert result.exit_code == 1
    assert "blocked" in result.output
    assert FakeHttp.calls == 0


def test_test_pings_anthropic_with_its_own_headers(backend: FakeKeyring) -> None:
    run("keys", "set", "claude", "--provider", "anthropic", "--stdin", stdin=f"{FAKE_KEY}\n")
    result = run("keys", "test", "claude")
    assert result.exit_code == 0, result.output
    assert FakeHttp.urls == ["https://api.anthropic.com/v1/models"]
    assert FakeHttp.headers[0]["x-api-key"] == FAKE_KEY
    assert FakeHttp.headers[0]["anthropic-version"] == "2023-06-01"


def test_test_refuses_a_provider_it_has_no_way_to_check(backend: FakeKeyring) -> None:
    run("keys", "set", "local", "--provider", "ollama", "--stdin", stdin=f"{FAKE_KEY}\n")
    result = run("keys", "test", "local")
    assert result.exit_code == 1
    assert "unsupported" in result.output
    assert FakeHttp.calls == 0
