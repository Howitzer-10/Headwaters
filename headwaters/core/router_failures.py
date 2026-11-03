"""Per-request failure memory and the honest terminal error (HDW-513).

A failure is remembered for the request that saw it and nowhere else. This is deliberately not a
cooldown: walls and backoff live in ``walls``/``quotas`` (HDW-511) and are untouched here, so the
two mechanisms cannot duplicate or contradict each other. A model excluded here is available
again to the next request, which is what a context failure needs: the same model can serve a
smaller payload.

``terminal_message`` turns the end of a request into something a user can act on: one line per
attempt, the missing-key fix command, and the tier that was never tried. Credential-looking text
is redacted before it is echoed, and key material is never included by construction.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from headwaters.core.executor.errors import ExecutorError
from headwaters.core.policy.filters import Candidate
from headwaters.core.router_types import Attempt
from headwaters.core.scope import TierName

__all__ = [
    "AUTH_CODES",
    "FailureMemory",
    "FailureRecord",
    "failure_class",
    "redact",
    "terminal_message",
]

# 401 and 403 are the provider saying the credential is missing or wrong; both are handled the
# same way here and told apart by the key store, not by the wire.
AUTH_CODES: frozenset[str] = frozenset({"auth", "auth_missing_key"})

# Attempt lines shown in the terminal message; the rest become "and N more".
MAX_ATTEMPT_LINES = 4

_PROVIDER_NAMES = {
    "anthropic": "Anthropic",
    "gemini": "Gemini",
    "ollama": "Ollama",
    "openai": "OpenAI",
    "opencode": "OpenCode",
    "openrouter": "OpenRouter",
}

_TIER_LABELS = {
    "local": "Local models",
    "free_cloud": "Free cloud models",
    "paid": "Paid models",
}

# Providers that need no API key: a key-store miss is not an authentication failure for them.
_KEYLESS_PROVIDERS: frozenset[str] = frozenset({"ollama"})

# Longest-first and masked to a bare ``****``: a masked value must never match a later rule.
_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"sk-or-v1-[A-Za-z0-9_-]+"), "****"),
    (re.compile(r"sk-[A-Za-z0-9_-]{6,}"), "****"),
    (re.compile(r"\bgsk_[A-Za-z0-9]{10,}"), "****"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{10,}"), "****"),
    (re.compile(r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/=-]{6,}"), r"\1****"),
    (
        re.compile(r"(?i)\b(api[_-]?key|token|secret|password)(\s*[=:]\s*)[^\s,;&\"']+"),
        r"\1\2****",
    ),
)


def redact(text: str) -> str:
    """Mask credential-looking substrings in anything echoable (upstream text, model ids)."""
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


def failure_class(exc: ExecutorError, *, missing_key: bool = False) -> str:
    """The class this request remembers for ``exc``.

    A provider that answered 401/403 is an authentication failure, not a generic provider error;
    ``missing_key`` (from the cheap, cached key store check) says which of the two it is.
    """
    if exc.status in (401, 403):
        return "auth_missing_key" if missing_key else "auth"
    return exc.code


@dataclass(frozen=True, slots=True)
class FailureRecord:
    """One failed model call, kept for the life of the request that made it."""

    model: str
    tier: TierName
    provider: str
    code: str
    status: int | None = None

    @property
    def auth(self) -> bool:
        return self.code in AUTH_CODES


@dataclass
class FailureMemory:
    """What failed in this request, and which models that makes unusable for it."""

    excluded: set[str] = field(default_factory=set)
    records: list[FailureRecord] = field(default_factory=list)

    def record(
        self,
        model: str,
        tier: TierName,
        provider: str,
        exc: ExecutorError,
        *,
        retrying: bool = False,
        missing_key: bool = False,
    ) -> FailureRecord:
        """Remember one failure. A retry-class wall does not exclude the model yet."""
        rec = FailureRecord(
            model=model,
            tier=tier,
            provider=provider,
            code=failure_class(exc, missing_key=missing_key),
            status=exc.status,
        )
        self.records.append(rec)
        if not retrying:
            self.excluded.add(model)
        return rec

    def exclude_provider(self, candidates: Sequence[Candidate], provider: str) -> int:
        """Exclude every model of a provider that cannot authenticate this request.

        Returns how many models were newly excluded, so the decision reasons can say so. The
        check that produced ``missing_key`` is the key store's; no network call is made here.
        """
        ids = {c.id for c in candidates if c.row.provider == provider}
        self.excluded |= ids
        return len(ids)

    def skip_line(self, attempt: int, cap: int) -> str:
        """The one line that says the retry loop remembers (slice A of HDW-513)."""
        failed = len({record.model for record in self.records})
        models = "model" if failed == 1 else "models"
        return (
            f"attempt {attempt} of {cap}: skipping {failed} {models} that already "
            "failed in this request"
        )


def _provider_name(provider: str) -> str:
    return _PROVIDER_NAMES.get(provider, provider or "The provider")


def _tier_label(tier: TierName) -> str:
    return _TIER_LABELS.get(tier, f"{tier} models")


def failure_text(rec: FailureRecord) -> str:
    """A short, safe description of one failure for the terminal message."""
    if rec.code == "auth_missing_key":
        return f"authentication: no {_provider_name(rec.provider)} key configured"
    if rec.code == "auth":
        status = f" ({rec.status})" if rec.status is not None else ""
        return f"authentication: {_provider_name(rec.provider)} rejected the credential{status}"
    if rec.code == "context":
        return "context too large for this payload"
    if rec.status is not None:
        return f"{rec.code} (HTTP {rec.status})"
    return rec.code


def _auth_advice(rec: FailureRecord, alias: str) -> str:
    """The exact command that fixes a missing or rejected key."""
    name = _provider_name(rec.provider)
    status = f" ({rec.status})" if rec.status is not None else ""
    if rec.provider in _KEYLESS_PROVIDERS:
        return f"{name} rejected the request{status}. Check the provider's own settings."
    if rec.code == "auth_missing_key":
        return (
            f"No {name} API key is configured, so {name} rejected the request without one"
            f"{status}. Save a key with: headwaters keys set {alias}"
        )
    return (
        f"{name} rejected the credential{status}. Save a replacement with: "
        f"headwaters keys set {alias}"
    )


def terminal_message(
    attempts: Sequence[Attempt],
    records: Sequence[FailureRecord],
    *,
    cap: int,
    code: str,
    untried: Sequence[TierName] = (),
    aliases: Mapping[str, str | None] | None = None,
) -> str:
    """The bounded, actionable end of a request (slice C of HDW-513).

    Shape: a one-line summary, one line per attempt (capped), the missing-key fix when there is
    one, and the tier that existed but was never tried. Every line is short; a chat host shows a
    handful of them without turning the error into a wall of text.
    """
    used = len(attempts)
    if code == "attempts_exhausted":
        lines = [f"All {used} allowed attempts failed."]
    else:
        lines = [f"No model is left to try after {used} attempt{'s' if used != 1 else ''}."]
    shown = list(records)[:MAX_ATTEMPT_LINES]
    for index, rec in enumerate(shown, 1):
        provider = rec.provider or "unknown"
        lines.append(
            f"- attempt {index}: {rec.model} ({rec.tier}, {provider}) - {failure_text(rec)}"
        )
    hidden = len(records) - len(shown)
    if hidden > 0:
        lines.append(f"- and {hidden} more")
    auth = next((rec for rec in records if rec.auth), None)
    if auth is not None:
        alias = (aliases or {}).get(auth.provider) or auth.provider
        lines.append(_auth_advice(auth, alias))
    if untried:
        names = ", ".join(_tier_label(tier) for tier in untried)
        lines.append(
            f"{names} were available in this scope but never tried: the attempts ran out first."
        )
    return redact("\n".join(lines))
