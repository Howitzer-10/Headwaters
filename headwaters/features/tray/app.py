"""The tray app itself: a 2-second refresh, the menu, and the actions behind it (HDW-504).

Everything that would touch the outside world is injected: the icon object, ``opener(url)``,
the notifier, the doctor and restart callables, the dialog, the log writer and the client. The
tests therefore drive the whole app - every action, every label, the icon colour - without a
notification area, a browser, a message box or a real daemon.

The doctor and restart actions run on a worker thread and report in a native message box, not
only a balloon tip: a toast is not durable on Windows 11, and a report nobody sees is no report.
"""

from __future__ import annotations

import os
import threading
import webbrowser
from collections.abc import Callable
from typing import Any

from headwaters.features.tray import TITLE, log_line
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
# A message box truncates, and a dialog is a summary: keep the body short and free of detail.
DIALOG_MAX_CHARS = 2000
# How many non-ok checks the body lists before it says how many are left.
DIALOG_MAX_CHECK_LINES = 20
DOCTOR_TITLE = "Headwaters doctor"
RESTART_TITLE = "Headwaters daemon"


class TrayApp:
    def __init__(
        self,
        *,
        icon: Any = None,
        opener: Callable[[str], bool] | None = None,
        notifier: Callable[[str], None] | None = None,
        doctor: Callable[[], str] | None = None,
        restarter: Callable[[], str] | None = None,
        dialog: Callable[[str, str], None] | None = None,
        logger: Callable[[str], None] | None = None,
        client: TrayClient | None = None,
        interval: float = DEFAULT_INTERVAL_S,
        builder: Callable[[tuple[MenuEntry, ...]], Any] | None = None,
    ) -> None:
        self._icon = icon
        self._opener = opener if opener is not None else webbrowser.open
        self._notifier = notifier
        self._doctor = doctor if doctor is not None else _run_doctor
        self._restarter = restarter if restarter is not None else _restart_daemon
        # dialog(title, message) where the platform has a real one; else the toast is all there is.
        self._dialog = dialog if dialog is not None else _default_dialog()
        self._logger = logger if logger is not None else log_line
        self._client = client if client is not None else TrayClient()
        self._interval = interval
        # Building the menu imports pystray; a test passes a builder so nothing headless needs it.
        self._builder = builder if builder is not None else build_menu
        self._state = TrayState.daemon_down()
        self._lock = threading.Lock()
        self._action_lock = threading.Lock()
        self._action_thread: threading.Thread | None = None
        self._action_name: str | None = None
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
        """The checks, off pystray's message loop, and the report in a real dialog (HDW-504).

        ``_run_doctor`` shells out for the live host checks, so running it on the message loop
        froze the menu with no acknowledgement - and the toast that eventually followed was a
        balloon tip Windows 11 drops. The worker keeps the icon responsive; the started/finished
        lines in the tray log survive the dialog too.
        """
        self._start_action("doctor", DOCTOR_TITLE, self._doctor, "doctor could not run")

    def restart(self) -> None:
        """Stop and start the daemon off the message loop: that is tens of seconds of waiting."""
        self._start_action(
            "restart daemon",
            RESTART_TITLE,
            self._restart_and_refresh,
            "could not restart the daemon",
        )

    def wait_for_action(self, timeout_s: float = 5.0) -> bool:
        """Join the worker an action started. False if it is still running after ``timeout_s``."""
        worker = self._action_thread
        if worker is None:
            return True
        worker.join(timeout_s)
        return not worker.is_alive()

    def quit(self) -> None:
        self._stopped.set()
        if self._icon is not None:
            self._icon.stop()

    # ------------------------------------------------------------------ internals

    def _loop(self) -> None:
        while not self._stopped.wait(self._interval):
            self.refresh()

    def _start_action(
        self,
        name: str,
        title: str,
        work: Callable[[], str],
        failure: str,
    ) -> None:
        """One slow action on its own worker, never on pystray's message loop.

        The guard is taken here and released by the worker, so a second click while the first
        is still running is dropped instead of stacking a second dialog on the first and racing
        the same log lines.
        """
        if not self._action_lock.acquire(blocking=False):
            self._log(f"{name}: ignored, {self._action_name or 'an action'} is still running")
            return
        worker = threading.Thread(
            target=self._run_action,
            name=f"tray-{name.replace(' ', '-')}",
            args=(name, title, work, failure),
            daemon=True,
        )
        self._action_name = name
        self._action_thread = worker
        try:
            worker.start()
        except RuntimeError:
            self._action_name = None
            self._action_thread = None
            self._action_lock.release()
            raise

    def _run_action(
        self,
        name: str,
        title: str,
        work: Callable[[], str],
        failure: str,
    ) -> None:
        try:
            # The order is deliberate. A started line and no finished line means the action hung;
            # no started line at all means the menu item never fired. The result is logged before
            # the dialog, so it survives a dialog that never opens.
            self._log(f"{name} started")
            try:
                message = work()
            except Exception:  # a convenience action's failure is never the tray's
                message = failure
            self._log(f"{name} finished: {message}")
            self._tell(title, message)
        finally:
            self._action_name = None
            self._action_thread = None
            self._action_lock.release()

    def _restart_and_refresh(self) -> str:
        try:
            return self._restarter()
        finally:
            self.refresh()

    def _log(self, line: str) -> None:
        try:
            self._logger(line)
        except Exception:  # a log is a diagnostic; it never breaks the action it describes
            return

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
        self._toast(message)

    def _tell(self, title: str, message: str) -> None:
        """A report where a person will actually see it: a dialog, else the old toast.

        Every message is still recorded in ``toasts`` - a dialog that opens, a dialog that
        cannot, and a toast all do the same. The body is clipped for the dialog only: a message
        box truncates itself, and clipping here keeps the cut predictable.
        """
        self.toasts.append(message)
        if self._dialog is not None:
            try:
                self._dialog(title, _clip(message, DIALOG_MAX_CHARS))
                return
            except Exception:  # no dialog here: fall back to the notification this always used
                pass
        self._toast(message)

    def _toast(self, message: str) -> None:
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


def _default_dialog() -> Callable[[str, str], None] | None:
    """The native dialog, where there is one: Windows only.

    ``ctypes.windll`` exists on Windows and nowhere else, so this checks before handing back the
    callable. A non-Windows box, or a ``ctypes`` without ``windll``, gets None and the toast.
    """
    if os.name != "nt":
        return None
    try:
        import ctypes

        if not hasattr(ctypes, "windll"):
            return None
    except Exception:  # an import that fails must not take the tray down at startup
        return None
    return _message_box


def _message_box(title: str, message: str) -> None:
    """A native Windows message box, and no new dependency.

    Raises rather than returning quietly when the platform has no message box or the call
    fails, so ``_tell`` knows to fall back to the notification path. A worker thread is fine:
    the box creates and owns its own window and blocks only that thread.
    """
    import ctypes

    windll = getattr(ctypes, "windll", None)
    if windll is None:
        raise OSError("no message box on this platform")
    MB_OK = 0x00000000
    MB_ICONINFORMATION = 0x00000040
    MB_SETFOREGROUND = 0x00010000
    MB_TOPMOST = 0x00040000
    shown = windll.user32.MessageBoxW(
        None, message, title, MB_OK | MB_ICONINFORMATION | MB_SETFOREGROUND | MB_TOPMOST
    )
    if shown == 0:
        raise OSError("MessageBoxW failed")


def _clip(text: str, limit: int) -> str:
    """``text`` at most ``limit`` characters, ending in an ellipsis when it was cut."""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _run_doctor() -> str:
    """``headwaters doctor``, in this process: the same checks, the same code path."""
    from headwaters.cli.runtime import load_rules_or_exit
    from headwaters.control.doctor import run_doctor

    return _doctor_summary(run_doctor(load_rules_or_exit()))


def _doctor_summary(report: Any) -> str:
    """The report in the few lines a person reads in a dialog, not the whole report.

    One ``name: status`` line per check that is not ``ok`` and a final count. No ``detail``:
    a detail can carry a filesystem path, a prompt or a masked key, and neither a dialog nor a
    toast is the place for one. ``run_doctor`` already scrubs; this narrows it further.
    """
    attention = [check for check in report.checks if check.status != "ok"]
    if not attention:
        return "doctor: every check passed"
    lines = [f"{check.title}: {check.status}" for check in attention[:DIALOG_MAX_CHECK_LINES]]
    hidden = len(attention) - len(lines)
    if hidden:
        lines.append(f"... and {hidden} more")
    noun = "check" if len(attention) == 1 else "checks"
    return "\n".join([*lines, "", f"{len(attention)} {noun} need attention"])


def _restart_daemon() -> str:
    """Stop the daemon and start it again: the same two calls the CLI makes (HDW-201)."""
    from headwaters.core.daemon_control import start_daemon, stop_daemon

    stop_daemon(timeout_s=20)
    started = start_daemon(timeout_s=45)
    return "daemon restarted" if getattr(started, "ok", False) else "the daemon did not start"
