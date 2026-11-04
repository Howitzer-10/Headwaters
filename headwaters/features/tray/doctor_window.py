"""The Doctor window (HDW-504 follow-up): a real window that says what to do.

The tray's Doctor action used to open a native message box whose body was ``title: status``
lines - ``Ollama: warn``, ``Private Mode: skip`` - which says nothing a person can act on and
counted "not applicable" as a problem. This module turns a :class:`headwaters.control.doctor.Report`
into the payload that window shows: one plain-English headline, and entries grouped into what
needs doing and what is optional, each entry carrying the check's own ``detail`` and ``fix``.

``tkinter`` is the standard library, so this adds no dependency. It is imported inside
:func:`show` only: importing this module must work on any platform, and a test asserts on
:func:`_content` - the exact lines the window inserts - without creating a display.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from headwaters.control.doctor import Report

__all__ = [
    "ALL_CLEAR",
    "GROUP_LABELS",
    "NEEDS_DOING",
    "OPTIONAL",
    "OPTIONAL_IDS",
    "DoctorEntry",
    "DoctorView",
    "show",
    "summarise",
]

NEEDS_DOING = "needs_doing"
OPTIONAL = "optional"
GROUP_LABELS = {
    NEEDS_DOING: "Needs doing",
    OPTIONAL: "Optional / not set up",
}
WINDOW_TITLE = "Headwaters doctor"
ALL_CLEAR = "Everything checks out. Nothing needs doing."
# Checks that cannot stop a request working, so a warn on one is a note and not a problem:
# repo hygiene (git), the optional third-party editor configs (host_configs), stale catalog
# metadata (catalog), a preferred port the daemon routes around (ports), and Ollama tuning
# (ollama_env). Every other id - including one added later - is a real problem, so a new check
# is never silently demoted.
OPTIONAL_IDS = frozenset({"git", "host_configs", "catalog", "ports", "ollama_env"})


@dataclass(frozen=True)
class DoctorEntry:
    """One row of the window: what was found, and what the person can do about it."""

    id: str
    title: str
    group: str
    detail: str
    fix: str | None = None


@dataclass(frozen=True)
class DoctorView:
    """The whole payload handed to the window renderer, and to a test's fake."""

    headline: str
    entries: tuple[DoctorEntry, ...] = ()

    @property
    def needs_doing(self) -> tuple[DoctorEntry, ...]:
        return tuple(entry for entry in self.entries if entry.group == NEEDS_DOING)

    @property
    def optional(self) -> tuple[DoctorEntry, ...]:
        return tuple(entry for entry in self.entries if entry.group == OPTIONAL)

    @property
    def all_clear(self) -> bool:
        """True when nothing actually needs doing, whatever optional notes exist."""
        return not self.needs_doing

    def __str__(self) -> str:
        """The headline only: the ``doctor finished: ...`` line in the tray log stays short."""
        return self.headline

    def text(self) -> str:
        """The payload as plain text: the dialog fallback when the window cannot open."""
        if self.all_clear:
            return ALL_CLEAR
        lines = [self.headline]
        for group in (NEEDS_DOING, OPTIONAL):
            entries = [entry for entry in self.entries if entry.group == group]
            if not entries:
                continue
            lines.append("")
            lines.append(GROUP_LABELS[group])
            for entry in entries:
                lines.append(f"- {entry.title}: {entry.detail}")
                if entry.fix:
                    lines.append(f"  What to do: {entry.fix}")
        return "\n".join(lines)


def summarise(report: Report) -> DoctorView:
    """A doctor report as the window shows it: skips dropped, problems before optional notes."""
    fixes = dict(report.fixes)
    entries: list[DoctorEntry] = []
    for check in report.checks:
        # "skip" means "not applicable" (Private Mode off, no keys saved), never a problem.
        if check.status in {"ok", "skip"}:
            continue
        entries.append(
            DoctorEntry(
                id=check.id,
                title=check.title,
                group=OPTIONAL if check.id in OPTIONAL_IDS else NEEDS_DOING,
                detail=check.detail or check.title,
                fix=check.fix or fixes.get(check.title),
            )
        )
    # Stable sort: the report's own order survives inside each group.
    entries.sort(key=lambda entry: entry.group != NEEDS_DOING)
    return DoctorView(_headline(entries), tuple(entries))


def _headline(entries: list[DoctorEntry]) -> str:
    count = sum(1 for entry in entries if entry.group == NEEDS_DOING)
    if not count:
        return ALL_CLEAR
    if count == 1:
        return "1 thing needs a look"
    return f"{count} things need a look"


def _content(view: DoctorView) -> tuple[tuple[str, str], ...]:
    """The window's lines and their style tags, built without a display so a test can read them.

    ``group_needs`` and ``group_optional`` are separate tags because the two groups must look
    different, not merely appear in a different place.
    """
    if view.all_clear:
        return ((ALL_CLEAR, "headline"),)
    rows: list[tuple[str, str]] = [(view.headline, "headline")]
    for group in (NEEDS_DOING, OPTIONAL):
        entries = [entry for entry in view.entries if entry.group == group]
        if not entries:
            continue
        rows.append(("", "blank"))
        tag = "group_needs" if group == NEEDS_DOING else "group_optional"
        rows.append((GROUP_LABELS[group], tag))
        for entry in entries:
            rows.append((entry.title, "title"))
            rows.append((entry.detail, "detail"))
            if entry.fix:
                rows.append((f"What to do: {entry.fix}", "fix"))
    return tuple(rows)


def show(view: DoctorView) -> None:
    """Open the window and block until it closes.

    Called on the tray action's worker thread, never on pystray's message loop: tkinter owns its
    own window and its own ``mainloop``, and blocking that loop would freeze the menu. The window
    comes to the front when it opens, and ``Esc`` and the close button both destroy it.
    """
    import tkinter as tk
    import tkinter.font as tkfont

    root = tk.Tk()
    root.title(WINDOW_TITLE)
    root.protocol("WM_DELETE_WINDOW", root.destroy)
    root.bind("<Escape>", lambda _event: root.destroy())

    buttons = tk.Frame(root, padx=14, pady=(0, 12))
    buttons.pack(side="bottom", fill="x")
    tk.Button(buttons, text="Close", width=10, command=root.destroy).pack(side="right")

    frame = tk.Frame(root, padx=14, pady=(12, 8))
    frame.pack(fill="both", expand=True)
    text = tk.Text(
        frame,
        wrap="word",
        width=76,
        height=18,
        relief="flat",
        borderwidth=0,
        padx=8,
        pady=6,
    )
    scrollbar = tk.Scrollbar(frame, orient="vertical", command=text.yview)
    text.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side="right", fill="y")
    text.pack(side="left", fill="both", expand=True)

    body = tkfont.nametofont("TkDefaultFont")
    headline_font = body.copy()
    headline_font.configure(size=max(16, int(body.cget("size")) + 6), weight="bold")
    bold_font = body.copy()
    bold_font.configure(weight="bold")
    text.tag_configure("headline", font=headline_font, spacing3=2)
    text.tag_configure("blank", spacing1=8)
    text.tag_configure("group_needs", font=bold_font, foreground="#9a1b1b", spacing1=4, spacing3=4)
    text.tag_configure(
        "group_optional", font=bold_font, foreground="#6b6b6b", spacing1=4, spacing3=4
    )
    text.tag_configure("title", font=bold_font, spacing1=10)
    text.tag_configure("detail", lmargin1=14, lmargin2=14, spacing1=2)
    text.tag_configure("fix", lmargin1=14, lmargin2=14, spacing1=2, foreground="#14532d")

    for line, tag in _content(view):
        text.insert("end", line + "\n", tag)
    text.configure(state="disabled")

    def _wheel(event: Any) -> str:
        text.yview_scroll(-int(event.delta / 120) * 3, "units")
        return "break"

    text.bind("<MouseWheel>", _wheel)

    root.minsize(560, 380)
    root.update_idletasks()
    width = max(560, root.winfo_reqwidth())
    height = max(380, root.winfo_reqheight())
    x = max(0, (root.winfo_screenwidth() - width) // 2)
    y = max(0, (root.winfo_screenheight() - height) // 3)
    root.geometry(f"{width}x{height}+{x}+{y}")
    # Topmost for a moment, then released: the window arrives in front without staying above
    # everything else the person then opens.
    root.lift()
    root.attributes("-topmost", True)
    root.after(300, lambda: root.attributes("-topmost", False))
    root.focus_force()
    try:
        root.mainloop()
    finally:
        # The close button, Esc and the window manager all destroy it; destroying it twice is
        # the normal case here, not an error.
        with contextlib.suppress(tk.TclError):
            root.destroy()
