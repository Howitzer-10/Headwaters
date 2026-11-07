"""Step 1's env fix: show the exact ``OLLAMA_*`` diff, write it only on a matching fingerprint.

``headwaters/control/doctor.py`` already reads these values and names what is wrong
(``read_ollama_env`` at :229, ``_ollama_env`` at :493): flash attention must be ``1``, the KV cache
type must be the one rules ask for, and max-loaded-models must not be above one. This module
reuses those three predicates — no new threshold — and adds the only thing the doctor lacks: the
ability to write the values, with the diff shown first and an exact undo.

What is written, and where: ``HKCU\\Environment``, the same user environment Ollama reads at
logon. Ollama reads the user environment when its tray app starts, so the page always says the
tray app has to be restarted; a value written here is invisible to a running Ollama.
``OLLAMA_NO_CLOUD`` is shown and never written: it is the person's own privacy switch (D19 keeps
Headwaters out of that decision), and the doctor reads it rather than setting it.

Anywhere without ``winreg`` — every non-Windows platform — there is no writer, so the step shows
the exact ``setx``/shell lines and refuses to apply (D4: a Windows-only real write, show-and-copy
elsewhere, stated as a limitation rather than a half-write).
"""

from __future__ import annotations

import hashlib
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

from headwaters.control.doctor import OLLAMA_ENV_VARS, read_ollama_env
from headwaters.core.rules import Rules

__all__ = [
    "WRITABLE",
    "Change",
    "EnvPlan",
    "EnvWriter",
    "NullWriter",
    "RegistryEnvWriter",
    "apply_plan",
    "plan_env",
    "platform_writer",
    "undo_values",
]

#: The three values the wizard may write. ``OLLAMA_NO_CLOUD`` is deliberately absent.
WRITABLE = ("OLLAMA_FLASH_ATTENTION", "OLLAMA_KV_CACHE_TYPE", "OLLAMA_MAX_LOADED_MODELS")

RESTART_NOTE = (
    "Ollama reads these when its tray app starts, so restart the Ollama tray app afterwards; "
    "a value written now is invisible to the copy that is already running."
)


class EnvWriter(Protocol):
    """The system side of the fix: read the user environment, then set or remove one value.

    ``set(name, None)`` removes the value — that is how an undo restores "it was not set".
    """

    #: ``False`` where the platform has no supported way to write it (everything but Windows).
    supported: bool

    def read(self) -> Mapping[str, str]: ...

    def set(self, name: str, value: str | None) -> None: ...


class NullWriter:
    """The honest "cannot write here" writer: it refuses, and the page shows the copy-paste text."""

    supported = False

    def read(self) -> Mapping[str, str]:
        return {}

    def set(self, name: str, value: str | None) -> None:
        raise RuntimeError("this platform has no supported way to write Ollama's environment")


class RegistryEnvWriter:
    """Write ``HKCU\\Environment``, the user environment Ollama itself reads at logon."""

    supported = True
    _SUBKEY = "Environment"

    def read(self) -> Mapping[str, str]:
        return read_ollama_env()

    def set(self, name: str, value: str | None) -> None:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._SUBKEY, 0, winreg.KEY_SET_VALUE) as key:
            if value is None:
                try:
                    winreg.DeleteValue(key, name)
                except FileNotFoundError:
                    return
                return
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)


def platform_writer(platform: str | None = None) -> EnvWriter:
    """A writer on Windows, the refusing one everywhere else."""
    return RegistryEnvWriter() if (platform or sys.platform) == "win32" else NullWriter()


@dataclass(frozen=True)
class Change:
    """One value: what it is now, what it would become, and why."""

    name: str
    current: str
    wanted: str
    why: str

    def line(self) -> str:
        now = self.current or "(not set)"
        return f"{self.name}: {now} -> {self.wanted}   ({self.why})"


@dataclass(frozen=True)
class EnvPlan:
    """The whole diff, without having changed anything."""

    changes: tuple[Change, ...] = ()
    values: dict[str, str] = field(default_factory=dict)
    notes: tuple[str, ...] = ()
    writable: bool = False
    fingerprint: str = ""

    @property
    def needed(self) -> bool:
        return bool(self.changes)

    def as_dict(self) -> dict[str, object]:
        return {
            "needed": self.needed,
            "changes": [c.line() for c in self.changes],
            "fingerprint": self.fingerprint,
            "writable": self.writable,
            "notes": list(self.notes),
        }


def _fingerprint(changes: tuple[Change, ...]) -> str:
    payload = "\n".join(f"{c.name}={c.current}->{c.wanted}" for c in changes)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def plan_env(
    rules: Rules, values: Mapping[str, str], *, writer: EnvWriter | None = None
) -> EnvPlan:
    """The diff between what Ollama sees and what the doctor wants, with the same three rules.

    ``OLLAMA_MAX_LOADED_MODELS`` is only proposed when it is set and wrong: the doctor treats
    "unset" as fine, so the wizard does not add a value the product never asked for.
    """
    wanted_kv = rules.tiers.local.kv_cache_type
    current = {name: values.get(name, "") for name in OLLAMA_ENV_VARS}
    changes: list[Change] = []
    if current["OLLAMA_FLASH_ATTENTION"] != "1":
        changes.append(
            Change(
                "OLLAMA_FLASH_ATTENTION",
                current["OLLAMA_FLASH_ATTENTION"],
                "1",
                "flash attention is what keeps a 12 GB card usable",
            )
        )
    if current["OLLAMA_KV_CACHE_TYPE"] != wanted_kv:
        changes.append(
            Change(
                "OLLAMA_KV_CACHE_TYPE",
                current["OLLAMA_KV_CACHE_TYPE"],
                wanted_kv,
                "the KV cache type rules.yaml asks for",
            )
        )
    if current["OLLAMA_MAX_LOADED_MODELS"] not in ("", "1"):
        changes.append(
            Change(
                "OLLAMA_MAX_LOADED_MODELS",
                current["OLLAMA_MAX_LOADED_MODELS"],
                "1",
                "one model at a time is what fits in VRAM",
            )
        )
    notes = [
        RESTART_NOTE,
        "OLLAMA_NO_CLOUD is read, never written by Headwaters: "
        f"it is currently {current['OLLAMA_NO_CLOUD'] or '(not set)'}.",
    ]
    writable = writer.supported if writer is not None else False
    if not writable:
        notes.append(
            "This platform has no supported way for Headwaters to write the user environment; "
            "the lines above are the change to apply by hand."
        )
    return EnvPlan(
        changes=tuple(changes),
        values=dict(current),
        notes=tuple(notes),
        writable=writable,
        fingerprint=_fingerprint(tuple(changes)),
    )


def apply_plan(
    plan: EnvPlan, *, shown: str, writer: EnvWriter, backup: Mapping[str, str]
) -> dict[str, str]:
    """Write the diff, but only when it is still the diff that was shown.

    Returns the previous values for the names actually written (``""`` for "was not set"), so the
    caller can store exactly what an undo has to restore. Raises ``ValueError`` when the world
    moved between the page and the button, or when the platform cannot write.
    """
    if not writer.supported:
        raise ValueError(
            "this platform has no supported way for Headwaters to write Ollama's environment; "
            "apply the lines shown on the page by hand"
        )
    if not plan.needed:
        raise ValueError("there is nothing to change: Ollama already has these values")
    if shown != plan.fingerprint:
        raise ValueError(
            "these values changed since the page was shown, so nothing was written; "
            "reload the wizard to see the diff again"
        )
    previous: dict[str, str] = {}
    for change in plan.changes:
        previous[change.name] = backup.get(change.name, "")
        writer.set(change.name, change.wanted)
    return previous


def undo_values(values: Mapping[str, str], *, writer: EnvWriter) -> None:
    """Put back exactly what was there: the old value, or absent when it was absent."""
    if not writer.supported:
        raise ValueError(
            "this platform has no supported way for Headwaters to restore Ollama's environment"
        )
    for name, value in values.items():
        writer.set(name, value or None)
