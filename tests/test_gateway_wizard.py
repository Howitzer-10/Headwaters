"""HDW-505: the wizard's HTTP surface, which is the hard constraint of this ticket.

A ★ package may not import a web framework (``.importlinter``: *Only headwaters.gateway may import
fastapi, starlette or uvicorn*), so the wizard is not an app of its own: it is one GET plus nine
enumerated POSTs on the dashboard's existing app, and the guards are the key writes' guards. These
tests are about that surface only — the steps themselves are in ``test_wizard_steps.py`` and the
whole walk is in ``test_wizard_e2e.py``.

What is checked here, and why each one is here rather than assumed:

* the five refusals, in the order the route applies them (host, origin, media, size, CSRF);
* the path table is closed: a path not in ``WIZARD_PATHS`` is a 404, so this is never "call
  anything" — and the table has exactly the nine writes the page renders forms for;
* a POST never reflects its sentence into a URL: the notice comes back once through a single-use
  token, then it is gone;
* the switch is real: with ``features.wizard: false`` the package is still *discovered* (the same
  ``pkgutil.iter_modules`` walk ``ledger``, ``paid_scopes`` and ``usage_report`` use), the page
  says it is switched off, and every write refuses in words instead of acting.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from headwaters.core.features import FeatureRegistry, discover_features
from headwaters.core.wizard import (
    NOT_INSTALLED,
    SWITCHED_OFF,
    WIZARD_FLAG,
    WIZARD_PAGE,
    WIZARD_PATHS,
    wizard_act,
    wizard_enabled,
    wizard_page,
    wizard_status,
)
from headwaters.daemon.wizard_backend import WizardBackend
from headwaters.gateway.server import build_status_page_app
from tests.wizard_support import (
    CSRF,
    DASHBOARD_PORT,
    built,
    detected_machine,
)

BASE = f"http://127.0.0.1:{DASHBOARD_PORT}"
OTHER_PORT = 4102


def app_for(registry: FeatureRegistry, *, port: int = DASHBOARD_PORT) -> TestClient:
    return TestClient(
        build_status_page_app(
            render=lambda section, error: "<h1>the dashboard</h1>",
            csrf=CSRF,
            port=port,
            wizard=WizardBackend(lambda: registry),
        ),
        base_url=f"http://127.0.0.1:{port}",
    )


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    from tests.wizard_support import attach_report

    h = built(tmp_path, hardware=lambda: detected_machine())
    attach_report(h.registry)
    return app_for(h.registry)


# -- the page ----------------------------------------------------------------------------------
def test_the_page_is_served_on_the_dashboard_surface_and_carries_no_secret(
    client: TestClient,
) -> None:
    response = client.get(WIZARD_PAGE)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.text
    assert "step 1 of 8" in body
    assert "the daemon" in body, "the page says out loud that it needs the daemon to be up"
    for forbidden in ("HF_TOKEN", "NVIDIA_API_KEY", "OPENROUTER_API_KEY", "sk-", "Bearer "):
        assert forbidden not in body, forbidden
    assert "?csrf=" not in body, "the token goes in a form body, never in a URL"


def test_every_enumerated_write_is_a_route_and_nothing_else_is(client: TestClient) -> None:
    assert len(WIZARD_PATHS) == 9
    for path in WIZARD_PATHS:
        response = client.post(path, data={"csrf": CSRF, "step": "detect"}, follow_redirects=False)
        assert response.status_code == 303, (path, response.status_code)
    unknown = client.post("/wizard/do-anything", data={"csrf": CSRF}, follow_redirects=False)
    assert unknown.status_code in (404, 405)


def test_an_unknown_step_in_the_query_string_is_ignored_rather_than_refused(
    client: TestClient,
) -> None:
    assert client.get(f"{WIZARD_PAGE}?step=99").status_code == 200
    assert client.get(f"{WIZARD_PAGE}?step=not-a-number").status_code == 200


# -- the five guards, in the order the route applies them --------------------------------------
def test_a_foreign_host_header_is_refused_before_anything_else(tmp_path: Path) -> None:
    from tests.wizard_support import attach_report

    h = built(tmp_path, hardware=lambda: detected_machine())
    attach_report(h.registry)
    client = app_for(h.registry)
    response = client.post(
        "/wizard/mark",
        data={"csrf": CSRF, "step": "detect", "state": "done"},
        headers={"host": f"evil.example:{DASHBOARD_PORT}"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "bad_host"


def test_the_host_must_be_this_dashboard_port_not_just_loopback(tmp_path: Path) -> None:
    from tests.wizard_support import attach_report

    h = built(tmp_path, hardware=lambda: detected_machine())
    attach_report(h.registry)
    client = app_for(h.registry)
    response = client.post(
        "/wizard/mark",
        data={"csrf": CSRF, "step": "detect", "state": "done"},
        headers={"host": f"127.0.0.1:{OTHER_PORT}"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "bad_host"


def test_a_foreign_origin_is_refused(client: TestClient) -> None:
    response = client.post(
        "/wizard/mark",
        data={"csrf": CSRF, "step": "detect", "state": "done"},
        headers={"origin": "https://evil.example"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "bad_origin"


def test_the_dashboard_origin_itself_is_allowed(client: TestClient) -> None:
    response = client.post(
        "/wizard/mark",
        data={"csrf": CSRF, "step": "detect", "state": "done"},
        headers={"origin": BASE},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_a_body_that_is_not_a_small_urlencoded_form_is_refused(client: TestClient) -> None:
    json_body = client.post(
        "/wizard/mark", json={"csrf": CSRF, "step": "detect"}, follow_redirects=False
    )
    assert json_body.status_code == 415
    assert json_body.json()["error"]["code"] == "unsupported_media_type"

    huge = client.post(
        "/wizard/mark",
        content=b"a=" + b"x" * (16 * 1024),
        headers={"content-type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    assert huge.status_code == 413
    assert huge.json()["error"]["code"] == "body_too_large"


def test_a_missing_or_wrong_csrf_token_writes_nothing(tmp_path: Path) -> None:
    h = built(tmp_path, hardware=lambda: detected_machine())
    client = app_for(h.registry)
    missing = client.post("/wizard/env/undo", data={"step": "detect"}, follow_redirects=False)
    assert missing.status_code == 403
    assert missing.json()["error"]["code"] == "bad_csrf"
    wrong = client.post("/wizard/env/undo", data={"csrf": "0" * len(CSRF)}, follow_redirects=False)
    assert wrong.status_code == 403
    assert wrong.json()["error"]["code"] == "bad_csrf"
    assert h.writer.writes == []


# -- the sentence comes back once, through a token, not through the URL ------------------------
def test_a_post_redirects_to_a_single_use_notice_and_the_notice_is_gone_after_once(
    client: TestClient,
) -> None:
    response = client.post(
        "/wizard/mark",
        data={"csrf": CSRF, "step": "ides", "state": "skipped"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith(f"{WIZARD_PAGE}?token=")
    assert "skipped" not in location, "the sentence is never put in a URL"

    shown = client.get(location)
    assert shown.status_code == 200
    assert "step 3 recorded as skipped" in shown.text

    again = client.get(location)
    assert "step 3 recorded as skipped" not in again.text, "the token is single use"

    clean = client.get(WIZARD_PAGE)
    assert "step 3 recorded as skipped" not in clean.text


def test_an_action_that_refuses_returns_303_and_shows_the_refusal_once(
    client: TestClient,
) -> None:
    response = client.post(
        "/wizard/host/apply",
        data={"csrf": CSRF, "host": "emacs", "fingerprint": "0" * 16},
        follow_redirects=False,
    )
    assert response.status_code == 303
    shown = client.get(response.headers["location"])
    assert "there is no host called" in shown.text


def test_a_backend_that_raises_becomes_500_not_a_crash(tmp_path: Path) -> None:
    class Broken:
        def page(self, **kwargs: Any) -> str:
            raise RuntimeError("boom")

        def act(self, action: str, form: Any) -> Any:
            raise RuntimeError("boom")

    client = TestClient(
        build_status_page_app(
            render=lambda section, error: "<h1>the dashboard</h1>",
            csrf=CSRF,
            port=DASHBOARD_PORT,
            wizard=Broken(),
        ),
        base_url=BASE,
    )
    assert client.get(WIZARD_PAGE).status_code == 500
    refused = client.post("/wizard/mark", data={"csrf": CSRF}, follow_redirects=False)
    assert refused.status_code == 500
    assert refused.json()["error"]["code"] == "action_failed"


# -- the switch, and the package that is not installed ----------------------------------------
def test_the_package_is_found_by_the_same_walk_the_other_features_use(
    tmp_path: Path,
) -> None:
    registry = FeatureRegistry()
    found = discover_features(registry)
    assert "headwaters.features.wizard" in found
    spec = registry.get(WIZARD_FLAG)
    assert spec is not None, "discovery registered the wizard's spec"
    assert spec.star is True, "the wizard is a ★ feature"
    assert spec.default_enabled is True, "a ★ feature is on unless the person turns it off"
    assert wizard_enabled(registry) is True
    assert registry.plugin(WIZARD_FLAG, "WizardPlugin") is not None


def test_with_the_flag_off_the_page_says_so_and_every_write_refuses(tmp_path: Path) -> None:
    from tests.wizard_support import attach_report

    h = built(tmp_path, flags={WIZARD_FLAG: False}, hardware=lambda: detected_machine())
    attach_report(h.registry)
    registry = h.registry

    assert wizard_enabled(registry) is False
    assert registry.plugin(WIZARD_FLAG, "WizardPlugin") is not None, "still installed"
    assert wizard_page(registry, csrf=CSRF) is None
    assert wizard_status(registry)["note"] == SWITCHED_OFF

    client = app_for(registry)
    page = client.get(WIZARD_PAGE)
    assert page.status_code == 200, "the link on the dashboard is never a bare 404"
    assert "switched off" in page.text
    assert "features.wizard" in page.text, "the page says which switch to turn on"

    refused = client.post(
        "/wizard/env/apply",
        data={"csrf": CSRF, "fingerprint": "0" * 16},
        follow_redirects=False,
    )
    assert refused.status_code == 303
    assert "switched off" in client.get(refused.headers["location"]).text
    assert h.writer.writes == []

    direct = wizard_act(registry, "mark", {"step": "detect", "state": "done"})
    assert direct["ok"] is False
    assert direct["error"] == SWITCHED_OFF


def test_with_the_package_absent_the_surface_still_answers_in_words(tmp_path: Path) -> None:
    registry = FeatureRegistry()  # nothing discovered, nothing registered
    assert wizard_enabled(registry) is False, "an absent package is not enabled"
    assert wizard_page(registry, csrf=CSRF) is None
    status = wizard_status(registry)
    assert status["discovered"] is False
    assert status["step_count"] == 8
    assert status["note"] == NOT_INSTALLED
    assert wizard_act(registry, "mark", {})["error"] == NOT_INSTALLED

    client = app_for(registry)
    page = client.get(WIZARD_PAGE)
    assert page.status_code == 200
    assert "not installed" in page.text
