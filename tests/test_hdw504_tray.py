"""HDW-504: the tray app, driven headless.

No notification area, no browser, no real icon: the icon object, ``opener(url)`` and the notifier
are injected, and the daemon is ``httpx.MockTransport``. What a test asserts on is the URL a
browser would have opened, the body the daemon would have received, and the centre pixel of the
icon Pillow would have drawn.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import httpx
import pytest
from PIL import Image
from typer.testing import CliRunner

from headwaters.cli.main import app as cli_app
from headwaters.core import paths
from headwaters.core.catalog.schema import Catalog
from headwaters.core.daemon_info import DaemonInfo, DaemonPorts
from headwaters.core.keys import STANDARD_ENV, KeyStore, provider_items
from headwaters.core.providers import Registry, default_registry
from headwaters.core.rules import Rules
from headwaters.features.tray import FEATURE_ID
from headwaters.features.tray.app import TrayApp
from headwaters.features.tray.client import TrayClient
from headwaters.features.tray.icon import (
    AMBER_RGB,
    CENTRE_PROBE,
    GREEN_RGB,
    LOCK_PROBE,
    PURPLE_RGB,
    RED_RGB,
    render,
)
from headwaters.features.tray.menu import SCOPES, MenuEntry, build_menu, menu_entries
from headwaters.features.tray.model import AMBER, GREEN, RED, TrayState
from tests.keys_support import SECRET, FakeKeyring
from tests.test_hdw514_registry import catalog_with, entry

TOKEN = "tray-test-token"
API_PORT, GATEWAY_PORT, DASH_PORT = 4002, 4000, 4001
DASHBOARD = f"http://127.0.0.1:{DASH_PORT}/"
SCOPE_URL = f"http://127.0.0.1:{API_PORT}/v1/scope"
ACMEAKE_ENV = "ACMEFAKE_API_KEY"

INFO = DaemonInfo(
    pid=4242,
    started_at=0.0,
    version="0.0.0",
    ports=DaemonPorts(API_PORT, GATEWAY_PORT, DASH_PORT),
)


# --------------------------------------------------------------------------- the fake daemon


def status_payload(**tray: Any) -> dict[str, Any]:
    block: dict[str, Any] = {
        "scope": "local",
        "effective_scope": "local",
        "tier": "balanced",
        "warm_model": "llama3:8b",
        "walls": [],
        "last_status": "ok",
        "below_floor": False,
        "keys_set": 1,
        "anonymous_provider": False,
    }
    block.update(tray)
    return {
        "pid": 4242,
        "scope": block["scope"],
        "private_lock": False,
        "airplane": False,
        "tray": block,
    }


PROVIDERS = [
    {"provider": "openai", "alias": "work", "source": "credential store"},
    {"provider": "ollama", "alias": "ollama", "source": "not set"},
]


def fake_daemon(
    *,
    status: dict[str, Any] | None = None,
    keys: Any = None,
    scope: Any = None,
    down: bool = False,
    timeout: bool = False,
) -> tuple[TrayClient, list[httpx.Request]]:
    """A client over ``httpx.MockTransport``. Nothing is listened on and nothing is opened."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if timeout:
            raise httpx.ConnectTimeout("no answer", request=request)
        path = request.url.path
        if path == "/v1/status":
            return httpx.Response(200, json=status if status is not None else status_payload())
        if path == "/v1/keys":
            payload = keys if keys is not None else {"ok": True, "providers": PROVIDERS}
            return httpx.Response(200, json=payload)
        if path == "/v1/scope":
            body = json.loads(request.content)
            reply = scope(body) if callable(scope) else (scope or {"ok": True, **body})
            return httpx.Response(200 if reply.get("ok") else 409, json=reply)
        return httpx.Response(404, json={"ok": False, "reason": "no such route"})

    client = TrayClient(
        http=httpx.Client(transport=httpx.MockTransport(handler)),
        info=(lambda: None) if down else (lambda: INFO),
        token=lambda: TOKEN,
    )
    return client, seen


class FakeIcon:
    """Stands in for ``pystray.Icon``: it records what the app set and never shows anything."""

    def __init__(self) -> None:
        self.icon: Any = None
        self.title = ""
        self.menu: Any = None
        self.notified: list[str] = []
        self.update_calls = 0
        self.stopped = False

    def update_menu(self) -> None:
        self.update_calls += 1

    def notify(self, message: str, title: str | None = None) -> None:
        self.notified.append(message)

    def stop(self) -> None:
        self.stopped = True

    def run(self) -> None:  # never entered in a test
        raise AssertionError("a test must not run a real icon loop")


class FakeItem:
    """A ``pystray.MenuItem``: items are invoked as ``item(icon)`` and that calls the action."""

    def __init__(self, text: str, action: Any, **options: Any) -> None:
        self.text = text
        self.action = action
        self.options = options

    def __call__(self, icon: Any = None) -> Any:
        return self.action(icon, self) if callable(self.action) else None


class StrictItem(FakeItem):
    """``pystray.MenuItem`` as it really behaves: ``checked`` must be None or a callable.

    ``pystray._base._assert_callable`` raises ``ValueError`` for a bare bool, and ``True`` is
    exactly what crashed the tray on every launch (HDW-504).
    """

    def __init__(self, text: str, action: Any, **options: Any) -> None:
        checked = options.get("checked")
        if checked is not None and not callable(checked):
            raise ValueError(checked)
        super().__init__(text, action, **options)


class FakeMenu(tuple):
    """``pystray.Menu`` holds its items and is iterable (not a tuple, and with no length), so
    this is iterable too and nothing may rely on more. A submenu is an item whose action is a
    menu, exactly as pystray takes it."""

    def __new__(cls, *items: Any) -> FakeMenu:
        return super().__new__(cls, items)

    @property
    def items(self) -> tuple[Any, ...]:
        return tuple(self)


def fake_menu(state: TrayState, actions: Any) -> FakeMenu:
    return build_menu(menu_entries(state, actions), menuitem=FakeItem, menu=FakeMenu)


def fake_builder(entries: tuple[MenuEntry, ...]) -> FakeMenu:
    """What ``TrayApp`` builds its menu with in a test: no pystray, no notification area."""
    return build_menu(entries, menuitem=FakeItem, menu=FakeMenu)


def app_with(**kwargs: Any) -> tuple[TrayApp, FakeIcon, list[str]]:
    """An app whose browser is a list: ``opener(url)`` records the URL and says it worked."""
    opened: list[str] = []

    def opener(url: str) -> bool:
        opened.append(url)
        return True

    icon = FakeIcon()
    kwargs.setdefault("client", fake_daemon()[0])
    kwargs.setdefault("notifier", None)
    kwargs.setdefault("builder", fake_builder)
    app = TrayApp(icon=icon, opener=opener, **kwargs)
    return app, icon, opened


# --------------------------------------------------------------------------- the menu


def test_the_menu_is_exactly_these_items_in_this_order() -> None:
    app, _, _ = app_with()
    app.refresh()
    texts = [item.text for item in app.menu()]
    assert texts == [
        app.state.status_line,
        "Scope",
        "Private Mode",
        "Keys",
        "Open dashboard",
        "Doctor",
        "Restart daemon",
        "Quit",
    ]


def test_the_scope_submenu_is_every_scope_checked_on_the_current_one() -> None:
    submenu = fake_menu(TrayState(daemon_up=True, scope="local"), _NoActions())[1].action
    assert isinstance(submenu, FakeMenu)
    assert [child.text for child in submenu] == list(SCOPES)
    assert [child.options["checked"](None) for child in submenu] == [
        scope == "local" for scope in SCOPES
    ]
    assert all(child.options["radio"] for child in submenu)


def test_build_menu_passes_pystray_a_callable_for_a_checked_item_never_a_bool() -> None:
    # ``menu_entries`` still returns plain data; only the pystray call gets a callable.
    entries = menu_entries(TrayState(daemon_up=True, scope="local"), _NoActions())
    assert entries[2].checked is False  # the data seam stays a bool
    built = build_menu(entries, menuitem=FakeItem, menu=FakeMenu)
    scope_items = built[1].action
    assert isinstance(scope_items, FakeMenu)
    for child in scope_items:
        assert callable(child.options["checked"])
        assert not isinstance(child.options["checked"], bool)
    assert built[2].options["checked"](None) is False


def test_a_pystray_that_rejects_a_non_callable_checked_no_longer_sees_one() -> None:
    # The crash: pystray raised ValueError(True) on the scope submenu at every launch.
    entries = menu_entries(TrayState(daemon_up=True, scope="local"), _NoActions())
    built = build_menu(entries, menuitem=StrictItem, menu=FakeMenu)
    submenu = built[1].action
    assert isinstance(submenu, FakeMenu)
    assert [child.options["checked"](None) for child in submenu] == [
        scope == "local" for scope in SCOPES
    ]
    # A checked Private Mode is the other ``True`` pystray used to reject.
    on = menu_entries(TrayState(daemon_up=True, private_lock=True), _NoActions())
    built_on = build_menu(on, menuitem=StrictItem, menu=FakeMenu)
    assert built_on[2].options["checked"](None) is True
    # An entry with no checked state still passes None, as it always did.
    assert built[5].options["checked"] is None


# --------------------------------------------------------------------------- staying alive


def test_spawn_child_sends_the_childs_stderr_to_a_capped_state_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A tray that dies at once used to be DEVNULL, which is why nobody could say why.
    from headwaters.features import tray as tray_feature

    monkeypatch.setenv(paths.HOME_ENV_VAR, str(tmp_path))
    tray_feature.log_path().parent.mkdir(parents=True)
    tray_feature.log_path().write_bytes(b"x" * (tray_feature.TRAY_LOG_MAX_BYTES + 1))
    seen: dict[str, Any] = {}

    class FakePopen:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            seen.update(kwargs)
            seen["argv"] = args[0]

        def poll(self) -> None:
            return None

    monkeypatch.setattr(tray_feature.subprocess, "Popen", FakePopen)
    tray_feature.spawn_child()

    assert seen["argv"] == tray_feature.command()
    assert seen["stdin"] is tray_feature.subprocess.DEVNULL
    assert seen["stdout"] is tray_feature.subprocess.DEVNULL
    assert seen["stderr"] is not tray_feature.subprocess.DEVNULL
    assert seen["stderr"].name == str(tray_feature.log_path())
    assert seen["stderr"].closed  # the child keeps its own handle; the parent closes its copy
    assert tray_feature.log_path().stat().st_size == 0  # truncated before this run writes


def test_a_tray_that_dies_at_once_is_logged_with_the_tail_of_its_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from headwaters.daemon import service
    from headwaters.features import tray as tray_feature

    warnings: list[str] = []
    monkeypatch.setattr(tray_feature, "log_tail", lambda: "ValueError: True")
    monkeypatch.setattr(service._log, "warning", lambda msg, *args: warnings.append(msg % args))
    monkeypatch.setattr(service, "TRAY_START_GRACE_S", 0.2)

    class DeadChild:
        def poll(self) -> int:
            return 1

    service.Daemon._report_tray_death(DeadChild())

    assert len(warnings) == 1
    assert "exited at once" in warnings[0]
    assert "ValueError: True" in warnings[0]
    assert str(tray_feature.log_path()) in warnings[0]


def test_the_disable_switch_still_stops_the_tray(monkeypatch: pytest.MonkeyPatch) -> None:
    from headwaters.features import tray as tray_feature

    monkeypatch.setenv(tray_feature.DISABLE_ENV, "1")
    assert tray_feature.run() == 0


def test_a_test_process_never_starts_a_real_tray(monkeypatch: pytest.MonkeyPatch) -> None:
    # PYTEST_CURRENT_TEST is the daemon's guard; it returns before reading rules or spawning.
    from headwaters.daemon.service import Daemon

    monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/test_hdw504_tray.py::a_case (call)")
    daemon = Daemon.__new__(Daemon)
    daemon.tray_child = None
    assert daemon.start_tray() is None
    assert daemon.tray_child is None


def test_a_missing_daemon_disables_the_switches_but_not_quit() -> None:
    items = fake_menu(TrayState.daemon_down(dashboard_url=DASHBOARD), _NoActions())
    by_text = {item.text: item for item in items}
    assert by_text["daemon not running"].options["enabled"] is False
    for switch in ("Scope", "Private Mode", "Keys"):
        assert by_text[switch].options["enabled"] is False
    for always in ("Doctor", "Restart daemon", "Quit"):
        assert by_text[always].options["enabled"] is True


def test_the_menu_wraps_an_action_so_pystray_can_call_it_with_an_icon() -> None:
    calls: list[str] = []
    actions = _NoActions(record=calls)
    quit_item = fake_menu(TrayState(daemon_up=True), actions)[-1]
    assert isinstance(quit_item, FakeItem)
    quit_item(None)  # what pystray does: the platform calls the item with the icon
    assert calls == ["quit"]


# --------------------------------------------------------------------------- the actions


def test_a_scope_switch_posts_the_right_path_and_body() -> None:
    client, seen = fake_daemon()
    app, _, _ = app_with(client=client)
    app.refresh()
    app.set_scope("cloud")
    posts = [request for request in seen if request.method == "POST"]
    assert len(posts) == 1
    assert str(posts[0].url) == SCOPE_URL
    assert json.loads(posts[0].content) == {"key": "default_scope", "value": "cloud"}
    assert posts[0].headers["authorization"] == f"Bearer {TOKEN}"
    assert app.toasts == ["Scope: cloud"]


def test_the_private_switch_posts_the_opposite_of_what_is_in_force() -> None:
    client, seen = fake_daemon()
    app, _, _ = app_with(client=client)
    app.refresh()
    app.toggle_private()
    app.toggle_private()  # the state did not change between the two, so this asks for it again
    sent = [json.loads(request.content) for request in seen if request.method == "POST"]
    assert sent == [
        {"key": "private_lock", "value": True},
        {"key": "private_lock", "value": True},
    ]


def test_the_private_switch_asks_to_turn_it_off_when_it_is_on() -> None:
    status = status_payload()
    status["private_lock"] = True
    client, seen = fake_daemon(status=status)
    app, _, _ = app_with(client=client)
    app.refresh()
    app.toggle_private()
    sent = [json.loads(request.content) for request in seen if request.method == "POST"]
    assert sent == [{"key": "private_lock", "value": False}]


def test_a_refusal_keeps_the_old_value_and_says_why_in_the_daemons_own_words() -> None:
    client, _ = fake_daemon(scope={"ok": False, "key": "private_lock", "reason": "not editable"})
    app, icon, _ = app_with(client=client)
    app.refresh()
    app.toggle_private()
    assert app.toasts == ["not editable"]
    assert icon.notified == ["not editable"]
    assert app.state.private_lock is False


def test_every_keys_item_opens_the_dashboard_at_that_provider() -> None:
    app, _, opened = app_with()
    app.refresh()
    app.open_keys(None)
    for provider in ("openai", "ollama"):
        app.open_keys(provider)
    assert opened == [
        f"{DASHBOARD}?section=keys",
        f"{DASHBOARD}?section=keys#keys-openai",
        f"{DASHBOARD}?section=keys#keys-ollama",
    ]


def test_open_dashboard_opens_the_status_page_and_nothing_else() -> None:
    app, _, opened = app_with()
    app.refresh()
    app.open_dashboard()
    assert opened == [DASHBOARD]


def test_a_browser_that_will_not_open_is_said_out_loud_not_raised() -> None:
    app, _, _ = app_with()
    app.refresh()
    app._opener = lambda url: False  # type: ignore[method-assign]
    app.open_dashboard()
    assert app.toasts == ["could not open the dashboard in a browser"]


def test_doctor_reports_counts_only() -> None:
    app, _, _ = app_with(client=fake_daemon()[0])
    app._doctor = lambda: "doctor: every check passed"  # type: ignore[method-assign]
    app.refresh()
    app.doctor()
    assert app.toasts == ["doctor: every check passed"]


def test_restart_reports_and_reads_the_daemon_again() -> None:
    client, seen = fake_daemon()
    app, _, _ = app_with(client=client)
    app._restarter = lambda: "daemon restarted"  # type: ignore[method-assign]
    app.refresh()
    status_reads = sum(1 for request in seen if request.url.path == "/v1/status")
    app.restart()
    assert app.toasts == ["daemon restarted"]
    assert sum(1 for request in seen if request.url.path == "/v1/status") == status_reads + 1


def test_quit_stops_the_icon_and_never_asks_the_daemon_anything() -> None:
    client, seen = fake_daemon()
    app, icon, _ = app_with(client=client)
    app.refresh()
    seen.clear()
    app.quit()
    assert icon.stopped is True
    assert seen == []


# --------------------------------------------------------------------------- the refresh


def test_a_slow_tick_does_not_stack_the_next_one() -> None:
    release = threading.Event()
    entered = threading.Event()

    def handler(request: httpx.Request) -> httpx.Response:
        entered.set()
        release.wait(5)
        return httpx.Response(200, json=status_payload())

    client = TrayClient(
        http=httpx.Client(transport=httpx.MockTransport(handler)),
        info=lambda: INFO,
        token=lambda: TOKEN,
    )
    app, _, _ = app_with(client=client)
    worker = threading.Thread(target=app.refresh, daemon=True)
    worker.start()
    assert entered.wait(5)
    assert app.refresh() is False  # the tick in flight is not joined: it is skipped
    release.set()
    worker.join(5)
    assert not worker.is_alive()


def test_a_timeout_is_a_daemon_down_tick_and_never_a_dialog() -> None:
    client, _ = fake_daemon(timeout=True)
    app, icon, _ = app_with(client=client)
    assert app.refresh() is True
    assert app.state.daemon_up is False
    assert app.state.color == RED
    assert app.toasts == []
    assert icon.notified == []
    assert icon.update_calls >= 1


def test_a_missing_daemon_is_a_normal_state() -> None:
    client, seen = fake_daemon(down=True)
    app, icon, _ = app_with(client=client)
    app.refresh()
    assert seen == []  # nothing was asked: there is no daemon to ask
    assert app.state.daemon_up is False
    assert app.state.status_line == "daemon not running"
    items = app.menu().items
    assert items[0].text == "daemon not running"
    assert items[0].options.get("enabled") is False
    assert icon.notified == []


# --------------------------------------------------------------------------- the icon


@pytest.mark.parametrize(
    ("state", "expected_rgb"),
    [
        ({"keys_set": 2}, GREEN_RGB),
        ({"walls": ["budget"]}, AMBER_RGB),
        ({"keys_set": 0, "anonymous_provider": False}, AMBER_RGB),
        ({"last_status": "verify_failed"}, RED_RGB),
    ],
)
def test_the_centre_of_the_icon_is_the_colour_of_the_state(
    state: dict[str, Any], expected_rgb: tuple[int, int, int]
) -> None:
    reading = TrayState.from_payloads(status_payload(**state), {"providers": PROVIDERS})
    assert reading.color == {GREEN_RGB: GREEN, AMBER_RGB: AMBER, RED_RGB: RED}[expected_rgb]
    image = render(reading.color, private=reading.private_lock)
    assert isinstance(image, Image.Image)
    assert image.size == (64, 64)
    assert image.convert("RGBA").getpixel(CENTRE_PROBE)[:3] == expected_rgb


def test_a_daemon_that_is_not_there_is_a_red_icon() -> None:
    down = TrayState.daemon_down()
    assert down.color == RED
    assert render(down.color).convert("RGBA").getpixel(CENTRE_PROBE)[:3] == RED_RGB


@pytest.mark.parametrize("color", [GREEN, AMBER, RED])
def test_private_mode_draws_the_purple_lock_over_whichever_colour_is_current(color: str) -> None:
    plain = render(color).convert("RGBA")
    locked = render(color, private=True).convert("RGBA")
    base = {"green": GREEN_RGB, "amber": AMBER_RGB, "red": RED_RGB}[color]
    assert plain.getpixel(LOCK_PROBE)[:3] == base  # no lock: the disc itself
    assert locked.getpixel(LOCK_PROBE)[:3] == PURPLE_RGB  # lock body, drawn over the disc
    # Amber with a purple lock is a real state, and the base colour still shows at the centre.
    assert locked.getpixel(CENTRE_PROBE)[:3] == base


def test_amber_with_the_purple_lock_is_a_real_state() -> None:
    status = status_payload(walls=["budget"])
    status["private_lock"] = True
    reading = TrayState.from_payloads(status, {"providers": PROVIDERS})
    assert reading.color == AMBER
    assert reading.private_lock is True
    assert reading.status_line.startswith("Local · Private Mode")


# --------------------------------------------------------------------------- no secrets, ever


def _labels(items: Any) -> list[str]:
    found: list[str] = []
    for item in items:
        if isinstance(item, FakeItem):
            found.append(item.text)
            if isinstance(item.action, FakeMenu):
                found.extend(_labels(item.action))
        elif isinstance(item, FakeMenu):
            found.extend(_labels(item))
    return found


def _tray_source() -> str:
    directory = Path(__file__).resolve().parent.parent / "headwaters" / "features" / "tray"
    return "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(directory.glob("*.py"))
    ).lower()


def _store_with(**secrets: str) -> tuple[KeyStore, Registry]:
    catalog: Catalog = catalog_with(
        entry(name="acmefake", env_var=ACMEAKE_ENV),
        entry(name="zeta", env_var="ZETA_API_KEY"),
    )
    registry = Registry.from_catalog(catalog)
    store = KeyStore(Rules(), env={}, backend=FakeKeyring(), registry=registry)
    for alias, provider in secrets.items():
        store.save(alias, SECRET, provider=provider)
    return store, registry


def test_no_variable_name_or_secret_appears_in_any_label_or_toast() -> None:
    store, registry = _store_with(work="acmefake")
    client, _ = fake_daemon(keys={"ok": True, "providers": provider_items(store)})
    app, icon, opened = app_with(client=client)
    app.refresh()
    labels = _labels(app.menu())
    for provider in app.state.providers:
        app.open_keys(provider.provider)
    seen_text = " ".join(labels + app.toasts + [icon.title])

    # The variables come from the registry itself, never retyped here.
    variables = {p.env_var for p in registry} | set(STANDARD_ENV.values())
    for variable in variables:
        assert variable.lower() not in seen_text.lower()
    assert SECRET not in seen_text
    assert "sk-" not in seen_text
    assert "http" not in " ".join(labels + app.toasts)  # a URL belongs in the browser, not a label
    assert all(url.startswith(DASHBOARD) for url in opened)


def test_a_new_registry_provider_becomes_a_keys_item_with_no_change_to_the_tray() -> None:
    store, registry = _store_with()
    client, _ = fake_daemon(keys={"ok": True, "providers": provider_items(store)})
    app, _, _ = app_with(client=client)
    app.refresh()
    labels = _labels(app.menu())
    assert "acmefake \u2014 not set" in labels
    assert "zeta \u2014 not set" in labels
    assert "Manage keys\u2026" in labels
    assert [row.provider for row in app.state.providers] == [p.name for p in store.providers()]
    assert len(registry) == 2


def test_the_tray_module_names_no_provider_of_its_own() -> None:
    source = _tray_source()
    names = {provider.name for provider in default_registry()} | set(STANDARD_ENV)
    for name in names:
        assert name.lower() not in source


# --------------------------------------------------------------------------- the CLI


def test_headwaters_tray_with_the_flag_off_says_how_to_turn_it_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(paths.HOME_ENV_VAR, str(tmp_path))
    config = tmp_path / "config"
    config.mkdir()
    (config / "rules.yaml").write_text(
        f"version: 1\nfeatures:\n  {FEATURE_ID}: false\n", encoding="utf-8"
    )
    result = CliRunner().invoke(cli_app, ["tray"])
    assert result.exit_code == 0
    assert f"features: {{{FEATURE_ID}: true}}" in result.output


def test_headwaters_tray_runs_the_tray_when_the_flag_is_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from headwaters.features import tray as tray_feature

    monkeypatch.setenv(paths.HOME_ENV_VAR, str(tmp_path))
    config = tmp_path / "config"
    config.mkdir()
    (config / "rules.yaml").write_text("version: 1\n", encoding="utf-8")
    monkeypatch.setattr(tray_feature, "run", lambda: 0)
    result = CliRunner().invoke(cli_app, ["tray"])
    assert result.exit_code == 0


class _NoActions:
    """A TrayActions that records instead of acting."""

    def __init__(self, record: list[str] | None = None) -> None:
        self.calls = record if record is not None else []

    def set_scope(self, scope: str) -> None:
        self.calls.append(f"scope:{scope}")

    def toggle_private(self) -> None:
        self.calls.append("private")

    def open_keys(self, provider: str | None = None) -> None:
        self.calls.append(f"keys:{provider}")

    def open_dashboard(self) -> None:
        self.calls.append("dashboard")

    def doctor(self) -> None:
        self.calls.append("doctor")

    def restart(self) -> None:
        self.calls.append("restart")

    def quit(self) -> None:
        self.calls.append("quit")
