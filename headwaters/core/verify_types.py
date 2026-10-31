"""Result types for basic verification (HDW-116).

Reports hold only codes, line numbers, pattern names and schema paths, never the candidate's text.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

CheckStatus = Literal["pass", "fail", "skipped"]


@dataclass(frozen=True)
class Reason:
    """One machine-readable failure. ``line`` is 1-based in the LF-normalized candidate."""

    code: str
    check: str
    message: str
    line: int | None = None

    def render(self) -> str:
        where = f" (line {self.line})" if self.line is not None else ""
        return f"{self.code}: {self.message}{where}"

    def to_dict(self) -> dict[str, object]:
        return {"code": self.code, "check": self.check, "message": self.message, "line": self.line}


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: CheckStatus
    detail: str = ""


@dataclass(frozen=True)
class VerifyReport:
    passed: bool
    checks: list[CheckResult] = field(default_factory=list)
    reasons: list[Reason] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "passed": self.passed,
            "checks": [
                {"name": c.name, "status": c.status, "detail": c.detail} for c in self.checks
            ],
            "reasons": [r.to_dict() for r in self.reasons],
        }


class Collector:
    """Accumulates which checks ran, were skipped or failed, then builds the report."""

    def __init__(self) -> None:
        self.reasons: list[Reason] = []
        self._ran: dict[str, bool] = {}
        self._skips: dict[str, list[str]] = {}

    def ran(self, name: str) -> None:
        self._ran[name] = True
        self._skips.setdefault(name, [])

    def skip(self, name: str, detail: str) -> None:
        self._ran.setdefault(name, False)
        self._skips.setdefault(name, []).append(detail)

    def fail(self, name: str, code: str, message: str, line: int | None = None) -> None:
        self.ran(name)
        self.reasons.append(Reason(code=code, check=name, message=message, line=line))

    def has_code(self, code: str) -> bool:
        return any(r.code == code for r in self.reasons)

    def report(self) -> VerifyReport:
        checks: list[CheckResult] = []
        for name, did_run in self._ran.items():
            failed = [r for r in self.reasons if r.check == name]
            if failed:
                checks.append(CheckResult(name, "fail", f"{len(failed)} problem(s)"))
            elif did_run:
                checks.append(CheckResult(name, "pass", "; ".join(self._skips[name])))
            else:
                checks.append(CheckResult(name, "skipped", "; ".join(self._skips[name])))
        return VerifyReport(passed=not self.reasons, checks=checks, reasons=list(self.reasons))
