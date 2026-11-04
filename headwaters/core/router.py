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
import math
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, Protocol

from headwaters.core import router_log
from headwaters.core.analyzer.requirements import AnalyzerInput, Requirements
from headwaters.core.catalog.schema import GpuProfile
from headwaters.core.executor.errors import ExecutorError, ScopeViolation
from headwaters.core.executor.executor import Executor
from headwaters.core.executor.types import ExecResult
from headwaters.core.features import FeatureRegistry, get_registry
from headwaters.core.hardware.state import HardwareState
from headwaters.core.plugins import EscalationPlugin, LedgerPlugin, VerifierPlugin
from headwaters.core.policy.decide import decide
from headwaters.core.policy.errors import NoRouteError, Suggestion
from headwaters.core.policy.filters import Candidate, hard_filter
from headwaters.core.policy.scoring import AGENTIC_TASK
from headwaters.core.policy.tool_budget import (
    HEADWATERS_NAMESPACE,
    AmbiguousTools,
    Composition,
    attribute,
    diagnose,
    measure,
    tool_name,
)
from headwaters.core.policy.types import PolicyContext, PolicyRequest, RouteDecision
from headwaters.core.quotas import QuotaTracker, WallDecision
from headwaters.core.router_failures import redact, terminal_message
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
    TierName,
    paid_scopes_available,
    resolve_scope,
)
from headwaters.core.secrets_detect import ScanLimits, SecretDetector, SecretMatch
from headwaters.core.state import Database
from headwaters.core.state_misc import SessionRow, get_session, pin_session

__all__ = ["CandidateSource", "RequirementsAnalyzer", "RoutePlan", "Router", "RouterConfig"]

CandidateSource = Callable[[Requirements], Sequence[Candidate]]

# HDW-511: the rejection reasons that mean "the window is too small", never "pull a model".
_CONTEXT_REASONS = frozenset({"ctx_too_small", "ctx_unknown", "no_fit_context_too_large"})


class RequirementsAnalyzer(Protocol):
    """``Analyzer`` and ``AnalyzerChain`` both satisfy this."""

    def analyze(self, inp: AnalyzerInput) -> Requirements: ...


@dataclass(frozen=True)
class RouterConfig:
    """``max_attempts`` bounds every model call of one request (retries, cascades and
    escalations together). ``None`` reads ``router.max_attempts`` from the live rules."""

    max_attempts: int | None = None

    def __post_init__(self) -> None:
        if self.max_attempts is not None and self.max_attempts < 1:
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

    def _max_attempts(self) -> int:
        override = self._config.max_attempts
        return override if override is not None else self._rules_source().router.max_attempts

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
            events = self._executor.run(
                decision,
                run.messages,
                request.tools,
                True,
                run.options,
                conversation_id=run.key,
            )
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
                # Tokens already reached the caller: never switch models mid-stream. The session
                # drops the pin, exactly as it does after a failed verification (FU-134-3).
                self._record_wall(run, decision, failure)
                run.attempts.append(Attempt(decision.model, decision.tier, failure.code))
                self._log_failure(run, decision, failure.code)
                self._forget(run, decision)
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
        note = ""
        try:
            chosen = self._select(request, key, reqs)
        except (ScopeError, NoRouteError) as exc:
            recovered = await self._recover_selection(request, key, reqs, exc)
            if isinstance(recovered, Exception):
                raise recovered from None
            request, reqs, chosen, note = recovered
        first = (
            replace(chosen.first, reasons=(*chosen.first.reasons, note)) if note else chosen.first
        )
        rejected = hard_filter(
            chosen.candidates, reqs.hard, chosen.scope, chosen.rules, chosen.hw
        ).rejected
        return RoutePlan(reqs, chosen.scope, first, chosen.candidates, rejected, key)

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
        note = ""
        try:
            chosen = self._select(request, key, reqs)
        except (ScopeError, NoRouteError) as exc:
            recovered = await self._recover_selection(request, key, reqs, exc)
            if isinstance(recovered, Exception):
                self._log_early(request, key, started, reqs, scope_name, recovered)
                raise recovered from None
            request, reqs, chosen, note = recovered
        run = RouteRun(
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
        if note:
            run.notes.append(note)
        return run

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

    # ------------------------------------------------- HDW-511: tool-cost diagnosis

    async def _recover_selection(
        self, request: RouteRequest, key: str, reqs: Requirements, exc: ScopeError | NoRouteError
    ) -> tuple[RouteRequest, Requirements, _Selection, str] | ScopeError | NoRouteError:
        """Explain a context shortfall, or prune non-headwaters tools when allowed.

        Returns either a pruned selection (with the note to record) or the error the caller
        should raise. Anything else — no tools, a non-context failure, a scope that is not
        local-only — leaves ``exc`` untouched.
        """
        if not request.tools:
            return exc
        composition = measure(self._analyzer, analyzer_input(request), reqs)
        if composition is None or composition.tools <= 0:
            return exc
        scope = self._local_only_scope(request)
        if scope is None:
            return exc
        pool = self._with_live_availability(self._candidates(reqs))
        if not self._context_shortfall(exc, pool, scope):
            return exc
        rules = self._rules_source()
        if rules.tools.prune_to_fit:
            pruned = await self._prune_to_fit(request, key, reqs, composition)
            if pruned is not None:
                return pruned
        return self._honest_error(exc, composition, reqs, pool, scope)

    def _context_shortfall(
        self, exc: ScopeError | NoRouteError, pool: Sequence[Candidate], scope: ScopeDecision
    ) -> bool:
        """True only when context is why nothing in scope could answer (HDW-511)."""
        if isinstance(exc, ScopeError):
            if exc.code != "local_unavailable":
                return False
            return any(
                c.is_local and c.fit is not None and c.fit.reason == "context_too_large"
                for c in pool
            )
        tiers = {c.id: c for c in pool}
        reasons = {
            rejection.reason
            for rejection in exc.rejected
            if (c := tiers.get(rejection.model_id)) is None or c.tier in scope.tiers
        }
        return bool(reasons) and reasons <= _CONTEXT_REASONS

    def _local_only_scope(self, request: RouteRequest) -> ScopeDecision | None:
        """The effective scope, when it allows local models only; ``None`` otherwise."""
        try:
            scope = resolve_scope(
                self._rules_source(),
                request.project,
                requested=request.scope,
                strategy=request.strategy,
                verify=request.verify,
                secret_matches=self._scan_secrets(request),
                secret_override=request.secret_override,
                paid_scopes=paid_scopes_available(self._registry),
                local_available=True,
            )
        except ScopeError:
            return None
        return scope if scope.local_only else None

    def _honest_error(
        self,
        exc: ScopeError | NoRouteError,
        composition: Composition,
        reqs: Requirements,
        pool: Sequence[Candidate],
        scope: ScopeDecision,
    ) -> ScopeError | NoRouteError:
        """Rewrite a context failure to state the real composition and the real fixes."""
        rules = self._rules_source()
        headroom = rules.scoring.ctx_headroom
        diagnosis = diagnose(
            composition,
            needed=math.ceil(headroom * reqs.hard.min_ctx),
            headroom=headroom,
            local_ctx=[c.row.ctx for c in pool if c.is_local and c.row.ctx],
            prune_enabled=rules.tools.prune_to_fit,
            widen=not scope.active_locks,
        )
        if isinstance(exc, ScopeError):
            return ScopeError(exc.code, diagnosis.message, diagnosis.fix_hint)
        tips = tuple(s for s in exc.suggestions if s.code != "pull_model")
        return NoRouteError(
            exc.code,
            diagnosis.message,
            etas=exc.etas,
            suggestions=(*tips, Suggestion("fix_tool_cost", diagnosis.fix_hint)),
            rejected=exc.rejected,
        )

    async def _prune_to_fit(
        self, request: RouteRequest, key: str, reqs: Requirements, composition: Composition
    ) -> tuple[RouteRequest, Requirements, _Selection, str] | None:
        """Drop the largest non-headwaters namespaces until a local model fits (HDW-511).

        Cumulative and largest-first, so the smallest number of drops does it. Every attempt
        re-runs analysis and the normal selection path; the first that yields an answer wins.
        Returns ``None`` when nothing helps, leaving the original request untouched.
        """
        rules = self._rules_source()
        tools = list(request.tools or ())
        names = [tool_name(spec) for spec in tools]
        try:
            mapping = attribute(names)
        except AmbiguousTools:
            return None
        namespaces = sorted(
            {namespace for namespace in mapping.values() if namespace != HEADWATERS_NAMESPACE},
            key=lambda namespace: (-composition.namespace_tokens(namespace), namespace),
        )
        if not namespaces:
            return None
        pool = self._with_live_availability(self._candidates(reqs))
        largest = max((c.row.ctx or 0 for c in pool if c.is_local), default=0)
        budget = largest or max(rules.tiers.local.ctx_steps)
        if composition.images > budget or composition.messages > budget:
            return None
        dropped: list[str] = []
        for namespace in namespaces:
            dropped.append(namespace)
            removed = set(dropped)
            kept = tuple(
                spec
                for spec, name in zip(tools, names, strict=True)
                if mapping[name] not in removed
            )
            pruned = replace(request, tools=kept)
            pruned_reqs = await self._analyze(pruned)
            try:
                chosen = self._select(pruned, key, pruned_reqs)
            except (ScopeError, NoRouteError):
                continue
            note = "tools pruned to fit a local model: dropped " + ", ".join(
                f"{name} ({composition.namespace_tokens(name):,} tokens)" for name in dropped
            )
            return pruned, pruned_reqs, chosen, note
        return None

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
                    decision,
                    run.messages,
                    run.request.tools,
                    False,
                    run.options,
                    conversation_id=run.key,
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
                exc.code, redact(exc.message), attempts=run.attempts, last_error=exc.as_dict()
            ) from exc
        wall = self._record_wall(run, decision, exc)
        waiting = wall is not None and wall.action == "retry"
        wait = wall.wait_s if wall is not None and waiting else 0.0
        cand = run.candidate(decision.model)
        provider = cand.row.provider if cand is not None else (exc.provider or "")
        # HDW-513 (slice C): a cloud provider the key store has no key for cannot answer, so the
        # failure is remembered as authentication, not as a model-quality problem. The check is
        # the cached key store's own lookup: no network call on the hot path.
        missing = bool(cand is not None and not cand.is_local) and (
            self._executor.key_present(provider) is False
        )
        rec = run.failures.record(
            decision.model,
            decision.tier,
            provider,
            exc,
            retrying=waiting,
            missing_key=missing,
        )
        run.attempts.append(Attempt(decision.model, decision.tier, rec.code, wait))
        if len(run.attempts) >= self._max_attempts():
            self._log_failure(run, decision, "attempts_exhausted")
            raise RouteFailed(
                "attempts_exhausted",
                self._failure_message(run, "attempts_exhausted"),
                attempts=run.attempts,
                last_error=exc.as_dict(),
            ) from exc
        if waiting:
            run.notes.append(f"{decision.model}: {exc.code}, retry in {wait:.1f}s")
            self._emit(run, "retrying", decision, exc.code)
            await self._sleep(wait)
            return decision
        if run.request.pin_strict and run.request.pin is not None:
            # HDW-304: a pinned model fails as itself. A retry of the same model is still the
            # same model, so only the failover to another model is off.
            run.notes.append(f"{decision.model}: {rec.code}, pinned (no failover)")
            self._log_failure(run, decision, rec.code)
            raise RouteFailed(
                rec.code, redact(exc.message), attempts=run.attempts, last_error=exc.as_dict()
            ) from exc
        if missing and cand is not None:
            # Slice C: the provider cannot authenticate, so do not spend another attempt on any
            # of its models. Cheap (the key store is cached) and it leaves the attempt budget to
            # tiers that can actually answer.
            skipped = run.failures.exclude_provider(run.candidates, provider)
            if skipped:
                run.notes.append(f"no key for {provider}: skipping its {skipped} models")
        run.notes.append(run.failures.skip_line(len(run.attempts) + 1, self._max_attempts()))
        run.notes.append(f"{decision.model}: {rec.code}, cascading")
        self._emit(run, "cascading", decision, rec.code)
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
        the hard filter still apply, so a local-only request retries on local models only. For an
        agentic request the escalation also keeps tool-call strength (HDW-154, D10): a candidate
        whose ``agentic_tools`` score is *below* the failed model's is skipped, and when that
        filter is what empties the pool the note says so.
        """
        pool = [c for c in run.candidates if c.id not in run.excluded]
        escalation = self._escalation()
        if stronger_than is not None:
            task = run.reqs.soft.task
            pool = [c for c in pool if c.scores.get(task, 0.0) > stronger_than]
            escalation = None
            if run.reqs.soft.agentic:
                pool = self._keep_tool_strength(run, last, pool)
        fresh = self._with_live_availability(pool)
        # HDW-304: the explicit model was tried and did not answer, so an ordinary pass-through
        # request drops it here and fails over. A strict pin never reaches this point.
        pin = run.request.pin if run.request.pin_strict else None
        try:
            return decide(
                PolicyRequest(
                    run.reqs,
                    run.scope,
                    pin,
                    run.request.force,
                    tier_attempts=tuple(a.tier for a in run.attempts),
                ),
                PolicyContext(fresh, self._rules_source(), run.hw, escalation),
            )
        except NoRouteError as err:
            raise RouteFailed(
                "no_route_left",
                self._failure_message(run, "no_route_left"),
                attempts=run.attempts,
                last_error=last_error,
                no_route=err,
            ) from err

    def _failure_message(self, run: RouteRun, code: str) -> str:
        """The bounded, actionable end of a request (slice C of HDW-513).

        One line per attempt, the missing-key fix command when a credential is the cause, and the
        tier that was available but never tried. Never includes key material.
        """
        aliases = {c.row.provider: self._executor.key_alias(c.row.provider) for c in run.candidates}
        return terminal_message(
            run.attempts,
            run.failures.records,
            cap=self._max_attempts(),
            code=code,
            untried=self._untried_tiers(run),
            aliases=aliases,
        )

    @staticmethod
    def _untried_tiers(run: RouteRun) -> tuple[TierName, ...]:
        """Tiers in scope with a usable model that no attempt of this request ever touched."""
        tried = {a.tier for a in run.attempts}
        out: list[TierName] = []
        for c in run.candidates:
            if (
                c.tier in run.scope.tiers
                and c.tier not in tried
                and c.tier not in out
                and c.availability.usable
            ):
                out.append(c.tier)
        return tuple(out)

    @staticmethod
    def _keep_tool_strength(
        run: RouteRun, last: RouteDecision, pool: list[Candidate]
    ) -> list[Candidate]:
        """Drop escalations that would call tools worse than the model that just failed.

        Not-below, not strictly-above (D10 refinement, HDW-154): equal tool strength still counts,
        or a good escalation would be lost and the answer flagged when it need not be.
        """
        failed = run.candidate(last.model)
        floor = failed.scores.get(AGENTIC_TASK, 0.0) if failed is not None else 0.0
        kept = [c for c in pool if c.scores.get(AGENTIC_TASK, 0.0) >= floor]
        if pool and not kept:
            run.notes.append(f"{last.model}: no stronger model keeps tool strength")
        return kept

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
        if len(run.attempts) >= self._max_attempts():
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
            active_locks=run.scope.active_locks,
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
