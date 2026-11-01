"""Run the registered verifiers on tool output (HDW-204, v3.4 B1).

``BasicVerifier`` (core) always runs; every registered ``VerifierPlugin`` runs too. A diff is
tried with ``git apply --check`` in the workspace folder, so ``verified`` on a diff means it really
applies to the user's files. The status follows the F11 rule: no problem found and nothing
checked is ``unchecked``, never ``pass``. Reports carry codes, never the checked text.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from headwaters.core.catalog.schema import Catalog
from headwaters.core.features import FeatureRegistry
from headwaters.core.plugins import VerifierPlugin
from headwaters.core.router_types import VerifyOutcome
from headwaters.core.verify_basic import BasicVerifier
from headwaters.mcp.tools.schemas import CheckItem, VerifyStatus

__all__ = ["Verification", "combine", "registered_verifiers", "verify_text"]


def registered_verifiers(registry: FeatureRegistry) -> list[VerifierPlugin]:
    """Every enabled ``VerifierPlugin`` (the registry checked each one's contract on register)."""
    return [p for p in registry.plugins_for("VerifierPlugin") if isinstance(p, VerifierPlugin)]


@dataclass(frozen=True)
class Verification:
    status: VerifyStatus
    reasons: tuple[str, ...] = ()
    checks: tuple[CheckItem, ...] = ()
    checked: int = 0

    @property
    def verified(self) -> bool:
        return self.status == "pass"

    @property
    def diff_applied(self) -> bool:
        """True when ``git apply --check`` ran on the diff and it applied."""
        return any(c.name == "diff" and c.status == "pass" for c in self.checks)


def verify_text(
    text: str,
    catalog: Catalog,
    plugins: Sequence[VerifierPlugin] = (),
    *,
    language: str | None = None,
    repo: Path | None = None,
) -> Verification:
    """Check ``text`` (a model answer, a diff or code). Blocking: run it off the event loop."""
    report = BasicVerifier.from_catalog(catalog, repo=repo).verify_report(text, language=language)
    reasons = [reason.render() for reason in report.reasons]
    checks = [CheckItem(name=c.name, status=c.status, detail=c.detail) for c in report.checks]
    checked = report.checked
    for plugin in plugins:
        try:
            found = list(plugin.verify(text))
        except Exception:  # a broken verifier must not fail the user's answer
            continue
        checked += 1
        checks.append(CheckItem(name=type(plugin).__name__, status="fail" if found else "pass"))
        reasons.extend(p for p in found if p not in reasons)
    status: VerifyStatus = "fail" if reasons else ("pass" if checked else "unchecked")
    return Verification(status, tuple(reasons), tuple(checks), checked)


def combine(
    routed: VerifyOutcome | None, mine: Verification | None
) -> tuple[VerifyStatus, tuple[str, ...]]:
    """One verdict from the router's own gate and this tool's check. A failure anywhere wins."""
    reasons: list[str] = []
    statuses: list[str] = []
    if routed is not None:
        reasons.extend(routed.problems)
        statuses.append(routed.status)
    if mine is not None:
        reasons.extend(r for r in mine.reasons if r not in reasons)
        statuses.append(mine.status)
    if "fail" in statuses or reasons:
        return "fail", tuple(reasons)
    return ("pass" if "pass" in statuses else "unchecked"), ()
