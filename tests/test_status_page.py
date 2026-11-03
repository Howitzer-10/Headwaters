"""HDW-404: the read-only status page, built from real walls and real decision rows.

Nothing here touches the network, the keyring or the user's real ``%APPDATA%``: the database is a
``tmp_path`` file, the page is served through the ASGI test client, and the HTML is asserted
against the content the daemon would really render.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.testclient import TestClient

from headwaters.control.status_page import StatusPage, build_page, render_html
from headwaters.core.features import FeatureRegistry, FeatureSpec
from headwaters.core.rules import RouterRules, Rules
from headwaters.core.state import Database, open_database
from headwaters.core.state_decisions import DecisionRow, insert_decision
from headwaters.core.state_limits import record_wall
from headwaters.gateway.server import build_status_page_app

NOW = 1_800_000_000.0
BASE = "http://127.0.0.1:4001"


@pytest.fixture
def db(tmp_path: Path) -> Database:
    return open_database(tmp_path / "state" / "test.db")


def page(
    db: Database,
    *,
    rules: Rules | None = None,
    registry: FeatureRegistry | None = None,
) -> StatusPage:
    return build_page(
        rules=Rules() if rules is None else rules,
        project=None,
        db=db,
        registry=FeatureRegistry() if registry is None else registry,
        version="9.9.9",
        now=NOW,
    )


def client(db: Database) -> TestClient:
    return TestClient(build_status_page_app(render=lambda: render_html(page(db))), base_url=BASE)


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


def test_the_page_has_no_editing_controls(db: Database) -> None:
    html = render_html(page(db))
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
    def boom() -> str:
        raise RuntimeError("state unreadable")

    with TestClient(build_status_page_app(render=boom), base_url=BASE) as http:
        failed = http.get("/")
        assert failed.status_code == 500
        assert "could not be built" in failed.text
