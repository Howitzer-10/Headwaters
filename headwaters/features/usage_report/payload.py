"""HDW-508: the usage report payload — aggregate counts only, in a closed and versioned shape.

Every field is one of four things: a count the state database already records, a closed-set name
the code itself emits, a short value derived from the machine (OS, GPU class, app version, date),
or an explicit ``null`` where the app genuinely does not know. Nothing else can be added without
editing :data:`FIELD_RULES`, because :func:`check_payload` refuses a payload whose paths and
leaves do not match that table — that is what makes "there are no free-text fields" a property of
the code rather than a promise in a document.

Two figures in the ticket are only approximately or not at all recoverable, and the payload says
so instead of inventing them:

* ``failover_requests_approx`` counts requests whose ``decisions.alternatives`` holds at least one
  failed attempt. That column mixes attempts that were tried and failed with fallback names that
  were never tried (``core.router_log._alternatives``), so the number is a proxy, and it is named
  for what it is.
* ``features_retained_14d`` is ``null`` for a feature whose first-seen stamp is younger than the
  retention window. Nothing in the app recorded ★ history before this ticket, so a fresh install
  answers "not yet known" rather than "no".
"""

from __future__ import annotations

import json
import re
import sys
import time
from collections.abc import Mapping, Sequence
from typing import Any

import headwaters
from headwaters.core.catalog.schema import Catalog
from headwaters.core.features import FeatureRegistry
from headwaters.core.hardware.state import HardwareState
from headwaters.core.rules import Rules
from headwaters.core.state import Database
from headwaters.core.state_misc import kv_get, kv_set

PAYLOAD_VERSION = 1
KIND = "headwaters_usage_report"
WINDOW_DAYS = 7
DAY = 86_400.0
RETENTION_DAYS = 14
#: No payload string is longer than a version, a date, an OS name, a bucket or a GPU class.
MAX_STRING = 40

OS_NAMES: dict[str, str] = {"win32": "windows", "cygwin": "windows", "linux": "linux"}
ENGINES = ("gateway", "mcp", "hook", "cli", "other")
SCOPES = ("auto", "free", "local", "cloud")
TIERS = ("local", "free_cloud", "paid", "unknown")
SAVINGS_BUCKETS = ("no_savings", "unknown", "0-1", "1-5", "5-20", "20-plus")
GPU_VENDORS = ("NVIDIA", "AMD", "Intel", "other")
OS_VALUES = ("windows", "macos", "linux", "other")

FIRST_SEEN_KEY = "report.first_seen"

#: The attempt statuses that are not a failover: a served answer, and a verification failure that
#: the router counted as a verify catch of its own.
_NOT_A_FAILOVER = frozenset({"ok", "verify_failed"})

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_VERSION = re.compile(r"^\d+\.\d+\.\d+(?:[.\-+][0-9A-Za-z.\-]+)?$")
_GPU = re.compile(r"^\d+ GB (" + "|".join(GPU_VENDORS) + r")$")


class PayloadShapeError(ValueError):
    """The payload is not the allow-listed shape, so it must not be sent."""


#: Path (``*`` matches one key of a per-feature map) to the only value kind allowed there.
FIELD_RULES: dict[str, Any] = {
    "payload_version": int,
    "kind": frozenset({KIND}),
    "generated_on": _DATE,
    "window_days": int,
    "app_version": _VERSION,
    "os": frozenset(OS_VALUES),
    "gpu_class": "gpu",
    "requests": int,
    "active_days": int,
    "verify_catches": int,
    "escalations": int,
    "failover_requests_approx": int,
    "savings_bucket": frozenset(SAVINGS_BUCKETS),
    **{f"engine_counts.{name}": int for name in ENGINES},
    **{f"scope_counts.{name}": int for name in SCOPES},
    **{f"tier_counts.{name}": int for name in TIERS},
    "features_enabled.*": bool,
    "features_retained_14d.*": "bool-or-null",
}


def _flatten(value: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    found: dict[str, Any] = {}
    for key, item in value.items():
        path = f"{prefix}{key}"
        if isinstance(item, Mapping):
            found.update(_flatten(item, f"{path}."))
        else:
            found[path] = item
    return found


def _matches(path: str, pattern: str) -> bool:
    return path == pattern or (pattern.endswith(".*") and path.startswith(pattern[:-1]))


def _check_leaf(path: str, value: Any, rule: Any) -> None:
    if rule == "gpu":
        if value is not None and (not isinstance(value, str) or not _GPU.match(value)):
            raise PayloadShapeError(f"gpu_class must be '<n> GB <vendor>' or null, got {value!r}")
        return
    if rule == "bool-or-null":
        if value is not None and not isinstance(value, bool):
            raise PayloadShapeError(f"{path} must be a bool or null, got {value!r}")
        return
    if isinstance(rule, frozenset):
        if not isinstance(value, str) or value not in rule:
            raise PayloadShapeError(f"{path} must be one of {sorted(rule)}, got {value!r}")
        return
    if isinstance(rule, re.Pattern):
        if not isinstance(value, str) or not rule.match(value):
            raise PayloadShapeError(f"{path} does not match its pattern: {value!r}")
        return
    if rule is int:
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise PayloadShapeError(f"{path} must be a non-negative int, got {value!r}")
        return
    if rule is bool:
        if not isinstance(value, bool):
            raise PayloadShapeError(f"{path} must be a bool, got {value!r}")
        return
    raise PayloadShapeError(f"no rule for {path}")


def check_payload(payload: Mapping[str, Any]) -> None:
    """Raise :class:`PayloadShapeError` unless ``payload`` is exactly the allow-listed shape.

    Every leaf must match a rule and every rule must be used, so a contributor who adds a ``notes``
    field, a float, a list or a nested object fails here before any endpoint sees the payload.
    """
    flat = _flatten(payload)
    unknown = sorted(path for path in flat if not any(_matches(path, p) for p in FIELD_RULES))
    if unknown:
        raise PayloadShapeError(f"payload has fields outside the allow-list: {unknown}")
    missing = [pattern for pattern in FIELD_RULES if not any(_matches(p, pattern) for p in flat)]
    if missing:
        raise PayloadShapeError(f"payload is missing allow-listed fields: {missing}")
    for path, value in flat.items():
        _check_leaf(path, value, next(r for p, r in FIELD_RULES.items() if _matches(path, p)))
        if isinstance(value, str) and len(value) > MAX_STRING:
            raise PayloadShapeError(f"{path} is too long to be anything but prose")
    if flat["payload_version"] != PAYLOAD_VERSION:
        raise PayloadShapeError(f"payload_version must be {PAYLOAD_VERSION}")


def os_name() -> str:
    """The platform in four names, never a kernel string, build number or hostname."""
    platform = sys.platform
    if platform in OS_NAMES:
        return OS_NAMES[platform]
    if platform.startswith("darwin"):
        return "macos"
    return "other"


def vram_gb(total_mb: int) -> int:
    """A VRAM class, not a model number: 10 GB reports as 8 GB, 12 GB as 12 GB."""
    whole = max(1, int(total_mb // 1024))
    return whole - whole % 4 if whole >= 8 else whole


def gpu_class(state: HardwareState | None) -> str | None:
    """``'12 GB NVIDIA'`` or ``None``: never a device name, driver version or serial."""
    gpu = None if state is None else state.gpu
    if gpu is None or not getattr(gpu, "total_mb", None):
        return None
    name = str(getattr(gpu, "name", "") or "").lower()
    if "nvidia" in name:
        vendor = "NVIDIA"
    elif "amd" in name or "radeon" in name:
        vendor = "AMD"
    elif "intel" in name:
        vendor = "Intel"
    else:
        vendor = "other"
    return f"{vram_gb(int(gpu.total_mb))} GB {vendor}"


def savings_bucket(summary: Mapping[str, Any] | None) -> str:
    """The ledger's weekly estimate, binned — or ``unknown`` when the ledger cannot price it.

    ``unknown`` is a real answer: no estimate was made, and it must not be turned into
    ``no_savings``. The ledger's own rule is that an unpriceable request is not a free one
    (``features/ledger/report.py::_rateable``), so the report may not compress the two.
    """
    if not isinstance(summary, Mapping) or not summary.get("available"):
        return "unknown"
    week = summary.get("week")
    if not isinstance(week, Mapping):
        return "unknown"
    value = week.get("estimated_savings_usd")
    if not isinstance(value, int | float) or isinstance(value, bool):
        return "unknown"
    if value <= 0:
        return "no_savings"
    if value < 1:
        return "0-1"
    if value < 5:
        return "1-5"
    if value < 20:
        return "5-20"
    return "20-plus"


def star_features(registry: FeatureRegistry) -> dict[str, bool]:
    """Every ★ feature and whether it is on — off ones included, because off is a real answer."""
    return {
        spec.id: registry.enabled(spec.id)
        for spec in registry.all()
        if spec.star and not spec.id.startswith("_")
    }


def observe_features(registry: FeatureRegistry, db: Database, *, now: float) -> dict[str, float]:
    """Stamp the first day each enabled ★ feature was seen — the only retention history there is.

    Nothing recorded ★ history before HDW-508, so a stamp can only start now; the 14-day figure
    answers ``null`` until a stamp is old enough. An off feature is not stamped: the question the
    ticket asks is how long an enabled feature stayed enabled.
    """
    stamps: dict[str, float] = {}
    for feature_id, on in star_features(registry).items():
        if not on:
            continue
        key = f"{FIRST_SEEN_KEY}.{feature_id}"
        stored = kv_get(db, key)
        if not isinstance(stored, int | float) or isinstance(stored, bool):
            kv_set(db, key, now)
            stored = now
        stamps[feature_id] = float(stored)
    return stamps


def retained_14d(
    registry: FeatureRegistry, stamps: Mapping[str, float], *, now: float
) -> dict[str, bool | None]:
    """Per ★ feature: still on after :data:`RETENTION_DAYS`, off, or not yet knowable."""
    retained: dict[str, bool | None] = {}
    for feature_id, on in star_features(registry).items():
        if not on:
            retained[feature_id] = False
        elif feature_id not in stamps:
            retained[feature_id] = None
        elif now - stamps[feature_id] >= RETENTION_DAYS * DAY:
            retained[feature_id] = True
        else:
            retained[feature_id] = None
    return retained


def _failovers(conn: Any, since: float) -> int:
    found = 0
    for row in conn.execute("SELECT alternatives FROM decisions WHERE ts >= ?", (since,)):
        raw = row["alternatives"]
        if not raw:
            continue
        try:
            attempts = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if isinstance(attempts, list) and any(
            isinstance(item, Mapping) and item.get("status") not in _NOT_A_FAILOVER
            for item in attempts
        ):
            found += 1
    return found


def _counts(conn: Any, since: float) -> dict[str, Any]:
    def one(sql: str) -> int:
        row = conn.execute(sql, (since,)).fetchone()
        return int(row[0] or 0)

    def grouped(sql: str, names: Sequence[str], fallback: str) -> dict[str, int]:
        counts = dict.fromkeys(names, 0)
        for row in conn.execute(sql, (since,)):
            name = str(row[0]) if row[0] in names else fallback
            counts[name] += int(row[1])
        return counts

    engines = grouped(
        "SELECT surface, COUNT(*) FROM decisions WHERE ts >= ? GROUP BY surface", ENGINES, "other"
    )
    scopes = grouped(
        "SELECT scope, COUNT(*) FROM decisions WHERE ts >= ? GROUP BY scope", SCOPES, "auto"
    )
    tiers = grouped(
        "SELECT COALESCE(m.tier, 'unknown') AS tier, COUNT(*) FROM decisions d"
        " LEFT JOIN models m ON m.id = d.chosen WHERE d.ts >= ? GROUP BY tier",
        TIERS,
        "unknown",
    )
    return {
        "requests": one("SELECT COUNT(*) FROM decisions WHERE ts >= ?"),
        "active_days": one(
            "SELECT COUNT(DISTINCT date(ts, 'unixepoch')) FROM decisions WHERE ts >= ?"
        ),
        "verify_catches": one(
            "SELECT COUNT(*) FROM outcomes o JOIN decisions d ON d.id = o.decision_id"
            " WHERE d.ts >= ? AND o.status = 'verify_failed'"
        ),
        "escalations": one(
            "SELECT COUNT(*) FROM outcomes o JOIN decisions d ON d.id = o.decision_id"
            " WHERE d.ts >= ? AND o.escalated_to IS NOT NULL"
        ),
        "failover_requests_approx": _failovers(conn, since),
        "engine_counts": engines,
        "scope_counts": scopes,
        "tier_counts": tiers,
    }


def _hardware(services: Mapping[str, Any]) -> HardwareState | None:
    probe = services.get("hardware")
    if not callable(probe):
        return None
    try:
        state = probe()
    except Exception:  # a broken GPU probe must never be the reason a report fails
        return None
    return state if isinstance(state, HardwareState) else None


def _version() -> str:
    text = str(getattr(headwaters, "__version__", "0.0.0"))
    return text if _VERSION.match(text) else "0.0.0"


def build(
    services: Mapping[str, Any], *, now: float | None = None, window_days: int = WINDOW_DAYS
) -> dict[str, Any]:
    """The payload. Reads the state database and the registry's services; opens no socket."""
    db = services.get("db")
    registry = services.get("registry")
    if not isinstance(db, Database):
        raise PayloadShapeError("the state database is not wired to the feature registry")
    if not isinstance(registry, FeatureRegistry):
        raise PayloadShapeError("the feature registry did not hand itself to the feature")
    moment = time.time() if now is None else now
    stamps = observe_features(registry, db, now=moment)
    with db.read() as conn:
        counts = _counts(conn, moment - window_days * DAY)
    payload: dict[str, Any] = {
        "payload_version": PAYLOAD_VERSION,
        "kind": KIND,
        "generated_on": time.strftime("%Y-%m-%d", time.gmtime(moment)),
        "window_days": int(window_days),
        "app_version": _version(),
        "os": os_name(),
        "gpu_class": gpu_class(_hardware(services)),
        **counts,
        "savings_bucket": savings_bucket(registry.dashboard_summary()),
        "features_enabled": star_features(registry),
        "features_retained_14d": retained_14d(registry, stamps, now=moment),
    }
    check_payload(payload)
    return payload


def describe_shape() -> list[tuple[str, str]]:
    """The payload's paths and their permitted values, for the preview header."""
    shaped: list[tuple[str, str]] = []
    for path, rule in FIELD_RULES.items():
        if isinstance(rule, frozenset):
            shaped.append((path, "one of " + ", ".join(sorted(rule))))
        elif isinstance(rule, re.Pattern):
            shaped.append((path, "pattern " + rule.pattern))
        elif isinstance(rule, str):
            shaped.append((path, rule))
        else:
            shaped.append((path, rule.__name__))
    return shaped


def services_for(
    db: Database,
    registry: FeatureRegistry,
    *,
    rules: Rules | None = None,
    catalog: Catalog | None = None,
) -> dict[str, Any]:
    """The services a build needs, with the registry present so ★ features can be counted."""
    services: dict[str, Any] = dict(registry.services)
    services.update({"db": db, "registry": registry})
    if rules is not None:
        services["rules"] = rules
    if catalog is not None:
        services["catalog"] = catalog
    return services


__all__ = [
    "ENGINES",
    "FIELD_RULES",
    "KIND",
    "MAX_STRING",
    "PAYLOAD_VERSION",
    "RETENTION_DAYS",
    "SAVINGS_BUCKETS",
    "SCOPES",
    "TIERS",
    "WINDOW_DAYS",
    "PayloadShapeError",
    "build",
    "check_payload",
    "describe_shape",
    "gpu_class",
    "observe_features",
    "os_name",
    "retained_14d",
    "savings_bucket",
    "services_for",
    "star_features",
    "vram_gb",
]
