"""The redacted diagnostic bundle behind ``headwaters doctor --bundle`` (HDW-402, FU-401-1).

The zip holds four members and nothing else: the check report (JSON and text), an environment
summary and a manifest. No prompt text, no model output, no log file, no key and no absolute path
outside Headwaters' own app directories: HDW-401 added a keyring-backed store, so the bundle
scrubs every string it writes and the test asserts the secrets are really gone.

The scrubber is deliberately dumb and total: known secrets are replaced first (the daemon token and
every stored key, which the CLI passes in), then anything shaped like a provider key or an
authorization header, then any path that is not under the app dirs.
"""

from __future__ import annotations

import json
import re
import sys
import time
import zipfile
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import headwaters
from headwaters.control.doctor import Report, read_ollama_env, render_text
from headwaters.core import paths

__all__ = [
    "BUNDLE_FORMAT",
    "PATH_MARK",
    "REDACTED",
    "app_roots",
    "build_bundle",
    "bundle_name",
    "redact",
]

BUNDLE_FORMAT = 1
REDACTED = "<redacted>"
PATH_MARK = "<path>"

_SECRET_PATTERNS = (
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"hf_[A-Za-z0-9]{16,}"),
    re.compile(r"gsk_[A-Za-z0-9]{16,}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]{8,}", re.IGNORECASE),
    re.compile(r"x-api-key\"?\s*[:=]\s*\"?[A-Za-z0-9._\-]{8,}", re.IGNORECASE),
)
_WIN_PATH = re.compile(r"[A-Za-z]:\\[^\\/:*?\"<>|\r\n]*(?:\\[^\\/:*?\"<>|\r\n]*)*")
_POSIX_PATH = re.compile(r"(?<![\w:/~])/(?:[\w.\-]+/)+[\w.\-]+")


def app_roots() -> tuple[Path, ...]:
    """The only absolute paths a bundle may keep: Headwaters' own config/state/cache/log dirs."""
    found = paths.get_paths()
    return (found.config, found.state, found.cache, found.log)


def _under(text: str, roots: Sequence[Path]) -> bool:
    candidate = text.strip().rstrip("\\/").lower().replace("\\", "/")
    for root in roots:
        base = str(root).rstrip("\\/").lower().replace("\\", "/")
        if base and (candidate == base or candidate.startswith(base + "/")):
            return True
    return False


def redact(text: str, *, secrets: Iterable[str] = (), roots: Sequence[Path] | None = None) -> str:
    """Scrub known secrets, key-shaped strings and paths outside the app dirs from ``text``."""
    out = text
    for secret in secrets:
        if secret and len(secret) >= 4:
            out = out.replace(secret, REDACTED)
    for pattern in _SECRET_PATTERNS:
        out = pattern.sub(REDACTED, out)
    kept = tuple(roots) if roots is not None else app_roots()
    out = _WIN_PATH.sub(
        lambda match: match.group(0) if _under(match.group(0), kept) else PATH_MARK, out
    )
    out = _POSIX_PATH.sub(
        lambda match: match.group(0) if _under(match.group(0), kept) else PATH_MARK, out
    )
    return out


def _scrub(value: Any, secrets: Sequence[str], roots: Sequence[Path]) -> Any:
    if isinstance(value, str):
        return redact(value, secrets=secrets, roots=roots)
    if isinstance(value, Mapping):
        return {key: _scrub(item, secrets, roots) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_scrub(item, secrets, roots) for item in value]
    return value


def bundle_name(now: float | None = None) -> str:
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(time.time() if now is None else now))
    return f"headwaters-diagnostics-{stamp}.zip"


def build_bundle(
    report: Report,
    *,
    secrets: Iterable[str] = (),
    directory: Path | None = None,
    environment: Mapping[str, Any] | None = None,
    roots: Sequence[Path] | None = None,
    now: float | None = None,
) -> Path:
    """Write the bundle and return its path. ``secrets`` are scrubbed from every member."""
    known = tuple(secret for secret in secrets if secret)
    kept = tuple(roots) if roots is not None else app_roots()
    base = Path(directory) if directory is not None else paths.state_dir() / "diagnostics"
    paths.long_safe(base).mkdir(parents=True, exist_ok=True)
    target = paths.long_safe(base / bundle_name(now))
    app_dirs = {
        name: str(path)
        for name, path in zip(("config", "state", "cache", "log"), kept, strict=False)
    }
    summary = (
        dict(environment)
        if environment is not None
        else {
            "ollama": read_ollama_env(),
            "app_dirs": app_dirs,
        }
    )
    manifest = {
        "format": BUNDLE_FORMAT,
        "created_at": time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if now is None else now)
        ),
        "headwaters": headwaters.__version__,
        "platform": sys.platform,
        "host": report.host,
        "checks": len(report.checks),
        "members": ["report.json", "report.txt", "environment.json", "manifest.json"],
        "redaction": (
            "known secrets and key-shaped strings become <redacted>; absolute paths outside the "
            "Headwaters app dirs become <path>; no prompt text, model output or log file is "
            "included"
        ),
    }
    members = {
        "report.json": json.dumps(_scrub(report.as_dict(), known, kept), indent=2),
        "report.txt": str(_scrub(render_text(report), known, kept)),
        "environment.json": json.dumps(_scrub(summary, known, kept), indent=2, default=str),
        "manifest.json": json.dumps(manifest, indent=2),
    }
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, text in members.items():
            archive.writestr(name, text)
    return target
