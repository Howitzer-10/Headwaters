"""Contract harness: run any plugin implementation against its interface.

Usage from a test::

    from tests.contracts.harness import run_contract
    run_contract("VerifierPlugin", MyVerifier())
"""

from __future__ import annotations

from collections.abc import Callable

from headwaters.core.plugins import (
    CURRENT_API_VERSION,
    INTERFACES,
    SUPPORTED_API_VERSIONS,
    check_plugin,
)


def _surface(p: object) -> None:
    assert isinstance(p.surface_id, str)  # type: ignore[attr-defined]
    assert p.surface_id  # type: ignore[attr-defined]
    p.start()  # type: ignore[attr-defined]
    p.stop()  # type: ignore[attr-defined]


def _verifier(p: object) -> None:
    result = p.verify("print('hello')")  # type: ignore[attr-defined]
    assert isinstance(result, list)
    assert all(isinstance(item, str) for item in result)


def _context(p: object) -> None:
    result = p.build_context("find the router", 256)  # type: ignore[attr-defined]
    assert isinstance(result, str)


def _bridge(p: object) -> None:
    assert isinstance(p.host_id, str)  # type: ignore[attr-defined]
    assert p.host_id  # type: ignore[attr-defined]
    assert isinstance(p.supports(p.host_id), bool)  # type: ignore[attr-defined]


def _dashboard(p: object) -> None:
    assert isinstance(p.render_summary(), dict)  # type: ignore[attr-defined]


def _ledger(p: object) -> None:
    # Decision metadata only: never prompts, outputs or keys.
    assert p.record({"route": "local", "wall": None}) is None  # type: ignore[attr-defined]


def _classifier(p: object) -> None:
    scores = p.classify({"length": 1.0})  # type: ignore[attr-defined]
    assert isinstance(scores, dict)
    assert all(isinstance(k, str) and isinstance(v, float) for k, v in scores.items())


def _escalation(p: object) -> None:
    assert isinstance(p.should_escalate(1, ["syntax error"]), bool)  # type: ignore[attr-defined]


BEHAVIOUR: dict[str, Callable[[object], None]] = {
    "SurfacePlugin": _surface,
    "VerifierPlugin": _verifier,
    "ContextPlugin": _context,
    "BridgePlugin": _bridge,
    "DashboardPlugin": _dashboard,
    "LedgerPlugin": _ledger,
    "ClassifierLayerPlugin": _classifier,
    "EscalationPlugin": _escalation,
}


def run_contract(interface: str, plugin: object) -> None:
    """Assert that ``plugin`` satisfies ``interface``: structure, api_version and behaviour."""
    assert interface in INTERFACES, f"unknown interface {interface}"
    check_plugin(interface, plugin)
    assert plugin.api_version in SUPPORTED_API_VERSIONS  # type: ignore[attr-defined]
    assert CURRENT_API_VERSION in SUPPORTED_API_VERSIONS
    BEHAVIOUR[interface](plugin)
