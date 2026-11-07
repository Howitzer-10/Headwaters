"""Step 2's models: what the catalog recommends for this GPU, and one pull with real progress.

Two halves, both catalog-driven so nothing here hard-codes a model name, a size or a VRAM
threshold:

- ``recommendations`` reads ``catalog.gpu_profiles`` (``vram_gb``, ``recommended`` per role) and
  answers "does this model fit here" with the product's own fit math
  (``headwaters.core.hardware.fit.evaluate_fit`` with ``FitConfig.from_rules``), against the
  hardware state's ``budget_gb``. The profile is the detected one, else the smallest-VRAM profile
  that lists the role — the same fallback ``router_prep.pull_hint`` uses.
- ``PullManager`` runs Ollama's native ``POST /api/pull`` through
  ``headwaters.core.executor.ollama.pull_stream``, one job at a time, and reduces its NDJSON into
  a percentage over the layers that report a size. A pull with no Ollama listening is an error
  the person is told about, never a silent no-op.

D13 is enforced here *and* in the client: a catalog profile is data and could name a
``:cloud``/``-cloud`` model, so a recommendation carries the refusal and a pull refuses before
opening a socket.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from headwaters.core.catalog.schema import Catalog, ModelEntry
from headwaters.core.executor.errors import LocalScopeViolation, ProviderError
from headwaters.core.executor.ollama import PullLine, cloud_tag, pull_stream
from headwaters.core.hardware.fit import FitConfig, evaluate_fit
from headwaters.core.hardware.state import HardwareState
from headwaters.core.ollama_remote import RemotePatterns, remote_patterns_from
from headwaters.core.rules import Rules

__all__ = [
    "PullJob",
    "PullManager",
    "Recommendation",
    "recommendations",
    "same_model",
]

ROLES = ("agentic_coder", "general", "vision", "classifier")
_MAX_LAYERS = 64


def same_model(a: str, b: str) -> bool:
    """Tag equality the way Ollama means it: ``qwen3:14b`` and ``qwen3:14b:latest`` alike."""
    return a.strip().lower().removesuffix(":latest") == b.strip().lower().removesuffix(":latest")


@dataclass(frozen=True)
class Recommendation:
    """One catalog recommendation, resolved against this machine."""

    model: str
    role: str
    profile: str
    fits: str  # fits | offload | no_fit | unknown
    num_ctx: int | None = None
    size_gb: float | None = None
    installed: bool = False
    refusal: str | None = None
    note: str = ""

    @property
    def pullable(self) -> bool:
        return self.refusal is None and not self.installed

    def as_dict(self) -> dict[str, object]:
        return {
            "model": self.model,
            "role": self.role,
            "profile": self.profile,
            "fits": self.fits,
            "num_ctx": self.num_ctx,
            "size_gb": self.size_gb,
            "installed": self.installed,
            "refusal": self.refusal,
            "note": self.note,
            "pullable": self.pullable,
        }


def _profile_for(
    catalog: Catalog, hardware: HardwareState | None
) -> tuple[str, Any] | tuple[None, None]:
    """The profile to recommend from: the detected one, else the smallest that lists a role."""
    if not catalog.gpu_profiles:
        return None, None
    if hardware is not None and hardware.profile in catalog.gpu_profiles:
        return hardware.profile, catalog.gpu_profiles[hardware.profile]
    name, profile = min(catalog.gpu_profiles.items(), key=lambda kv: kv[1].vram_gb)
    return name, profile


def recommendations(
    *,
    catalog: Catalog | None,
    hardware: HardwareState | None,
    rules: Rules,
    installed: Sequence[str] = (),
) -> tuple[Recommendation, ...]:
    """Every model the catalog recommends for this machine, with fit and installed state.

    An empty catalog or one with no ``gpu_profiles`` returns ``()`` — the step then says the
    catalog has no recommendation rather than inventing one. A model missing from
    ``catalog.models`` is reported as ``unknown`` fit, never assumed to fit.
    """
    if catalog is None:
        return ()
    profile_name, profile = _profile_for(catalog, hardware)
    if profile is None or profile_name is None:
        return ()
    entries: dict[str, ModelEntry] = {entry.id: entry for entry in catalog.models}
    patterns = remote_patterns_from(catalog)
    cfg = FitConfig.from_rules(rules)
    # The smallest context step, expressed as the prompt it covers: the honest question for a
    # recommendation is "does this fit at a usable window", not "does it fit for my last prompt".
    est_tokens = max(1, int(cfg.ctx_steps[0] / cfg.ctx_headroom)) if cfg.ctx_steps else 1
    budget = (
        hardware.budget_gb
        if hardware is not None and hardware.budget_gb > 0
        else float(profile.vram_gb)
    )
    found: list[Recommendation] = []
    for role in ROLES:
        for name in profile.recommended.get(role) or ():
            entry = entries.get(name)
            fit_status = "unknown"
            num_ctx: int | None = None
            size_gb: float | None = None
            note = ""
            if entry is not None:
                size_gb = entry.size_gb
                result = evaluate_fit(entry, budget, est_tokens, cfg)
                fit_status, num_ctx = result.status, result.num_ctx
                if result.status == "no_fit":
                    note = result.reason
            else:
                note = "the catalog recommends it but has no entry for it, so its fit is unknown"
            found.append(
                Recommendation(
                    model=name,
                    role=role,
                    profile=profile_name,
                    fits=fit_status,
                    num_ctx=num_ctx,
                    size_gb=size_gb,
                    installed=any(same_model(name, have) for have in installed),
                    refusal=cloud_tag(name, patterns),
                    note=note,
                )
            )
    return tuple(found)


def explain(exc: BaseException, host: str) -> str:
    """A sentence for the page about a failed pull. Never includes a response body wholesale."""
    if isinstance(exc, LocalScopeViolation):
        return str(exc)
    if isinstance(exc, ProviderError):
        if exc.kind == "connection":
            return (
                f"Ollama did not answer at {host}, so nothing was pulled. Start Ollama and retry."
            )
        if exc.kind == "timeout":
            return f"Ollama stopped answering at {host} part way through the pull."
        body = " ".join(exc.body.split())[:200]
        return f"Ollama refused the pull ({exc.kind} {exc.status})." + (
            f" It said: {body}" if body else ""
        )
    return f"the pull failed: {' '.join(str(exc).split())[:200]}"


@dataclass
class PullJob:
    """One pull, as the page needs it: state, a percentage when it is knowable, and a sentence.

    ``percent`` is ``None`` — not ``0`` — until a layer reports a total, because "size unknown"
    and "nothing downloaded" are different truths and the page says which one it is.
    """

    model: str
    state: str = "running"  # running | done | failed | refused
    status: str = "starting"
    completed: int = 0
    total: int = 0
    error: str = ""
    started: float = 0.0
    finished: float = 0.0
    layers: dict[str, tuple[int, int]] = field(default_factory=dict)

    @property
    def percent(self) -> float | None:
        return round(self.completed * 100.0 / self.total, 1) if self.total > 0 else None

    def as_dict(self) -> dict[str, object]:
        return {
            "model": self.model,
            "state": self.state,
            "status": self.status,
            "percent": self.percent,
            "completed": self.completed,
            "total": self.total,
            "error": self.error,
            "started": self.started,
            "finished": self.finished,
        }

    def fed(self, line: PullLine) -> None:
        """Fold one NDJSON line in, per layer, so a repeated layer is never double counted."""
        if line.status:
            self.status = line.status
        if not line.digest or line.total <= 0:
            return
        if line.digest not in self.layers and len(self.layers) >= _MAX_LAYERS:
            return
        done, total = self.layers.get(line.digest, (0, 0))
        self.layers[line.digest] = (
            max(done, min(line.completed, line.total)),
            max(total, line.total),
        )
        self.completed = sum(d for d, _ in self.layers.values())
        self.total = sum(t for _, t in self.layers.values())


Lines = Callable[[str, str, RemotePatterns], Iterator[PullLine]]


class PullManager:
    """One pull at a time, on a background thread, readable from the request thread.

    One at a time because two concurrent pulls of a 9 GB model is not a thing anyone means to do,
    and because the progress page is one line. Everything the page reads goes through a lock.
    """

    def __init__(
        self, *, lines: Lines | None = None, clock: Callable[[], float] = time.time
    ) -> None:
        self._lines = lines or _default_lines
        self._clock = clock
        self._lock = threading.Lock()
        self._job: PullJob | None = None
        self._thread: threading.Thread | None = None

    def snapshot(self) -> PullJob | None:
        with self._lock:
            return None if self._job is None else replace(self._job, layers=dict(self._job.layers))

    def running(self) -> bool:
        with self._lock:
            return self._job is not None and self._job.state == "running"

    def start(self, host: str, model: str, patterns: RemotePatterns = ()) -> PullJob:
        """Begin one pull. A cloud tag, an empty tag or a busy manager is refused in words."""
        refusal = cloud_tag(model, patterns)
        if refusal is not None:
            return self._stamp(PullJob(model=model, state="refused", error=refusal))
        if not model.strip():
            return self._stamp(PullJob(model="", state="refused", error="no model was named"))
        if self.running():
            with self._lock:
                assert self._job is not None
                return replace(self._job, layers=dict(self._job.layers))
        with self._lock:
            self._job = PullJob(model=model, state="running", started=self._clock())
            job = self._job
        self._thread = threading.Thread(
            target=self._run, args=(job, host, model, patterns), name="wizard-pull", daemon=True
        )
        self._thread.start()
        return self.snapshot() or job

    def wait(self, timeout: float | None = None) -> None:
        """Block until the current job's thread ends: for tests, never for a request."""
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def _stamp(self, job: PullJob) -> PullJob:
        with self._lock:
            self._job = job
        return replace(job, layers=dict(job.layers))

    def _run(self, job: PullJob, host: str, model: str, patterns: RemotePatterns) -> None:
        try:
            for line in self._lines(host, model, patterns):
                with self._lock:
                    job.fed(line)
            with self._lock:
                job.state = "done"
                job.status = job.status or "success"
                job.finished = self._clock()
        except Exception as exc:
            with self._lock:
                job.state = "failed"
                job.error = explain(exc, host)
                job.finished = self._clock()


def _default_lines(host: str, model: str, patterns: RemotePatterns) -> Iterator[PullLine]:
    return pull_stream(host, model, remote_patterns=patterns)


def job_from_dict(raw: Mapping[str, Any] | None) -> PullJob | None:
    """The last job, read back from ``kv`` only to *display* it — never to resume it."""
    if not isinstance(raw, Mapping) or not raw.get("model"):
        return None
    return PullJob(
        model=str(raw.get("model")),
        state=str(raw.get("state") or "failed"),
        status=str(raw.get("status") or ""),
        completed=int(raw.get("completed") or 0),
        total=int(raw.get("total") or 0),
        error=str(raw.get("error") or ""),
        started=float(raw.get("started") or 0.0),
        finished=float(raw.get("finished") or 0.0),
    )
