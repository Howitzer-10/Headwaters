"""HDW-502: hand a task back when the free and local tiers cannot do it well.

Three facts make this advice honest, and all three are in the decision already: the answer is
below the quality floor, no paid tier is in scope (the user asked for free/local, or the daily
cap just withheld paid), and a host on this machine runs on a subscription the user already pays
for (``hosts.subscription``). Then the answer carries one line naming that host, and a handoff
packet is stored so the next session — on the host with the subscription — starts from it.

``rules.advice`` decides where the line appears: ``off`` says nothing and stores no packet,
``header_only`` puts it in the routing header, ``inline`` puts it there and in the answer body.
This feature only produces the line; the surfaces place it.
"""

from __future__ import annotations

from collections.abc import Sequence

from headwaters.core.features import FeatureRegistry
from headwaters.core.plugins import CURRENT_API_VERSION, AdvicePlugin
from headwaters.core.rules import Rules
from headwaters.core.scope import PAID_TIERS
from headwaters.core.state import Database
from headwaters.core.state_misc import add_handoff, latest_handoff_id, link_handoff

HANDOFF_KIND = "handback"

# Host ids as the installer writes them, for the sentence only. Never a model id.
HOST_LABELS: dict[str, str] = {
    "copilot": "GitHub Copilot",
    "vscode": "GitHub Copilot",
    "claude_desktop": "Claude Desktop",
    "cursor": "Cursor",
    "cline": "Cline",
    "opencode": "OpenCode",
}

HANDOFF_ADVICE = (
    "Handback: no paid tier is in scope and the best available model is below the quality "
    "floor for this task. {host} runs on your subscription — continue there"
)


def subscribed_hosts(rules: Rules) -> tuple[str, ...]:
    """Every host this machine has a subscription for, in a stable order."""
    return tuple(sorted(host for host, on in rules.hosts.subscription.items() if on))


def host_label(host: str) -> str:
    return HOST_LABELS.get(host, host.replace("_", " ").title())


def is_handback(
    *, below_floor: bool, tiers: Sequence[str], hosts: Sequence[str], mode: str
) -> bool:
    """True when this decision is one the free and local tiers could only serve below the floor."""
    return bool(below_floor and mode != "off" and hosts and PAID_TIERS.isdisjoint(tiers))


def advisory_text(host: str, packet_id: int | None) -> str:
    """The handback line; the packet sentence is dropped when no packet could be stored."""
    text = f"{HANDOFF_ADVICE.format(host=host)}."
    if packet_id is not None:
        text += f" Handoff packet {packet_id} is saved for the next session."
    return text


class Advisory(AdvicePlugin):
    """Composes the handback line and stores the packet, if there is one to store."""

    api_version = CURRENT_API_VERSION

    def __init__(self, registry: FeatureRegistry) -> None:
        self._registry = registry

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
    ) -> str | None:
        services = self._registry.services
        rules = services.get("rules")
        if not isinstance(rules, Rules):
            return None
        hosts = subscribed_hosts(rules)
        if not is_handback(
            below_floor=below_floor, tiers=scope_tiers, hosts=hosts, mode=rules.advice
        ):
            return None
        packet_id = self._packet(
            services.get("db"),
            session,
            host=hosts[0],
            model=model,
            tier=tier,
            score=score,
            floor=floor,
            scope_tiers=scope_tiers,
        )
        return advisory_text(host_label(hosts[0]), packet_id)

    def _packet(
        self,
        db: object,
        session: str,
        *,
        host: str,
        model: str,
        tier: str,
        score: float,
        floor: float,
        scope_tiers: Sequence[str],
    ) -> int | None:
        """Store the packet once per session, then point the session row at it.

        Reusing the session's latest packet keeps a repeatedly-advised session from growing a
        new row per request: the packet names the task handback, not the request.
        """
        if not isinstance(db, Database) or not session:
            return None
        existing = latest_handoff_id(db, session)
        if existing is not None:
            return existing
        packet = {
            "kind": HANDOFF_KIND,
            "reason": "below_floor_no_paid",
            "host": host,
            "model": model,
            "tier": tier,
            "score": round(score, 2),
            "floor": round(floor, 2),
            "scope_tiers": list(scope_tiers),
        }
        packet_id = add_handoff(db, session, packet)
        link_handoff(db, session, packet_id)
        return packet_id
