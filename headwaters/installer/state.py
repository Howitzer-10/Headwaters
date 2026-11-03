"""Backups and the install manifest (HDW-403).

Every file is copied to ``<name>.headwaters-backup`` before it is written and recorded with the
digest of what was written. Uninstall restores a backup only when the file still holds exactly what
the installer wrote: if the user edited it afterwards, it is left alone and reported.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

from headwaters.installer.plan import read_text

__all__ = [
    "BACKUP_SUFFIX",
    "backup_file",
    "digest",
    "load_manifest",
    "manifest_path",
    "remember",
    "restore",
    "save_manifest",
    "write_file",
]

BACKUP_SUFFIX = ".headwaters-backup"
MANIFEST_DIR = "install"
MANIFEST_NAME = "manifest.json"


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def manifest_path(state: Path) -> Path:
    return state / MANIFEST_DIR / MANIFEST_NAME


def load_manifest(state: Path) -> dict[str, list[dict[str, Any]]]:
    """``{host: [entry, ...]}``; anything unreadable counts as "nothing installed"."""
    path = manifest_path(state)
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, list[dict[str, Any]]] = {}
    for host, rows in raw.items():
        if isinstance(rows, list):
            out[str(host)] = [row for row in rows if isinstance(row, dict)]
    return out


def save_manifest(state: Path, data: dict[str, list[dict[str, Any]]]) -> None:
    path = manifest_path(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")


def backup_file(path: Path) -> Path | None:
    """Copy the file once. A re-install keeps the first backup, which is the true original."""
    if not path.is_file():
        return None
    target = path.with_name(path.name + BACKUP_SUFFIX)
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    return target


def write_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def remember(
    state: Path,
    host: str,
    path: Path,
    *,
    backup: Path | None,
    after: str,
    created: bool,
) -> None:
    data = load_manifest(state)
    rows = data.setdefault(host, [])
    row = {
        "path": str(path),
        "backup": None if backup is None else str(backup),
        "created": created,
        "digest": digest(after),
    }
    for index, existing in enumerate(rows):
        if existing.get("path") == str(path):
            rows[index] = row
            break
    else:
        rows.append(row)
    save_manifest(state, data)


def restore(entry: dict[str, Any], *, force: bool = False) -> str | None:
    """Put a file back the way it was. Returns a problem line, or ``None`` when it was restored."""
    path = Path(str(entry.get("path", "")))
    backup = entry.get("backup")
    created = bool(entry.get("created"))
    current = read_text(path)
    if current is not None and digest(current) != entry.get("digest") and not force:
        return f"{path} changed since install; left alone (use --force to restore it anyway)"
    if created:
        if path.is_file():
            path.unlink()
        _drop_backup(backup)
        return None
    if not backup or not Path(str(backup)).is_file():
        return f"{path}: the backup is gone, so it cannot be restored"
    shutil.copy2(str(backup), path)
    _drop_backup(backup)
    return None


def _drop_backup(backup: object) -> None:
    if not backup:
        return
    path = Path(str(backup))
    if path.is_file():
        path.unlink()
