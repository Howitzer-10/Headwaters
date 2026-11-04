"""HDW-404 / HDW-503: the local dashboard - the status page and its four key writes.

Nothing here touches the network, the keyring or the user's real ``%APPDATA%``: the database is a
``tmp_path`` file, the page is served through the ASGI test client, the credential store is
``FakeKeyring`` and every provider ping goes to a fake HTTP client.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from headwaters.control import keys as control_keys
from headwaters.control.keys import DashboardKeys
from headwaters.control.status_page import StatusPage, build_page, render_html
from headwaters.core.catalog.http import SourceError
from headwaters.core.features import FeatureRegistry, FeatureSpec
from headwaters.core.keys import STANDARD_ENV, KeyStore
from headwaters.core.rules import RouterRules, Rules
from headwaters.core.state import Database, open_database
from headwaters.core.state_decisions import DecisionRow, insert_decision
from headwaters.core.state_key_tests import get_key_test, record_key_test
from headwaters.core.state_limits import record_wall
from headwaters.gateway.server import build_status_page_app
from tests.keys_support import ENV_SECRET, OTHER_SECRET, SECRET, FakeKeyring

NOW = 1_800_000_000.0
DASHBOARD_PORT = 4001
BASE = f"http://127.0.0.1:{DASHBOARD_PORT}"
CSRF = "csrf-token-for-the-tests"


class FakeHttp:
    """The metadata endpoint behind the Test button: it answers, fails, or is never reached."""

    def __init__(self, error: str | None = None) -> None:
        self.error = error
        self.urls: list[str] = []

    def request_json(
        self, method: str, url: str, headers: dict[str, str] | None = None, **kw: Any
    ) -> Any:
        self.urls.append(url)
        if self.error is not None:
            raise SourceError(self.error)
        return {"data": []}


def save_form(alias: str = "openrouter", provider: str = "openrouter") -> dict[str, str]:
    return {"csrf": CSRF, "alias": alias, "provider": provider, "secret": SECRET}


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return open_database(tmp_path / "state" / "test.db")


@pytest.fixture
def store() -> KeyStore:
    """A store over an in-memory keyring and an empty environment, never the real ones."""
    return KeyStore(Rules(), env={}, backend=FakeKeyring())


def page(
    db: Database,
    *,
    rules: Rules | None = None,
    registry: FeatureRegistry | None = None,
    keys: KeyStore | None = None,
    csrf: str = "",
) -> StatusPage:
    return build_page(
        rules=Rules() if rules is None else rules,
        project=None,
        db=db,
        registry=FeatureRegistry() if registry is None else registry,
        keys=keys,
        csrf=csrf,
        version="9.9.9",
        now=NOW,
    )


def client(
    db: Database,
    *,
    store: KeyStore | None = None,
    rules: Rules | None = None,
    http: FakeHttp | None = None,
) -> TestClient:
    live = Rules() if rules is None else rules

    def render(section: str | None, error: str | None) -> str:
        return render_html(
            page(db, rules=live, keys=store, csrf=CSRF), section=section, error=error
        )

    actions = (
        None
        if store is None
        # Saving a key pings it, so a write test never reaches the network either: the fake is the
        # default, and a test that cares about the ping passes its own.
        else DashboardKeys(
            store=lambda: store,
            rules=lambda: live,
            db=db,
            http=http if http is not None else FakeHttp(),
        )
    )
    return TestClient(
        build_status_page_app(render=render, keys=actions, csrf=CSRF, port=DASHBOARD_PORT),
        base_url=BASE,
    )


def test_the_page_shows_readable_walls_from_the_real_state(db: Database) -> None:
    record_wall(db, "provider:openrouter:paid", "exhausted", until=NOW + 90)
    record_wall(db, "model:qwen:14b", "rate_limit", until=None)
    html = render_html(page(db))
    assert "OpenRouter: paid credits" in html  # FU-122-4: words, not "provider:...:paid"
    assert "provider:openrouter:paid" not in html
    assert "Model qwen:14b" in html
    assert "1m 30s" in html
    assert "unknown" in html  # a wall with no end time says so
    assert "after a balance check" not in html


def test_the_page_shows_the_fallback_order(db: Database) -> None:
    assert "interleave" in render_html(page(db))
    descends = Rules(router=RouterRules(tier_order="descend", tier_direction="local_first"))
    html = render_html(page(db, rules=descends))
    assert "Fallback order" in html
    assert "descend (local_first)" in html
    assert "interleave" not in html


def test_the_page_shows_the_decision_log_with_locks_and_the_offload_penalty(
    db: Database,
) -> None:
    insert_decision(
        db,
        DecisionRow(
            surface="gateway",
            scope="local",
            strategy="cost_saver",
            active_locks=["private_lock"],
            chosen="qwen:14b",
            floor=75.0,
            score=64.5,
            below_floor=True,
            offload_penalty=30.0,
            reasons=["cheap: qwen:14b score 64.5, floor 75, below floor by 10.5"],
        ),
    )
    data = page(db, rules=Rules(private_lock=True, default_scope="local"))
    assert data.locks == ["private_lock"]  # FU-128-1: every lock in force, not just narrowing
    assert data.decisions[0].offload_penalty == 30.0
    html = render_html(data)
    assert "qwen:14b" in html
    assert "-30.0" in html  # FU-133-1: the points lost to system RAM are on the page
    assert "private_lock" in html
    assert "below floor" in html


def test_the_page_lists_core_features_with_the_star_legend(db: Database) -> None:
    registry = FeatureRegistry()
    registry.register(FeatureSpec(id="paid_scopes", star=True, title="Paid scopes"))
    html = render_html(page(db, registry=registry))
    assert "Gateway: OpenAI and Anthropic endpoints" in html  # core, free forever
    assert "★ Paid scopes" in html
    assert "★ = free during beta" in html


def test_the_page_has_no_editing_controls_without_a_key_backend(db: Database) -> None:
    html = render_html(page(db))
    assert "Keys" not in html  # nothing to manage, so no section and no form
    for tag in ("<form", "<input", "<button", "<script", "onclick"):
        assert tag not in html, tag


def test_the_page_is_served_read_only_and_refuses_a_foreign_host(db: Database) -> None:
    with client(db) as http:
        served = http.get("/")
        assert served.status_code == 200
        assert "Headwaters status" in served.text
        assert served.headers["cache-control"] == "no-store"
        assert http.get("/index.html").status_code == 200
        # The accept-when: a browser on another machine cannot reach this through a rebound
        # name, even though it talks to 127.0.0.1 on this host.
        refused = http.get("/", headers={"Host": "evil.example.com"})
        assert refused.status_code == 421
        assert refused.json()["error"]["code"] == "bad_host"
        assert http.get("/", headers={"Origin": "http://evil.example.com"}).status_code == 403


def test_a_failing_build_does_not_take_the_page_down() -> None:
    def boom(section: str | None, error: str | None) -> str:
        raise RuntimeError("state unreadable")

    with TestClient(build_status_page_app(render=boom), base_url=BASE) as http:
        failed = http.get("/")
        assert failed.status_code == 500
        assert "could not be built" in failed.text


# --------------------------------------------------------------- HDW-503: the Keys section


def test_the_keys_section_lists_every_provider_the_registry_names(
    db: Database, store: KeyStore
) -> None:
    data = page(db, keys=store, csrf=CSRF)
    assert [group.name for group in data.keys] == [info.name for info in store.providers()]
    html = render_html(data)
    assert 'id="keys"' in html
    assert 'id="keys-ollama"' in html  # a keyless provider keeps its group: it exists
    assert "needs no key" in html
    assert 'action="/keys/set"' in html
    assert f'value="{CSRF}"' in html  # every form carries the page's token


def test_the_page_never_shows_an_environment_variable_name(db: Database, store: KeyStore) -> None:
    html = render_html(page(db, keys=store, csrf=CSRF))
    assert [name for name in STANDARD_ENV.values() if name in html] == []


def test_the_keys_section_comes_first_only_when_it_is_asked_for(
    db: Database, store: KeyStore
) -> None:
    asked = render_html(page(db, keys=store, csrf=CSRF), section="keys")
    usual = render_html(page(db, keys=store, csrf=CSRF))
    assert asked.index('id="keys"') < asked.index("Scope and locks")
    assert usual.index('id="keys"') > usual.index("Scope and locks")


def test_the_page_says_why_paid_scope_is_unavailable_until_a_paid_key_exists(
    db: Database, store: KeyStore
) -> None:
    assert "No paid provider has a key" in render_html(page(db, keys=store, csrf=CSRF))
    store.save("anthropic", SECRET)
    assert "No paid provider has a key" not in render_html(page(db, keys=store, csrf=CSRF))


# ------------------------------------------------------- HDW-503: the writes and their refusals


def test_without_a_key_backend_the_page_serves_and_has_no_write_routes(db: Database) -> None:
    with client(db) as http:
        assert http.get("/").status_code == 200
        assert http.post("/keys/set", data={"csrf": CSRF, "alias": "nvidia"}).status_code == 404


def test_a_write_from_a_foreign_host_is_refused_with_403(db: Database, store: KeyStore) -> None:
    with client(db, store=store) as http:
        refused = http.post(
            "/keys/set",
            data={"csrf": CSRF, "alias": "nvidia", "provider": "nvidia", "secret": SECRET},
            headers={"Host": "evil.example.com"},
        )
        assert refused.status_code == 403
        assert refused.json()["error"]["code"] == "bad_host"
        assert store.secret("nvidia") is None


def test_a_write_against_another_port_on_this_host_is_refused(
    db: Database, store: KeyStore
) -> None:
    with client(db, store=store) as http:
        for host in (f"127.0.0.1:{DASHBOARD_PORT + 1}", "127.0.0.1", "localhost:1"):
            refused = http.post(
                "/keys/remove", data={"csrf": CSRF, "alias": "nvidia"}, headers={"Host": host}
            )
            assert refused.status_code == 403, host
            assert refused.json()["error"]["code"] == "bad_host"


def test_a_write_from_another_origin_is_refused_even_with_the_token(
    db: Database, store: KeyStore
) -> None:
    data = {"csrf": CSRF, "alias": "nvidia", "provider": "nvidia", "secret": SECRET}
    with client(db, store=store) as http:
        for origin in ("http://evil.example.com", f"http://127.0.0.1:{DASHBOARD_PORT + 1}", "null"):
            refused = http.post("/keys/set", data=data, headers={"Origin": origin})
            assert refused.status_code == 403, origin
            assert refused.json()["error"]["code"] == "bad_origin"
        assert store.secret("nvidia") is None
        # This dashboard's own origin, and the Referer a same-site form post carries, are fine.
        own = http.post("/keys/set", data=data, headers={"Origin": BASE}, follow_redirects=False)
        assert own.status_code == 303
        store.remove("nvidia")
        referer = {"Referer": f"{BASE}/?section=keys"}
        same_site = http.post("/keys/set", data=data, headers=referer, follow_redirects=False)
        assert same_site.status_code == 303


def test_a_write_needs_the_page_token(db: Database, store: KeyStore) -> None:
    with client(db, store=store) as http:
        missing = http.post(
            "/keys/set", data={"alias": "nvidia", "provider": "nvidia", "secret": SECRET}
        )
        assert missing.status_code == 403
        assert missing.json()["error"]["code"] == "bad_csrf"
        wrong = http.post(
            "/keys/set",
            data={
                "csrf": "not-the-token",
                "alias": "nvidia",
                "provider": "nvidia",
                "secret": SECRET,
            },
        )
        assert wrong.status_code == 403
        assert wrong.json()["error"]["code"] == "bad_csrf"
        assert store.secret("nvidia") is None


def test_every_write_route_refuses_a_missing_or_wrong_token_and_a_foreign_host(
    db: Database, store: KeyStore
) -> None:
    store.save("openrouter", SECRET)
    paths = ("/keys/set", "/keys/remove", "/keys/test", "/keys/test-all")
    with client(db, store=store) as http:
        for path in paths:
            missing = http.post(path, data={"alias": "openrouter"})
            assert missing.status_code == 403, path
            assert missing.json()["error"]["code"] == "bad_csrf", path
            wrong = http.post(path, data={"csrf": "not-the-token", "alias": "openrouter"})
            assert wrong.status_code == 403, path
            assert wrong.json()["error"]["code"] == "bad_csrf", path
            foreign = http.post(
                path,
                data={"csrf": CSRF, "alias": "openrouter"},
                headers={"Host": "evil.example.com"},
            )
            assert foreign.status_code == 403, path
            assert foreign.json()["error"]["code"] == "bad_host", path
            elsewhere = http.post(
                path,
                data={"csrf": CSRF, "alias": "openrouter"},
                headers={"Origin": "http://evil.example.com"},
            )
            assert elsewhere.status_code == 403, path
            assert elsewhere.json()["error"]["code"] == "bad_origin", path


def test_a_write_only_accepts_a_small_form(db: Database, store: KeyStore) -> None:
    with client(db, store=store) as http:
        json_body = http.post("/keys/set", json={"csrf": CSRF, "alias": "nvidia", "secret": SECRET})
        assert json_body.status_code == 415
        assert json_body.json()["error"]["code"] == "unsupported_media_type"
        huge = http.post(
            "/keys/set",
            data={"csrf": CSRF, "alias": "nvidia", "provider": "nvidia", "secret": "x" * 9000},
        )
        assert huge.status_code == 413
        assert huge.json()["error"]["code"] == "body_too_large"
        assert store.secret("nvidia") is None


def test_a_get_to_a_write_path_is_not_allowed(db: Database, store: KeyStore) -> None:
    with client(db, store=store) as http:
        assert http.get("/keys/set").status_code == 405
        assert http.put("/keys/remove", data={"csrf": CSRF}).status_code == 405
        assert http.request("OPTIONS", "/keys/test").status_code == 405
        assert http.get("/keys/test-all").status_code == 405


def test_saving_a_key_shows_it_masked_and_never_its_prefix(db: Database, store: KeyStore) -> None:
    with client(db, store=store) as http:
        saved = http.post(
            "/keys/set",
            data={"csrf": CSRF, "alias": "nvidia", "provider": "nvidia", "secret": SECRET},
            follow_redirects=False,
        )
        assert saved.status_code == 303
        assert saved.headers["location"] == "/?section=keys#keys-nvidia"
        assert store.secret("nvidia") == SECRET
        # Saving tests the key, so the row comes back with an answer instead of "never tested".
        row = get_key_test(db, "nvidia")
        assert row is not None
        assert SECRET not in row.detail
        html = http.get("/?section=keys").text
        assert "<code>nvidia</code>" in html
        assert "credential store" in html
        assert f"****{SECRET[-4:]}" in html  # the last four characters only
        assert SECRET[:8] not in html  # no prefix of the key, not even four characters
        assert SECRET[:-4] not in html
        assert SECRET not in html


def test_saving_a_key_tests_it_at_once_and_the_row_shows_the_fresh_answer(
    db: Database, store: KeyStore
) -> None:
    fake = FakeHttp()
    with client(db, store=store, http=fake) as http:
        saved = http.post("/keys/set", data=save_form(), follow_redirects=False)
        assert saved.status_code == 303
        assert saved.headers["location"] == "/?section=keys#keys-openrouter"
        assert fake.urls == ["https://openrouter.ai/api/v1/key"]  # the same ping as Test
        row = get_key_test(db, "openrouter")
        assert row is not None
        assert row.status == "ok"
        html = http.get("/?section=keys").text
        assert '<span class="status ok">ok</span>' in html
        assert SECRET not in html


def test_a_failed_ping_does_not_fail_the_save(db: Database, store: KeyStore) -> None:
    fake = FakeHttp(error="HTTP 401 from openrouter.ai")
    with client(db, store=store, http=fake) as http:
        saved = http.post("/keys/set", data=save_form(), follow_redirects=False)
        assert saved.status_code == 303  # saving and testing are separate outcomes
        assert saved.headers["location"] == "/?section=keys#keys-openrouter"
        assert store.secret("openrouter") == SECRET  # the key is saved and stays saved
        row = get_key_test(db, "openrouter")
        assert row is not None
        assert row.status == "unauthorized"
        html = http.get("/?section=keys").text
        assert '<span class="status bad">unauthorized</span>' in html
        assert "HTTP 401" in html  # the host and code, never the key
        assert SECRET not in html


def test_saving_a_key_replaces_the_last_answer_instead_of_forgetting_it(
    db: Database, store: KeyStore
) -> None:
    rejected = FakeHttp(error="HTTP 401 from openrouter.ai")
    with client(db, store=store, http=rejected) as http:
        http.post("/keys/set", data=save_form(), follow_redirects=False)
    first = get_key_test(db, "openrouter")
    assert first is not None
    assert first.status == "unauthorized"
    with client(db, store=store, http=FakeHttp()) as http:
        http.post("/keys/set", data=save_form(), follow_redirects=False)
    second = get_key_test(db, "openrouter")
    assert second is not None
    assert second.status == "ok"  # the new key's answer, not the old one dropped
    assert second.ts >= first.ts


def test_saving_a_key_while_blocked_records_blocked_and_sends_nothing(
    db: Database, store: KeyStore
) -> None:
    for rules in (Rules(airplane=True), Rules(private_lock=True), Rules(default_scope="local")):
        fake = FakeHttp()
        with client(db, store=store, rules=rules, http=fake) as http:
            saved = http.post("/keys/set", data=save_form(), follow_redirects=False)
            assert saved.status_code == 303
        assert fake.urls == []  # the network block sends nothing at all
        assert store.secret("openrouter") == SECRET
        row = get_key_test(db, "openrouter")
        assert row is not None
        assert row.status == "blocked"
        assert SECRET not in row.detail


@pytest.mark.parametrize(
    ("status", "css"),
    [
        ("ok", "ok"),
        ("unauthorized", "bad"),
        ("http_error", "bad"),
        ("unreachable", "bad"),
        ("blocked", "warn"),
        ("unsupported", "warn"),
    ],
)
def test_each_status_renders_with_its_colour_class_and_its_word(
    db: Database, store: KeyStore, status: str, css: str
) -> None:
    store.save("openrouter", SECRET)
    record_key_test(db, "openrouter", status, "a host and an HTTP code", now=NOW)
    html = render_html(page(db, keys=store), section="keys")
    assert f'<span class="status {css}">{status}</span>' in html
    assert f'<strong class="status {css}">openrouter</strong>' in html  # the name agrees
    assert "a host and an HTTP code" in html


def test_the_provider_name_carries_the_status_colour_and_the_word_keeps_the_dot(
    db: Database, store: KeyStore
) -> None:
    store.save("openrouter", SECRET)
    record_key_test(db, "openrouter", "unauthorized", "HTTP 401", now=NOW)
    data = page(db, keys=store)
    html = render_html(data, section="keys")
    tested = next(
        group for group in data.keys if any(key.source != "not set" for key in group.keys)
    )
    assert f'<strong class="status bad">{tested.name}</strong>' in html
    assert '<span class="status bad">last test unauthorized</span>' in html
    # A provider with no key is muted, not red: no answer is not a bad answer.
    untested = next(group.name for group in data.keys if not group.key_required)
    assert f'<strong class="status idle">{untested}</strong>' in html
    # The dot stays on the status word: a coloured name must not read as a bullet.
    assert "span.status::before" in html
    assert "strong.status::before" not in html


def test_a_key_that_was_never_tested_has_a_neutral_indicator(db: Database, store: KeyStore) -> None:
    store.save("openrouter", SECRET)
    html = render_html(page(db, keys=store), section="keys")
    assert '<span class="status idle">never tested</span>' in html
    # The colours are variables next to --line and --muted, not per-element hex.
    assert "--ok:" in html
    assert "--bad:" in html


def test_removing_a_key_takes_it_off_the_page(db: Database, store: KeyStore) -> None:
    store.save("nvidia", SECRET)
    with client(db, store=store) as http:
        removed = http.post(
            "/keys/remove", data={"csrf": CSRF, "alias": "nvidia"}, follow_redirects=False
        )
        assert removed.status_code == 303
        assert removed.headers["location"] == "/?section=keys#keys-nvidia"
        assert store.secret("nvidia") is None
        html = http.get("/?section=keys").text
        assert f"****{SECRET[-4:]}" not in html  # the mask is gone, not just the alias row
        assert "no key saved" in html
        assert SECRET not in html


def test_a_key_from_the_environment_cannot_be_removed_here_and_the_page_says_so(
    db: Database,
) -> None:
    env = {STANDARD_ENV["openrouter"]: ENV_SECRET}
    env_store = KeyStore(Rules(), env=env, backend=FakeKeyring())
    with client(db, store=env_store) as http:
        removed = http.post(
            "/keys/remove", data={"csrf": CSRF, "alias": "openrouter"}, follow_redirects=False
        )
        assert removed.status_code == 303  # the honest answer is the note, not a refusal
        assert env_store.secret("openrouter") == ENV_SECRET
        html = http.get("/?section=keys").text
        assert "cannot be removed here" in html
        assert ENV_SECRET not in html


def test_a_test_run_is_recorded_and_the_page_shows_the_answer(
    db: Database, store: KeyStore
) -> None:
    store.save("openrouter", SECRET)
    fake = FakeHttp()
    with client(db, store=store, http=fake) as http:
        tested = http.post(
            "/keys/test", data={"csrf": CSRF, "alias": "openrouter"}, follow_redirects=False
        )
        assert tested.status_code == 303
        assert tested.headers["location"] == "/?section=keys#keys-openrouter"
        assert fake.urls == ["https://openrouter.ai/api/v1/key"]
        row = get_key_test(db, "openrouter")
        assert row is not None
        assert row.status == "ok"
        html = http.get("/?section=keys").text
        assert "accepted" in html
        assert SECRET not in html


def test_a_failed_test_is_recorded_and_never_echoes_the_key(db: Database, store: KeyStore) -> None:
    store.save("openrouter", SECRET)
    fake = FakeHttp(error="HTTP 401 from openrouter.ai")
    with client(db, store=store, http=fake) as http:
        tested = http.post(
            "/keys/test", data={"csrf": CSRF, "alias": "openrouter"}, follow_redirects=False
        )
        assert tested.status_code == 303
        row = get_key_test(db, "openrouter")
        assert row is not None
        assert row.status == "unauthorized"
        html = http.get("/?section=keys").text
        assert "unauthorized" in html
        assert SECRET not in html


def test_test_all_tests_every_saved_key_and_leaves_the_rest_alone(
    db: Database, store: KeyStore
) -> None:
    store.save("openrouter", SECRET)
    store.save("anthropic", OTHER_SECRET)
    fake = FakeHttp()
    with client(db, store=store, http=fake) as http:
        tested = http.post("/keys/test-all", data={"csrf": CSRF}, follow_redirects=False)
        assert tested.status_code == 303
        assert tested.headers["location"] == "/?section=keys"  # no single section to jump to
        assert sorted(fake.urls) == [
            "https://api.anthropic.com/v1/models",
            "https://openrouter.ai/api/v1/key",
        ]
        saved = get_key_test(db, "openrouter")
        assert saved is not None
        assert saved.status == "ok"
        also = get_key_test(db, "anthropic")
        assert also is not None
        assert also.status == "ok"
        # A provider with no key was not contacted and did not get an answer recorded.
        assert get_key_test(db, "openai") is None
        assert get_key_test(db, "nvidia") is None
        html = http.get("/?section=keys").text
        assert html.count('<span class="status ok">ok</span>') == 2
        assert SECRET not in html
        assert OTHER_SECRET not in html


def test_test_all_asks_the_one_ping_path_for_every_alias_in_one_call(
    db: Database, store: KeyStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    store.save("openrouter", SECRET)
    store.save("anthropic", OTHER_SECRET)
    calls: list[list[str]] = []
    real = control_keys.check_and_record

    def spy(store_: KeyStore, rules_: Rules, db_: Database | None = None, **kw: Any) -> list[Any]:
        calls.append(list(kw.get("aliases") or ()))
        return real(store_, rules_, db_, **kw)

    monkeypatch.setattr(control_keys, "check_and_record", spy)
    with client(db, store=store, http=FakeHttp()) as http:
        tested = http.post("/keys/test-all", data={"csrf": CSRF}, follow_redirects=False)
        assert tested.status_code == 303
    assert len(calls) == 1  # one call for the whole page, not one per key
    assert sorted(calls[0]) == ["anthropic", "openrouter"]


def test_test_all_sends_nothing_while_the_private_lock_is_on(db: Database, store: KeyStore) -> None:
    store.save("openrouter", SECRET)
    fake = FakeHttp()
    locked = Rules(private_lock=True)
    with client(db, store=store, rules=locked, http=fake) as http:
        tested = http.post("/keys/test-all", data={"csrf": CSRF}, follow_redirects=False)
        assert tested.status_code == 303
    assert fake.urls == []
    row = get_key_test(db, "openrouter")
    assert row is not None
    assert row.status == "blocked"
    html = render_html(page(db, rules=locked, keys=store), section="keys")
    assert '<span class="status warn">blocked</span>' in html


def test_test_all_with_no_saved_key_comes_back_as_a_sentence(db: Database, store: KeyStore) -> None:
    with client(db, store=store) as http:
        nothing = http.post("/keys/test-all", data={"csrf": CSRF}, follow_redirects=False)
        assert nothing.status_code == 400
        assert "there is no saved key to test" in nothing.text


def test_the_test_all_button_carries_the_token_and_stays_clear_of_the_row_actions(
    db: Database, store: KeyStore
) -> None:
    empty = render_html(page(db, keys=store, csrf=CSRF), section="keys")
    assert 'action="/keys/test-all"' not in empty  # nothing saved, so nothing to test
    store.save("openrouter", SECRET)
    html = render_html(page(db, keys=store, csrf=CSRF), section="keys")
    form = (
        '<form method="post" action="/keys/test-all">'
        f'<input type="hidden" name="csrf" value="{CSRF}">'
        '<button type="submit">Test all saved keys</button>'
    )
    assert form in html
    assert html.index('action="/keys/test-all"') < html.index('<details id="keys-')  # above them
    assert '<td><form method="post" action="/keys/test-all"' not in html  # never a row action


def test_a_refused_write_comes_back_as_the_page_with_a_sentence(
    db: Database, store: KeyStore
) -> None:
    with client(db, store=store) as http:
        unknown = http.post(
            "/keys/set",
            data={"csrf": CSRF, "alias": "nvidia", "provider": "nonesuch", "secret": SECRET},
            follow_redirects=False,
        )
        assert unknown.status_code == 400
        assert "not a provider this install can call" in unknown.text
        assert SECRET not in unknown.text
        assert store.secret("nvidia") is None
        bad_alias = http.post(
            "/keys/set",
            data={"csrf": CSRF, "alias": "../escape", "provider": "nvidia", "secret": SECRET},
            follow_redirects=False,
        )
        assert bad_alias.status_code == 400
        assert SECRET not in bad_alias.text
        assert all(".." not in alias for alias in store.aliases())
        nothing = http.post(
            "/keys/test", data={"csrf": CSRF, "alias": "nvidia"}, follow_redirects=False
        )
        assert nothing.status_code == 400
        assert "there is no key to test for nvidia" in nothing.text
