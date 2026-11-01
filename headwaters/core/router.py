"""Router façade: the one core API every engine calls (HDW-117, v3 section 2, v3.4 section A).

``Router.handle(request)`` runs the whole pipeline:

    1. analyze          requirements (layers 1-3)
    2. resolve scope    Private lock > project rule > secret > requested > default
    3. decide           the policy ladder picks a model and its fallbacks
    4. execute          cloud through litellm, local through Ollama
    5. classify errors  wall events feed the quota tracker; cascade to the next candidate
                        (bounded attempts; a stream only fails over BEFORE its first token)
    6. verify           registered verifiers, per the ``verify`` setting
    7. escalate         ``EscalationPlugin`` when present; without one a failed check is reported
    8. log              one decision row and one outcome row (metadata only)

``handle`` returns an awaitable ``RouteResult`` or, when ``request.stream`` is set, an async
iterator of ``RouteEvent``; ``run`` and ``stream`` are the typed forms of the same thing.

Failure contract: ``ScopeError`` and ``NoRouteError`` (already structured) are raised as they are
when the first decision cannot be made. Once attempts have been made, running out of candidates
or attempts raises ``RouteFailed``. Local scope is fail-closed: candidates outside the effective
scope never reach the policy's choice, the executor refuses cloud tiers again, and a failure is a
structured error, never a cloud fallback. Streaming does not escalate (the caller already has the
tokens); a failed check is reported on the final result.

Privacy: this module never logs or stores request text, only decision metadata.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from headwaters.core import router_log
from headwaters.core.analyzer.requirements import AnalyzerInput, Requirements
from headwaters.core.catalog.schema import GpuProfile
from headwaters.core.executor.errors import ExecutorError, ScopeViolation
from headwaters.core.executor.executor import Executor
from headwaters.core.executor.types import ExecResult
from headwaters.core.features import FeatureRegistry, get_registry
from headwaters.core.hardware.residency import ResidencyConfig
from headwaters.core.hardware.state import HardwareState
from headwaters.core.plugins import EscalationPlugin, LedgerPlugin, VerifierPlugin
from headwaters.core.policy.decide import decide
from headwaters.core.policy.errors import NoRouteError
from headwaters.core.policy.filters import Candidate, hard_filter
from headwaters.core.policy.types import PolicyContext, PolicyRequest, RouteDecision
from headwaters.core.quotas import QuotaTracker, WallDecision
from headwaters.core.router_log import LogEntry
from headwaters.core.router_plan import RoutePlan
from headwaters.core.router_prep import (
    analyzer_input,
    apply_request_hints,
    clean_event,
    executor_messages,
    executor_options,
    local_usable,
    only_local,
    pull_hint,
    scan_groups,
    with_session_model,
    without_reasoning,
)
from headwaters.core.router_run import RouteRun
from headwaters.core.router_session import session_key
from headwaters.core.router_types import (
    Attempt,
    ProgressEvent,
    RouteEvent,
    RouteFailed,
    RouteRequest,
    RouteResult,
    Stage,
    VerifyOutcome,
)
from headwaters.core.router_verify import run_verifiers, wants_verify
from headwaters.core.rules import Rules, RulesProvider, as_provider
from headwaters.core.scope import (
    ScopeDecision,
    ScopeError,
    paid_scopes_available,
    resolve_scope,
)
from headwaters.core.secrets_detect import ScanLimits, SecretDetector, SecretMatch
from headwaters.core.state import Database
from headwaters.core.state_misc import SessionRow, get_session, pin_session

__all__ = ["CandidateSource", "RequirementsAnalyzer", "RoutePlan", "Router", "RouterConfig"]

CandidateSource = Callable[[Requirements], Sequence[Candidate]]


class RequirementsAnalyzer(Protocol):
    """``Analyzer`` and ``AnalyzerChain`` both satisfy this."""

    def analyze(self, inp: AnalyzerInput) -> Requirements: ...


@dataclass(frozen=True)
class RouterConfig:
    """``max_attempts`` bounds every model call of one request (retries, cascades and
    escalations together). First-draft value, see docs/ASSUMPTIONS.md."""

    max_attempts: int = 4
    residency: ResidencyConfig = field(default_factory=ResidencyConfig)

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")


@dataclass(frozen=True)
class _Selection:
    """Steps 2-3 of the pipeline: the scope, the candidates it was decided on, the first pick."""

    rules: Rules
    scope: ScopeDecision
    candidates: tuple[Candidate, ...]
    hw: HardwareState | None
    first: RouteDecision


class Router:
    def __init__(
        self,
        rules: Rules | RulesProvider,
        db: Database,
        analyzer: RequirementsAnalyzer,
        candidates: CandidateSource,
        executor: Executor,
        quotas: QuotaTracker,
        *,
        secrets: SecretDetector | None = None,
        verifiers: Sequence[VerifierPlugin] = (),
        registry: FeatureRegistry | None = None,
        hardware: Callable[[], HardwareState | None] | None = None,
        config: RouterConfig | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        gpu_profiles: Mapping[str, GpuProfile] | None = None,
    ) -> None:
        self._gpu_profiles: Mapping[str, GpuProfile] = gpu_profiles or {}
        self._rules_source = as_provider(rules)  # read once per request (HDW-131)
        self._db = db
        self._analyzer = analyzer
        self._candidates = candidates
        self._executor = executor
        self._quotas = quotas
        self._secrets = secrets
        self._verifiers = tuple(verifiers)
        self._registry = registry if registry is not None else get_registry()
        self._hardware = hardware
        self._config = config or RouterConfig()
        self._sleep = sleep

    # ------------------------------------------------------------------ public API

    def handle(
        self, request: RouteRequest
    ) -> Coroutine[Any, Any, RouteResult] | AsyncIterator[RouteEvent]:
        """``request.stream`` False -> awaitable ``RouteResult``; True -> async ``RouteEvent``s."""
        return self.stream(request) if request.stream else self.run(request)

    async def run(self, request: RouteRequest) -> RouteResult:
        run = await self._prepare(request)
        decision = run.first
        while True:
            decision, result = await self._execute(run, decision)
            verify = self._verify(run, decision, result)
            if verify is not None and verify.failed:
                run.attempts.append(Attempt(decision.model, decision.tier, "verify_failed"))
                nxt = self._escalate(run, decision, verify)
                if nxt is not None:
                    decision = nxt
                    continue
            else:
                run.attempts.append(Attempt(decision.model, decision.tier, "ok"))
            return self._finish(run, decision, result, verify)

    async def stream(self, request: RouteRequest) -> AsyncIterator[RouteEvent]:
        run = await self._prepare(request)
        decision = run.first
        while True:
            events = self._executor.run(decision, run.messages, request.tools, True, run.options)
            seen_first = False
            result: ExecResult | None = None
            failure: ExecutorError | None = None
            try:
                async for raw in events:
                    event = clean_event(raw)  # D12: no reasoning text on the wire
                    if event is None:
                        continue
                    if event.kind == "first_token":
                        seen_first = True
                        self._emit(run, "executing", decision, "first_token")
                        yield RouteEvent("route", decision=decision)
                    elif event.kind == "done":
                        result = event.result
                    elif event.kind == "error":
                        failure = event.error
                    yield RouteEvent("stream", event=event)
            except ExecutorError as exc:
                failure = exc
            finally:
                aclose = getattr(events, "aclose", None)
                if aclose is not None:
                    await aclose()
            if failure is None and result is not None:
                verify = self._verify(run, decision, result)
                failed = verify is not None and verify.failed
                run.attempts.append(
                    Attempt(decision.model, decision.tier, "verify_failed" if failed else "ok")
                )
                yield RouteEvent("final", result=self._finish(run, decision, result, verify))
                return
            if failure is None:
                failure = ExecutorError(
                    "no_result", "The model returned no result.", model=decision.model
                )
            if seen_first or not failure.before_first_token:
                # Tokens already reached the caller: never switch models mid-stream.
                self._record_wall(run, decision, failure)
                run.attempts.append(Attempt(decision.model, decision.tier, failure.code))
                self._log_failure(run, decision, failure.code)
                yield RouteEvent("final", error=failure)
                return
            decision = await self._recover(run, decision, failure)

    # ------------------------------------------------------------------ steps 1-3

    async def plan(self, request: RouteRequest) -> RoutePlan:
        """Dry run of steps 1-3: which model would answer, and why. For ``headwaters pick``.

        Calls no model, writes nothing (no decision row, no session pin) and never logs
        request text. Raises ``ScopeError`` / ``NoRouteError`` exactly as ``handle`` would.
        """
        key = session_key(
            messages=request.messages,
            system=request.system,
            headers=request.headers,
            explicit=request.session_id,
        )
        reqs = await self._analyze(request)
        chosen = self._select(request, key, reqs)
        rejected = hard_filter(
            chosen.candidates, reqs.hard, chosen.scope, chosen.rules, chosen.hw
        ).rejected
        return RoutePlan(reqs, chosen.scope, chosen.first, chosen.candidates, rejected, key)

    async def _analyze(self, request: RouteRequest) -> Requirements:
        """Layer 1-3 analysis, then the caller's ``task_hint`` / ``reasoning`` (HDW-140)."""
        found = await asyncio.to_thread(self._analyzer.analyze, analyzer_input(request))
        return apply_request_hints(found, request)

    async def _prepare(self, request: RouteRequest) -> RouteRun:
        started = time.monotonic()
        key = session_key(
            messages=request.messages,
            system=request.system,
            headers=request.headers,
            explicit=request.session_id,
        )
        self._progress(request, "analyzing")
        reqs = await self._analyze(request)
        scope_name = request.scope or self._rules_source().default_scope
        try:
            chosen = self._select(request, key, reqs)
        except (ScopeError, NoRouteError) as exc:
            self._log_early(request, key, started, reqs, scope_name, exc)
            raise
        return RouteRun(
            request=request,
            key=key,
            started=started,
            reqs=reqs,
            scope=chosen.scope,
            candidates=chosen.candidates,
            hw=chosen.hw,
            messages=executor_messages(request),
            options=executor_options(request),
            first=chosen.first,
        )

    def _select(self, request: RouteRequest, key: str, reqs: Requirements) -> _Selection:
        """Steps 2-3: secrets, candidates, scope and the policy ladder. No side effects."""
        rules = self._rules_source()  # one reading for the whole selection
        matches = self._scan_secrets(request)
        candidates = self._with_live_availability(self._candidates(reqs))
        if rules.airplane:
            candidates = only_local(candidates)
        session = self._get_session(key)
        last_good = session.pinned_model if session is not None else None
        candidates = with_session_model(candidates, last_good)
        hw = self._hardware() if self._hardware is not None else None
        hint = pull_hint(reqs, hw, self._gpu_profiles)
        self._progress(request, "scope")
        scope = resolve_scope(
            rules,
            request.project,
            requested=request.scope,
            strategy=request.strategy,
            verify=request.verify,
            secret_matches=matches,
            secret_override=request.secret_override,
            paid_scopes=paid_scopes_available(self._registry),
            local_available=local_usable(candidates),
            local_fix_hint=hint,
        )
        self._progress(request, "deciding")
        first = decide(
            PolicyRequest(reqs, scope, request.pin, request.force),
            PolicyContext(candidates, rules, hw, self._escalation(), hint),
        )
        return _Selection(rules, scope, candidates, hw, first)

    def _with_live_availability(self, candidates: Sequence[Candidate]) -> tuple[Candidate, ...]:
        """Walls live in shared SQLite, so another engine may have set one a moment ago.

        The tracker is read on every request; a wall or cooldown it knows about wins over what
        the candidate source said, while ``ok`` leaves the source's own reading alone.
        """
        out: list[Candidate] = []
        for c in candidates:
            provider = c.row.provider
            live = self._quotas.availability(
                c.id, provider, self._executor.key_alias(provider), c.tier
            )
            out.append(c if live.status == "ok" else replace(c, availability=live))
        return tuple(out)

    def _scan_secrets(self, request: RouteRequest) -> list[SecretMatch]:
        if self._secrets is None:
            return []
        limits = ScanLimits.from_rules(self._rules_source().secrets)
        return self._secrets.scan_groups(scan_groups(request), limits)

    def _get_session(self, key: str) -> SessionRow | None:
        try:
            return get_session(self._db, key)
        except Exception:  # a locked or missing database must not block routing
            return None

    # ------------------------------------------------------------------ steps 4-5

    async def _execute(
        self, run: RouteRun, decision: RouteDecision
    ) -> tuple[RouteDecision, ExecResult]:
        """Run ``decision``; on a failure before any answer, retry or cascade (bounded)."""
        while True:
            self._emit(run, "executing", decision)
            try:
                result = await self._executor.run(
                    decision, run.messages, run.request.tools, False, run.options
                )
            except ExecutorError as exc:
                decision = await self._recover(run, decision, exc)
                continue
            return decision, without_reasoning(result)  # D12: reasoning text never leaves

    def _record_wall(
        self, run: RouteRun, decision: RouteDecision, exc: ExecutorError
    ) -> WallDecision | None:
        """Give the wall classifier's reading to the quota tracker (it persists the cooldown)."""
        if exc.wall is None:
            return None
        cand = run.candidate(decision.model)
        provider = cand.row.provider if cand is not None else exc.provider
        if exc.status == 429:  # a rate-limited call still used the model's allowance (F22)
            self._quotas.count_request(decision.model, provider)
        return self._quotas.record_wall(
            exc.wall,
            model=decision.model,
            provider=provider,
            key=self._executor.key_alias(provider),
            tier=decision.tier,
        )

    async def _recover(
        self, run: RouteRun, decision: RouteDecision, exc: ExecutorError
    ) -> RouteDecision:
        """Handle a failed call: the decision to try next, or raise ``RouteFailed``."""
        if isinstance(exc, ScopeViolation):  # a scope breach is never retried or cascaded
            run.attempts.append(Attempt(decision.model, decision.tier, exc.code))
            self._log_failure(run, decision, exc.code)
            raise RouteFailed(
                exc.code, exc.message, attempts=run.attempts, last_error=exc.as_dict()
            ) from exc
        wall = self._record_wall(run, decision, exc)
        wait = wall.wait_s if wall is not None and wall.action == "retry" else 0.0
        run.attempts.append(Attempt(decision.model, decision.tier, exc.code, wait))
        if len(run.attempts) >= self._config.max_attempts:
            self._log_failure(run, decision, "attempts_exhausted")
            raise RouteFailed(
                "attempts_exhausted",
                "Every allowed attempt failed.",
                attempts=run.attempts,
                last_error=exc.as_dict(),
            ) from exc
        if wall is not None and wall.action == "retry":
            run.notes.append(f"{decision.model}: {exc.code}, retry in {wait:.1f}s")
            self._emit(run, "retrying", decision, exc.code)
            await self._sleep(wait)
            return decision
        run.excluded.add(decision.model)
        run.notes.append(f"{decision.model}: {exc.code}, cascading")
        self._emit(run, "cascading", decision, exc.code)
        try:
            return self._redecide(run, decision, exc.as_dict())
        except RouteFailed:
            self._log_failure(run, decision, "no_route_left")
            raise

    def _redecide(
        self,
        run: RouteRun,
        last: RouteDecision,
        last_error: Mapping[str, object] | None = None,
        *,
        stronger_than: float | None = None,
    ) -> RouteDecision:
        """Decide again without the models already tried, using fresh walls and quotas.

        ``stronger_than`` (escalation, F10) keeps only candidates whose task score is above it
        and passes ``escalation=None`` so the optimistic rung cannot fire again. The scope and
        the hard filter still apply, so a local-only request retries on local models only.
        """
        pool = [c for c in run.candidates if c.id not in run.excluded]
        escalation = self._escalation()
        if stronger_than is not None:
            task = run.reqs.soft.task
            pool = [c for c in pool if c.scores.get(task, 0.0) > stronger_than]
            escalation = None
        fresh = self._with_live_availability(pool)
        try:
            return decide(
                PolicyRequest(run.reqs, run.scope, run.request.pin, run.request.force),
                PolicyContext(fresh, self._rules_source(), run.hw, escalation),
            )
        except NoRouteError as err:
            raise RouteFailed(
                "no_route_left",
                "No other model can serve this request in the current scope.",
                attempts=run.attempts,
                last_error=last_error,
                no_route=err,
            ) from err

    # ------------------------------------------------------------------ steps 6-7

    def _plugins(self, interface: str) -> list[Any]:
        return list(self._registry.plugins_for(interface))

    def _escalation(self) -> EscalationPlugin | None:
        found = self._plugins("EscalationPlugin")
        return found[0] if found else None

    def _ledgers(self) -> list[LedgerPlugin]:
        return self._plugins("LedgerPlugin")

    def _verify(
        self, run: RouteRun, decision: RouteDecision, result: ExecResult
    ) -> VerifyOutcome | None:
        if not wants_verify(
            run.scope.verify, agentic=run.reqs.soft.agentic, required=decision.verify_required
        ):
            return None
        self._emit(run, "verifying", decision)
        verifiers = [*self._verifiers, *self._plugins("VerifierPlugin")]
        outcome = run_verifiers(
            verifiers,
            result,
            tools=run.request.tools,
            response_format=run.request.response_format,
        )
        if decision.verify_required and outcome.checked == 0:
            run.notes.append(
                f"{decision.model}: mandatory verification checked nothing (unchecked)"
            )
            return replace(outcome, unchecked=True)
        return outcome

    def _escalate(
        self, run: RouteRun, decision: RouteDecision, verify: VerifyOutcome
    ) -> RouteDecision | None:
        """The next decision after a failed check, or ``None`` to report the failure as it is."""
        if len(run.attempts) >= self._config.max_attempts:
            return None
        plugin = self._escalation()
        if plugin is None:
            # D10: the core's one verified retry, only for a mandatory verification.
            if not decision.verify_required or run.escalations >= 1:
                return None
        else:
            try:
                wanted = plugin.should_escalate(run.escalations + 1, verify.problems)
            except Exception:  # a broken plugin means: do not escalate
                return None
            if not wanted:
                return None
        run.excluded.add(decision.model)
        try:
            # F10: go up, never sideways. Only models with a higher task score qualify.
            nxt = self._redecide(run, decision, stronger_than=decision.breakdown.quality)
        except RouteFailed:
            run.excluded.discard(decision.model)
            return None  # nothing stronger left: hand back the answer we have, flagged as failed
        run.escalations += 1
        run.notes.append(f"{decision.model}: verification failed, escalating to {nxt.model}")
        self._emit(run, "escalating", decision, "verify_failed")
        return nxt

    # ------------------------------------------------------------------ step 8

    def _finish(
        self,
        run: RouteRun,
        decision: RouteDecision,
        result: ExecResult,
        verify: VerifyOutcome | None,
    ) -> RouteResult:
        cand = run.candidate(decision.model)
        provider = cand.row.provider if cand is not None else ""
        self._quotas.record_success(
            model=decision.model, provider=provider, key=self._executor.key_alias(provider)
        )
        cost = (
            router_log.cost_usd(cand.row.price_in, cand.row.price_out, result)
            if cand is not None
            else None
        )
        failed = verify is not None and verify.failed
        entry = self._entry(
            run,
            decision,
            "verify_failed" if failed else "ok",
            result=result,
            verify=verify,
            cost=cost,
            escalated=decision.model if run.escalations and not failed else None,
        )
        decision_id = router_log.write(self._db, entry)
        router_log.notify_ledgers(self._ledgers(), entry, decision_id)
        if failed:  # F16: never pin a model whose answer just failed verification
            self._forget(run, decision)
        else:
            self._remember(run, decision)
        self._emit(run, "done", decision, "verify_failed" if failed else "ok")
        return RouteResult(
            result=result,
            decision=decision,
            session_key=run.key,
            attempts=tuple(run.attempts),
            decision_id=decision_id,
            verify=verify,
            first_choice=run.first.model if run.first.model != decision.model else None,
            reasons=(*decision.reasons, *run.notes),
        )

    def _entry(
        self,
        run: RouteRun,
        decision: RouteDecision | None,
        status: str,
        *,
        result: ExecResult | None = None,
        verify: VerifyOutcome | None = None,
        cost: float | None = None,
        escalated: str | None = None,
    ) -> LogEntry:
        return LogEntry(
            surface=run.request.surface,
            session=run.key,
            scope=run.scope.scope,
            strategy=run.scope.strategy,
            status=status,
            latency_ms=int((time.monotonic() - run.started) * 1000),
            locks=run.scope.locks,
            requirements=run.reqs,
            decision=decision,
            attempts=tuple(run.attempts),
            notes=tuple(run.notes),
            result=result,
            verify=verify,
            cost_usd=cost,
            escalated_to=escalated,
        )

    def _log_failure(self, run: RouteRun, decision: RouteDecision, status: str) -> None:
        entry = self._entry(run, decision, status)
        router_log.notify_ledgers(self._ledgers(), entry, router_log.write(self._db, entry))

    def _log_early(
        self,
        request: RouteRequest,
        key: str,
        started: float,
        reqs: Requirements,
        scope_name: str,
        exc: ScopeError | NoRouteError,
    ) -> None:
        """Log a request that never got a decision (a scope error or no candidate)."""
        entry = LogEntry(
            surface=request.surface,
            session=key,
            scope=scope_name,
            strategy=request.strategy or self._rules_source().strategy,
            status=exc.code,
            latency_ms=int((time.monotonic() - started) * 1000),
            requirements=reqs,
            notes=(f"{type(exc).__name__}: {exc.code}",),
        )
        router_log.notify_ledgers(self._ledgers(), entry, router_log.write(self._db, entry))

    def _remember(self, run: RouteRun, decision: RouteDecision) -> None:
        """Pin the model that answered ``ok`` so the next turn of this session keeps it (D11)."""
        session = self._get_session(run.key)
        if session is not None and session.pinned_model == decision.model:
            return
        try:
            pin_session(self._db, run.key, decision.model, decision.tier)
        except Exception:  # best effort
            return

    def _forget(self, run: RouteRun, decision: RouteDecision) -> None:
        """Drop the pin when the pinned model itself just failed verification (F16)."""
        session = self._get_session(run.key)
        if session is None or session.pinned_model != decision.model:
            return
        try:
            pin_session(self._db, run.key, None, None)
        except Exception:  # best effort
            return

    # ------------------------------------------------------------------ progress

    @staticmethod
    def _progress(request: RouteRequest, stage: Stage, **kw: Any) -> None:
        if request.on_progress is None:
            return
        try:
            request.on_progress(ProgressEvent(stage, **kw))
        except Exception:  # a callback must never break routing
            return

    def _emit(self, run: RouteRun, stage: Stage, decision: RouteDecision, detail: str = "") -> None:
        self._progress(
            run.request, stage, attempt=len(run.attempts) + 1, model=decision.model, detail=detail
        )
