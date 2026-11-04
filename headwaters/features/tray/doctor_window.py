"""The Doctor window (HDW-504 follow-up): a real window that says what to do.

The tray's Doctor action used to open a native message box whose body was ``title: status``
lines - ``Ollama: warn``, ``Private Mode: skip`` - which says nothing a person can act on and
counted "not applicable" as a problem. This module turns a :class:`headwaters.control.doctor.Report`
into the payload that window shows: one plain-English headline, then one bold title, one sentence
saying what was found and why it matters, and one sentence saying what to do.

The checks' own ``detail`` and ``fix`` strings are written for ``headwaters doctor`` and the log,
not for this window: they name ports, Windows error codes and config keys. :data:`PLAIN_CHECKS`
translates the checks an ordinary person can meet. Anything not in that table falls back to the
check's own words rather than disappearing, so a check added later always reaches the window; the
raw strings for every check still go to the tray log and the CLI.

Checks that only concern whoever develops Headwaters (git line endings, the hosts' own config
files) are collected into one quiet ``Developer notes (N)`` line instead of full entries.

``tkinter`` is the standard library, so this adds no dependency. It is imported inside
:func:`show` only: importing this module must work on any platform, and a test asserts on
:func:`_content` - the exact lines the window inserts - without creating a display.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from headwaters.control.doctor import Check, Report

__all__ = [
    "ALL_CLEAR",
    "DEVELOPER",
    "DEVELOPER_IDS",
    "DEVELOPER_LABEL",
    "GROUP_LABELS",
    "NEEDS_DOING",
    "OPTIONAL",
    "OPTIONAL_IDS",
    "PLAIN_CHECKS",
    "DoctorEntry",
    "DoctorView",
    "PlainCheck",
    "show",
    "summarise",
]

NEEDS_DOING = "needs_doing"
OPTIONAL = "optional"
DEVELOPER = "developer"
GROUP_LABELS = {
    NEEDS_DOING: "Needs doing",
    OPTIONAL: "Optional / not set up",
}
WINDOW_TITLE = "Headwaters doctor"
ALL_CLEAR = "Everything checks out. Nothing needs doing."
# Checks that cannot stop a request working, so a warn on one is a note and not a problem: the
# hosts' own config (host_configs), stale catalog metadata (catalog), a preferred port the daemon
# routes around (ports), and Ollama tuning (ollama_env). Every other id - including one added
# later - is a real problem, so a new check is never silently demoted.
OPTIONAL_IDS = frozenset({"git", "host_configs", "catalog", "ports", "ollama_env"})
# A subset of the above that is only interesting to whoever develops Headwaters. Their findings
# get one quiet summary line at the bottom, never a full entry, and never count as "to check".
DEVELOPER_IDS = frozenset({"git", "host_configs"})
DEVELOPER_LABEL = "Developer notes ({count}) - details are in the log."
# The display order, and the sort rank: a real problem first, then notes, then the dev footer.
_GROUP_ORDER = (NEEDS_DOING, OPTIONAL, DEVELOPER)


@dataclass(frozen=True)
class PlainCheck:
    """Three plain sentences that replace a check's raw ``detail``/``fix`` in the window."""

    title: str
    meaning: str
    fix: str


# Keyed by :attr:`headwaters.control.doctor.Check.id`. Only the checks an ordinary person can meet
# are translated; every other id keeps the check's own detail and fix (see :func:`_entry`).
PLAIN_CHECKS: dict[str, PlainCheck] = {
    "ollama": PlainCheck(
        title="Ollama isn't running",
        meaning="Headwaters uses Ollama to run models on this computer, and it isn't answering.",
        fix="Install and start Ollama, then reopen Doctor.",
    ),
    "gpu": PlainCheck(
        title="No local graphics card",
        meaning=(
            "Headwaters needs an NVIDIA card to run models on this computer; without one it uses "
            "the cloud instead."
        ),
        fix="Install the NVIDIA driver if you want local models.",
    ),
    "private_mode": PlainCheck(
        title="Private mode isn't fully private",
        meaning="Private mode is on, but Ollama could still send a request to the cloud.",
        fix="Turn off Ollama's cloud models, or accept that some requests may use the cloud.",
    ),
    "keys": PlainCheck(
        title="An API key isn't working",
        meaning="One of your provider keys was rejected or couldn't be reached.",
        fix="Check your keys on the Keys page and re-enter any that failed.",
    ),
    "daemon": PlainCheck(
        title="Headwaters isn't running",
        meaning="The background service that answers requests is not up.",
        fix="Start it with `headwaters start`.",
    ),
}


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
    def developer(self) -> tuple[DoctorEntry, ...]:
        """Developer-only findings: counted into the footer line, never shown one by one."""
        return tuple(entry for entry in self.entries if entry.group == DEVELOPER)

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
                lines.append(entry.title)
                lines.append(entry.detail)
                if entry.fix:
                    lines.append(f"Fix: {entry.fix}")
        if self.developer:
            lines.append("")
            lines.append(DEVELOPER_LABEL.format(count=len(self.developer)))
        return "\n".join(lines)


def summarise(report: Report) -> DoctorView:
    """A doctor report as the window shows it: skips dropped, problems before notes."""
    fixes = dict(report.fixes)
    entries: list[DoctorEntry] = []
    for check in report.checks:
        # "skip" means "not applicable" (Private Mode off, no keys saved), never a problem.
        if check.status in {"ok", "skip"}:
            continue
        entries.append(_entry(check, fixes.get(check.title)))
    # Stable sort: the report's own order survives inside each group.
    entries.sort(key=lambda entry: _GROUP_ORDER.index(entry.group))
    return DoctorView(_headline(entries), tuple(entries))


def _entry(check: Check, borrowed_fix: str | None) -> DoctorEntry:
    """One check as the window shows it: plain words when we have them, its own otherwise."""
    if check.id in DEVELOPER_IDS:
        group = DEVELOPER
    elif check.id in OPTIONAL_IDS:
        group = OPTIONAL
    else:
        group = NEEDS_DOING
    plain = PLAIN_CHECKS.get(check.id)
    if plain is None:
        return DoctorEntry(
            id=check.id,
            title=check.title,
            group=group,
            detail=check.detail or check.title,
            fix=check.fix or borrowed_fix,
        )
    return DoctorEntry(
        id=check.id,
        title=plain.title,
        group=group,
        detail=plain.meaning,
        fix=plain.fix,
    )


# One, two and the rest: the headline counts what needs doing, in words a person would use.
_NUMBER_WORDS = {1: "One", 2: "Two"}


def _headline(entries: list[DoctorEntry]) -> str:
    count = sum(1 for entry in entries if entry.group == NEEDS_DOING)
    if not count:
        return ALL_CLEAR
    word = _NUMBER_WORDS.get(count, str(count))
    noun = "thing" if count == 1 else "things"
    return f"{word} {noun} to check"


def _content(view: DoctorView) -> tuple[tuple[str, str], ...]:
    """The window's lines and their style tags, built without a display so a test can read them.

    An all-clear view is one sentence: the window never opens for it. Otherwise each entry is a
    bold title, one plain sentence, and a ``Fix:`` line; developer-only findings collapse to one
    quiet line at the bottom, because their detail is for the log, not for this person.

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
                rows.append((f"Fix: {entry.fix}", "fix"))
    if view.developer:
        rows.append(("", "blank"))
        rows.append((DEVELOPER_LABEL.format(count=len(view.developer)), "developer"))
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
    text.tag_configure("developer", spacing1=10, foreground="#8a8a8a")

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
