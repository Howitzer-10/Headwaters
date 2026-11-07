"""The dashboard's status page (HDW-404, HDW-503): the facts of ``headwaters status``, as HTML.

Core module: no web framework and no daemon import (D14). The daemon builds a page from its live
rules, project rule, feature registry, the shared SQLite state and the credential store;
``headwaters.gateway`` serves it on the loopback dashboard port. ``build_page`` is pure data (so a
test can build one from real walls, real decision rows and a fake keyring), ``render_html`` is
pure text.

Who may change things, and how (HDW-503). This page has exactly four writes - save a key, remove
a key, test a key, test every saved key - and they are the whole reason it is no longer read-only:

- the dashboard has no bearer token, because a browser opens it from a bookmark with nothing but a
  URL. So the page carries a CSRF token instead: random per daemon start, held in memory only
  (never in ``daemon.json``), and rendered into every form here;
- the write routes refuse a request whose ``Host`` is not loopback *and* on this dashboard's own
  port, whose ``Origin`` (or ``Referer``, when there is one) is not this dashboard's origin, that
  is not a small form-encoded body, or that does not carry that token. A cross-site form can send
  the request but cannot read the token, which is what makes the browser's own same-origin rules
  enough;
- nothing else is routed for a write, and everything else on the page is still metadata.

Nothing here has a secret in it. A key is shown as saved plus at most its last four characters
(``core.keys.tail_mask``): no prefix, because a prefix identifies the vendor and a screenshot of
this page should not. The environment variable behind a provider is a machine detail and does not
appear here at all (D18) - a key that comes from the environment says so in words, and says that
removing it here cannot unset it.
"""

from __future__ import annotations

import html
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from headwaters.core.features import CORE_FEATURES, STAR_LEGEND, FeatureRegistry
from headwaters.core.keys import KeyStore, tail_mask
from headwaters.core.project_config import ProjectConfig
from headwaters.core.quotas import wall_label
from headwaters.core.rules import Rules, tier_order_label
from headwaters.core.scope import ScopeError, paid_scopes_available, resolve_scope
from headwaters.core.state import Database
from headwaters.core.state_decisions import get_outcome, recent_decisions
from headwaters.core.state_key_tests import KeyTestRow, key_tests
from headwaters.core.state_limits import active_walls
from headwaters.core.state_models import list_models
from headwaters.core.usage_report import REPORT_FLAG, REPORT_TITLE

__all__ = [
    "KEYS_SECTION",
    "PageDecision",
    "PageKey",
    "PageProvider",
    "PageWall",
    "StatusPage",
    "build_page",
    "render_html",
]

DECISIONS_SHOWN = 10
# ``?section=keys``: the server-side reordering, so the write the person came for is at the top
# without a line of script.
KEYS_SECTION = "keys"


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
class PageKey:
    """One alias in the Keys section: the key's source and the provider's last answer."""

    alias: str
    provider: str
    source: str  # "credential store" | "environment" | "not set"
    masked: str  # at most the last four characters: never a prefix (tail_mask)
    status: str | None  # the last recorded test, or None when it was never tested
    tested_at: float | None
    detail: str


@dataclass(frozen=True)
class PageProvider:
    """One provider's group in the Keys section, in the registry's order (D18)."""

    name: str
    key_required: bool
    tier: str  # "paid" | "free_cloud" | "local" | "other" (an alias whose provider left)
    access: str  # the one formatted label (HDW-519, D1.5): never built on this page
    keys: Sequence[PageKey]


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
    # HDW-501: whatever a DashboardPlugin contributes, or None when no feature contributes one.
    # The page never names a feature, so a deleted feature folder means no section, not an error.
    savings: dict[str, object] | None = None
    catalog: str | None = None
    keys: Sequence[PageProvider] = ()
    csrf: str = ""  # the dashboard's per-start token; empty when the page has no key store
    paid_scope_ready: bool = True


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
    """Registered ★ features, minus internal example ones (ids starting with ``_``).

    The usage report is listed even when its package is missing, and says so (HDW-508):
    PRIVACY.md promises the toggle is visible, and a row that only appears after the switch is on
    could not be used to decide about the switch.
    """
    found = [
        (spec.id, spec.label, registry.enabled(spec.id))
        for spec in registry.all()
        if spec.star and not spec.id.startswith("_")
    ]
    if not any(feature_id == REPORT_FLAG for feature_id, _label, _on in found):
        found.append((REPORT_FLAG, f"{REPORT_TITLE} (package not installed)", False))
    return found


def _key_groups(store: KeyStore, tested: dict[str, KeyTestRow]) -> list[PageProvider]:
    """One group per provider the registry names (D18), with the aliases that belong to it.

    A provider with no alias yet still gets its group: that is how a person sees that a provider
    exists and needs a key. An alias whose provider is no longer in the registry keeps its group
    too, so a stored key is never invisible and can still be removed.
    """
    found: dict[str, list[PageKey]] = {}
    for row in store.entries():
        result = tested.get(row.alias)
        found.setdefault(row.provider, []).append(
            PageKey(
                alias=row.alias,
                provider=row.provider,
                source=row.source,
                masked=tail_mask(store.secret(row.alias)),
                status=None if result is None else result.status,
                tested_at=None if result is None else result.ts,
                detail="" if result is None else result.detail,
            )
        )
    groups = [
        PageProvider(
            name=info.name,
            key_required=info.key_required,
            tier=info.tier,
            access=info.access,
            keys=tuple(found.pop(info.name, ())),
        )
        for info in store.providers()
    ]
    groups += [
        PageProvider(name, True, "other", "not in this catalog", tuple(rows))
        for name, rows in found.items()
    ]
    return groups


def build_page(
    *,
    rules: Rules,
    project: ProjectConfig | None,
    project_path: Path | None = None,
    db: Database,
    registry: FeatureRegistry,
    keys: KeyStore | None = None,
    csrf: str = "",
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
    groups = () if keys is None else _key_groups(keys, key_tests(db))
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
        savings=registry.dashboard_summary(),
        catalog=catalog,
        keys=groups,
        csrf=csrf,
        paid_scope_ready=any(
            group.tier == "paid" and any(key.source != "not set" for key in group.keys)
            for group in groups
        ),
    )


_CSS = """
:root { color-scheme: light dark; --line: #8884; --muted: #8a8a8a;
        --ok: #14742f; --bad: #b00020; --warn: #8a5a00; }
@media (prefers-color-scheme: dark) {
  :root { --ok: #63d68f; --bad: #ff8f8f; --warn: #e8c36a; }
}
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
/* The dot belongs to a status word, so it stays span-qualified: a coloured provider name
   (<strong class="status ok">) takes the colour and never a bullet. */
span.status::before { content: "\\25CF"; margin-right: .35em; }
.status.ok { color: var(--ok); }
.status.bad { color: var(--bad); }
.status.warn { color: var(--warn); }
.status.idle { color: var(--muted); }
p.error { color: #b00020; font-weight: 600; }
form { margin: .35rem 0; }
td form, td input { display: inline-block; margin-right: .3rem; }
summary { cursor: pointer; }
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


def _savings_section(page: StatusPage) -> str:
    """The dashboard plugin's summary, labelled with whatever words the plugin supplied.

    The heading says estimate, and the plugin's own sentence carries the reference model, its
    dated rate and how many requests the figure covers: the page adds no number of its own, so it
    cannot state a figure the plugin did not stand behind (D7, HDW-501).
    """
    summary = page.savings
    if not summary:
        return ""
    label = _e(str(summary.get("label", "")))
    if not summary.get("available"):
        return f'<h2>Savings estimate</h2><p class="muted">{label}</p>'
    totals = summary.get("totals") or {}
    if not isinstance(totals, Mapping):
        return f'<h2>Savings estimate</h2><p class="muted">{label}</p>'
    rows = [
        ["Requests", str(totals.get("requests", 0))],
        ["Priced", str(totals.get("priced_requests", 0))],
        ["Unpriced (excluded)", str(totals.get("unpriced_requests", 0))],
        ["Free or local", str(totals.get("free_or_local_requests", 0))],
        ["Actual spend", f"${totals.get('actual_usd', 0.0):.6f}"],
        ["Reference model would charge", f"${totals.get('counterfactual_usd', 0.0):.6f}"],
        ["Estimated difference", f"${totals.get('estimated_savings_usd', 0.0):.6f}"],
    ]
    days = summary.get("by_day") or ()
    day_rows = [
        [
            _e(str(day.get("day", ""))),
            str(day.get("requests", 0)),
            f"${day.get('actual_usd', 0.0):.6f}",
            f"${day.get('counterfactual_usd', 0.0):.6f}",
            f"${day.get('estimated_savings_usd', 0.0):.6f}",
        ]
        for day in days
        if isinstance(day, Mapping)
    ]
    section = (
        "<h2>Savings estimate</h2>"
        + _table(["Measure", "Value"], rows)
        + (
            ""
            if not day_rows
            else _table(["Day (UTC)", "Requests", "Actual", "Reference", "Difference"], day_rows)
        )
        + f'<p class="muted">{label}</p>'
    )
    return section


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


def _provider_state(group: PageProvider) -> str:
    if not group.key_required:
        return "no key needed"
    sources = {key.source for key in group.keys}
    if "credential store" in sources:
        return "key saved"
    if "environment" in sources:
        return "key from the environment"
    return "no key saved"


def _provider_status(group: PageProvider) -> str | None:
    """The provider's current answer: its newest tested alias, or None when nothing was tested.

    One lookup for the whole heading, so the name's colour and the "last test ..." word cannot
    disagree. No alias, no key saved and no test result all mean None - neutral, never alarming.
    """
    tested = [key for key in group.keys if key.status is not None]
    if not tested:
        return None
    return max(tested, key=lambda key: key.tested_at or 0.0).status


def _provider_test(status: str | None) -> str:
    """The provider's latest answer, as HTML: the coloured word, never the colour alone."""
    if status is None:
        return _status("never tested", None)
    return _status(f"last test {status}", status)


def _provider_name(group: PageProvider, status: str | None) -> str:
    """The provider's name in its current status colour. ``<strong>``: no dot, no bullet."""
    return f'<strong class="status {_status_class(status)}">{_e(group.name)}</strong>'


# The status word -> the colour beside it. Green is an answer, red is a key to fix, amber is "not
# checked by design" (blocked, unsupported), muted is "never tested". Strings, not imports: this
# module does not import ``control.keys`` (D14).
_STATUS_CLASS = {
    "ok": "ok",
    "unauthorized": "bad",
    "http_error": "bad",
    "unreachable": "bad",
    "blocked": "warn",
    "unsupported": "warn",
}


def _status_class(status: str | None) -> str:
    """The colour class for a status: muting the unknown keeps a missing answer neutral."""
    return "idle" if status is None else _STATUS_CLASS.get(status, "warn")


def _status(word: str, status: str | None) -> str:
    """``word`` with a coloured dot. The word stays, so a screenshot reads without colour."""
    return f'<span class="status {_status_class(status)}">{_e(word)}</span>'


def _csrf_field(page: StatusPage) -> str:
    return f'<input type="hidden" name="csrf" value="{_e(page.csrf)}">'


def _tested(key: PageKey) -> str:
    if key.status is None:
        return _status("never tested", None)
    detail = f' <span class="reasons">{_e(key.detail)}</span>' if key.detail else ""
    return f"{_status(key.status, key.status)} · {_e(_stamp(key.tested_at))}{detail}"


def _key_actions(page: StatusPage, key: PageKey) -> str:
    hidden = _csrf_field(page) + f'<input type="hidden" name="alias" value="{_e(key.alias)}">'
    replace = (
        '<form method="post" action="/keys/set">'
        + hidden
        + f'<input type="hidden" name="provider" value="{_e(key.provider)}">'
        + '<input type="password" name="secret" size="12" autocomplete="off" '
        + f'aria-label="New key for {_e(key.alias)}">'
        + '<button type="submit">Replace</button></form>'
    )
    test = (
        '<form method="post" action="/keys/test">'
        + hidden
        + '<button type="submit">Test</button></form>'
    )
    remove = (
        '<form method="post" action="/keys/remove">'
        + hidden
        + '<button type="submit">Remove</button></form>'
    )
    return replace + test + remove


def _provider_note(group: PageProvider) -> str:
    notes: list[str] = []
    if not group.key_required:
        notes.append(f"{group.name} needs no key: a request to it carries none.")
    elif not any(key.source != "not set" for key in group.keys):
        notes.append(
            f"No key is saved for {group.name}, so Headwaters does not send requests to it: they "
            "go to free cloud or local models instead."
        )
    if any(key.source == "environment" for key in group.keys):
        notes.append(
            "A key that comes from the system environment cannot be removed here: it comes back "
            "from the environment until it is removed there."
        )
    return "".join(f'<p class="muted">{_e(note)}</p>' for note in notes)


def _provider_block(page: StatusPage, group: PageProvider) -> str:
    status = _provider_status(group)
    summary = (
        f"{_provider_name(group, status)} · {_e(group.access)} · "
        f"{_e(_provider_state(group))} · {_provider_test(status)}"
    )
    head = f'<details id="keys-{_e(group.name)}"><summary>{summary}</summary>'
    body = ""
    if group.keys:
        rows = [
            [
                f"<code>{_e(key.alias)}</code>",
                _e(key.source),
                _e(key.masked or "—"),
                _tested(key),
                _key_actions(page, key),
            ]
            for key in group.keys
        ]
        body = _table(["Alias", "Where the key is", "Shown as", "Last test", "Actions"], rows)
    return head + body + _provider_note(group) + "</details>"


def _add_key_form(page: StatusPage) -> str:
    options = "".join(
        f'<option value="{_e(group.name)}">{_e(group.name)}'
        + ("" if group.key_required else " (needs no key)")
        + "</option>"
        for group in page.keys
    )
    return (
        '<form method="post" action="/keys/set">'
        + _csrf_field(page)
        + '<label>Alias <input name="alias" size="14" autocomplete="off" '
        'placeholder="the provider name"></label> '
        + f'<label>Provider <select name="provider">{options}</select></label> '
        + '<label>Key <input type="password" name="secret" size="30" autocomplete="off"></label> '
        + '<button type="submit">Save key</button></form>'
    )


def _test_all_form(page: StatusPage) -> str:
    """One POST for every saved key, above the provider blocks and clear of the per-row actions."""
    if not any(key.source != "not set" for group in page.keys for key in group.keys):
        return ""
    return (
        '<form method="post" action="/keys/test-all">'
        + _csrf_field(page)
        + '<button type="submit">Test all saved keys</button>'
        + '<span class="muted"> one cheap metadata request per saved key</span></form>'
    )


def _keys_section(page: StatusPage) -> str:
    if not page.keys:
        return ""
    out = [
        '<h2 id="keys">Keys</h2>',
        '<p class="muted">Headwaters sends these keys to the providers they belong to. A saved '
        "key goes to the system credential store, and this page only ever shows its last four "
        "characters.</p>",
    ]
    if not page.paid_scope_ready:
        out.append(
            '<p class="muted">No paid provider has a key, so paid scope cannot be used on this '
            "install: requests go to free cloud and local models. One paid key is enough to "
            "enable it.</p>"
        )
    out.append(_add_key_form(page))
    out.append(_test_all_form(page))
    out.extend(_provider_block(page, group) for group in page.keys)
    return "".join(out)


def render_html(page: StatusPage, *, section: str | None = None, error: str | None = None) -> str:
    """One self-contained page: no scripts, no external assets, and only the keys' four writes.

    ``section`` reorders the page (``keys`` puts the Keys section first) and ``error`` is the
    sentence the daemon shows when a write was refused. Both are decided by the caller: this
    function only lays the page out.
    """
    generated = datetime.fromtimestamp(page.now).strftime("%Y-%m-%d %H:%M:%S")
    catalog = f" · catalog: {_e(page.catalog)}" if page.catalog else ""
    keys = _keys_section(page)
    rest = (
        _scope_section(page)
        + _walls_section(page)
        + _warm_section(page)
        + _decisions_section(page)
        + _savings_section(page)
        + _features_section(page)
    )
    return (
        "<!doctype html>\n"
        '<html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="robots" content="noindex">'
        "<title>Headwaters status</title>"
        f"<style>{_CSS}</style></head><body>"
        f"<h1>Headwaters status</h1>"
        f'<p class="muted">Local dashboard · version {_e(page.version or "unknown")}'
        f" · {_e(generated)}{catalog}</p>"
        + _editing_note(page)
        + ("" if error is None else f'<p class="error">{_e(error)}</p>')
        + (keys + rest if section == KEYS_SECTION else rest + keys)
        + "<footer>Local only: this server refuses every address but 127.0.0.1.</footer>"
        "</body></html>\n"
    )


def _editing_note(page: StatusPage) -> str:
    if not page.keys:
        return '<p class="muted">This page shows local state and changes nothing.</p>'
    return (
        '<p class="muted">This page shows local state. The Keys section can save, replace, remove '
        "and test a key, or test every saved key; nothing else here changes anything.</p>"
    )
