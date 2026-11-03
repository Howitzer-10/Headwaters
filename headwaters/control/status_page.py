"""The read-only status page (HDW-404): the same facts as ``headwaters status``, as HTML.

Core module: no web framework and no daemon import (D14). The daemon builds a page from its live
rules, project rule, feature registry and the shared SQLite state; ``headwaters.gateway`` serves
it on the loopback dashboard port. ``build_page`` is pure data (so a test can build one from real
walls and real decision rows), ``render_html`` is pure text.

Read-only means no editing controls at all: buttons, forms and switches belong to the ★ dashboard
(HDW-503). Nothing here has a secret in it: walls, decisions and quota rows are metadata.
"""

from __future__ import annotations

import html
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from headwaters.core.features import CORE_FEATURES, STAR_LEGEND, FeatureRegistry
from headwaters.core.project_config import ProjectConfig
from headwaters.core.quotas import wall_label
from headwaters.core.rules import Rules, tier_order_label
from headwaters.core.scope import ScopeError, paid_scopes_available, resolve_scope
from headwaters.core.state import Database
from headwaters.core.state_decisions import get_outcome, recent_decisions
from headwaters.core.state_limits import active_walls
from headwaters.core.state_models import list_models

__all__ = [
    "PageDecision",
    "PageWall",
    "StatusPage",
    "build_page",
    "render_html",
]

DECISIONS_SHOWN = 10


@dataclass(frozen=True)
class PageWall:
    subject: str
    label: str  # FU-122-4: "OpenRouter: paid credits", not "provider:openrouter:paid"
    kind: str
    eta_s: float | None
    hits: int


@dataclass(frozen=True)
class PageDecision:
    decision_id: int | None
    ts: float | None
    surface: str
    scope: str
    strategy: str
    model: str | None
    score: float | None
    floor: float | None
    below_floor: bool
    offload_penalty: float | None  # FU-133-1
    active_locks: Sequence[str]  # FU-128-2
    reasons: Sequence[str]
    status: str | None  # the outcome row, when one was written


@dataclass(frozen=True)
class StatusPage:
    """Everything the page shows, already decided. Rendering it makes no query and no call."""

    version: str
    now: float
    default_scope: str
    strategy: str
    effective: str | None
    tiers: Sequence[str]
    locks: Sequence[str]
    scope_error: str | None
    scope_fix: str | None
    private_lock: bool
    airplane: bool
    project_rule: str
    project_file: str | None
    walls: Sequence[PageWall]
    warm: Sequence[tuple[str, float | None]]
    decisions: Sequence[PageDecision]
    # HDW-516: "interleave" or "descend (cloud_first)", the fallback order the router uses.
    tier_order: str = "interleave"
    core: Sequence[tuple[str, str]] = CORE_FEATURES
    stars: Sequence[tuple[str, str, bool]] = ()
    legend: str = STAR_LEGEND
    catalog: str | None = None


def _walls(db: Database, now: float) -> list[PageWall]:
    return [
        PageWall(
            subject=w.subject,
            label=wall_label(w.subject),
            kind=w.kind,
            eta_s=None if w.until is None else max(0.0, w.until - now),
            hits=w.consecutive_count,
        )
        for w in active_walls(db, now)
    ]


def _decisions(db: Database, limit: int) -> list[PageDecision]:
    found: list[PageDecision] = []
    for row in recent_decisions(db, limit):
        outcome = get_outcome(db, row.id) if row.id is not None else None
        found.append(
            PageDecision(
                decision_id=row.id,
                ts=row.ts,
                surface=row.surface,
                scope=row.scope,
                strategy=row.strategy,
                model=row.chosen,
                score=row.score,
                floor=row.floor,
                below_floor=row.below_floor,
                offload_penalty=row.offload_penalty,
                active_locks=list(row.active_locks),
                reasons=list(row.reasons),
                status=None if outcome is None else outcome.status,
            )
        )
    return found


def _stars(registry: FeatureRegistry) -> list[tuple[str, str, bool]]:
    """Registered ★ features, minus internal example ones (ids starting with ``_``)."""
    return [
        (spec.id, spec.label, registry.enabled(spec.id))
        for spec in registry.all()
        if spec.star and not spec.id.startswith("_")
    ]


def build_page(
    *,
    rules: Rules,
    project: ProjectConfig | None,
    project_path: Path | None = None,
    db: Database,
    registry: FeatureRegistry,
    version: str = "",
    catalog: str | None = None,
    now: float | None = None,
    limit: int = DECISIONS_SHOWN,
) -> StatusPage:
    """Read the local state once and describe the page. No network, no model call."""
    moment = time.time() if now is None else now
    decision, scope_error = None, None
    try:
        decision = resolve_scope(
            rules,
            project,
            paid_scopes=paid_scopes_available(registry),
            local_available=True,
        )
    except ScopeError as exc:
        scope_error = exc
    if decision is not None:
        effective, tiers, locks = (
            decision.scope,
            sorted(decision.tiers),
            list(decision.active_locks),
        )
    else:
        # The scope could not be resolved; the locks still apply, so they are read from the rules.
        effective, tiers = None, []
        locks = ["private_lock"] if rules.private_lock else []
        if rules.airplane:
            locks.append("airplane")
    return StatusPage(
        version=version,
        now=moment,
        default_scope=rules.default_scope,
        strategy=rules.strategy,
        effective=effective,
        tiers=tiers,
        locks=locks,
        scope_error=None if scope_error is None else scope_error.message,
        scope_fix=None if scope_error is None else scope_error.fix_hint,
        private_lock=rules.private_lock,
        airplane=rules.airplane,
        project_rule=project.privacy if project is not None else "any",
        project_file=None if project_path is None else str(project_path),
        walls=_walls(db, moment),
        warm=[(m.id, m.size_vram_gb) for m in list_models(db) if m.warm],
        decisions=_decisions(db, limit),
        tier_order=tier_order_label(rules.router.tier_order, rules.router.tier_direction),
        stars=_stars(registry),
        catalog=catalog,
    )


_CSS = """
:root { color-scheme: light dark; --line: #8884; --muted: #8a8a8a; }
body { font: 15px/1.45 "Segoe UI", system-ui, sans-serif; margin: 0 auto; max-width: 60rem;
       padding: 1.5rem; }
h1 { font-size: 1.4rem; margin: 0; }
h2 { font-size: 1.05rem; margin: 2rem 0 .5rem; border-bottom: 1px solid var(--line);
     padding-bottom: .25rem; }
p.muted, .muted, footer { color: var(--muted); font-size: .85rem; }
table { border-collapse: collapse; width: 100%; }
th, td { text-align: left; padding: .3rem .6rem .3rem 0; vertical-align: top;
         border-bottom: 1px solid var(--line); }
th { font-weight: 600; }
td.reasons, span.reasons { font-size: .8rem; color: var(--muted); }
code { font-size: .85rem; }
.on::before { content: "on"; } .off::before { content: "off"; }
"""


def _e(value: object) -> str:
    return html.escape("" if value is None else str(value))


def _eta(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    total = round(seconds)
    if total < 60:
        return f"{total}s"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours, mins = divmod(minutes, 60)
    return f"{hours}h {mins}m"


def _stamp(ts: float | None) -> str:
    return "—" if ts is None else datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def _table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    head = "".join(f"<th>{_e(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _scope_section(page: StatusPage) -> str:
    effective = (
        f"{_e(page.effective)} (tiers: {_e(', '.join(page.tiers))})"
        if page.effective is not None
        else f"none: {_e(page.scope_error)}"
    )
    rows = [
        ["Default scope", f"{_e(page.default_scope)} (strategy: {_e(page.strategy)})"],
        ["Fallback order", _e(page.tier_order)],
        ["Effective scope", effective],
        ["Locks in force", _e(", ".join(page.locks) or "none")],
        ["Private lock", "on" if page.private_lock else "off"],
        ["Airplane mode", "on (local models only)" if page.airplane else "off"],
        [
            "Project rule",
            f"privacy: {_e(page.project_rule)}"
            + (
                f" ({_e(page.project_file)})"
                if page.project_file
                else " (no .headwaters.yaml here)"
            ),
        ],
    ]
    out = ["<h2>Scope and locks</h2>", _table(["Item", "Value"], rows)]
    if page.scope_fix:
        out.append(f'<p class="muted">fix: {_e(page.scope_fix)}</p>')
    return "".join(out)


def _walls_section(page: StatusPage) -> str:
    if not page.walls:
        return "<h2>Walls</h2><p>No walls: every model is available.</p>"
    rows = [
        [_e(w.label), _e(w.kind.replace("_", " ")), _e(_eta(w.eta_s)), str(w.hits)]
        for w in page.walls
    ]
    return "<h2>Walls</h2>" + _table(["Subject", "State", "Ends in", "Hits"], rows)


def _warm_section(page: StatusPage) -> str:
    if not page.warm:
        return "<h2>Warm model</h2><p>None (as of the last model refresh).</p>"
    parts = [
        f"<li>{_e(model)}" + (f" ({vram:.1f} GB on the GPU)" if vram else "") + "</li>"
        for model, vram in page.warm
    ]
    return "<h2>Warm model</h2><ul>" + "".join(parts) + "</ul>"


def _decisions_section(page: StatusPage) -> str:
    if not page.decisions:
        return "<h2>Recent decisions</h2><p>No routing decision yet.</p>"
    rows = [
        [
            _e(_stamp(d.ts)),
            _e(d.surface),
            _e(d.scope),
            _e(d.model or "—"),
            f"{d.score:.1f} / {d.floor:.0f}"
            if d.score is not None and d.floor is not None
            else "—",
            f"-{d.offload_penalty:.1f}" if d.offload_penalty else "",
            _e(d.status or "—"),
            f'<span class="reasons">{_e("; ".join(d.reasons))}</span>',
        ]
        for d in page.decisions
    ]
    return "<h2>Recent decisions</h2>" + _table(
        ["Time", "Surface", "Scope", "Model", "Score / floor", "Offload", "Status", "Reasons"], rows
    )


def _features_section(page: StatusPage) -> str:
    rows = [[f"<code>{_e(i)}</code>", _e(title), "core"] for i, title in page.core]
    rows += [
        [f"<code>{_e(i)}</code>", _e(label), "on" if on else "off"] for i, label, on in page.stars
    ]
    return (
        "<h2>Features</h2>"
        + _table(["Id", "Feature", "State"], rows)
        + f'<p class="muted">{_e(page.legend)}</p>'
    )


def render_html(page: StatusPage) -> str:
    """One self-contained page: no scripts, no external assets, nothing to click."""
    generated = datetime.fromtimestamp(page.now).strftime("%Y-%m-%d %H:%M:%S")
    catalog = f" · catalog: {_e(page.catalog)}" if page.catalog else ""
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="robots" content="noindex">'
        "<title>Headwaters status</title>"
        f"<style>{_CSS}</style></head><body>"
        f"<h1>Headwaters status</h1>"
        f'<p class="muted">Read-only · version {_e(page.version or "unknown")}'
        f" · {_e(generated)}{catalog}</p>"
        '<p class="muted">This page shows local state and changes nothing. '
        "Editing controls live in the ★ dashboard.</p>"
        + _scope_section(page)
        + _walls_section(page)
        + _warm_section(page)
        + _decisions_section(page)
        + _features_section(page)
        + "<footer>Local only: this server refuses every address but 127.0.0.1.</footer>"
        "</body></html>\n"
    )
