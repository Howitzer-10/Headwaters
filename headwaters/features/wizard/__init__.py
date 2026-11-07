"""The setup wizard ★ (HDW-505): eight steps, each one skippable and safe to repeat.

The shape of this package is the shape of the ticket. ``steps`` says what each step is and turns
already-read data into the page's facts; ``envfix`` and ``pull`` are the two places that touch a
system (Ollama's environment, and Ollama's own ``/api/pull``); ``state`` remembers only what has to
survive a restart (a step record, and the values an undo has to put back); ``page`` renders.

This module wires them: it reads the world once per request, decides the sentences, and performs
exactly one enumerated action when one is asked for. Two rules are load-bearing:

* **Nothing is written before it is shown.** Every write is keyed on the fingerprint of what the
  page displayed, so a request can never introduce a value the person did not see.
* **A missing thing is said, not faked.** No catalog, no Ollama, no report package, no Windows and
  no GPU each become a sentence on the page; the step stays visible and stays skippable.
"""

from __future__ import annotations

import contextlib
import importlib
import secrets
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from typing import Any, get_args

import httpx

from headwaters.control.doctor import read_ollama_env, read_ollama_version
from headwaters.core import rules_edit
from headwaters.core.executor.ollama import PullLine, list_installed, pull_stream
from headwaters.core.features import FeatureRegistry, FeatureSpec
from headwaters.core.keys import NOT_SET, KeyStore, provider_items
from headwaters.core.ollama_remote import RemotePatterns, remote_patterns_from
from headwaters.core.plugins import CURRENT_API_VERSION, WizardPlugin
from headwaters.core.rules import STRATEGIES, Rules, Scope
from headwaters.core.rules_io import load_rules, rules_path
from headwaters.core.state_misc import kv_delete, kv_get, kv_set
from headwaters.core.usage_report import REPORT_FLAG, preview_report
from headwaters.core.wizard import WIZARD_MARKS, WIZARD_PATHS
from headwaters.features.wizard import envfix, steps
from headwaters.features.wizard import page as page_render
from headwaters.features.wizard import pull as pull_mod
from headwaters.features.wizard.state import (
    STEP_KEY,
    clear_env_backup,
    env_backup,
    record_env_backup,
    record_step,
    step_records,
)

__all__ = ["FEATURE_ID", "FEATURE_SPEC", "TITLE", "Wizard", "register"]

FEATURE_ID = "wizard"
TITLE = "Setup wizard"

FEATURE_SPEC = FeatureSpec(
    id=FEATURE_ID,
    star=True,
    title=TITLE,
    interfaces=("WizardPlugin",),
    default_enabled=True,
)

#: Which step each enumerated action belongs to, so a redirect can land back where it started.
ACTION_STEP: dict[str, int] = {
    "env_apply": 1,
    "env_undo": 1,
    "pull": 2,
    "host_apply": 3,
    "mode_set": 5,
    "autostart_set": 6,
    "report_consent": 7,
    "selftest": 8,
}

REPORT_STATE_MODULE = "headwaters.features.usage_report.state"


def _clean(exc: BaseException) -> str:
    return " ".join(str(exc).split())[:400]


def report_state() -> Any | None:
    """The report package's own state module, or ``None`` when it is not installed.

    Imported lazily and by name, exactly as ``headwaters/cli/cmd_report.py`` does: a static import
    would make the wizard fail to load when the ★ report package is absent, which is the one case
    step 7 exists to describe honestly.
    """
    try:
        return importlib.import_module(REPORT_STATE_MODULE)
    except ImportError:
        return None


class CoreOllama:
    """The three calls the wizard makes on Ollama, all through the one shipped client."""

    def version(self, host: str) -> tuple[str | None, str]:
        return read_ollama_version(host)

    def installed(self, host: str) -> tuple[str, ...]:
        return list_installed(host)

    def lines(self, host: str, model: str, patterns: RemotePatterns) -> Iterator[PullLine]:
        return pull_stream(host, model, remote_patterns=patterns)


class Wizard(WizardPlugin):
    """The eight steps over the live rules, catalog, hardware reading and state."""

    api_version = CURRENT_API_VERSION

    def __init__(
        self,
        registry: FeatureRegistry,
        *,
        ollama: Any = None,
        hosts: Any = None,
        autostart: Any = None,
        writer: Callable[[], envfix.EnvWriter] | None = None,
        probe: Callable[[], tuple[str, bool, str]] | None = None,
        pull: pull_mod.PullManager | None = None,
        keys: Callable[[], list[str]] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._registry = registry
        self._ollama = ollama or CoreOllama()
        self._hosts = hosts if hosts is not None else steps.HostGate()
        self._autostart = autostart if autostart is not None else steps.AutostartGate()
        self._writer = writer or envfix.platform_writer
        self._probe = probe
        self._pull = pull or pull_mod.PullManager(clock=clock)
        self._key_lister = keys
        self._clock = clock

    # ------------------------------------------------------------------ services

    def _services(self) -> Mapping[str, object]:
        return dict(getattr(self._registry, "services", {}) or {})

    def _db(self) -> Any:
        return self._services().get("db")

    def _need_db(self) -> Any:
        db = self._db()
        if db is None:
            raise ValueError("there is no state database, so this step cannot record anything")
        return db

    def _rules(self) -> Rules:
        rules = self._services().get("rules")
        return rules if isinstance(rules, Rules) else Rules()

    def _catalog(self) -> Any:
        return self._services().get("catalog")

    def _hardware(self) -> Any:
        reader = self._services().get("hardware")
        if reader is None:
            return None
        return reader() if callable(reader) else reader

    def _host(self) -> str:
        return str(self._rules().tiers.local.host).rstrip("/")

    def _patterns(self) -> RemotePatterns:
        catalog = self._catalog()
        return () if catalog is None else remote_patterns_from(catalog)

    def _writer_for(self) -> envfix.EnvWriter:
        return self._writer()

    def _current_env(self) -> dict[str, str]:
        """What Ollama sees now: the writer's own view when it has one, else the doctor's read.

        One seam for both, so a test never reads the real user environment and the page never
        reports a value from a different source than the one the write would change.
        """
        seen = dict(self._writer_for().read())
        return seen if seen else dict(read_ollama_env())

    def _records(self) -> dict[int, Any]:
        db = self._db()
        return {} if db is None else step_records(db)

    def _installed(self) -> tuple[tuple[str, ...] | None, str]:
        try:
            return tuple(self._ollama.installed(self._host())), ""
        except Exception as exc:
            return None, pull_mod.explain(exc, self._host())

    def _key_names(self) -> list[str]:
        if self._key_lister is not None:
            return list(self._key_lister())
        store = KeyStore(self._rules())
        return [item["provider"] for item in provider_items(store) if item.get("source") != NOT_SET]

    # ------------------------------------------------------------------ describe

    def status(self) -> dict[str, object]:
        records = self._records()
        listed = [
            {
                "n": definition.n,
                "id": definition.id,
                "title": definition.title,
                "state": "" if definition.n not in records else records[definition.n].state,
                "writes": definition.writes,
            }
            for definition in steps.STEPS
        ]
        return {
            "feature": FEATURE_ID,
            "title": TITLE,
            "discovered": True,
            "steps": listed,
            "step_count": len(steps.STEPS),
            "recorded": len(records),
            "writes": len(WIZARD_PATHS),
        }

    def page(
        self,
        *,
        csrf: str = "",
        step: int | None = None,
        error: str | None = None,
        notice: str | None = None,
    ) -> str:
        """The page for one step, with all eight in the rail. Reads only; a reload is safe."""
        records = self._records()
        views: list[dict[str, Any]] = []
        for definition in steps.STEPS:
            record = records.get(definition.n)
            views.append(
                {
                    "n": definition.n,
                    "id": definition.id,
                    "title": definition.title,
                    "summary": definition.summary,
                    "writes": definition.writes,
                    "state": "" if record is None else record.state,
                    "recorded": None if record is None else record.as_dict(),
                    "view": {},
                    "error": "",
                }
            )
        current = views[self._pick(views, step)]
        try:
            current["view"] = self._view(str(current["id"]))
        except Exception as exc:
            current["error"] = f"this step could not read what it needs: {_clean(exc)}"
        return page_render.render_page(
            steps=views,
            current=current,
            csrf=csrf,
            notice=notice or "",
            error=error or "",
        )

    @staticmethod
    def _pick(views: Sequence[Mapping[str, Any]], step: int | None) -> int:
        """The step asked for, else the first one not recorded yet, else the first."""
        if step is not None:
            for index, view in enumerate(views):
                if view.get("n") == step:
                    return index
        for index, view in enumerate(views):
            if not view.get("state"):
                return index
        return 0

    def _view(self, step_id: str) -> dict[str, object]:
        builder = {
            "detect": self._detect,
            "models": self._models,
            "ides": self._ides,
            "keys": self._keys,
            "mode": self._mode,
            "autostart": self._autostart_view,
            "report": self._report,
            "selftest": self._selftest,
        }.get(step_id)
        if builder is None:
            raise ValueError(f"there is no step called {step_id!r}")
        return builder()

    # ------------------------------------------------------------------ the steps

    def _detect(self) -> dict[str, object]:
        rules = self._rules()
        writer = self._writer_for()
        plan = envfix.plan_env(rules, self._current_env(), writer=writer)
        version, version_error = self._ollama.version(self._host())
        installed, installed_error = self._installed()
        db = self._db()
        return steps.detect_view(
            hardware=self._hardware(),
            catalog_present=self._catalog() is not None,
            ollama_host=self._host(),
            ollama_version=version,
            ollama_error=version_error or installed_error,
            installed=installed,
            plan=plan,
            env_undoable=db is not None and env_backup(db) is not None,
        )

    def _models(self) -> dict[str, object]:
        catalog = self._catalog()
        hardware = self._hardware()
        installed, error = self._installed()
        found = pull_mod.recommendations(
            catalog=catalog,
            hardware=hardware,
            rules=self._rules(),
            installed=installed or (),
        )
        profile = found[0].profile if found else (None if hardware is None else hardware.profile)
        return steps.models_view(
            profile=profile,
            recommendations=found,
            installed_known=installed is not None,
            ollama_error=error,
            pull=self._pull.snapshot(),
            pull_waiting=self._pull.running(),
        )

    def _ides(self) -> dict[str, object]:
        return {"kind": "ides", "hosts": [view.as_dict() for view in self._hosts.views()]}

    def _keys(self) -> dict[str, object]:
        try:
            return steps.keys_view(self._key_names())
        except Exception as exc:
            return steps.keys_view([], error=_clean(exc))

    def _mode(self) -> dict[str, object]:
        return steps.mode_view(
            self._rules(),
            scope_options=get_args(Scope),
            strategy_options=STRATEGIES,
        )

    def _autostart_view(self) -> dict[str, object]:
        return steps.autostart_view(self._autostart)

    def _report(self) -> dict[str, object]:
        return steps.report_view(self._registry)

    def _selftest(self) -> dict[str, object]:
        source = str(rules_path())
        rules_error = ""
        try:
            load_rules(rules_path())
        except Exception as exc:
            rules_error = _clean(exc)
        catalog = self._catalog()
        version, ollama_error = self._ollama.version(self._host())
        installed, installed_error = self._installed()
        try:
            key_providers: list[str] | None = self._key_names()
            keys_error = ""
        except Exception as exc:
            key_providers, keys_error = None, _clean(exc)
        report_error = ""
        try:
            discovered = preview_report(self._registry) is not None
        except Exception as exc:
            discovered, report_error = False, _clean(exc)
        url, route_ok, route_error = (self._probe or self._default_probe)()
        return steps.selftest_view(
            steps.SelfTestInputs(
                rules_source=source,
                rules_error=rules_error,
                catalog_models=None if catalog is None else len(catalog.models),
                catalog_profiles=None if catalog is None else len(catalog.gpu_profiles),
                catalog_error="" if catalog is not None else "the catalog did not load",
                state_round_trip=self._state_round_trip(),
                state_error="" if self._db() is not None else "there is no state database",
                ollama_version=version,
                ollama_error=ollama_error,
                installed=installed,
                installed_error=installed_error,
                key_providers=key_providers,
                keys_error=keys_error,
                report_discovered=discovered,
                report_error=report_error,
                route_url=url,
                route_ok=route_ok,
                route_error=route_error,
            )
        )

    def _state_round_trip(self) -> bool:
        db = self._db()
        if db is None:
            return False
        token = secrets.token_hex(6)
        kv_set(db, "wizard.selftest", {"token": token})
        read = kv_get(db, "wizard.selftest")
        kv_delete(db, "wizard.selftest")
        return isinstance(read, dict) and read.get("token") == token

    def _default_probe(self) -> tuple[str, bool, str]:
        """One real request to this daemon's own page on loopback, with a short timeout."""
        port = self._services().get("dashboard_port")
        if not port:
            return (
                "",
                False,
                "the daemon did not report its dashboard port, so its own route was not probed",
            )
        url = f"http://127.0.0.1:{int(port)}/"
        try:
            reply = httpx.get(url, timeout=2.0, trust_env=False)
        except Exception as exc:
            return url, False, f"{url} did not answer: {_clean(exc)}"
        ok = 200 <= int(reply.status_code) < 400
        return url, ok, "" if ok else f"{url} answered {reply.status_code}"

    # ------------------------------------------------------------------ act

    def act(self, action: str, form: Mapping[str, str]) -> dict[str, object]:
        """One enumerated action, refusing in words when it cannot be done honestly."""
        handler = {
            "env_apply": self._do_env_apply,
            "env_undo": self._do_env_undo,
            "pull": self._do_pull,
            "host_apply": self._do_host_apply,
            "mode_set": self._do_mode_set,
            "autostart_set": self._do_autostart_set,
            "report_consent": self._do_report_consent,
            "selftest": self._do_selftest,
            "mark": self._do_mark,
        }.get(action)
        if handler is None:
            return {
                "ok": False,
                "step": 0,
                "notice": None,
                "error": f"there is no wizard action called {action!r}",
            }
        try:
            step, notice = handler(form)
        except Exception as exc:
            return {
                "ok": False,
                "step": ACTION_STEP.get(action, 0),
                "notice": None,
                "error": _clean(exc),
            }
        return {"ok": True, "step": step, "notice": notice, "error": None}

    def _do_env_apply(self, form: Mapping[str, str]) -> tuple[int, str]:
        db = self._need_db()
        writer = self._writer_for()
        current = self._current_env()
        plan = envfix.plan_env(self._rules(), current, writer=writer)
        previous = envfix.apply_plan(
            plan, shown=form.get("fingerprint", ""), writer=writer, backup=current
        )
        record_env_backup(db, previous, plan.fingerprint)
        return 1, (
            f"changed {len(previous)} value(s). {envfix.RESTART_NOTE} "
            "The previous values are recorded, so the next button can put them back."
        )

    def _do_env_undo(self, form: Mapping[str, str]) -> tuple[int, str]:
        db = self._need_db()
        previous = env_backup(db)
        if not previous:
            raise ValueError(
                "there is nothing recorded to put back: the wizard only undoes values it wrote"
            )
        envfix.undo_values(previous, writer=self._writer_for())
        clear_env_backup(db)
        return 1, (f"put {len(previous)} value(s) back as they were. {envfix.RESTART_NOTE}")

    def _do_pull(self, form: Mapping[str, str]) -> tuple[int, str]:
        model = form.get("model", "").strip()
        catalog = self._catalog()
        known = set() if catalog is None else {entry.id for entry in catalog.models}
        if model not in known:
            raise ValueError(
                f"{model or 'that name'} is not a model in the catalog, so it will not be pulled"
            )
        job = self._pull.start(self._host(), model, self._patterns())
        if job.state == "refused":
            raise ValueError(job.error or "the pull was refused")
        return 2, (
            f"pulling {model} in the daemon, not in this page: reload to follow it. "
            "Reloading does not start it again."
        )

    def _do_host_apply(self, form: Mapping[str, str]) -> tuple[int, str]:
        notice = self._hosts.apply(form.get("host", ""), form.get("fingerprint", ""))
        return 3, notice

    def _do_mode_set(self, form: Mapping[str, str]) -> tuple[int, str]:
        scope = form.get("scope", "").strip()
        strategy = form.get("strategy", "").strip()
        allowed_scopes = set(get_args(Scope))
        if scope not in allowed_scopes:
            raise ValueError(f"{scope!r} is not a scope; pick one of {sorted(allowed_scopes)}")
        if strategy not in set(STRATEGIES):
            raise ValueError(f"{strategy!r} is not a strategy; pick one of {list(STRATEGIES)}")
        rules_edit.set_setting("default_scope", scope)
        rules_edit.set_setting("strategy", strategy)
        return 5, (
            f"defaults saved to rules.yaml: scope {scope}, strategy {strategy}. "
            "The daemon picks the file up on its own."
        )

    def _do_autostart_set(self, form: Mapping[str, str]) -> tuple[int, str]:
        on = form.get("on", "") == "1"
        return 6, self._autostart.set(on)

    def _do_report_consent(self, form: Mapping[str, str]) -> tuple[int, str]:
        on = form.get("on", "") == "1"
        if not on:
            rules_edit.set_nested("features", REPORT_FLAG, False)
            self._refresh_flags()
            return 7, (
                "the usage report is off. The preview record is kept, so "
                "`headwaters report send` can still send one report if you ask it to."
            )
        shown = preview_report(self._registry)
        if shown is None:
            raise ValueError(
                "the ★ usage-report package is not installed, so there is no payload to consent to"
            )
        module = report_state()
        if module is None:
            raise ValueError("the ★ usage-report package's state module is missing")
        version = int(str(shown.get("payload_version") or 0))
        if version <= 0:
            raise ValueError("the report package reported no payload version, so nothing was saved")
        module.record_consent(self._need_db(), payload_version=version)
        rules_edit.set_nested("features", REPORT_FLAG, True)
        self._refresh_flags()
        return 7, (
            f"the usage report is on, consented for payload version {version}. "
            "Nothing is sent until an endpoint is configured."
        )

    def _refresh_flags(self) -> None:
        """Re-read ``features:`` from disk, as a restart would, so this page is not stale."""
        with contextlib.suppress(Exception):
            self._registry.set_flags(load_rules(rules_path()).features)

    def _do_selftest(self, form: Mapping[str, str]) -> tuple[int, str]:
        return 8, "the checks ran; the result of each one is below"

    def _do_mark(self, form: Mapping[str, str]) -> tuple[int, str]:
        number = steps.step_index(form.get("step", "").strip())
        if number is None:
            raise ValueError(f"there is no step called {form.get('step', '')!r}")
        state = form.get("state", "").strip()
        db = self._need_db()
        if state == "clear":
            kv_delete(db, STEP_KEY.format(n=number))
            return number, f"the record for step {number} was cleared; nothing else changed"
        if state not in WIZARD_MARKS:
            raise ValueError(f"a step is done or skipped, not {state!r}")
        record_step(db, number, state)
        return number, (
            f"step {number} recorded as {state}. Nothing was written to this machine: "
            "every step recomputes what it shows when you come back to it."
        )


def register(registry: FeatureRegistry) -> FeatureSpec:
    """Register the ★ spec and its plugin, the way ``ledger`` and ``usage_report`` do."""
    registry.register(FEATURE_SPEC, {"WizardPlugin": Wizard(registry)})
    return FEATURE_SPEC
