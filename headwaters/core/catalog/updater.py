"""Catalog updater: signed, ETag-conditional, atomic, offline-safe.

Order of trust when loading: cached download (re-verified every load) -> previous good copy ->
bundled seed. A download is accepted only if its ed25519 signature verifies AND it validates
against the catalog schema. Requests carry no prompt data: just the URL, ``If-None-Match`` and
a fixed ``User-Agent``. Nothing here touches the network when ``airplane`` is on.

The signed file is ``<url>`` (the catalog JSON, signed byte-for-byte) with the detached
base64 signature at ``<url>.sig``.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from headwaters.core import paths
from headwaters.core.catalog import trust
from headwaters.core.catalog.loader import Catalog, parse_catalog
from headwaters.core.rules import Rules

CACHE_SUBDIR = "catalog"
CURRENT_NAME = "catalog.cache.json"
PREVIOUS_NAME = "catalog.previous.json"
MAX_BYTES = 5_000_000
TIMEOUT_SECONDS = 10.0
USER_AGENT = "Headwaters-catalog-updater"

Source = Literal["cache", "previous", "seed"]
Outcome = Literal["updated", "not_modified", "disabled", "error"]


@dataclass(frozen=True)
class FetchResult:
    """``body`` is None for a 304 Not Modified."""

    status: int
    body: bytes | None
    etag: str | None = None


Fetcher = Callable[[str, str | None], FetchResult]


@dataclass(frozen=True)
class UpdateResult:
    outcome: Outcome
    message: str


@dataclass(frozen=True)
class CatalogStatus:
    source: Source
    catalog_version: str
    etag: str | None
    fetched_at: float | None
    url: str | None
    update_blocked: str | None


def cache_dir() -> Path:
    return paths.cache_dir() / CACHE_SUBDIR


def seed_path() -> Path:
    """Bundled seed: ``catalog/seed_catalog.json`` in a source checkout."""
    return Path(__file__).resolve().parents[3] / "catalog" / "seed_catalog.json"


def blocked_reason(rules: Rules, *, manual: bool = False) -> str | None:
    """Why no network call may be made, or None. ``manual`` ignores ``auto_update``."""
    if rules.airplane:
        return "airplane mode is on"
    if rules.catalog.disable_when_local and (rules.private_lock or rules.default_scope == "local"):
        return "local-only lock state"
    if not manual and not rules.catalog.auto_update:
        return "catalog.auto_update is off"
    if not rules.catalog.url:
        return "catalog.url is not configured"
    return None


def default_fetch(url: str, etag: str | None) -> FetchResult:
    """Plain conditional GET. Only https URLs are allowed."""
    if not url.lower().startswith("https://"):
        raise ValueError("catalog URL must use https")
    headers = {"User-Agent": USER_AGENT}
    if etag:
        headers["If-None-Match"] = etag
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            body: bytes = response.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise ValueError("catalog download is too large")
            return FetchResult(response.status, body, response.headers.get("ETag"))
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            return FetchResult(304, None, etag)
        raise


def _read_envelope(path: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads(paths.long_safe(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(raw, dict) and isinstance(raw.get("data"), str):
        return raw
    return None


def _write_atomic(path: Path, text: str) -> None:
    safe = paths.long_safe(path)
    safe.parent.mkdir(parents=True, exist_ok=True)
    tmp = safe.with_name(safe.name + f".{os.getpid()}.tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, safe)
    finally:
        tmp.unlink(missing_ok=True)


def _verified_catalog(envelope: dict[str, Any], key_b64: str, source: Path) -> Catalog | None:
    try:
        data = str(envelope["data"])
        trust.verify(data.encode("utf-8"), str(envelope.get("signature", "")), key_b64)
        return parse_catalog(data, source)
    except (trust.SignatureError, ValueError):
        return None


def load_active(
    public_key_b64: str | None = None, directory: Path | None = None
) -> tuple[Catalog, Source]:
    """Best available catalog without any network access. Never raises for bad cache files."""
    key = trust.EMBEDDED_PUBLIC_KEY_B64 if public_key_b64 is None else public_key_b64
    base = directory or cache_dir()
    candidates: list[tuple[str, Source]] = [(CURRENT_NAME, "cache"), (PREVIOUS_NAME, "previous")]
    for name, source in candidates:
        envelope = _read_envelope(base / name)
        if envelope is not None:
            catalog = _verified_catalog(envelope, key, base / name)
            if catalog is not None:
                return catalog, source
    seed = seed_path()
    return parse_catalog(paths.long_safe(seed).read_text(encoding="utf-8"), seed), "seed"


def update(
    rules: Rules,
    *,
    manual: bool = True,
    fetch: Fetcher | None = None,
    public_key_b64: str | None = None,
    directory: Path | None = None,
    now: float | None = None,
) -> UpdateResult:
    """Fetch, verify and atomically install a newer catalog. Never raises on network trouble."""
    reason = blocked_reason(rules, manual=manual)
    if reason is not None:
        return UpdateResult("disabled", f"Catalog update skipped: {reason}.")
    fetch = fetch or default_fetch
    url = str(rules.catalog.url)
    key = trust.EMBEDDED_PUBLIC_KEY_B64 if public_key_b64 is None else public_key_b64
    base = directory or cache_dir()
    current_path = base / CURRENT_NAME
    current = _read_envelope(current_path)
    if current is not None and _verified_catalog(current, key, current_path) is None:
        current = None  # a cache we would not accept: do not send its validator
    etag = current.get("etag") if current is not None else None
    try:
        result = fetch(url, etag if isinstance(etag, str) else None)
        if result.status == 304 or result.body is None:
            return UpdateResult("not_modified", "Catalog is up to date (304); cache unchanged.")
        sig = fetch(url + ".sig", None)
        if sig.body is None:
            raise ValueError("signature file missing")
        trust.verify(result.body, sig.body, key)
        data = result.body.decode("utf-8")
        catalog = parse_catalog(data, Path(url))
    except (OSError, ValueError) as exc:
        return UpdateResult("error", f"Catalog update rejected, cache unchanged: {exc}")
    envelope = {
        "data": data,
        "signature": sig.body.decode("ascii", "replace").strip(),
        "etag": result.etag,
        "fetched_at": time.time() if now is None else now,
        "url": url,
    }
    if current is not None:
        _write_atomic(base / PREVIOUS_NAME, json.dumps(current, ensure_ascii=False))
    _write_atomic(current_path, json.dumps(envelope, ensure_ascii=False))
    return UpdateResult("updated", f"Catalog updated to {catalog.catalog_version}.")


def status(
    rules: Rules, public_key_b64: str | None = None, directory: Path | None = None
) -> CatalogStatus:
    catalog, source = load_active(public_key_b64, directory)
    base = directory or cache_dir()
    envelope = _read_envelope(base / CURRENT_NAME) if source == "cache" else None
    fetched = envelope.get("fetched_at") if envelope else None
    etag = envelope.get("etag") if envelope else None
    return CatalogStatus(
        source=source,
        catalog_version=catalog.catalog_version,
        etag=etag if isinstance(etag, str) else None,
        fetched_at=float(fetched) if isinstance(fetched, int | float) else None,
        url=rules.catalog.url,
        update_blocked=blocked_reason(rules, manual=True),
    )
