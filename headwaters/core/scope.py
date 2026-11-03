"""Scope resolver: which tiers may serve a request.

    effective_scope = intersect(
        private_lock ? local : ANY,
        project_rule (.headwaters.yaml privacy: local | free | any),
        secret_detected ? local : ANY,       # secrets.action: local | refuse | off
        requested_scope (picker model / MCP param),
        default_scope (rules.yaml) when nothing was requested,
    )

Scopes are sets of tiers, so the intersection is exact and a lock can only ever remove tiers
(project rules and the Private lock tighten, never loosen). An empty or unsatisfiable result is a
structured ``ScopeError`` with a fix hint, never a silent fallback to a looser scope.

Without the paid-scopes ★ feature, ``auto`` and ``cloud`` exclude the paid tiers and the reasons
say so. The resolver handles scope only: ranking inside a scope is the policy layer's job.

Reasons never contain prompt text or matched secrets, only pattern names.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, cast, get_args

from headwaters.core.features import FeatureRegistry
from headwaters.core.project_config import Privacy, ProjectConfig
from headwaters.core.rules import Rules, Scope, Strategy
from headwaters.core.secrets_detect import SecretMatch

TierName = Literal["paid", "openrouter_auto", "free_cloud", "local"]
VerifyMode = Literal["always", "agentic", "off"]

PAID_SCOPES_FEATURE_ID = "paid_scopes"
ALIAS_PREFIX = "headwaters/"

ALL_TIERS: frozenset[TierName] = frozenset({"paid", "openrouter_auto", "free_cloud", "local"})
PAID_TIERS: frozenset[TierName] = frozenset({"paid", "openrouter_auto"})
LOCAL_TIERS: frozenset[TierName] = frozenset({"local"})
FREE_TIERS: frozenset[TierName] = frozenset({"free_cloud", "local"})

SCOPE_TIERS: dict[Scope, frozenset[TierName]] = {
    "auto": ALL_TIERS,
    "free": FREE_TIERS,
    "local": LOCAL_TIERS,
    "cloud": frozenset({"paid", "openrouter_auto", "free_cloud"}),
}
PRIVACY_TIERS: dict[Privacy, frozenset[TierName]] = {
    "any": ALL_TIERS,
    "free": FREE_TIERS,
    "local": LOCAL_TIERS,
}

# Picker model suffixes (``headwaters/free+verify``, ``headwaters/auto+quality``).
SUFFIX_STRATEGY: dict[str, Strategy] = {"quality": "quality_first", "primary": "primary_first"}
SUFFIX_VERIFY: dict[str, VerifyMode] = {"verify": "always", "noverify": "off"}
# ``headwaters/quality`` is the optional picker entry: auto scope + quality_first.
_EXTRA_ALIASES: dict[str, tuple[Scope, Strategy]] = {
    "quality": ("auto", "quality_first"),
    # The duck picker entry (HDW-301) is the cheapest fast model that clears the floor. Its
    # rubber-duck prompt overlay is not built yet (HDW-902), so it routes as ``cost_saver``.
    "duck": ("auto", "cost_saver"),
}


class ScopeError(Exception):
    """The request cannot be served within the effective scope (or is not understood)."""

    def __init__(self, code: str, message: str, fix_hint: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.fix_hint = fix_hint

    def as_dict(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message, "fix_hint": self.fix_hint}


@dataclass(frozen=True)
class ParsedModel:
    """A parsed ``headwaters/<name>[+suffix...]`` picker model id."""

    scope: Scope
    strategy: Strategy | None = None
    verify: VerifyMode | None = None
    suffixes: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScopeDecision:
    scope: Scope  # nearest named scope; ``tiers`` is authoritative
    tiers: frozenset[TierName]
    strategy: Strategy
    verify: VerifyMode
    locks: tuple[str, ...] = ()  # locks that narrowed the request
    # Every lock in force, narrowing or not: private_lock, project_rule, secret, airplane.
    # Hints that forbid "use cloud" key on this, not on ``locks``.
    active_locks: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    secret_patterns: tuple[str, ...] = ()  # pattern names only, never matched text

    @property
    def local_only(self) -> bool:
        return self.tiers == LOCAL_TIERS

    @property
    def allows_cloud(self) -> bool:
        return bool(self.tiers - LOCAL_TIERS)


def paid_scopes_available(registry: FeatureRegistry) -> bool:
    """True when the paid-scopes ★ feature is registered and switched on."""
    return registry.enabled(PAID_SCOPES_FEATURE_ID)


def parse_model_name(model: str) -> ParsedModel | None:
    """Parse a picker model id. Returns ``None`` for ids that are not ``headwaters/...``.

    ``headwaters/free+verify`` -> free scope, verify always; ``headwaters/auto+quality`` ->
    auto scope, quality_first. Unknown names or suffixes raise ``ScopeError``.
    """
    text = model.strip()
    if not text.lower().startswith(ALIAS_PREFIX):
        return None
    name, *suffixes = text[len(ALIAS_PREFIX) :].lower().split("+")
    strategy: Strategy | None = None
    if name in get_args(Scope):
        scope = cast(Scope, name)
    elif name in _EXTRA_ALIASES:
        scope, strategy = _EXTRA_ALIASES[name]
    else:
        known = ", ".join(ALIAS_PREFIX + n for n in sorted([*SCOPE_TIERS, *_EXTRA_ALIASES]))
        raise ScopeError(
            "unknown_model", f"Unknown Headwaters model {model!r}.", f"Use one of: {known}."
        )
    verify: VerifyMode | None = None
    for suffix in suffixes:
        if suffix in SUFFIX_STRATEGY:
            strategy = SUFFIX_STRATEGY[suffix]
        elif suffix in SUFFIX_VERIFY:
            verify = SUFFIX_VERIFY[suffix]
        else:
            allowed = ", ".join(f"+{s}" for s in (*SUFFIX_STRATEGY, *SUFFIX_VERIFY))
            raise ScopeError(
                "unknown_suffix",
                f"Unknown suffix '+{suffix}' in {model!r}.",
                f"Allowed suffixes: {allowed}.",
            )
    return ParsedModel(scope, strategy, verify, tuple(suffixes))


def _name_for(tiers: frozenset[TierName]) -> Scope:
    """The named scope that best describes ``tiers`` (``tiers`` itself stays authoritative)."""
    if "local" not in tiers:
        return "cloud"
    if tiers == LOCAL_TIERS:
        return "local"
    return "free" if tiers <= FREE_TIERS else "auto"


def _narrow(
    tiers: frozenset[TierName],
    limit: frozenset[TierName],
    lock: str,
    narrowed: list[str],
    error: ScopeError,
) -> frozenset[TierName]:
    result = tiers & limit
    if not result:
        raise error
    if result != tiers:
        narrowed.append(lock)
    return result


def resolve_scope(
    rules: Rules,
    project: ProjectConfig | None = None,
    *,
    requested: Scope | None = None,
    strategy: Strategy | None = None,
    verify: VerifyMode | None = None,
    secret_matches: Sequence[SecretMatch] = (),
    secret_override: bool = False,
    paid_scopes: bool = False,
    local_available: bool = True,
    local_fix_hint: str | None = None,
) -> ScopeDecision:
    """Resolve the effective scope for one request.

    ``requested`` comes from the picker model or an MCP ``scope`` parameter; when ``None`` the
    ``rules.default_scope`` applies. ``secret_override`` must only be set by an explicit user
    action for this request; it lifts the secret lock but never the Private lock or a project
    rule. ``paid_scopes`` says whether the ★ paid-scopes feature is on
    (see ``paid_scopes_available``). ``local_available`` / ``local_fix_hint`` come from the
    hardware and discovery layers and only matter when the result is local-only.
    """
    base: Scope = requested if requested is not None else rules.default_scope
    reasons = [f"{'requested' if requested is not None else 'default'} scope: {base}"]
    narrowed: list[str] = []
    active: list[str] = []
    tiers = SCOPE_TIERS[base]

    # Locks, strongest first. Each can only remove tiers.
    if rules.private_lock:
        active.append("private_lock")
        tiers = _narrow(
            tiers,
            LOCAL_TIERS,
            "private_lock",
            narrowed,
            ScopeError(
                "private_lock_conflict",
                f"The Private lock allows local models only, but scope '{base}' was requested.",
                "Disable the Private lock in the tray (or run `headwaters private off`), "
                "or request the local scope.",
            ),
        )
        reasons.append("Private lock on: local models only")

    privacy: Privacy = project.privacy if project is not None else "any"
    if privacy != "any":
        active.append("project_rule")
        tiers = _narrow(
            tiers,
            PRIVACY_TIERS[privacy],
            "project_rule",
            narrowed,
            ScopeError(
                "project_rule_conflict",
                f"This project's privacy rule ({privacy}) excludes every tier "
                f"that scope '{base}' allows.",
                "Request a scope the project allows, or edit `privacy:` in .headwaters.yaml.",
            ),
        )
        reasons.append(f"project rule privacy={privacy}")

    names = tuple(sorted({m.pattern for m in secret_matches}))
    if secret_matches and rules.secrets.action != "off":
        shown = ", ".join(names)
        if secret_override:
            reasons.append(f"secret detected ({shown}): override confirmed for this request")
        elif rules.secrets.action == "refuse":
            raise ScopeError(
                "secret_refused",
                f"A secret was detected in the request ({shown}) and secrets.action is 'refuse'.",
                "Remove the secret from the prompt, or set secrets.action to 'local' "
                "in rules.yaml.",
            )
        else:
            active.append("secret")
            tiers = _narrow(
                tiers,
                LOCAL_TIERS,
                "secret",
                narrowed,
                ScopeError(
                    "secret_conflict",
                    f"A secret was detected ({shown}), so the request must stay local, "
                    f"but scope '{base}' excludes local models.",
                    "Remove the secret from the prompt, or confirm the override for this request.",
                ),
            )
            reasons.append(f"secret detected ({shown}): local only")

    if rules.airplane:
        active.append("airplane")  # the router drops non-local candidates; no tier change here

    if not paid_scopes and tiers & PAID_TIERS:
        tiers = tiers - PAID_TIERS
        reasons.append("paid tiers excluded: the paid-scopes feature is not enabled")

    if tiers == LOCAL_TIERS and not local_available:
        raise ScopeError(
            "local_unavailable",
            "Only local models are allowed here, but none is available.",
            local_fix_hint
            or "Start Ollama and pull a model (run `headwaters doctor` for a recommendation).",
        )

    return ScopeDecision(
        scope=_name_for(tiers),
        tiers=tiers,
        strategy=strategy if strategy is not None else rules.strategy,
        verify=verify if verify is not None else rules.verify,
        locks=tuple(narrowed),
        active_locks=tuple(active),
        reasons=tuple(reasons),
        secret_patterns=names,
    )
