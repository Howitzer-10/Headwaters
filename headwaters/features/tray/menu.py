"""The tray menu, as data (HDW-504).

Exactly these items, in this order: the status line, Scope, Private Mode, Keys, Open dashboard,
Doctor, Start daemon, Restart daemon, Quit. Building the real ``pystray.Menu`` is one call at the
end, so the menu can be tested - and every label with it - without a notification area, and
without pystray being imported at all.

Start exists because a red icon (no daemon) used to offer only Restart, which is the wrong word
for starting something that is not there: Start is enabled while the daemon is down and greyed
out while it is up.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, get_args

from headwaters.core.rules import Scope
from headwaters.features.tray.model import TrayState

__all__ = ["SCOPES", "MenuEntry", "TrayActions", "build_menu", "menu_entries"]

# The scopes come from the rules model itself, so the menu cannot drift from what routes.
SCOPES: tuple[str, ...] = get_args(Scope)
EM_DASH = "\u2014"


class TrayActions(Protocol):
    """What the menu calls. ``TrayApp`` in production; a fake in the tests."""

    def set_scope(self, scope: str) -> None: ...

    def toggle_private(self) -> None: ...

    def open_keys(self, provider: str | None = None) -> None: ...

    def open_dashboard(self) -> None: ...

    def doctor(self) -> None: ...

    def start(self) -> None: ...

    def restart(self) -> None: ...

    def quit(self) -> None: ...


@dataclass(frozen=True)
class MenuEntry:
    """One menu item, in the terms pystray understands."""

    text: str
    action: Callable[[], None] | None = None
    enabled: bool = True
    checked: bool | None = None
    radio: bool = False
    default: bool = False
    children: tuple[MenuEntry, ...] = ()


def menu_entries(state: TrayState, actions: TrayActions) -> tuple[MenuEntry, ...]:
    """The whole menu for one reading of the daemon."""
    up = state.daemon_up
    scope_children = tuple(
        MenuEntry(
            text=scope,
            action=_call(actions.set_scope, scope),
            enabled=up,
            checked=state.scope == scope,
            radio=True,
        )
        for scope in SCOPES
    )
    # A provider is whatever the registry calls it: no display name, no rewriting, no table.
    key_children: list[MenuEntry] = [
        MenuEntry(
            text=f"{row.provider} {EM_DASH} {'set' if row.is_set else 'not set'}",
            action=_call(actions.open_keys, row.provider),
            enabled=up,
        )
        for row in state.providers
    ]
    key_children.append(
        MenuEntry(
            text="Manage keys\u2026",
            action=_call(actions.open_keys, None),
            enabled=up,
        )
    )
    return (
        MenuEntry(text=state.status_line, enabled=False),
        MenuEntry(text="Scope", enabled=up, children=scope_children),
        MenuEntry(
            text="Private Mode",
            action=actions.toggle_private,
            enabled=up,
            checked=state.private_lock,
        ),
        MenuEntry(text="Keys", enabled=up, children=tuple(key_children)),
        MenuEntry(
            text="Open dashboard",
            action=actions.open_dashboard,
            enabled=bool(state.dashboard_url),
            default=True,
        ),
        MenuEntry(text="Doctor", action=actions.doctor),
        # A red icon means there is nothing to restart: Start is the honest word, and it is
        # greyed out once a daemon is already answering.
        MenuEntry(text="Start daemon", action=actions.start, enabled=not up),
        MenuEntry(text="Restart daemon", action=actions.restart),
        MenuEntry(text="Quit", action=actions.quit),
    )


def build_menu(
    entries: Sequence[MenuEntry],
    *,
    menuitem: Callable[..., Any] | None = None,
    menu: Callable[..., Any] | None = None,
) -> Any:
    """Turn the entries into a ``pystray.Menu`` (or into whatever a test passes in).

    pystray is imported here, not at module import: a headless ``--collect-only`` must not need a
    notification area to exist.
    """
    if menuitem is None or menu is None:
        import pystray

        menuitem = menuitem or pystray.MenuItem
        menu = menu or pystray.Menu
    built = []
    for entry in entries:
        if entry.children:
            # A submenu is an item whose action is a menu: pystray keeps the parent's label that
            # way, and the parent is still the item the platform greys out.
            built.append(
                menuitem(
                    entry.text,
                    build_menu(entry.children, menuitem=menuitem, menu=menu),
                    enabled=entry.enabled,
                )
            )
            continue
        built.append(
            menuitem(
                entry.text,
                None if entry.action is None else _bind(entry.action),
                checked=_checked(entry.checked),
                radio=entry.radio,
                default=entry.default,
                enabled=entry.enabled,
            )
        )
    return menu(*built)


def _checked(value: bool | None) -> Callable[[Any], bool] | None:
    """``checked`` as pystray demands it: None or a callable, never a bare bool (HDW-504).

    ``pystray.MenuItem`` rejects anything but None or a callable, and ``True`` raises. A menu
    rebuilt on every refresh needs the current value, so the callable answers with the value this
    reading of the daemon produced.
    """
    if value is None:
        return None

    def current(_item: Any) -> bool:
        return value

    return current


def _bind(action: Callable[[], None]) -> Callable[[Any, Any], None]:
    """``(icon, item)`` -> ``action()``: exactly two parameters.

    pystray reads ``co_argcount`` to decide whether an action fits, and it counts a keyword
    default as one, so ``lambda _icon, _item, call=action: call()`` is rejected (HDW-504).
    """

    def bound(_icon: Any, _item: Any) -> None:
        action()

    return bound


def _call(method: Callable[..., None], argument: Any) -> Callable[[], None]:
    return lambda: method(argument)
