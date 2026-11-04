"""The tray app itself: a 2-second refresh, the menu, and the actions behind it (HDW-504).

Everything that would touch the outside world is injected: the icon object, ``opener(url)``,
the notifier, the doctor and restart callables, and the client. The tests therefore drive the
whole app - every action, every label, the icon colour - without a notification area, a browser
or a real daemon.
"""

from __future__ import annotations

import threading
import webbrowser
from collections.abc import Callable
from typing import Any

from headwaters.features.tray import TITLE
from headwaters.features.tray.client import TrayClient
from headwaters.features.tray.icon import render
from headwaters.features.tray.menu import MenuEntry, build_menu, menu_entries
from headwaters.features.tray.model import TrayState

__all__ = ["APP_ID", "DEFAULT_INTERVAL_S", "TrayApp"]

APP_ID = "headwaters.tray"
DEFAULT_INTERVAL_S = 2.0
# The dashboard routes: the status page reads ``?section=keys`` and anchors each provider (HDW-503).
KEYS_SECTION = "?section=keys"
# A fake icon in a test has no title, notify or update_menu; none of them is worth an exception.
_ICON_ERRORS = (AttributeError, RuntimeError, OSError)


class TrayApp:
    def __init__(
        self,
        *,
        icon: Any = None,
        opener: Callable[[str], bool] | None = None,
        notifier: Callable[[str], None] | None = None,
        doctor: Callable[[], str] | None = None,
        restarter: Callable[[], str] | None = None,
        client: TrayClient | None = None,
        interval: float = DEFAULT_INTERVAL_S,
        builder: Callable[[tuple[MenuEntry, ...]], Any] | None = None,
    ) -> None:
        self._icon = icon
        self._opener = opener if opener is not None else webbrowser.open
        self._notifier = notifier
        self._doctor = doctor if doctor is not None else _run_doctor
        self._restarter = restarter if restarter is not None else _restart_daemon
        self._client = client if client is not None else TrayClient()
        self._interval = interval
        # Building the menu imports pystray; a test passes a builder so nothing headless needs it.
        self._builder = builder if builder is not None else build_menu
        self._state = TrayState.daemon_down()
        self._lock = threading.Lock()
        self._stopped = threading.Event()
        self._thread: threading.Thread | None = None
        # Every toast this tray raised, in order: what a test asserts on (no secrets in any).
        self.toasts: list[str] = []

    @property
    def state(self) -> TrayState:
        return self._state

    def run(self) -> int:
        """Show the icon and refresh until Quit. Blocking, like ``pystray.Icon.run``."""
        if self._icon is None:
            self._icon = _make_icon(self.menu())
        self.refresh()
        self._thread = threading.Thread(target=self._loop, name="tray-refresh", daemon=True)
        self._thread.start()
        try:
            self._icon.run()
        finally:
            self._stopped.set()
        return 0

    def refresh(self) -> bool:
        """One tick. False when a refresh is already in flight: ticks never stack."""
        if not self._lock.acquire(blocking=False):
            return False
        try:
            try:
                state = self._client.fetch()
            except Exception:  # a broken client is a missing daemon, never a dialog
                state = TrayState.daemon_down()
            self._show(state)
            return True
        finally:
            self._lock.release()

    def menu(self) -> Any:
        return self._builder(menu_entries(self._state, self))

    # ------------------------------------------------------------------ actions

    def set_scope(self, scope: str) -> None:
        self._write("default_scope", scope, f"Scope: {scope}")

    def toggle_private(self) -> None:
        wanted = not self._state.private_lock
        self._write("private_lock", wanted, f"Private Mode: {'on' if wanted else 'off'}")

    def open_dashboard(self) -> None:
        url = self._state.dashboard_url
        if url and not self._opener(url):
            self._notify("could not open the dashboard in a browser")

    def open_keys(self, provider: str | None = None) -> None:
        url = self._state.dashboard_url
        if not url:
            return
        target = (
            f"{url}{KEYS_SECTION}" if provider is None else f"{url}{KEYS_SECTION}#keys-{provider}"
        )
        if not self._opener(target):
            self._notify("could not open the dashboard in a browser")

    def doctor(self) -> None:
        try:
            self._notify(self._doctor())
        except Exception:  # the doctor is a convenience; its failure is not the tray's
            self._notify("doctor could not run")

    def restart(self) -> None:
        try:
            self._notify(self._restarter())
        except Exception:
            self._notify("could not restart the daemon")
        self.refresh()

    def quit(self) -> None:
        self._stopped.set()
        if self._icon is not None:
            self._icon.stop()

    # ------------------------------------------------------------------ internals

    def _loop(self) -> None:
        while not self._stopped.wait(self._interval):
            self.refresh()

    def _write(self, key: str, value: bool | str, done: str) -> None:
        reply = self._client.set_setting(key, value)
        if reply is None:
            self._notify("the daemon is not running")
        elif reply.get("ok"):
            self._notify(done)
        else:
            # The daemon refused, so the old value stays; say why, in its own words.
            self._notify(str(reply.get("reason") or "the daemon kept its previous value"))
        self.refresh()

    def _show(self, state: TrayState) -> None:
        self._state = state
        icon = self._icon
        if icon is None:
            return
        try:
            icon.icon = render(state.color, private=state.private_lock)
            icon.title = f"{TITLE} - {state.status_line}"
            icon.menu = self.menu()
            icon.update_menu()
        except _ICON_ERRORS:
            return

    def _notify(self, message: str) -> None:
        """A toast, and a record of it: no key, masked value, variable name or URL goes in."""
        self.toasts.append(message)
        if self._notifier is not None:
            self._notifier(message)
            return
        icon = self._icon
        if icon is not None:
            try:
                icon.notify(message, TITLE)
            except _ICON_ERRORS:
                return


def _make_icon(menu: Any) -> Any:
    """The real thing. Imported here so nothing headless needs a notification area."""
    import pystray

    return pystray.Icon(APP_ID, render(TrayState().color), TITLE, menu)


def _run_doctor() -> str:
    """``headwaters doctor``, in this process: the same checks, the same code path."""
    from headwaters.cli.runtime import load_rules_or_exit
    from headwaters.control.doctor import run_doctor

    report = run_doctor(load_rules_or_exit())
    # Counts only: a check's detail can carry a path, and a toast is not the place for it.
    attention = sum(1 for check in report.checks if check.status != "ok")
    if not attention:
        return "doctor: every check passed"
    return f"doctor: {attention} check(s) need attention - run headwaters doctor"


def _restart_daemon() -> str:
    """Stop the daemon and start it again: the same two calls the CLI makes (HDW-201)."""
    from headwaters.core.daemon_control import start_daemon, stop_daemon

    stop_daemon(timeout_s=20)
    started = start_daemon(timeout_s=45)
    return "daemon restarted" if getattr(started, "ok", False) else "the daemon did not start"
