"""The credential store (HDW-401): keyring first, environment variables as the fallback.

Secrets live in the OS credential store (Windows Credential Manager, behind ``keyring``) under the
service name ``headwaters`` and the key's *alias* as the user name. A key saved here is used by
three readers that must agree: the executor (``api_key_for``), the router's key-scoped walls
(``key_alias_for``, the alias only, never the secret) and the key-poll timer (``secret_for``).

Only aliases and provider names are stored in the index entry; never a secret. Nothing in this
module logs a value: messages carry the alias.

The environment fallback reads the standard variables (``OPENROUTER_API_KEY``,
``ANTHROPIC_API_KEY``, ``OPENAI_API_KEY``), the variable of each ``catalog.providers`` entry
(HDW-514, so a new provider needs no ``rules.yaml`` edit) and each ``models.openai_compatible``
endpoint's ``api_key_env``, so an install that only exports variables keeps working. Those
variable names are a machine contract: ``providers()`` is what the CLI and the dashboard show,
and it carries plain provider names only. A key saved in the
credential store wins over the variable of the same alias, because ``headwaters keys set`` is the
explicit answer.
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, cast

from headwaters.core.providers import Registry, default_registry
from headwaters.core.rules import Rules

__all__ = [
    "CACHE_TTL_S",
    "SHORT_FORMS",
    "STANDARD_ENV",
    "KeyInfo",
    "KeyStore",
    "KeyStoreError",
    "ProviderInfo",
    "default_backend",
    "mask",
    "tail_mask",
]

SERVICE = "headwaters"
# The alias index: one credential-store entry holding JSON metadata (alias, provider, env var).
_INDEX_USER = "__aliases__"
# litellm's own variables. Only what the router can actually call: Ollama and OpenCode need none.
STANDARD_ENV: dict[str, str] = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}
_ALIAS = re.compile(r"^[A-Za-z0-9_.-]+$")
# Short spellings a person may type for a provider; they resolve to the canonical name.
SHORT_FORMS: dict[str, str] = {
    "hf": "huggingface",
    "or": "openrouter",
    "nim": "nvidia",
    "poll": "pollinations",
}
# Providers the router knows without a key: they have no key to save, but they are real names.
# ``opencode`` left this set in HDW-515: it is a catalog row now and every callable model needs a
# Console or Go key.
_KEYLESS = (("ollama", "local"),)
# A credential-store read is not free on Windows (the timer, the router and the CLI all read it),
# so metadata and resolved values are cached briefly. Saving or removing a key clears its own
# entry at once; another process's change is picked up within this window.
CACHE_TTL_S = 2.0


class KeyStoreError(RuntimeError):
    """The key could not be saved or read; the message is safe to show to the user."""


class KeyringBackendLike(Protocol):
    """What this module needs from ``keyring`` (the module itself, or a fake in tests)."""

    def get_password(self, service_name: str, username: str) -> str | None: ...

    def set_password(self, service_name: str, username: str, password: str) -> None: ...

    def delete_password(self, service_name: str, username: str) -> None: ...


def default_backend() -> KeyringBackendLike | None:
    """The process-wide keyring backend, or ``None`` when the package is not installed.

    Tests replace this (``keyring.set_keyring``) or pass ``backend=`` to ``KeyStore``; the real
    Windows Credential Manager is never read by the suite.
    """
    try:
        import keyring
    except Exception:  # a missing/broken keyring must not stop routing
        return None
    return cast(KeyringBackendLike, keyring)


def mask(secret: str | None) -> str:
    """How a key is shown to a person: a short prefix and suffix, never enough to use."""
    if not secret:
        return ""
    if len(secret) < 12:
        return "*" * min(len(secret), 6)
    return f"{secret[:4]}...{secret[-4:]}"


def tail_mask(secret: str | None, keep: int = 4) -> str:
    """How a key is shown on the dashboard: the last few characters, and never a prefix.

    ``mask`` keeps a prefix because ``headwaters keys list`` prints into the user's own terminal.
    The dashboard renders into a browser, where a prefix is enough to tell which vendor a key
    belongs to if a screenshot travels, so only the tail survives here. Four trailing characters
    are enough for a person to recognise which of their keys it is.
    """
    if not secret:
        return ""
    if len(secret) <= keep:
        return "*" * len(secret)
    return "*" * 4 + secret[-keep:]


@dataclass(frozen=True, slots=True)
class _Record:
    alias: str
    provider: str
    env_var: str | None


@dataclass(frozen=True, slots=True)
class KeyInfo:
    """One row of ``headwaters keys list``: metadata plus the masked value."""

    alias: str
    provider: str
    env_var: str | None
    source: str  # "credential store" | "environment" | "not set"
    masked: str


@dataclass(frozen=True, slots=True)
class ProviderInfo:
    """One provider as a person sees it: the plain name, whether it needs a key, its tier.

    The environment variable behind a provider is deliberately not here: it is a machine detail.
    """

    name: str
    key_required: bool
    tier: str  # "paid" | "free_cloud" | "local"


class KeyStore:
    """Aliases -> secrets, backed by the OS credential store with an environment fallback."""

    def __init__(
        self,
        rules: Rules | None = None,
        *,
        env: Mapping[str, str] | None = None,
        backend: KeyringBackendLike | None = None,
        ttl: float = CACHE_TTL_S,
        registry: Registry | None = None,
    ) -> None:
        self._rules = rules or Rules()
        self._registry = registry
        self._env = os.environ if env is None else env
        self._backend = backend
        self._ttl = ttl
        self._index_cache: tuple[float, dict[str, _Record]] | None = None
        self._secret_cache: dict[str, tuple[float, str | None]] = {}

    def __repr__(self) -> str:
        # Never the values: a store may appear in a log line or a traceback.
        return f"KeyStore(aliases={len(self._records())})"

    # ------------------------------------------------------------------ lookups

    def providers(self) -> list[ProviderInfo]:
        """Every provider name a person can use, in one place.

        This is the single source of provider names for the CLI (``keys set|list|test``) and the
        dashboard and tray, so the two surfaces cannot drift: the built-in providers, then each
        ``catalog.providers`` entry (HDW-514), then each ``models.openai_compatible`` endpoint,
        then the providers that need no key. Names are plain lower-case words; the environment
        variable a provider falls back to is never part of the answer.
        """
        found: dict[str, ProviderInfo] = {}
        for name in STANDARD_ENV:
            found[name] = ProviderInfo(name, True, "paid")
        for entry in self._providers():
            found[entry.name] = ProviderInfo(entry.name, entry.key_required, entry.tier)
        for endpoint in self._rules.models.openai_compatible:
            needs_key = bool(endpoint.api_key_env)
            found[endpoint.name] = ProviderInfo(endpoint.name, needs_key, "free_cloud")
        for name, tier in _KEYLESS:
            found.setdefault(name, ProviderInfo(name, False, tier))
        return list(found.values())

    def provider_name(self, text: str) -> str | None:
        """The canonical provider for what a person typed, or ``None`` when it is not one.

        Accepts the plain name, a short form (``hf``) and the environment-variable spelling
        (``NVIDIA_API_KEY``), so a second alias is never stored for the same provider.
        """
        typed = text.strip()
        names = {p.name for p in self.providers()}
        if typed in names:
            return typed
        lowered = typed.lower()
        if lowered in names:
            return lowered
        short = SHORT_FORMS.get(lowered)
        if short in names:
            return short
        spellings = {var.lower(): name for name, var in STANDARD_ENV.items()}
        # Two products of one company can share a variable (HDW-515: one key covers ``opencode``
        # and ``opencode-go``). The first catalog entry that declares it wins, so the answer never
        # depends on anything but the registry order a person can read.
        for entry in self._providers():
            spellings.setdefault(entry.env_var.lower(), entry.name)
        spellings.update(
            {
                e.api_key_env.lower(): e.name
                for e in self._rules.models.openai_compatible
                if e.api_key_env
            }
        )
        return spellings.get(lowered)

    def api_key_for(self, provider: str) -> str | None:
        """The secret a call to ``provider`` uses, from the store or its env variable."""
        found = self._resolve(provider)
        return None if found is None else found[1]

    def key_alias_for(self, provider: str) -> str | None:
        """The alias of the key ``provider`` uses, or ``None`` when it has no key.

        The alias is what the router puts in a wall or quota subject (``key:<alias>:paid``);
        the secret never leaves this module.
        """
        found = self._resolve(provider)
        return None if found is None else found[0].alias

    def secret_for(self, identifier: str) -> str | None:
        """The secret of an alias or a provider name (what a stored paid wall names)."""
        if identifier in self._read_index():
            return self.secret(identifier)
        return self.api_key_for(identifier)

    def provider_for(self, identifier: str) -> str | None:
        """The provider an alias belongs to, or the name itself when it is a known provider."""
        record = self._records().get(identifier)
        return None if record is None else record.provider

    def secret(self, alias: str) -> str | None:
        """The secret for exactly this alias: credential store first, then its env variable."""
        record = self._records().get(alias)
        if record is None:
            return None
        now = time.monotonic()
        cached = self._secret_cache.get(alias)
        if cached is not None and now - cached[0] <= self._ttl:
            return cached[1]
        value = self._get(alias)
        if not value and record.env_var:
            value = self._env.get(record.env_var)
        value = value.strip() if value else None
        self._secret_cache[alias] = (now, value or None)
        return self._secret_cache[alias][1]

    def entries(self) -> list[KeyInfo]:
        """Every known alias (saved ones first), with the masked value or ``not set``."""
        rows: list[KeyInfo] = []
        saved = self._read_index()
        defaults = self._default_records()
        for alias in sorted(saved):
            rows.append(self._info(saved[alias]))
        for alias in sorted(defaults):
            if alias not in saved:
                rows.append(self._info(defaults[alias]))
        return rows

    def aliases(self) -> list[str]:
        return [row.alias for row in self.entries()]

    # ------------------------------------------------------------------ writes

    def save(
        self,
        alias: str,
        secret: str,
        *,
        provider: str | None = None,
        env_var: str | None = None,
    ) -> KeyInfo:
        """Store a key under ``alias`` (no whitespace, no shell-hostile names)."""
        name = alias.strip()
        if not _ALIAS.fullmatch(name):
            raise KeyStoreError(
                "the alias may only contain letters, digits, dot, dash and underscore"
            )
        value = secret.strip()
        if not value:
            raise KeyStoreError("the key is empty")
        if any(character.isspace() for character in value):
            raise KeyStoreError("the key contains whitespace")
        saved = dict(self._read_index())
        current = saved.get(name) or self._default_records().get(name)
        provider_name = provider or (current.provider if current is not None else name)
        if env_var is not None:
            env_name: str | None = env_var
        elif current is not None and current.provider == provider_name:
            env_name = current.env_var
        else:
            env_name = self._default_env(provider_name)
        self._set(name, value)
        saved[name] = _Record(name, provider_name, env_name)
        self._write_index(saved)
        self._secret_cache.pop(name, None)
        return self._info(saved[name])

    def remove(self, alias: str) -> bool:
        """Delete a stored key. True when there was one (in the store or the index)."""
        saved = dict(self._read_index())
        record = saved.pop(alias.strip(), None)
        existed = record is not None
        existed = self._delete(alias.strip()) or existed
        if record is not None:
            self._write_index(saved)
        self._secret_cache.pop(alias.strip(), None)
        return existed

    # ------------------------------------------------------------------ internals

    def _resolve(self, provider: str) -> tuple[_Record, str] | None:
        for record in self._candidates(provider):
            value = self.secret(record.alias)
            if value:
                return record, value
        return None

    def _candidates(self, provider: str) -> list[_Record]:
        """Saved aliases for the provider (sorted, so the choice is stable), then its default."""
        found = sorted(
            (r for r in self._read_index().values() if r.provider == provider),
            key=lambda r: r.alias,
        )
        default = self._default_records().get(provider)
        if default is not None and all(r.alias != default.alias for r in found):
            found.append(default)
        return found

    def _records(self) -> dict[str, _Record]:
        found = self._default_records()
        found.update(self._read_index())
        return found

    def _providers(self) -> Registry:
        if self._registry is None:
            self._registry = default_registry()
        return self._registry

    def _default_records(self) -> dict[str, _Record]:
        found = {name: _Record(name, name, var) for name, var in STANDARD_ENV.items()}
        for entry in self._providers():
            found[entry.name] = _Record(entry.name, entry.name, entry.env_var)
        for endpoint in self._rules.models.openai_compatible:
            found[endpoint.name] = _Record(endpoint.name, endpoint.name, endpoint.api_key_env)
        return found

    def _default_env(self, provider: str) -> str | None:
        standard = STANDARD_ENV.get(provider)
        if standard is not None:
            return standard
        for endpoint in self._rules.models.openai_compatible:
            if endpoint.name == provider:
                return endpoint.api_key_env
        return self._providers().env_var(provider)

    def _info(self, record: _Record) -> KeyInfo:
        value = self.secret(record.alias)
        if value and self._get(record.alias):
            source = "credential store"
        elif value:
            source = "environment"
        else:
            source = "not set"
        return KeyInfo(record.alias, record.provider, record.env_var, source, mask(value))

    def _backend_or_default(self) -> KeyringBackendLike | None:
        return self._backend if self._backend is not None else default_backend()

    def _get(self, user: str) -> str | None:
        backend = self._backend_or_default()
        if backend is None:
            return None
        try:
            return backend.get_password(SERVICE, user)
        except Exception:  # a locked or broken store must never break a call
            return None

    def _set(self, user: str, value: str) -> None:
        backend = self._backend_or_default()
        if backend is None:
            raise KeyStoreError(
                "no OS credential store is available; set the provider's environment variable"
            )
        try:
            backend.set_password(SERVICE, user, value)
        except Exception as exc:
            raise KeyStoreError(f"the OS credential store refused the key ({exc})") from exc

    def _delete(self, user: str) -> bool:
        backend = self._backend_or_default()
        if backend is None:
            return False
        try:
            backend.delete_password(SERVICE, user)
        except Exception:  # ``PasswordDeleteError`` for a missing entry, and broken stores
            return False
        return True

    def _read_index(self) -> dict[str, _Record]:
        now = time.monotonic()
        cached = self._index_cache
        if cached is not None and now - cached[0] <= self._ttl:
            return cached[1]
        records = self._parse_index(self._get(_INDEX_USER))
        self._index_cache = (now, records)
        return records

    def _parse_index(self, raw: str | None) -> dict[str, _Record]:
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except ValueError:
            return {}
        if not isinstance(data, dict):
            return {}
        records: dict[str, _Record] = {}
        for alias, spec in data.items():
            if not isinstance(alias, str) or not _ALIAS.fullmatch(alias):
                continue
            provider = alias
            env_var = STANDARD_ENV.get(alias) or self._providers().env_var(alias)
            if isinstance(spec, dict):
                given_provider = spec.get("provider")
                if isinstance(given_provider, str) and given_provider:
                    provider = given_provider
                given_env = spec.get("env_var")
                if isinstance(given_env, str):
                    env_var = given_env or None
                elif given_env is None:
                    env_var = STANDARD_ENV.get(provider) or self._providers().env_var(provider)
            records[alias] = _Record(alias, provider, env_var)
        return records

    def _write_index(self, records: dict[str, _Record]) -> None:
        payload = {
            alias: {"provider": record.provider, "env_var": record.env_var}
            for alias, record in records.items()
        }
        self._set(_INDEX_USER, json.dumps(payload, sort_keys=True))
        self._index_cache = (time.monotonic(), dict(records))
