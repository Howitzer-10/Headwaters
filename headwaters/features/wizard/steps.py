"""The eight steps as data: what each one shows, and the two gates that touch the system.

Everything a step *shows* is a pure function of data that was already read: the plugin in
``__init__`` does the reading, this module turns it into the page's plain dicts and decides the
sentences. The two exceptions are the gates — ``HostGate`` (the installer's plan/apply) and
``AutostartGate`` (``schtasks``) — because each one has to re-check the world between the page
and the button, which is exactly what makes "show the diff first" mean something.

No step here invents a fact. A missing catalog, an unreachable Ollama, an absent report package
and a platform that cannot write the environment all become a sentence on the page, never a
plausible-looking default.
"""

from __future__ import annotations

import hashlib
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from headwaters.core.features import FeatureRegistry
from headwaters.core.rules import Rules
from headwaters.core.usage_report import preview_report, report_status
from headwaters.features.wizard import envfix
from headwaters.features.wizard.pull import PullJob, Recommendation
from headwaters.installer import context as installer_context
from headwaters.installer import engine, hosts
from headwaters.packaging import autostart
from headwaters.packaging.layout import Install

__all__ = [
    "STEPS",
    "AutostartGate",
    "AutostartView",
    "Check",
    "HostGate",
    "HostView",
    "Step",
    "autostart_view",
    "detect_view",
    "keys_view",
    "mode_view",
    "models_view",
    "report_view",
    "selftest_view",
    "step_index",
]


@dataclass(frozen=True)
class Step:
    """One step: its number, stable id, and the sentence that says what it is for."""

    n: int
    id: str
    title: str
    summary: str
    writes: bool


STEPS: tuple[Step, ...] = (
    Step(
        1,
        "detect",
        "Detect this machine",
        "The GPU and the VRAM budget Headwaters will use, whether Ollama answers, and the exact "
        "Ollama environment diff — applied only if you say so, and undoable.",
        True,
    ),
    Step(
        2,
        "models",
        "Choose models",
        "What the catalog recommends for this GPU, with the product's own fit math, and one pull "
        "with real progress from Ollama.",
        True,
    ),
    Step(
        3,
        "ides",
        "Install into your editors",
        "A diff of each editor's own config before anything is written. Applying one re-plans "
        "first, so an edit you made in the meantime is never overwritten.",
        True,
    ),
    Step(
        4,
        "keys",
        "Provider keys",
        "Which providers have a key. Nothing here reads or shows a key: adding one happens in "
        "the dashboard's Keys section.",
        False,
    ),
    Step(
        5,
        "mode",
        "Default scope and strategy",
        "Where requests go by default, and how the router chooses between models.",
        True,
    ),
    Step(
        6,
        "autostart",
        "Start Headwaters at logon",
        "The scheduled task, shown before it is created. Available on Windows only.",
        True,
    ),
    Step(
        7,
        "report",
        "Opt-in usage report",
        "The exact payload that would be sent, then consent for that payload version. Off by "
        "default; this step works with the report switched off and stays listed if it is absent.",
        True,
    ),
    Step(
        8,
        "selftest",
        "Self-test",
        "Real checks that assert what they check — rules, catalog, state, Ollama, keys, the "
        "report preview and this daemon's own route. It does not load a model.",
        False,
    ),
)

_BY_ID = {step.id: step for step in STEPS}


def step_index(step_id: str) -> int | None:
    """The number of a step by id, or ``None`` when there is no such step."""
    step = _BY_ID.get(step_id)
    return None if step is None else step.n


def _gpu(gpu: Any) -> dict[str, object] | None:
    if gpu is None:
        return None
    return {"name": str(getattr(gpu, "name", "")), "total_gb": round(float(gpu.total_mb) / 1024, 1)}


def detect_view(
    *,
    hardware: Any,
    catalog_present: bool,
    ollama_host: str,
    ollama_version: str | None,
    ollama_error: str,
    installed: Sequence[str] | None,
    plan: envfix.EnvPlan,
    env_undoable: bool = False,
) -> dict[str, object]:
    """Step 1: what was detected, and the env diff that was *not* applied."""
    view: dict[str, object] = {
        "kind": "detect",
        "gpu": _gpu(getattr(hardware, "gpu", None)),
        "budget_gb": None if hardware is None else round(float(hardware.budget_gb), 1),
        "profile": None if hardware is None else str(hardware.profile),
        "hardware_notes": [] if hardware is None else [str(n) for n in hardware.notes][:6],
        "no_gpu_detected": hardware is None or getattr(hardware, "gpu", None) is None,
        "catalog_present": catalog_present,
        "ollama": {
            "host": ollama_host,
            "version": ollama_version,
            "error": ollama_error,
            "installed_count": None if installed is None else len(installed),
        },
        "env": plan.as_dict(),
        "env_undoable": env_undoable,
    }
    return view


def models_view(
    *,
    profile: str | None,
    recommendations: Sequence[Recommendation],
    installed_known: bool,
    ollama_error: str,
    pull: PullJob | None,
    pull_waiting: bool,
) -> dict[str, object]:
    """Step 2: the catalog's recommendation for this profile and the state of the one pull."""
    return {
        "kind": "models",
        "profile": profile,
        "recommendations": [rec.as_dict() for rec in recommendations],
        "installed_known": installed_known,
        "ollama_error": ollama_error,
        "pull": None if pull is None else pull.as_dict(),
        "pull_waiting": pull_waiting,
        "empty_reason": (
            ""
            if recommendations
            else "the catalog has no GPU profile to recommend from, so there is nothing to suggest"
        ),
    }


@dataclass(frozen=True)
class HostView:
    """One editor host: whether it is already installed, and the diff an install would make."""

    host: str
    title: str
    scopes: tuple[str, ...]
    status: str  # installed | changes | unchanged | error
    diff: str
    fingerprint: str
    notes: tuple[str, ...] = ()
    problems: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "host": self.host,
            "title": self.title,
            "scopes": list(self.scopes),
            "status": self.status,
            "diff": self.diff,
            "fingerprint": self.fingerprint,
            "notes": list(self.notes),
            "problems": list(self.problems),
            "errors": list(self.errors),
        }


def _host_fingerprint(result: engine.HostResult) -> str:
    payload = "\n".join(f"{item.path}\t{item.after}" for item in result.changes if item.changed)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _view_for(
    result: engine.HostResult, adapter_scopes: Sequence[str], installed: bool
) -> HostView:
    pending = result.pending
    if result.errors:
        status = "error"
    elif installed and not pending:
        status = "installed"
    elif pending:
        status = "changes"
    else:
        status = "unchanged"
    return HostView(
        host=result.host,
        title=result.title,
        scopes=tuple(adapter_scopes),
        status=status,
        diff=result.diff(),
        fingerprint=_host_fingerprint(result),
        notes=tuple(result.notes),
        problems=tuple(result.problems),
        errors=tuple(result.errors),
    )


class HostGate:
    """Plan every editor install, and apply one only when its diff is still the one shown."""

    def __init__(
        self,
        *,
        context: Callable[[], Any] | None = None,
        apply_context: Callable[[], Any] | None = None,
    ) -> None:
        self._context = context or (
            lambda: installer_context.default_context(require_launcher=False)
        )
        self._apply_context = apply_context or installer_context.default_context

    def views(self, only: str | None = None) -> tuple[HostView, ...]:
        """Every host's plan, read-only. A host that raises is reported, never skipped silently."""
        try:
            ctx = self._context()
        except Exception as exc:
            return (
                HostView(
                    host="",
                    title="",
                    scopes=(),
                    status="error",
                    diff="",
                    fingerprint="",
                    errors=(f"the installer could not read its context: {_clean(exc)}",),
                ),
            )
        found: list[HostView] = []
        for adapter in hosts.all_adapters():
            if only is not None and adapter.id != only:
                continue
            try:
                result = engine.plan(adapter, ctx)
                installed = adapter.installed(ctx)
            except Exception as exc:
                found.append(
                    HostView(
                        host=adapter.id,
                        title=adapter.title,
                        scopes=(adapter.scope,),
                        status="error",
                        diff="",
                        fingerprint="",
                        errors=(_clean(exc),),
                    )
                )
                continue
            found.append(_view_for(result, (adapter.scope,), installed))
        return tuple(found)

    def apply(self, host_id: str, shown: str) -> str:
        """Apply one host, after re-planning and comparing the fingerprint shown on the page."""
        adapter = hosts.get_host(host_id)
        if adapter is None:
            raise ValueError(f"there is no host called {host_id!r}")
        ctx = self._apply_context()
        fresh = engine.plan(adapter, ctx)
        if not fresh.pending:
            return f"{adapter.title} already has this entry; nothing was written"
        if _host_fingerprint(fresh) != shown:
            raise ValueError(
                "this editor's config changed since the page was shown, so nothing was written; "
                "reload the wizard to see the diff again"
            )
        result = engine.apply(adapter, ctx)
        if result.errors:
            raise ValueError("; ".join(_clean(err) for err in result.errors)[:400])
        return f"{adapter.title}: wrote {len(result.written)} file(s)" + (
            ", verified" if result.verified else ""
        )


def _clean(exc: BaseException) -> str:
    return " ".join(str(exc).split())[:300]


@dataclass(frozen=True)
class AutostartView:
    """Step 6: the state of the at-logon task and the exact commands that would change it."""

    supported: bool
    state: str
    target: str
    argv: tuple[str, ...]
    xml: str
    note: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "supported": self.supported,
            "state": self.state,
            "target": self.target,
            "argv": list(self.argv),
            "xml": self.xml,
            "note": self.note,
        }


class AutostartGate:
    """``schtasks`` behind one gate: read the state, show the task, change it only on request.

    Windows only, by the product's own decision (``packaging/autostart.py`` is Task Scheduler and
    nothing else), so anywhere else this reports ``unsupported`` and changes nothing.
    """

    def __init__(
        self,
        *,
        install: Callable[[], Install] | None = None,
        runner: Callable[..., Any] | None = None,
        platform: str | None = None,
    ) -> None:
        self._install = install or Install.default
        self._runner = runner
        self._platform = platform or sys.platform

    @property
    def supported(self) -> bool:
        return self._platform == "win32"

    def view(self) -> AutostartView:
        """The current state, the task file's path and the commands, with nothing run."""
        if not self.supported:
            return AutostartView(
                supported=False,
                state="unsupported",
                target="",
                argv=(),
                xml="",
                note="the at-logon task is Task Scheduler, so it is available on Windows only",
            )
        try:
            install = self._install()
            state = autostart.status(runner=self._runner)
        except Exception as exc:
            return AutostartView(
                supported=False,
                state="unsupported",
                target="",
                argv=(),
                xml="",
                note=f"the at-logon task cannot be read here: {_clean(exc)}",
            )
        steps = autostart.create_steps(install.autostart_xml)
        xml = autostart.task_xml(user=autostart.current_user(), command=install.cli)
        return AutostartView(
            supported=True,
            state=state,
            target=str(install.autostart_xml),
            argv=tuple(" ".join(step.argv) for step in steps),
            xml=xml,
            note="restarting the daemon is not needed; the task applies at your next logon",
        )

    def set(self, on: bool) -> str:
        """Create, enable or disable the task. Refuses on any other platform, in words."""
        if not self.supported:
            raise ValueError("the at-logon task is Task Scheduler, so it is Windows only")
        install = self._install()
        state = autostart.status(runner=self._runner)
        if on:
            if state == autostart.STATUS_ENABLED:
                return "the at-logon task is already on"
            if state == autostart.STATUS_DISABLED:
                code = autostart.apply(autostart.enable_steps(), runner=self._runner)
                if code != 0:
                    raise ValueError(f"schtasks refused to enable the task (exit {code})")
                return "the at-logon task is on"
            _, code = autostart.create(install, runner=self._runner)
            if code != 0:
                raise ValueError(f"schtasks refused to create the task (exit {code})")
            return "the at-logon task was created and is on"
        if state != autostart.STATUS_ENABLED:
            return "the at-logon task is already off"
        code = autostart.apply(autostart.disable_steps(), runner=self._runner)
        if code != 0:
            raise ValueError(f"schtasks refused to disable the task (exit {code})")
        return "the at-logon task is off; the task itself is kept"


def autostart_view(gate: AutostartGate) -> dict[str, object]:
    return gate.view().as_dict()


def keys_view(providers: Sequence[str], *, error: str = "") -> dict[str, object]:
    """Step 4: plain provider names only (D18), and where a key is added instead of here."""
    return {
        "kind": "keys",
        "providers": [str(name) for name in providers],
        "count": len(providers),
        "error": error,
        "manage_url": "/?section=keys",
        "note": (
            "This page never shows a key, an alias or an environment variable name. "
            "Adding or removing one is the dashboard's Keys section."
        ),
    }


def mode_view(
    rules: Rules, *, scope_options: Sequence[str], strategy_options: Sequence[str]
) -> dict[str, object]:
    """Step 5: the two settings a surface may edit, with what is in force now."""
    return {
        "kind": "mode",
        "scope": str(rules.default_scope),
        "scope_options": [str(option) for option in scope_options],
        "strategy": str(rules.strategy),
        "strategy_options": [str(option) for option in strategy_options],
        "private_lock": bool(rules.private_lock),
        "airplane": bool(rules.airplane),
    }


def report_view(registry: FeatureRegistry) -> dict[str, object]:
    """Step 7: the report's switch and the exact preview, whether or not the package exists."""
    status = report_status(registry)
    preview = preview_report(registry)
    return {
        "kind": "report",
        "discovered": bool(status.get("discovered")),
        "enabled": bool(status.get("enabled")),
        "payload_version": status.get("payload_version"),
        "consented_version": status.get("consented_version"),
        "consent_current": bool(status.get("consent_current")),
        "endpoint_set": bool(status.get("endpoint_set")),
        "fields": status.get("fields"),
        "preview": None
        if preview is None
        else {
            "json": str(preview.get("json") or ""),
            "shape": [(str(path), str(rule)) for path, rule in (preview.get("shape") or ())][:64],
            "payload_version": preview.get("payload_version"),
            "consented_version": preview.get("consented_version"),
            "gate": preview.get("gate"),
            "endpoint_set": bool(preview.get("endpoint_set")),
        },
        "note": (
            ""
            if status.get("discovered")
            else "the ★ usage-report package is not installed, so this step can only be skipped"
        ),
    }


@dataclass(frozen=True)
class Check:
    """One self-test result. ``ok`` is ``None`` when the check does not apply on this machine."""

    name: str
    ok: bool | None
    detail: str

    def as_dict(self) -> dict[str, object]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


@dataclass(frozen=True)
class SelfTestInputs:
    """Everything step 8 asserts on, already gathered — so the test can gather fakes instead."""

    rules_source: str
    rules_error: str = ""
    catalog_models: int | None = None
    catalog_profiles: int | None = None
    catalog_error: str = ""
    state_round_trip: bool = False
    state_error: str = ""
    ollama_version: str | None = None
    ollama_error: str = ""
    installed: Sequence[str] | None = None
    installed_error: str = ""
    key_providers: Sequence[str] | None = None
    keys_error: str = ""
    report_discovered: bool = False
    report_error: str = ""
    route_url: str = ""
    route_ok: bool = False
    route_error: str = ""


def selftest_view(inputs: SelfTestInputs) -> dict[str, object]:
    """Step 8: one check per real thing that was done, and an explicit "no model was loaded"."""
    checks: list[Check] = [
        Check(
            "rules.yaml parses",
            not inputs.rules_error,
            inputs.rules_error or f"read {inputs.rules_source}",
        ),
        Check(
            "the catalog loads",
            None if inputs.catalog_error else bool(inputs.catalog_models),
            inputs.catalog_error
            or f"{inputs.catalog_models or 0} models, {inputs.catalog_profiles or 0} GPU profiles",
        ),
        Check(
            "the state database round-trips",
            inputs.state_round_trip if not inputs.state_error else False,
            inputs.state_error or "wrote, read back and deleted one key in the kv table",
        ),
        Check(
            "Ollama answers",
            None if inputs.ollama_error else bool(inputs.ollama_version),
            inputs.ollama_error or f"{inputs.ollama_version} reported by Ollama",
        ),
        Check(
            "models are installed",
            None if inputs.installed is None else bool(inputs.installed),
            inputs.installed_error
            or (
                "Ollama did not answer, so this was not checked"
                if inputs.installed is None
                else f"{len(inputs.installed)} model(s) installed"
            ),
        ),
        Check(
            "providers with a key",
            None if inputs.key_providers is None else True,
            inputs.keys_error
            or (
                "the credential store could not be read"
                if inputs.key_providers is None
                else f"{len(inputs.key_providers)} of the known providers have a key"
            ),
        ),
        Check(
            "the usage-report preview builds",
            None if not inputs.report_discovered else not inputs.report_error,
            ""
            if inputs.report_discovered and not inputs.report_error
            else (inputs.report_error or "the ★ usage-report package is not installed"),
        ),
        Check(
            "this daemon answers on its own route",
            inputs.route_ok,
            inputs.route_error or f"GET {inputs.route_url} answered",
        ),
    ]
    return {
        "kind": "selftest",
        "checks": [check.as_dict() for check in checks],
        "passed": sum(1 for check in checks if check.ok is True),
        "failed": sum(1 for check in checks if check.ok is False),
        "skipped": sum(1 for check in checks if check.ok is None),
        "runs_model": False,
        "note": (
            "It deliberately does not load a model: loading one takes VRAM the person may be "
            "using, and the fit maths already says what would fit."
        ),
    }
