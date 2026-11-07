"""Versioned plugin interfaces.

Every plugin carries an ``api_version`` attribute. Core checks it at registration time, so an
incompatible plugin is refused instead of failing later. Interfaces are ``runtime_checkable``
Protocols, so ``isinstance`` verifies that the required members exist (it does not check
signatures; the contract tests in ``tests/contracts`` exercise behaviour).

Interfaces are versioned together: bump ``CURRENT_API_VERSION`` only for a breaking change and
keep ``SUPPORTED_API_VERSIONS`` listing every version core still accepts.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

CURRENT_API_VERSION = 1
SUPPORTED_API_VERSIONS: frozenset[int] = frozenset({CURRENT_API_VERSION})


@runtime_checkable
class SurfacePlugin(Protocol):
    """A user-facing surface (tray, status widget, wizard)."""

    api_version: int
    surface_id: str

    def start(self) -> None: ...

    def stop(self) -> None: ...


@runtime_checkable
class VerifierPlugin(Protocol):
    """Checks a candidate answer; returns a list of problems (empty means it passed)."""

    api_version: int

    def verify(self, candidate: str) -> list[str]: ...


@runtime_checkable
class ContextPlugin(Protocol):
    """Builds extra repo context for a request within a token budget."""

    api_version: int

    def build_context(self, query: str, budget_tokens: int) -> str: ...


@runtime_checkable
class BridgePlugin(Protocol):
    """Integration with a host application (installer or hook support)."""

    api_version: int
    host_id: str

    def supports(self, host_id: str) -> bool: ...


@runtime_checkable
class DashboardPlugin(Protocol):
    """Contributes a section to the dashboard / status page."""

    api_version: int

    def render_summary(self) -> dict[str, object]: ...


@runtime_checkable
class LedgerPlugin(Protocol):
    """Records decision metadata (never prompts, outputs or keys)."""

    api_version: int

    def record(self, event: Mapping[str, object]) -> None: ...


@runtime_checkable
class ClassifierLayerPlugin(Protocol):
    """An extra classifier layer; returns a score per label."""

    api_version: int

    def classify(self, features: Mapping[str, float]) -> dict[str, float]: ...


@runtime_checkable
class EscalationPlugin(Protocol):
    """Decides whether a failed attempt should escalate to a stronger candidate."""

    api_version: int

    def should_escalate(self, attempt: int, problems: Sequence[str]) -> bool: ...


@dataclass(frozen=True)
class BudgetCheck:
    """What a ``BudgetPlugin`` answers: may this paid call go ahead, and why not.

    ``reason`` is a fixed, text-free explanation the router can put in the decision's reasons
    when the paid tier is withheld; ``None`` when the call is allowed.
    """

    allowed: bool
    spent_usd: float = 0.0
    cap_usd: float | None = None
    reason: str | None = None


@runtime_checkable
class BudgetPlugin(Protocol):
    """Decides whether one more paid call fits the spend the user allowed for the day.

    Core asks this only for a decision that landed on a paid tier, so a plugin that is absent
    (or switched off) leaves the paid tier exactly as the policy ladder built it (HDW-502).
    """

    api_version: int

    def check(self, *, model: str, tier: str, estimated_usd: float) -> BudgetCheck: ...


@runtime_checkable
class AdvicePlugin(Protocol):
    """Adds a handback advisory when a decision could only be served below the quality floor.

    Returns the line to show (or ``None``). Every argument is decision metadata: a model id, a
    tier, scores and the session key. Never request text (HDW-502).
    """

    api_version: int

    def advise(
        self,
        *,
        model: str,
        tier: str,
        below_floor: bool,
        floor: float,
        score: float,
        scope_tiers: Sequence[str],
        session: str,
    ) -> str | None: ...


@runtime_checkable
class ReportPlugin(Protocol):
    """Builds, previews and sends the opt-in usage report (HDW-508).

    Every method works while the feature is switched off: a switch whose payload cannot be seen
    before it is turned on cannot be consented to. Only ``send_due`` and ``send_now`` may open a
    socket, and each is gated in one place (``headwaters/features/usage_report/send.py``).
    Nothing here ever sees request text, prompts, outputs, file names, paths or keys.
    """

    api_version: int

    def status(self) -> dict[str, object]:
        """The switch, the endpoint state, when it last sent and when it is next due. No I/O."""
        ...

    def preview(self) -> dict[str, object]:
        """Exactly the payload a send would POST, built but not sent."""
        ...

    def send_due(self, *, now: float | None = None) -> dict[str, object]:
        """Send only when the feature is on and the interval has elapsed; never otherwise."""
        ...

    def send_now(self, *, now: float | None = None) -> dict[str, object]:
        """One-shot send at the user's explicit request. Does not turn the feature on."""
        ...

    def survey(self) -> dict[str, object] | None:
        """The product-feedback prompt that is owed now, or ``None``. Reads state, posts nothing."""
        ...


@runtime_checkable
class WizardPlugin(Protocol):
    """The setup wizard's eight steps (HDW-505): describe them, render them, act on one.

    Rendering and acting are separate because consent is concrete: ``page`` shows the diff, the
    payload or the target, and ``act`` performs exactly one enumerated action afterwards. Every
    method works while the feature is switched off, so a surface can say what is off and why
    instead of hiding the wizard. Nothing here ever returns a secret: no key value, no provider
    key variable name and no token reach a page (D18).
    """

    api_version: int

    def status(self) -> dict[str, object]:
        """The eight steps, the recorded state of each, and what is missing. No writes."""
        ...

    def page(
        self,
        *,
        csrf: str = "",
        step: int | None = None,
        error: str | None = None,
        notice: str | None = None,
    ) -> str:
        """The whole wizard page, read live from this machine. Writes nothing."""
        ...

    def act(self, action: str, form: Mapping[str, str]) -> dict[str, object]:
        """One enumerated action. Returns ``{"ok", "step", "notice", "error"}``."""
        ...


INTERFACES: dict[str, type] = {
    "SurfacePlugin": SurfacePlugin,
    "VerifierPlugin": VerifierPlugin,
    "ContextPlugin": ContextPlugin,
    "BridgePlugin": BridgePlugin,
    "DashboardPlugin": DashboardPlugin,
    "LedgerPlugin": LedgerPlugin,
    "ClassifierLayerPlugin": ClassifierLayerPlugin,
    "EscalationPlugin": EscalationPlugin,
    "BudgetPlugin": BudgetPlugin,
    "AdvicePlugin": AdvicePlugin,
    "ReportPlugin": ReportPlugin,
    "WizardPlugin": WizardPlugin,
}


class PluginContractError(ValueError):
    """A plugin does not satisfy its declared interface."""


def check_plugin(interface: str, plugin: object) -> None:
    """Raise ``PluginContractError`` unless ``plugin`` satisfies ``interface`` at a supported
    API version."""
    protocol = INTERFACES.get(interface)
    if protocol is None:
        raise PluginContractError(f"unknown plugin interface: {interface!r}")
    if not isinstance(plugin, protocol):
        raise PluginContractError(f"{type(plugin).__name__} does not implement {interface}")
    version = getattr(plugin, "api_version", None)
    if not isinstance(version, int) or isinstance(version, bool):
        raise PluginContractError(f"{type(plugin).__name__}.api_version must be an int")
    if version not in SUPPORTED_API_VERSIONS:
        raise PluginContractError(
            f"{type(plugin).__name__} targets api_version {version}; "
            f"supported: {sorted(SUPPORTED_API_VERSIONS)}"
        )
