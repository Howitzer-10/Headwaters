# Core source, part B: supporting modules

Walls, quotas, hardware fit, analyzer, catalog loader/updater, verification, config I/O.

33 files.

| File | Lines | Purpose |
| --- | ---: | --- |
| `headwaters/core/__init__.py` | 1 |  |
| `headwaters/core/analyzer/__init__.py` | 24 | Request analysis: layer 1 deterministic (HDW-106), layer 3 tiny CPU classifier (HDW-107). |
| `headwaters/core/analyzer/_extract.py` | 130 | Pull text, images and tool-call bytes out of OpenAI/Anthropic style messages (HDW-106). |
| `headwaters/core/analyzer/classifier.py` | 200 | Analyzer layer 3: tiny CPU LLM classifier (HDW-107, v3.4 section C). |
| `headwaters/core/analyzer/layers.py` | 80 | Analyzer layer interface and chain (HDW-107, v3.4 section C). |
| `headwaters/core/analyzer/requirements.py` | 265 | Analyzer layer 1: deterministic requirements (HDW-106, v3 section 4.1). |
| `headwaters/core/catalog/__init__.py` | 1 |  |
| `headwaters/core/catalog/loader.py` | 122 | Load and validate the curated catalog; resolve model ids including unknown models. |
| `headwaters/core/catalog/merge.py` | 199 | Merge live discovery with the curated catalog and user overrides into ``ModelRecord``s. |
| `headwaters/core/catalog/schema.py` | 239 | Typed model of the curated catalog (``catalog/seed_catalog.json`` and later signed updates). |
| `headwaters/core/catalog/trust.py` | 43 | ed25519 verification of the curated catalog. |
| `headwaters/core/catalog/updater.py` | 223 | Catalog updater: signed, ETag-conditional, atomic, offline-safe. |
| `headwaters/core/hardware/__init__.py` | 1 |  |
| `headwaters/core/hardware/fit.py` | 140 | KV-cache math, model fit and ``num_ctx`` selection (HDW-109, v3 section 7). |
| `headwaters/core/hardware/gpu.py` | 222 | GPU probes (HDW-108): NVML first, then ``nvidia-smi``. Pure data in, no policy. |
| `headwaters/core/hardware/residency.py` | 60 | Warm bonus and eviction penalty inputs (HDW-109, v3 section 7.3). |
| `headwaters/core/hardware/state.py` | 276 | ``HardwareState`` snapshot (HDW-108): GPU, reserve, profile, battery, GPU-busy. |
| `headwaters/core/hardware/sysmem.py` | 89 | NVIDIA Sysmem Fallback detection (HDW-109, v3.1 C1). |
| `headwaters/core/paths.py` | 99 | Filesystem locations for Headwaters. |
| `headwaters/core/quotas.py` | 325 | Quota tracker and cooldowns (HDW-111, v3 section 4.4). |
| `headwaters/core/rules_io.py` | 130 | Reading, validating, defaulting and schema export for config files. |
| `headwaters/core/rules_watch.py` | 128 | Hot reload of ``rules.yaml``: debounced file watching with last-good fallback. |
| `headwaters/core/state_limits.py` | 149 | Repositories for ``walls`` and ``quotas``. Each call is one short transaction. |
| `headwaters/core/state_misc.py` | 156 | Repositories for ``sessions``, ``jobs``, ``handoffs`` and ``kv``. |
| `headwaters/core/state_models.py` | 171 | Repositories for ``models`` and ``scores``. Each call is one short transaction. |
| `headwaters/core/state_schema.py` | 87 | Versioned DDL for the shared SQLite database. |
| `headwaters/core/stdio.py` | 92 | UTF-8 stdio and a stdout guard for MCP stdio servers. |
| `headwaters/core/verify_basic.py` | 247 | Basic output verification (HDW-116): the cheap checks that make downgrading safe. |
| `headwaters/core/verify_diff.py` | 88 | ``git apply --check`` for candidate diffs, with CRLF/LF handling (HDW-116). |
| `headwaters/core/verify_fences.py` | 61 | Line-ending normalization and Markdown fence splitting for verification (HDW-116). |
| `headwaters/core/verify_schema.py` | 119 | JSON Schema and tool-call argument validation for verification (HDW-116). |
| `headwaters/core/verify_types.py` | 88 | Result types for basic verification (HDW-116). |
| `headwaters/core/walls.py` | 280 | Wall classifier (HDW-110, v3 section 4.4): turn a failed provider response into a ``WallEvent``. |

## headwaters/core/__init__.py

````python

````

## headwaters/core/analyzer/__init__.py

````python
"""Request analysis: layer 1 deterministic (HDW-106), layer 3 tiny CPU classifier (HDW-107)."""

from headwaters.core.analyzer.classifier import TinyLlmClassifier
from headwaters.core.analyzer.layers import AnalyzerChain, AnalyzerLayer, build_chain
from headwaters.core.analyzer.requirements import (
    Analyzer,
    AnalyzerInput,
    HardRequirements,
    Requirements,
    SoftRequirements,
)

__all__ = [
    "Analyzer",
    "AnalyzerChain",
    "AnalyzerInput",
    "AnalyzerLayer",
    "HardRequirements",
    "Requirements",
    "SoftRequirements",
    "TinyLlmClassifier",
    "build_chain",
]
````

## headwaters/core/analyzer/_extract.py

````python
"""Pull text, images and tool-call bytes out of OpenAI/Anthropic style messages (HDW-106)."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

IMAGE_PART_TYPES = frozenset({"image_url", "image", "input_image"})
TEXT_PART_TYPES = frozenset({"text", "input_text", "output_text"})
JSON_FORMAT_TYPES = frozenset({"json_object", "json_schema", "json"})
MAX_DEPTH = 6


def dumps(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        return ""


class Collected:
    """What one pass over the messages found."""

    __slots__ = (
        "all_text",
        "code_extra_chars",
        "image_count",
        "last_user_text",
        "system_text",
        "user_side_text",
    )

    def __init__(self) -> None:
        self.all_text: list[str] = []
        self.system_text: list[str] = []
        self.user_side_text: list[str] = []  # everything except system prompts (for languages)
        self.last_user_text = ""
        self.code_extra_chars = 0  # tool-call arguments: JSON/code-like, counted at code rate
        self.image_count = 0


def _scan_content(content: Any, texts: list[str], out: Collected, depth: int) -> None:
    """Append text parts to ``texts``; count images; count tool-use input as code bytes."""
    if depth > MAX_DEPTH or content is None:
        return
    if isinstance(content, str):
        texts.append(content)
        return
    if isinstance(content, Mapping):
        content = [content]
    if not isinstance(content, Iterable):
        return
    for part in content:
        if isinstance(part, str):
            texts.append(part)
            continue
        if not isinstance(part, Mapping):
            continue
        kind = part.get("type")
        if kind in IMAGE_PART_TYPES:
            out.image_count += 1
        elif kind == "tool_result":
            nested: list[str] = []
            _scan_content(part.get("content"), nested, out, depth + 1)
            out.all_text.extend(nested)
            out.user_side_text.extend(nested)
        elif kind == "tool_use":
            out.code_extra_chars += len(dumps(part.get("input")))
        else:
            text = part.get("text")
            if isinstance(text, str) and (kind is None or kind in TEXT_PART_TYPES):
                texts.append(text)


def collect(
    messages: Sequence[Mapping[str, Any]],
    system: str | Sequence[Mapping[str, Any]] | None,
) -> Collected:
    out = Collected()
    if system is not None:
        texts: list[str] = []
        _scan_content(system, texts, out, 0)
        out.system_text.extend(texts)
        out.all_text.extend(texts)
    for message in messages:
        role = str(message.get("role", "user"))
        texts = []
        _scan_content(message.get("content"), texts, out, 0)
        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, Sequence) and not isinstance(tool_calls, str):
            for call in tool_calls:
                function = call.get("function") if isinstance(call, Mapping) else None
                if isinstance(function, Mapping):
                    arguments = function.get("arguments")
                    out.code_extra_chars += len(
                        arguments if isinstance(arguments, str) else dumps(arguments)
                    )
        out.all_text.extend(texts)
        if role in ("system", "developer"):
            out.system_text.extend(texts)
            continue
        out.user_side_text.extend(texts)
        if role == "user":
            joined = "\n".join(texts).strip()
            if joined:
                out.last_user_text = joined
    return out


def header(headers: Mapping[str, str] | None, name: str) -> str:
    if not headers:
        return ""
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value
    return ""


def wants_json(response_format: Mapping[str, Any] | str | None) -> bool:
    if response_format is None:
        return False
    if isinstance(response_format, str):
        return response_format.lower() in JSON_FORMAT_TYPES
    kind = response_format.get("type")
    if isinstance(kind, str) and kind.lower() in JSON_FORMAT_TYPES:
        return True
    return any(response_format.get(key) for key in ("json_schema", "schema"))
````

## headwaters/core/analyzer/classifier.py

````python
"""Analyzer layer 3: tiny CPU LLM classifier (HDW-107, v3.4 section C).

Runs only when the earlier layers left the result ambiguous. One Ollama native ``/api/chat``
call to a small model with ``options.num_gpu = 0`` (CPU only, so it never evicts the main model
from VRAM), a JSON-schema ``format`` forcing ``{task, complexity 1-5, risk}``, and a hard
timeout (default 2 s). Answers are cached by sha256 of model + prompt text. Any error or
timeout returns ``None`` so layer 1's result stands.

Privacy: the call goes to a loopback Ollama only (``assert_loopback``), so it is identical in
every scope and never reaches a cloud endpoint. The cache holds labels only, keyed by a hash;
prompt text is never stored or logged.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from headwaters.core.analyzer import _extract
from headwaters.core.analyzer.ollama_client import (
    ClassifierUnavailableError,
    OllamaChatClient,
    OllamaHttpClient,
)
from headwaters.core.analyzer.requirements import AnalyzerInput, Requirements
from headwaters.core.catalog.schema import Catalog
from headwaters.core.rules import ClassifierRules, LocalTier

SOURCE_CLASSIFIER = "classifier"
AUTO_SMALL = "auto_small"
TASKS = ("code", "reasoning", "chat", "summarize", "extract", "creative", "vision")
RISKS = ("low", "medium", "high")
_MAX_PROMPT_CHARS = 2000  # head + tail of the last user message sent to the tiny model
_MIN_CONFIDENCE = 0.75  # model answers are trusted a little above the usual threshold
_SYSTEM = (
    "Classify the user's request for a model router. Reply with JSON only. "
    "task: the main kind of work. complexity: 1 (trivial) to 5 (very hard). "
    "risk: low, medium or high chance that a wrong answer causes harm."
)

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "task": {"type": "string", "enum": list(TASKS)},
        "complexity": {"type": "integer", "minimum": 1, "maximum": 5},
        "risk": {"type": "string", "enum": list(RISKS)},
    },
    "required": ["task", "complexity", "risk"],
}


@dataclass(frozen=True, slots=True)
class Label:
    task: str
    complexity: int
    risk: str


def resolve_classifier_model(catalog: Catalog, tier: LocalTier) -> str | None:
    """Turn ``tiers.local.classifier.model`` into a model id; ``None`` if nothing is known.

    ``auto_small`` picks the catalog's recommended classifier for the configured GPU profile
    (for profile ``auto``, the smallest-VRAM profile that lists one, the safe choice).
    """
    wanted = tier.classifier.model
    if wanted != AUTO_SMALL:
        return wanted
    profiles = catalog.gpu_profiles
    if tier.profile in profiles:
        ordered = [profiles[tier.profile]]
    else:
        ordered = sorted(profiles.values(), key=lambda p: p.vram_gb)
    for profile in ordered:
        models = profile.recommended.get("classifier")
        if models:
            return models[0]
    return None


def prompt_text(inp: AnalyzerInput) -> str:
    """The last user message as plain text, clipped to head + tail."""
    text = _extract.collect(inp.messages, None).last_user_text
    if len(text) > _MAX_PROMPT_CHARS:
        half = _MAX_PROMPT_CHARS // 2
        text = text[:half] + "\n...\n" + text[-half:]
    return text


def parse_label(reply: Mapping[str, Any]) -> Label | None:
    message = reply.get("message")
    content = message.get("content") if isinstance(message, Mapping) else None
    if not isinstance(content, str):
        return None
    try:
        data = json.loads(content)
    except ValueError:
        return None
    if not isinstance(data, dict):
        return None
    task, risk, complexity = data.get("task"), data.get("risk"), data.get("complexity")
    if task not in TASKS or risk not in RISKS:
        return None
    if isinstance(complexity, bool) or not isinstance(complexity, int):
        return None
    return Label(task=task, complexity=max(1, min(5, complexity)), risk=risk)


class TinyLlmClassifier:
    """Layer 3. Implements ``AnalyzerLayer``."""

    name = "tiny_llm"

    def __init__(
        self,
        model: str,
        *,
        client: OllamaChatClient | None = None,
        host: str = "http://localhost:11434",
        settings: ClassifierRules | None = None,
        cache_size: int = 256,
    ) -> None:
        self.model = model
        self.settings = settings or ClassifierRules()
        self._client: OllamaChatClient = client or OllamaHttpClient(host)
        self._cache: OrderedDict[str, Label] = OrderedDict()
        self._cache_size = max(1, cache_size)
        self._lock = threading.Lock()
        self.calls = 0  # model calls made (cache hits excluded)

    def cache_key(self, prompt: str) -> str:
        digest = hashlib.sha256()
        digest.update(self.model.encode("utf-8"))
        digest.update(b"\0")
        digest.update(prompt.encode("utf-8"))
        return digest.hexdigest()

    def _get(self, key: str) -> Label | None:
        with self._lock:
            label = self._cache.get(key)
            if label is not None:
                self._cache.move_to_end(key)
            return label

    def _put(self, key: str, label: Label) -> None:
        with self._lock:
            self._cache[key] = label
            self._cache.move_to_end(key)
            while len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)

    def build_payload(self, prompt: str) -> dict[str, Any]:
        return {
            "model": self.model,
            "stream": False,
            "format": SCHEMA,
            "messages": [
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": prompt},
            ],
            "options": {"num_gpu": self.settings.num_gpu, "temperature": 0},
        }

    def classify(self, prompt: str) -> Label | None:
        """Label ``prompt`` (cache first, then one CPU-only model call); ``None`` on failure."""
        key = self.cache_key(prompt)
        cached = self._get(key)
        if cached is not None:
            return cached
        self.calls += 1
        try:
            reply = self._client.chat(self.build_payload(prompt), self.settings.timeout_s)
        except (ClassifierUnavailableError, OSError, TimeoutError):
            return None
        label = parse_label(reply)
        if label is not None:
            self._put(key, label)
        return label

    def refine(self, inp: AnalyzerInput, current: Requirements) -> Requirements | None:
        prompt = prompt_text(inp)
        if not prompt:
            return None
        label = self.classify(prompt)
        if label is None:
            return None
        task = "vision" if current.hard.vision else label.task
        soft = replace(current.soft, task=task, complexity=label.complexity, risk=label.risk)
        return replace(
            current,
            soft=soft,
            confidence=max(current.confidence, _MIN_CONFIDENCE),
            source=SOURCE_CLASSIFIER,
            ambiguous=False,
        )
````

## headwaters/core/analyzer/layers.py

````python
"""Analyzer layer interface and chain (HDW-107, v3.4 section C).

Chain: layer 1 (deterministic rules, always) -> optional layer 2 slot (embedding kNN, HDW-603)
-> layer 3 (tiny CPU LLM). Each later layer runs only while the result is still ambiguous
(confidence below the catalog threshold). A layer that fails, times out or has nothing to add
returns ``None`` and the previous result stands, so the chain never fails because of a layer.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from headwaters.core.analyzer.classifier import TinyLlmClassifier, resolve_classifier_model
from headwaters.core.analyzer.ollama_client import OllamaChatClient
from headwaters.core.analyzer.requirements import Analyzer, AnalyzerInput, Requirements
from headwaters.core.catalog.schema import Catalog
from headwaters.core.rules import LocalTier


@runtime_checkable
class AnalyzerLayer(Protocol):
    """A refinement layer. Return a new ``Requirements`` or ``None`` to keep the input."""

    name: str

    def refine(self, inp: AnalyzerInput, current: Requirements) -> Requirements | None: ...


class AnalyzerChain:
    """Layer 1 followed by refinement layers, each gated on ambiguity."""

    def __init__(
        self,
        base: Analyzer,
        *,
        layer2: AnalyzerLayer | None = None,
        layer3: AnalyzerLayer | None = None,
    ) -> None:
        self.base = base
        self.layers: tuple[AnalyzerLayer, ...] = tuple(x for x in (layer2, layer3) if x)

    @property
    def threshold(self) -> float:
        return self.base.hints.confidence_threshold

    def analyze(self, inp: AnalyzerInput) -> Requirements:
        result = self.base.analyze(inp)
        for layer in self.layers:
            if result.confidence >= self.threshold:
                break
            try:
                refined = layer.refine(inp, result)
            except Exception:  # a layer must never break routing
                refined = None
            if refined is not None:
                result = refined
        return result


def chain_names(chain: AnalyzerChain) -> Sequence[str]:
    return ("rules", *(layer.name for layer in chain.layers))


def build_chain(
    catalog: Catalog,
    tier: LocalTier,
    *,
    layer2: AnalyzerLayer | None = None,
    client: OllamaChatClient | None = None,
    enable_layer3: bool = True,
) -> AnalyzerChain:
    """Layer 1 from the catalog hints, optional layer 2, layer 3 when a classifier model exists."""
    base = Analyzer.from_catalog(catalog)
    layer3: AnalyzerLayer | None = None
    model = resolve_classifier_model(catalog, tier) if enable_layer3 else None
    if model is not None:
        layer3 = TinyLlmClassifier(model, client=client, host=tier.host, settings=tier.classifier)
    return AnalyzerChain(base, layer2=layer2, layer3=layer3)
````

## headwaters/core/analyzer/requirements.py

````python
"""Analyzer layer 1: deterministic requirements (HDW-106, v3 section 4.1).

Turns one request (messages, tools, response_format, file paths, headers) into
``Requirements``: hard needs that filter models (vision, tools, json, min context) and soft
signals that rank them (task, complexity, risk, agentic, languages, hints). Pure Python, no I/O,
no network, no model call; typical requests take well under 5 ms.

Keyword lists, agent user-agents, extension maps and thresholds are DATA from the catalog
(``Catalog.analyzer_hints``), never code. Ambiguous results carry ``confidence`` below the
catalog threshold so a later layer (HDW-107) can refine them.

Privacy: this module never logs or stores request text.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from headwaters.core.analyzer import _extract
from headwaters.core.catalog.schema import AnalyzerHints, Catalog

SOURCE_DETERMINISTIC = "deterministic"

_KEYWORD_SCAN_HEAD = 4000  # chars of the last user message scanned for keywords (head + tail)
_KEYWORD_SCAN_TAIL = 4000
_SHORT_PROMPT_TOKENS = 100
# Tie-break order when two tasks score the same (first wins, confidence drops).
_TASK_ORDER = ("vision", "code", "reasoning", "extract", "summarize", "creative", "chat")
_FENCE = re.compile(r"```([^\n`]*)\n(.*?)```", re.DOTALL)
_IMAGE_BONUS = 3
_JSON_BONUS = 2
_FENCE_BONUS = 2

TokenCounter = Callable[[str], int]


@dataclass(frozen=True, slots=True)
class HardRequirements:
    """Models that cannot meet these are filtered out."""

    vision: bool
    tools: bool
    json: bool
    min_ctx: int


@dataclass(frozen=True, slots=True)
class SoftRequirements:
    """Signals used to rank the models that pass the hard filter."""

    task: str
    complexity: int  # 1..5
    risk: str  # low | medium | high
    agentic: bool
    languages: tuple[str, ...]
    reasoning_hint: bool
    duck_hint: bool


@dataclass(frozen=True, slots=True)
class Requirements:
    hard: HardRequirements
    soft: SoftRequirements
    confidence: float
    source: str
    est_tokens: int
    ambiguous: bool


@dataclass(frozen=True, slots=True)
class AnalyzerInput:
    """One request, in either wire format.

    ``messages`` follow OpenAI chat or Anthropic message shapes (content is a string or a list
    of parts). ``system`` is the Anthropic top-level system prompt (string or text blocks);
    OpenAI system messages stay inside ``messages``. ``file_paths`` are paths the host attached.
    """

    messages: Sequence[Mapping[str, Any]]
    system: str | Sequence[Mapping[str, Any]] | None = None
    tools: Sequence[Mapping[str, Any]] | None = None
    response_format: Mapping[str, Any] | str | None = None
    file_paths: Sequence[str] = ()
    headers: Mapping[str, str] | None = None
    max_output_tokens: int | None = None


def _compile(patterns: Iterable[str]) -> tuple[re.Pattern[str], ...]:
    return tuple(re.compile(p, re.IGNORECASE) for p in patterns)


def _count(patterns: Iterable[re.Pattern[str]], text: str) -> int:
    return sum(1 for p in patterns if p.search(text))


class Analyzer:
    """Deterministic analyzer. Build once from catalog hints (regexes are precompiled)."""

    def __init__(
        self,
        hints: AnalyzerHints | None = None,
        token_counter: TokenCounter | None = None,
    ) -> None:
        """``token_counter`` (e.g. a tokenizer) replaces the chars-per-token estimate."""
        self.hints = hints or AnalyzerHints()
        self._counter = token_counter
        self._reasoning = _compile(self.hints.reasoning_keywords)
        self._duck = _compile(self.hints.duck_keywords)
        self._risk = _compile(self.hints.risk_keywords)
        self._task = {t: _compile(p) for t, p in self.hints.task_keywords.items()}
        self._fence_aliases = {k.lower(): v for k, v in self.hints.fence_aliases.items()}
        self._extensions = {k.lower(): v for k, v in self.hints.extension_languages.items()}
        self._agents = tuple(a.lower() for a in self.hints.agent_user_agents)
        self._ext_in_text: re.Pattern[str] | None = None
        if self._extensions:
            names = sorted((e[1:] for e in self._extensions), key=len, reverse=True)
            alternation = "|".join(re.escape(n) for n in names)
            self._ext_in_text = re.compile(rf"(?<=[\w)\]])\.({alternation})(?![\w])", re.IGNORECASE)

    @classmethod
    def from_catalog(cls, catalog: Catalog, token_counter: TokenCounter | None = None) -> Analyzer:
        return cls(catalog.analyzer_hints, token_counter)

    def estimate_tokens(self, chunks: Iterable[str], code_extra_chars: int, images: int) -> int:
        """Counter if given; else chars/3.5 inside code fences and tool calls, chars/4 elsewhere."""
        hints = self.hints
        total = 0.0
        for chunk in chunks:
            if self._counter is not None:
                total += self._counter(chunk)
                continue
            code_chars = sum(len(m.group(2)) for m in _FENCE.finditer(chunk))
            total += code_chars / hints.chars_per_token_code
            total += (len(chunk) - code_chars) / hints.chars_per_token_prose
        total += code_extra_chars / hints.chars_per_token_code
        total += images * hints.image_tokens
        return math.ceil(total)

    def _languages(self, texts: Iterable[str], file_paths: Sequence[str]) -> tuple[str, ...]:
        found: dict[str, None] = {}
        for text in texts:
            for match in _FENCE.finditer(text):
                tag = match.group(1).strip().split(" ", 1)[0].lower()
                if tag:
                    found.setdefault(self._fence_aliases.get(tag, tag))
            if self._ext_in_text is not None:
                for ext_match in self._ext_in_text.finditer(text):
                    language = self._extensions.get("." + ext_match.group(1).lower())
                    if language:
                        found.setdefault(language)
        for path in file_paths:
            name = path.replace("\\", "/").rsplit("/", 1)[-1]
            if "." in name:
                language = self._extensions.get("." + name.rsplit(".", 1)[-1].lower())
                if language:
                    found.setdefault(language)
        return tuple(found)

    def _is_agentic(self, inp: AnalyzerInput, system_tokens: int, has_tools: bool) -> bool:
        if has_tools or system_tokens > self.hints.agentic_system_prompt_tokens:
            return True
        agent = _extract.header(inp.headers, "user-agent").lower()
        return bool(agent) and any(known in agent for known in self._agents)

    def _task_and_confidence(
        self,
        scan_text: str,
        *,
        images: bool,
        wants_json: bool,
        has_fences: bool,
        reasoning_hint: bool,
        agentic: bool,
        last_user_tokens: int,
    ) -> tuple[str, float]:
        scores = dict.fromkeys(_TASK_ORDER, 0)
        for task, patterns in self._task.items():
            scores[task] += _count(patterns, scan_text)
        if images:
            scores["vision"] += _IMAGE_BONUS
        if wants_json:
            scores["extract"] += _JSON_BONUS
        if has_fences:
            scores["code"] += _FENCE_BONUS
        if reasoning_hint:
            scores["reasoning"] += 1
        ranked = sorted(_TASK_ORDER, key=lambda t: (-scores[t], _TASK_ORDER.index(t)))
        top, second = scores[ranked[0]], scores[ranked[1]]
        if top == 0:
            if agentic:
                return "chat", 0.45
            return "chat", 0.7 if last_user_tokens <= _SHORT_PROMPT_TOKENS else 0.4
        margin = top - second
        return ranked[0], 0.45 if margin == 0 else 0.7 if margin == 1 else 0.85

    def _complexity(self, est_tokens: int, *, reasoning: bool, agentic: bool) -> int:
        level = 1 + sum(1 for step in self.hints.complexity_token_steps if est_tokens > step)
        level += int(reasoning) + int(agentic)
        return max(1, min(5, level))

    def analyze(self, inp: AnalyzerInput) -> Requirements:
        collected = _extract.collect(inp.messages, inp.system)
        has_tools = bool(inp.tools)
        json_needed = _extract.wants_json(inp.response_format)
        has_images = collected.image_count > 0

        tools_chars = len(_extract.dumps(inp.tools)) if has_tools else 0
        est_tokens = self.estimate_tokens(
            collected.all_text,
            collected.code_extra_chars + tools_chars,
            collected.image_count,
        )
        system_tokens = self.estimate_tokens(collected.system_text, 0, 0)
        agentic = self._is_agentic(inp, system_tokens, has_tools)

        user_text = collected.last_user_text
        if len(user_text) > _KEYWORD_SCAN_HEAD + _KEYWORD_SCAN_TAIL:
            scan_text = user_text[:_KEYWORD_SCAN_HEAD] + "\n" + user_text[-_KEYWORD_SCAN_TAIL:]
        else:
            scan_text = user_text
        reasoning_hint = _count(self._reasoning, scan_text) > 0
        duck_hint = _count(self._duck, scan_text) > 0
        risk_hits = _count(self._risk, scan_text)
        risk = "high" if risk_hits >= 2 else "medium" if risk_hits == 1 else "low"

        languages = self._languages(collected.user_side_text, inp.file_paths)
        has_fences = any(_FENCE.search(t) for t in collected.user_side_text)
        task, confidence = self._task_and_confidence(
            scan_text,
            images=has_images,
            wants_json=json_needed,
            has_fences=has_fences,
            reasoning_hint=reasoning_hint,
            agentic=agentic,
            last_user_tokens=self.estimate_tokens([user_text], 0, 0),
        )

        hard = HardRequirements(
            vision=has_images,
            tools=has_tools,
            json=json_needed,
            min_ctx=est_tokens + max(0, inp.max_output_tokens or 0),
        )
        soft = SoftRequirements(
            task=task,
            complexity=self._complexity(est_tokens, reasoning=reasoning_hint, agentic=agentic),
            risk=risk,
            agentic=agentic,
            languages=languages,
            reasoning_hint=reasoning_hint,
            duck_hint=duck_hint,
        )
        return Requirements(
            hard=hard,
            soft=soft,
            confidence=confidence,
            source=SOURCE_DETERMINISTIC,
            est_tokens=est_tokens,
            ambiguous=confidence < self.hints.confidence_threshold,
        )
````

## headwaters/core/catalog/__init__.py

````python

````

## headwaters/core/catalog/loader.py

````python
"""Load and validate the curated catalog; resolve model ids including unknown models."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from headwaters.core import paths
from headwaters.core.catalog.schema import Catalog, ModelEntry
from headwaters.core.rules_io import ConfigError, format_validation_error

SEED_RELATIVE_PATH = Path("catalog") / "seed_catalog.json"
SCHEMA_RELATIVE_PATH = Path("catalog") / "catalog.schema.json"

__all__ = [
    "SCHEMA_RELATIVE_PATH",
    "SEED_RELATIVE_PATH",
    "Catalog",
    "CatalogIndex",
    "load_catalog",
    "parse_catalog",
    "schema_json",
]


def load_catalog(path: Path) -> Catalog:
    """Read and validate a catalog file. Raises ``ConfigError`` with one line per problem."""
    try:
        text = paths.long_safe(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(path, [f"cannot read file: {exc.strerror or exc}"]) from exc
    return parse_catalog(text, path)


def parse_catalog(text: str, source: Path) -> Catalog:
    """Validate catalog JSON text; ``source`` is only used in error messages."""
    path = source
    try:
        data: Any = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(path, [f"JSON syntax error at line {exc.lineno}: {exc.msg}"]) from exc
    if not isinstance(data, dict):
        raise ConfigError(path, ["top level must be an object"])
    try:
        return Catalog.model_validate(data)
    except ValidationError as exc:
        problems = []
        for line, item in zip(format_validation_error(exc), exc.errors(), strict=True):
            problems.append(line.removeprefix("(root): ") if item["loc"] == () else line)
        raise ConfigError(path, problems) from exc


class CatalogIndex:
    """Lookup by id or alias. Unknown models get conservative defaults and ``unscored``."""

    def __init__(self, catalog: Catalog) -> None:
        self.catalog = catalog
        self._by_name: dict[str, ModelEntry] = {}
        for entry in catalog.models:
            for name in (entry.id, *entry.aliases):
                self._by_name.setdefault(name.lower(), entry)

    def find(self, name: str) -> ModelEntry | None:
        return self._by_name.get(name.lower())

    def resolve(self, name: str, provider: str = "unknown") -> ModelEntry:
        known = self.find(name)
        if known is not None:
            return known
        defaults = self.catalog.unknown_model_defaults
        return ModelEntry(
            id=name,
            provider=provider,
            tools_ok=defaults.tools_ok,
            json_ok=defaults.json_ok,
            ctx=defaults.ctx,
            scores=defaults.scores.model_copy(),
            unscored=True,
        )


def schema_json() -> str:
    """Deterministic JSON Schema text for the catalog (LF endings, trailing newline)."""
    schema = Catalog.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "Headwaters catalog"
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    """``python -m headwaters.core.catalog.loader [--write|--check] [PATH]`` (schema file)."""
    parser = argparse.ArgumentParser(prog="headwaters.core.catalog.loader")
    parser.add_argument("--check", action="store_true", help="fail if PATH differs from generated")
    parser.add_argument("--write", action="store_true", help="write the generated schema to PATH")
    parser.add_argument("path", nargs="?", type=Path, default=SCHEMA_RELATIVE_PATH)
    args = parser.parse_args(argv)
    generated = schema_json()
    if args.check:
        current = None
        if args.path.exists():
            current = args.path.read_bytes().decode("utf-8").replace("\r\n", "\n")
        if current != generated:
            sys.stderr.write(f"{args.path} is out of date; run with --write and commit it.\n")
            return 1
        return 0
    if args.write:
        args.path.parent.mkdir(parents=True, exist_ok=True)
        with args.path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(generated)
        return 0
    sys.stdout.write(generated)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
````

## headwaters/core/catalog/merge.py

````python
"""Merge live discovery with the curated catalog and user overrides into ``ModelRecord``s.

Order (v3 §4.2): live -> curated catalog (matched by id, alias or source-supplied names) ->
user overrides from ``rules.yaml``. Live facts (context, tools, JSON, prices, size, arch) win over
the catalog; the catalog fills gaps and supplies scores, deprecations and ``retains_data``.
Models the catalog does not know get the conservative defaults and ``unscored=True``.
Scores are stored on a 0..100 scale (catalog 0..1 x 100), like the floors in ``rules.yaml``.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from headwaters.core.catalog.schema import Catalog, ModelEntry
from headwaters.core.catalog.sources import LiveModel
from headwaters.core.rules import ModelOverride, Rules
from headwaters.core.state_models import ModelRow, ScoreRow

_TIER_FROM_HINT = {"paid": "paid", "free": "free_cloud"}


@dataclass
class ModelRecord:
    row: ModelRow
    seed_scores: dict[str, float] = field(default_factory=dict)
    override_scores: dict[str, float] = field(default_factory=dict)
    catalog_id: str | None = None

    def score_rows(self) -> list[ScoreRow]:
        tasks = sorted(set(self.seed_scores) | set(self.override_scores))
        return [
            ScoreRow(
                model_id=self.row.id,
                task=t,
                seed=self.seed_scores.get(t),
                override=self.override_scores.get(t),
            )
            for t in tasks
        ]


class CatalogLookup:
    """Case-insensitive lookup by id/alias; Ollama ``:latest`` is treated as the bare name."""

    def __init__(self, catalog: Catalog) -> None:
        self.catalog = catalog
        self._by_name: dict[str, ModelEntry] = {}
        for entry in catalog.models:
            for name in (entry.id, *entry.aliases):
                self._by_name.setdefault(name.lower(), entry)
        self._by_id = {m.id: m for m in catalog.models}

    def find(self, names: list[str]) -> ModelEntry | None:
        for name in names:
            key = name.lower()
            for candidate in (key, key.removesuffix(":latest")):
                if candidate in self._by_name:
                    return self._by_name[candidate]
        return None

    def get(self, model_id: str) -> ModelEntry | None:
        return self._by_id.get(model_id)


def _scores_100(entry_scores: dict[str, float | None]) -> dict[str, float]:
    return {t: round(v * 100, 3) for t, v in entry_scores.items() if v is not None}


def _find_override(rules: Rules, names: list[str]) -> ModelOverride | None:
    wanted = {n.lower() for n in names}
    for key, override in rules.models.overrides.items():
        if key.lower() in wanted:
            return override
    return None


def _apply_override(record: ModelRecord, override: ModelOverride) -> None:
    changes: dict[str, object] = {}
    if override.tier is not None:
        changes["tier"] = override.tier
    if override.tools_ok is not None:
        changes["tools_ok"] = override.tools_ok
    if override.json_ok is not None:
        changes["json_ok"] = override.json_ok
    if override.ctx is not None:
        changes["ctx"] = override.ctx
    if changes:
        record.row = replace(record.row, **changes)  # type: ignore[arg-type]
    record.override_scores.update({t: float(s) for t, s in override.scores.items()})


def _from_live(
    item: LiveModel, entry: ModelEntry | None, catalog: Catalog, now: float
) -> ModelRecord:
    defaults = catalog.unknown_model_defaults
    if entry is not None:
        return ModelRecord(
            ModelRow(
                id=item.id,
                tier=item.tier,
                provider=item.provider,
                modalities=item.modalities or entry.modalities,
                tools_ok=item.tools_ok if item.tools_ok is not None else entry.tools_ok,
                json_ok=item.json_ok if item.json_ok is not None else entry.json_ok,
                ctx=item.ctx or entry.ctx,
                price_in=item.price_in if item.price_in is not None else entry.price_in,
                price_out=item.price_out if item.price_out is not None else entry.price_out,
                size_gb=item.size_gb or entry.size_gb,
                arch=item.arch or (entry.arch.model_dump() if entry.arch else None),
                source=item.source,
                last_seen=now,
                deprecated_by=entry.deprecated_by,
                retains_data=entry.retains_data or bool(item.retains_data),
                warm=item.warm,
                size_vram_gb=item.size_vram_gb,
            ),
            _scores_100(entry.scores.model_dump()),
            catalog_id=entry.id,
        )
    return ModelRecord(
        ModelRow(
            id=item.id,
            tier=item.tier,
            provider=item.provider,
            modalities=item.modalities,
            tools_ok=item.tools_ok if item.tools_ok is not None else defaults.tools_ok,
            json_ok=item.json_ok if item.json_ok is not None else defaults.json_ok,
            ctx=item.ctx or defaults.ctx,
            price_in=item.price_in,
            price_out=item.price_out,
            size_gb=item.size_gb,
            arch=item.arch,
            source=item.source,
            last_seen=now,
            unscored=True,
            retains_data=bool(item.retains_data),
            warm=item.warm,
            size_vram_gb=item.size_vram_gb,
        ),
        _scores_100(defaults.scores.model_dump()),
    )


def _from_catalog_only(entry: ModelEntry, tier: str, now: float) -> ModelRecord:
    return ModelRecord(
        ModelRow(
            id=entry.id,
            tier=tier,
            provider=entry.provider,
            modalities=entry.modalities,
            tools_ok=entry.tools_ok,
            json_ok=entry.json_ok,
            ctx=entry.ctx,
            price_in=entry.price_in,
            price_out=entry.price_out,
            size_gb=entry.size_gb,
            arch=entry.arch.model_dump() if entry.arch else None,
            source="catalog",
            last_seen=now,
            deprecated_by=entry.deprecated_by,
            retains_data=entry.retains_data,
        ),
        _scores_100(entry.scores.model_dump()),
        catalog_id=entry.id,
    )


def merge_models(
    live: list[LiveModel], catalog: Catalog, rules: Rules, now: float
) -> list[ModelRecord]:
    lookup = CatalogLookup(catalog)
    records: dict[str, ModelRecord] = {}
    matched: set[str] = set()
    for item in live:
        entry = lookup.find([item.id, *item.match_names])
        if entry is not None:
            matched.add(entry.id)
        records.setdefault(item.id, _from_live(item, entry, catalog, now))
    # Curated cloud entries with no live counterpart (e.g. the user's own paid keys).
    for entry in catalog.models:
        if entry.id in matched or entry.id in records:
            continue
        tier = next((_TIER_FROM_HINT[h] for h in entry.tier_hints if h in _TIER_FROM_HINT), None)
        if tier is not None:  # local models only count once installed
            records[entry.id] = _from_catalog_only(entry, tier, now)
    for record in records.values():
        names = [record.row.id]
        if record.catalog_id:
            names.append(record.catalog_id)
            curated = lookup.get(record.catalog_id)
            names.extend(curated.aliases if curated else [])
        override = _find_override(rules, names)
        if override is not None:
            _apply_override(record, override)
    return sorted(records.values(), key=lambda r: r.row.id)


__all__ = ["CatalogLookup", "ModelRecord", "merge_models"]
````

## headwaters/core/catalog/schema.py

````python
"""Typed model of the curated catalog (``catalog/seed_catalog.json`` and later signed updates).

Model slugs, limits, prices, error regexes and placeholder patterns live in the catalog data,
never in code. Unknown keys are errors so typos are reported. Scores use a 0..1 scale.
"""

from __future__ import annotations

import re
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Tier = Literal["paid", "free", "local"]
Role = Literal["agentic_coder", "general", "vision", "classifier"]
GpuClass = str
_DATE = r"^\d{4}-\d{2}-\d{2}$"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _check_regex(value: str) -> str:
    try:
        re.compile(value)
    except re.error as exc:
        raise ValueError(f"invalid regular expression {value!r}: {exc}") from exc
    return value


class Scores(_Model):
    code: float | None = Field(default=None, ge=0, le=1)
    reasoning: float | None = Field(default=None, ge=0, le=1)
    chat: float | None = Field(default=None, ge=0, le=1)
    summarize: float | None = Field(default=None, ge=0, le=1)
    extract: float | None = Field(default=None, ge=0, le=1)
    creative: float | None = Field(default=None, ge=0, le=1)
    vision: float | None = Field(default=None, ge=0, le=1)
    agentic_tools: float | None = Field(default=None, ge=0, le=1)


class Arch(_Model):
    n_layers: int = Field(gt=0)
    n_kv_heads: int = Field(gt=0)
    head_dim: int = Field(gt=0)


class ModelEntry(_Model):
    id: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list)
    tier_hints: list[Tier] = Field(default_factory=list)
    provider: str = Field(min_length=1)
    modalities: list[str] = Field(default_factory=lambda: ["text"])
    tools_ok: bool | None = None
    json_ok: bool | None = None
    ctx: int | None = Field(default=None, gt=0)
    price_in: float | None = Field(default=None, ge=0, description="USD per million tokens")
    price_out: float | None = Field(default=None, ge=0, description="USD per million tokens")
    price_date: str | None = Field(default=None, pattern=_DATE)
    size_gb: float | None = Field(default=None, gt=0)
    arch: Arch | None = None
    scores: Scores = Field(default_factory=Scores)
    expected_tps: dict[GpuClass, float] = Field(default_factory=dict)
    deprecated_by: str | None = None
    retains_data: bool = False
    unscored: bool = False
    verify: bool = False

    @field_validator("expected_tps")
    @classmethod
    def _tps_positive(cls, value: dict[GpuClass, float]) -> dict[GpuClass, float]:
        for gpu, tps in value.items():
            if tps <= 0:
                raise ValueError(f"expected_tps[{gpu}] must be positive")
        return value

    @model_validator(mode="after")
    def _price_needs_date(self) -> Self:
        if (self.price_in is not None or self.price_out is not None) and not self.price_date:
            raise ValueError("prices require price_date")
        return self


class FreeLimit(_Model):
    provider: str = Field(min_length=1)
    model: str | None = None
    rpm: int | None = Field(default=None, gt=0)
    rpd: int | None = Field(default=None, gt=0)
    verify: bool = False


WallKind = Literal["rate_limit", "exhausted", "overloaded", "context", "other"]
WallScope = Literal["model", "provider", "key"]


class WallPattern(_Model):
    """One wall signal. ``provider`` is a provider id or ``*`` (any provider)."""

    provider: str = Field(min_length=1)
    status_codes: list[int] = Field(default_factory=list)
    body_regex: str | None = None
    kind: WallKind
    scope: WallScope
    verify: bool = False

    @field_validator("body_regex")
    @classmethod
    def _regex_ok(cls, value: str | None) -> str | None:
        return None if value is None else _check_regex(value)

    @model_validator(mode="after")
    def _has_matcher(self) -> Self:
        if not self.status_codes and self.body_regex is None:
            raise ValueError("a wall pattern needs status_codes or body_regex")
        return self


class NamedPattern(_Model):
    name: str = Field(min_length=1)
    regex: str
    verify: bool = False

    @field_validator("regex")
    @classmethod
    def _regex_ok(cls, value: str) -> str:
        return _check_regex(value)


class GpuProfile(_Model):
    vram_gb: float = Field(gt=0)
    recommended: dict[Role, list[str]] = Field(default_factory=dict)


class SubscriptionRate(_Model):
    plan: str = Field(min_length=1)
    usd_per_month: float = Field(ge=0)
    included_credits: float | None = Field(default=None, ge=0)
    price_date: str = Field(pattern=_DATE)
    verify: bool = False


class UnknownModelDefaults(_Model):
    """Conservative values given to a model the catalog does not know."""

    scores: Scores
    tools_ok: bool = False
    json_ok: bool = False
    ctx: int = Field(gt=0)


ANALYZER_TASKS = ("code", "reasoning", "chat", "summarize", "extract", "creative")


class AnalyzerHints(_Model):
    """Data for the deterministic analyzer (HDW-106): keywords, agent user-agents, thresholds.

    Every list is data so it can be updated without a release. Regexes are matched
    case-insensitively against the last user message. All defaults are empty or neutral.
    """

    agentic_system_prompt_tokens: int = Field(default=1500, gt=0)
    confidence_threshold: float = Field(default=0.6, ge=0, le=1)
    chars_per_token_code: float = Field(default=3.5, gt=0)
    chars_per_token_prose: float = Field(default=4.0, gt=0)
    image_tokens: int = Field(default=800, ge=0)
    complexity_token_steps: list[int] = Field(default_factory=lambda: [2000, 8000])
    agent_user_agents: list[str] = Field(default_factory=list)  # case-insensitive substrings
    reasoning_keywords: list[str] = Field(default_factory=list)
    duck_keywords: list[str] = Field(default_factory=list)
    risk_keywords: list[str] = Field(default_factory=list)
    task_keywords: dict[str, list[str]] = Field(default_factory=dict)
    fence_aliases: dict[str, str] = Field(default_factory=dict)
    extension_languages: dict[str, str] = Field(default_factory=dict)  # ".py" -> "python"

    @field_validator("reasoning_keywords", "duck_keywords", "risk_keywords")
    @classmethod
    def _regexes_ok(cls, value: list[str]) -> list[str]:
        for item in value:
            _check_regex(item)
        return value

    @field_validator("task_keywords")
    @classmethod
    def _task_keywords_ok(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        for task, patterns in value.items():
            if task not in ANALYZER_TASKS:
                raise ValueError(f"unknown task {task!r}; use one of {', '.join(ANALYZER_TASKS)}")
            for item in patterns:
                _check_regex(item)
        return value

    @field_validator("extension_languages")
    @classmethod
    def _extensions_ok(cls, value: dict[str, str]) -> dict[str, str]:
        for ext in value:
            if not ext.startswith(".") or len(ext) < 2:
                raise ValueError(f"extension {ext!r} must start with '.'")
        return value


class Catalog(_Model):
    schema_version: int = Field(ge=1)
    catalog_version: str = Field(min_length=1)
    models: list[ModelEntry] = Field(default_factory=list)
    unknown_model_defaults: UnknownModelDefaults
    free_limits: list[FreeLimit] = Field(default_factory=list)
    wall_patterns: list[WallPattern] = Field(default_factory=list)
    placeholder_patterns: list[NamedPattern] = Field(default_factory=list)
    gpu_profiles: dict[str, GpuProfile] = Field(default_factory=dict)
    secret_patterns: list[NamedPattern] = Field(default_factory=list)
    secret_placeholders: list[NamedPattern] = Field(default_factory=list)
    subscription_rates: list[SubscriptionRate] = Field(default_factory=list)
    analyzer_hints: AnalyzerHints = Field(default_factory=AnalyzerHints)
    prompt_bank_ref: str | None = None

    @model_validator(mode="after")
    def _cross_references(self) -> Self:
        problems: list[str] = []
        names: dict[str, int] = {}
        for index, entry in enumerate(self.models):
            for name in {entry.id.lower(), *(a.lower() for a in entry.aliases)}:
                if name in names:
                    other = self.models[names[name]].id
                    problems.append(f"name {name!r} is used by {other!r} and {entry.id!r}")
                names.setdefault(name, index)
        ids = {m.id for m in self.models}
        for entry in self.models:
            if entry.deprecated_by is not None and entry.deprecated_by not in ids:
                problems.append(f"{entry.id}: deprecated_by {entry.deprecated_by!r} is unknown")
        for profile_name, profile in self.gpu_profiles.items():
            for role, model_ids in profile.recommended.items():
                for model_id in model_ids:
                    if model_id not in ids:
                        problems.append(f"gpu_profiles.{profile_name}.{role}: unknown {model_id!r}")
        if problems:
            raise ValueError("; ".join(problems))
        return self
````

## headwaters/core/catalog/trust.py

````python
"""ed25519 verification of the curated catalog.

The catalog file is signed byte-for-byte; the detached signature is base64 text. The public key
is embedded in the app (``EMBEDDED_PUBLIC_KEY_B64``, base64 of the raw 32-byte key). The owner
generates the key pair and signs releases with ``scripts/sign_catalog.py``; the private key
never lives in this repo.
"""

from __future__ import annotations

import base64

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

# Owner: run ``python scripts/sign_catalog.py keygen`` and paste the printed public key here.
# While empty, no downloaded catalog can be accepted (the bundled seed is still used).
EMBEDDED_PUBLIC_KEY_B64 = ""


class SignatureError(ValueError):
    """The catalog signature is missing, malformed or does not match the data."""


def verify(data: bytes, signature_b64: bytes | str, public_key_b64: str) -> None:
    """Raise ``SignatureError`` unless ``signature_b64`` is a valid signature of ``data``."""
    if not public_key_b64:
        raise SignatureError("no catalog public key is embedded in this build")
    raw_sig = (
        signature_b64.encode("ascii", "replace")
        if isinstance(signature_b64, str)
        else signature_b64
    )
    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64, validate=True))
        signature = base64.b64decode(raw_sig.strip(), validate=True)
    except ValueError as exc:
        raise SignatureError(f"malformed key or signature: {exc}") from exc
    try:
        key.verify(signature, data)
    except InvalidSignature as exc:
        raise SignatureError("signature does not match the catalog data") from exc
````

## headwaters/core/catalog/updater.py

````python
"""Catalog updater: signed, ETag-conditional, atomic, offline-safe.

Order of trust when loading: cached download (re-verified every load) -> previous good copy ->
bundled seed. A download is accepted only if its ed25519 signature verifies AND it validates
against the catalog schema. Requests carry no prompt data: just the URL, ``If-None-Match`` and
a fixed ``User-Agent``. Nothing here touches the network when ``airplane`` is on.

The signed file is ``<url>`` (the catalog JSON, signed byte-for-byte) with the detached
base64 signature at ``<url>.sig``.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from headwaters.core import paths
from headwaters.core.catalog import trust
from headwaters.core.catalog.loader import Catalog, parse_catalog
from headwaters.core.rules import Rules

CACHE_SUBDIR = "catalog"
CURRENT_NAME = "catalog.cache.json"
PREVIOUS_NAME = "catalog.previous.json"
MAX_BYTES = 5_000_000
TIMEOUT_SECONDS = 10.0
USER_AGENT = "Headwaters-catalog-updater"

Source = Literal["cache", "previous", "seed"]
Outcome = Literal["updated", "not_modified", "disabled", "error"]


@dataclass(frozen=True)
class FetchResult:
    """``body`` is None for a 304 Not Modified."""

    status: int
    body: bytes | None
    etag: str | None = None


Fetcher = Callable[[str, str | None], FetchResult]


@dataclass(frozen=True)
class UpdateResult:
    outcome: Outcome
    message: str


@dataclass(frozen=True)
class CatalogStatus:
    source: Source
    catalog_version: str
    etag: str | None
    fetched_at: float | None
    url: str | None
    update_blocked: str | None


def cache_dir() -> Path:
    return paths.cache_dir() / CACHE_SUBDIR


def seed_path() -> Path:
    """Bundled seed: ``catalog/seed_catalog.json`` in a source checkout."""
    return Path(__file__).resolve().parents[3] / "catalog" / "seed_catalog.json"


def blocked_reason(rules: Rules, *, manual: bool = False) -> str | None:
    """Why no network call may be made, or None. ``manual`` ignores ``auto_update``."""
    if rules.airplane:
        return "airplane mode is on"
    if rules.catalog.disable_when_local and (rules.private_lock or rules.default_scope == "local"):
        return "local-only lock state"
    if not manual and not rules.catalog.auto_update:
        return "catalog.auto_update is off"
    if not rules.catalog.url:
        return "catalog.url is not configured"
    return None


def default_fetch(url: str, etag: str | None) -> FetchResult:
    """Plain conditional GET. Only https URLs are allowed."""
    if not url.lower().startswith("https://"):
        raise ValueError("catalog URL must use https")
    headers = {"User-Agent": USER_AGENT}
    if etag:
        headers["If-None-Match"] = etag
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            body: bytes = response.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise ValueError("catalog download is too large")
            return FetchResult(response.status, body, response.headers.get("ETag"))
    except urllib.error.HTTPError as exc:
        if exc.code == 304:
            return FetchResult(304, None, etag)
        raise


def _read_envelope(path: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads(paths.long_safe(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(raw, dict) and isinstance(raw.get("data"), str):
        return raw
    return None


def _write_atomic(path: Path, text: str) -> None:
    safe = paths.long_safe(path)
    safe.parent.mkdir(parents=True, exist_ok=True)
    tmp = safe.with_name(safe.name + f".{os.getpid()}.tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, safe)
    finally:
        tmp.unlink(missing_ok=True)


def _verified_catalog(envelope: dict[str, Any], key_b64: str, source: Path) -> Catalog | None:
    try:
        data = str(envelope["data"])
        trust.verify(data.encode("utf-8"), str(envelope.get("signature", "")), key_b64)
        return parse_catalog(data, source)
    except (trust.SignatureError, ValueError):
        return None


def load_active(
    public_key_b64: str | None = None, directory: Path | None = None
) -> tuple[Catalog, Source]:
    """Best available catalog without any network access. Never raises for bad cache files."""
    key = trust.EMBEDDED_PUBLIC_KEY_B64 if public_key_b64 is None else public_key_b64
    base = directory or cache_dir()
    candidates: list[tuple[str, Source]] = [(CURRENT_NAME, "cache"), (PREVIOUS_NAME, "previous")]
    for name, source in candidates:
        envelope = _read_envelope(base / name)
        if envelope is not None:
            catalog = _verified_catalog(envelope, key, base / name)
            if catalog is not None:
                return catalog, source
    seed = seed_path()
    return parse_catalog(paths.long_safe(seed).read_text(encoding="utf-8"), seed), "seed"


def update(
    rules: Rules,
    *,
    manual: bool = True,
    fetch: Fetcher | None = None,
    public_key_b64: str | None = None,
    directory: Path | None = None,
    now: float | None = None,
) -> UpdateResult:
    """Fetch, verify and atomically install a newer catalog. Never raises on network trouble."""
    reason = blocked_reason(rules, manual=manual)
    if reason is not None:
        return UpdateResult("disabled", f"Catalog update skipped: {reason}.")
    fetch = fetch or default_fetch
    url = str(rules.catalog.url)
    key = trust.EMBEDDED_PUBLIC_KEY_B64 if public_key_b64 is None else public_key_b64
    base = directory or cache_dir()
    current_path = base / CURRENT_NAME
    current = _read_envelope(current_path)
    if current is not None and _verified_catalog(current, key, current_path) is None:
        current = None  # a cache we would not accept: do not send its validator
    etag = current.get("etag") if current is not None else None
    try:
        result = fetch(url, etag if isinstance(etag, str) else None)
        if result.status == 304 or result.body is None:
            return UpdateResult("not_modified", "Catalog is up to date (304); cache unchanged.")
        sig = fetch(url + ".sig", None)
        if sig.body is None:
            raise ValueError("signature file missing")
        trust.verify(result.body, sig.body, key)
        data = result.body.decode("utf-8")
        catalog = parse_catalog(data, Path(url))
    except (OSError, ValueError) as exc:
        return UpdateResult("error", f"Catalog update rejected, cache unchanged: {exc}")
    envelope = {
        "data": data,
        "signature": sig.body.decode("ascii", "replace").strip(),
        "etag": result.etag,
        "fetched_at": time.time() if now is None else now,
        "url": url,
    }
    if current is not None:
        _write_atomic(base / PREVIOUS_NAME, json.dumps(current, ensure_ascii=False))
    _write_atomic(current_path, json.dumps(envelope, ensure_ascii=False))
    return UpdateResult("updated", f"Catalog updated to {catalog.catalog_version}.")


def status(
    rules: Rules, public_key_b64: str | None = None, directory: Path | None = None
) -> CatalogStatus:
    catalog, source = load_active(public_key_b64, directory)
    base = directory or cache_dir()
    envelope = _read_envelope(base / CURRENT_NAME) if source == "cache" else None
    fetched = envelope.get("fetched_at") if envelope else None
    etag = envelope.get("etag") if envelope else None
    return CatalogStatus(
        source=source,
        catalog_version=catalog.catalog_version,
        etag=etag if isinstance(etag, str) else None,
        fetched_at=float(fetched) if isinstance(fetched, int | float) else None,
        url=rules.catalog.url,
        update_blocked=blocked_reason(rules, manual=True),
    )
````

## headwaters/core/hardware/__init__.py

````python

````

## headwaters/core/hardware/fit.py

````python
"""KV-cache math, model fit and ``num_ctx`` selection (HDW-109, v3 section 7).

All sizes are GiB (1024^3), matching ``HardwareState.budget_gb``; catalog ``size_gb`` is read
as GiB too. The KV formula is an upper bound for sliding-window / shared-KV architectures
(assumption R7), so calibrate weights against ``/api/ps`` after the first load.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from headwaters.core.catalog.schema import Arch, ModelEntry

GIB = 1024**3
KV_BYTES_PER_ELEM: Mapping[str, float] = {"f16": 2.0, "q8_0": 1.0, "q4_0": 0.5}
DEFAULT_KV_TYPE = "f16"  # unknown type: assume the biggest (safe)
CTX_STEPS: tuple[int, ...] = (8192, 16384, 24576, 32768, 49152, 65536)
CTX_HEADROOM = 1.2  # num_ctx must be >= 1.2 x estimated prompt tokens
GB_TO_GIB = 1e9 / 1024**3
DEFAULT_OVERHEAD_GB = 1.0  # v3 section 7.1 says 0.5-1.0; top of range = safe
DEFAULT_OFFLOAD_WEIGHT_FACTOR = 2.0  # weights up to 2x the GPU budget run (slowly) offloaded

FitStatus = Literal["fits", "offload", "no_fit"]


@dataclass(frozen=True)
class FitConfig:
    kv_type: str = "q8_0"
    overhead_gb: float = DEFAULT_OVERHEAD_GB
    offload_weight_factor: float = DEFAULT_OFFLOAD_WEIGHT_FACTOR
    ctx_steps: tuple[int, ...] = CTX_STEPS


@dataclass(frozen=True)
class FitResult:
    model_id: str
    status: FitStatus
    num_ctx: int | None  # None for no_fit
    reason: str
    weights_gb: float = 0.0
    kv_gb: float = 0.0
    budget_gb: float = 0.0
    max_ctx_tokens: int = 0  # continuous maximum that fits (not rounded to a step)
    weights_calibrated: bool = False

    @property
    def usable(self) -> bool:
        return self.status != "no_fit"


def kv_bytes_per_token(arch: Arch, kv_type: str = "q8_0") -> float:
    elem = KV_BYTES_PER_ELEM.get(kv_type, KV_BYTES_PER_ELEM[DEFAULT_KV_TYPE])
    return 2.0 * arch.n_layers * arch.n_kv_heads * arch.head_dim * elem


def kv_bytes(arch: Arch, ctx: int, kv_type: str = "q8_0") -> float:
    """``2 * layers * kv_heads * head_dim * ctx * bytes(kv_type)``."""
    return kv_bytes_per_token(arch, kv_type) * ctx


def max_ctx_tokens(arch: Arch, weights_gb: float, budget_gb: float, cfg: FitConfig) -> int:
    free_bytes = (budget_gb - weights_gb - cfg.overhead_gb) * GIB
    if free_bytes <= 0:
        return 0
    return int(free_bytes // kv_bytes_per_token(arch, cfg.kv_type))


class WeightsCalibration:
    """Real weight sizes learned from ``/api/ps`` (``size`` is weights + KV + buffers)."""

    def __init__(self) -> None:
        self._weights_gb: dict[str, float] = {}
        self._lock = threading.Lock()

    def observe(
        self, model_id: str, ps_size_bytes: int, loaded_ctx: int, arch: Arch, kv_type: str
    ) -> float | None:
        """Record ``size - kv(loaded_ctx)`` as the weights size. Returns it, or None if bogus."""
        weights = (ps_size_bytes - kv_bytes(arch, loaded_ctx, kv_type)) / GIB
        if weights <= 0:
            return None
        with self._lock:
            self._weights_gb[model_id] = weights
        return weights

    def get(self, model_id: str) -> float | None:
        with self._lock:
            return self._weights_gb.get(model_id)


def _need_tokens(est_tokens: int) -> int:
    return math.ceil(CTX_HEADROOM * max(0, est_tokens))


def evaluate_fit(
    entry: ModelEntry,
    budget_gb: float,
    est_tokens: int,
    cfg: FitConfig | None = None,
    calibration: WeightsCalibration | None = None,
    sysmem_flagged: bool = False,
) -> FitResult:
    """Classify ``entry`` as fits / offload / no_fit and pick ``num_ctx`` for the request."""
    cfg = cfg or FitConfig()
    mid = entry.id
    if entry.arch is None or entry.size_gb is None:
        return FitResult(mid, "no_fit", None, "missing_catalog_data", budget_gb=budget_gb)
    arch = entry.arch
    observed = calibration.get(mid) if calibration else None
    weights = observed if observed is not None else entry.size_gb * GB_TO_GIB
    calibrated = observed is not None
    need = _need_tokens(est_tokens)
    ctx_cap = entry.ctx
    steps = [s for s in cfg.ctx_steps if ctx_cap is None or s <= ctx_cap]
    cont = max_ctx_tokens(arch, weights, budget_gb, cfg)

    def result(
        status: FitStatus, ctx: int | None, reason: str, kv_ctx: int | None = None
    ) -> FitResult:
        kv_gb = kv_bytes(arch, kv_ctx or ctx or 0, cfg.kv_type) / GIB
        return FitResult(mid, status, ctx, reason, weights, kv_gb, budget_gb, cont, calibrated)

    if not steps or need > steps[-1]:
        return result("no_fit", None, "context_too_large")
    fitting = [s for s in steps if s <= cont]
    spill_ctx = next(s for s in steps if s >= need)  # smallest step that holds the request
    if sysmem_flagged:
        return result("offload", spill_ctx, "sysmem_fallback_detected")
    if fitting and fitting[-1] >= need:
        return result("fits", fitting[-1], "ok")
    if weights + cfg.overhead_gb <= budget_gb:
        return result("offload", spill_ctx, "kv_exceeds_budget")
    if weights <= budget_gb * cfg.offload_weight_factor:
        return result("offload", spill_ctx, "weights_exceed_budget")
    return result("no_fit", None, "weights_too_large")
````

## headwaters/core/hardware/gpu.py

````python
"""GPU probes (HDW-108): NVML first, then ``nvidia-smi``. Pure data in, no policy.

Both probes return ``None`` when they cannot answer; callers fall back to the next source and
finally to manual config. Nothing here talks to the network.
"""

from __future__ import annotations

import contextlib
import importlib
import logging
import os
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

_log = logging.getLogger(__name__)

_MIB = 1024 * 1024
# NVML reports "not available" per-process memory as a huge sentinel on Windows (WDDM).
_NVML_NA_THRESHOLD = 1 << 60
_SMI_TIMEOUT_S = 5.0

GpuSource = Literal["nvml", "nvidia-smi", "manual"]


@dataclass(frozen=True)
class GpuProcess:
    """One process using the GPU. ``mem_mb`` is ``None`` when the driver hides it."""

    pid: int
    name: str
    mem_mb: float | None


@dataclass(frozen=True)
class GpuInfo:
    name: str
    total_mb: float
    free_mb: float | None  # None for a manual entry: nothing was measured
    display_active: bool | None  # None = unknown
    source: GpuSource
    processes: tuple[GpuProcess, ...] = field(default_factory=tuple)

    @property
    def used_mb(self) -> float | None:
        return None if self.free_mb is None else max(0.0, self.total_mb - self.free_mb)


GpuProbe = Callable[[], GpuInfo | None]


def _process_name(pid: int) -> str:
    try:
        import psutil

        return str(psutil.Process(pid).name())
    except Exception:
        return ""


def _nvml_mem_mb(raw: object) -> float | None:
    if not isinstance(raw, int) or raw < 0 or raw >= _NVML_NA_THRESHOLD:
        return None
    return raw / _MIB


def _text(value: object) -> str:
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)


def _nvml_processes(nvml: Any, handle: Any) -> tuple[GpuProcess, ...]:
    seen: dict[int, GpuProcess] = {}
    for getter in ("nvmlDeviceGetComputeRunningProcesses", "nvmlDeviceGetGraphicsRunningProcesses"):
        try:
            entries = getattr(nvml, getter)(handle)
        except Exception:
            continue
        for entry in entries:
            pid = int(entry.pid)
            mem = _nvml_mem_mb(getattr(entry, "usedGpuMemory", None))
            old = seen.get(pid)
            if old is None or (old.mem_mb is None and mem is not None):
                seen[pid] = GpuProcess(pid=pid, name=_process_name(pid), mem_mb=mem)
    return tuple(seen.values())


def probe_nvml(module_name: str = "pynvml") -> GpuInfo | None:
    """Read the GPU with the largest memory through NVML (``nvidia-ml-py``)."""
    try:
        nvml = importlib.import_module(module_name)
        nvml.nvmlInit()
    except Exception as exc:
        _log.debug("NVML unavailable: %s", exc)
        return None
    try:
        best: tuple[float, Any] | None = None
        for index in range(int(nvml.nvmlDeviceGetCount())):
            handle = nvml.nvmlDeviceGetHandleByIndex(index)
            total = float(nvml.nvmlDeviceGetMemoryInfo(handle).total)
            if best is None or total > best[0]:
                best = (total, handle)
        if best is None:
            return None
        handle = best[1]
        mem = nvml.nvmlDeviceGetMemoryInfo(handle)
        try:
            # R14 resolved: nvidia-ml-py exposes nvmlDeviceGetDisplayActive (0/1).
            display: bool | None = bool(nvml.nvmlDeviceGetDisplayActive(handle))
        except Exception:
            display = None
        return GpuInfo(
            name=_text(nvml.nvmlDeviceGetName(handle)),
            total_mb=float(mem.total) / _MIB,
            free_mb=float(mem.free) / _MIB,
            display_active=display,
            source="nvml",
            processes=_nvml_processes(nvml, handle),
        )
    except Exception as exc:
        _log.debug("NVML query failed: %s", exc)
        return None
    finally:
        with contextlib.suppress(Exception):
            nvml.nvmlShutdown()


SmiRunner = Callable[[Sequence[str]], str | None]


def find_nvidia_smi() -> str | None:
    found = shutil.which("nvidia-smi")
    if found:
        return found
    system_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
    candidate = os.path.join(system_root, "System32", "nvidia-smi.exe")
    return candidate if os.path.isfile(candidate) else None


def run_nvidia_smi(args: Sequence[str]) -> str | None:
    exe = find_nvidia_smi()
    if exe is None:
        return None
    try:
        done = subprocess.run(
            [exe, *args], capture_output=True, text=True, timeout=_SMI_TIMEOUT_S, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout if done.returncode == 0 else None


def _smi_number(cell: str) -> float | None:
    try:
        return float(cell.strip())
    except ValueError:
        return None


def _smi_display(cell: str) -> bool | None:
    value = cell.strip().lower()
    if value in {"enabled", "yes", "active"}:
        return True
    if value in {"disabled", "no", "inactive"}:
        return False
    return None


def _smi_processes(runner: SmiRunner) -> tuple[GpuProcess, ...]:
    out = runner(
        ["--query-compute-apps=pid,process_name,used_memory", "--format=csv,noheader,nounits"]
    )
    procs: list[GpuProcess] = []
    for line in (out or "").splitlines():
        cells = [c.strip() for c in line.split(",")]
        if len(cells) < 3 or not cells[0].isdigit():
            continue
        name = os.path.basename(cells[1].replace("\\", "/"))
        procs.append(GpuProcess(pid=int(cells[0]), name=name, mem_mb=_smi_number(cells[2])))
    return tuple(procs)


def probe_nvidia_smi(runner: SmiRunner = run_nvidia_smi) -> GpuInfo | None:
    """Fallback probe: parse ``nvidia-smi --query-gpu`` CSV (largest GPU wins)."""
    out = runner(
        [
            "--query-gpu=name,memory.total,memory.free,display_active",
            "--format=csv,noheader,nounits",
        ]
    )
    if not out:
        return None
    best: GpuInfo | None = None
    for line in out.splitlines():
        cells = [c.strip() for c in line.split(",")]
        if len(cells) < 4:
            continue
        total, free = _smi_number(cells[1]), _smi_number(cells[2])
        if total is None or free is None or total <= 0:
            continue
        info = GpuInfo(
            name=cells[0],
            total_mb=total,
            free_mb=free,
            display_active=_smi_display(cells[3]),
            source="nvidia-smi",
        )
        if best is None or info.total_mb > best.total_mb:
            best = info
    if best is None:
        return None
    return GpuInfo(
        name=best.name,
        total_mb=best.total_mb,
        free_mb=best.free_mb,
        display_active=best.display_active,
        source="nvidia-smi",
        processes=_smi_processes(runner),
    )
````

## headwaters/core/hardware/residency.py

````python
"""Warm bonus and eviction penalty inputs (HDW-109, v3 section 7.3).

Pure functions: the router (HDW-1xx) turns these into score terms. The numeric weights are
tunable inputs, not catalog facts; defaults are a first draft.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class LoadedModel:
    """One entry of Ollama ``/api/ps``. ``size_gb`` is what it holds on the GPU."""

    name: str
    size_gb: float


@dataclass(frozen=True)
class ResidencyConfig:
    max_loaded_models: int = 1  # 12 GB profile: OLLAMA_MAX_LOADED_MODELS=1
    warm_bonus: float = 0.15
    eviction_penalty_per_model: float = 0.25


@dataclass(frozen=True)
class ResidencyInputs:
    warm: bool
    warm_bonus: float
    evicts: tuple[str, ...]
    eviction_penalty: float


def default_max_loaded(profile: str) -> int:
    return 2 if profile == "desktop_16gb" else 1


def residency_inputs(
    model_name: str,
    needed_gb: float,
    loaded: Sequence[LoadedModel],
    budget_gb: float,
    cfg: ResidencyConfig | None = None,
) -> ResidencyInputs:
    """Warm models earn a bonus; loading a cold one may evict others (oldest first)."""
    cfg = cfg or ResidencyConfig()
    if any(m.name == model_name for m in loaded):
        return ResidencyInputs(True, cfg.warm_bonus, (), 0.0)
    keep = list(loaded)
    evicted: list[str] = []
    while keep and (
        len(keep) >= cfg.max_loaded_models or sum(m.size_gb for m in keep) + needed_gb > budget_gb
    ):
        evicted.append(keep.pop(0).name)
    return ResidencyInputs(
        False, 0.0, tuple(evicted), cfg.eviction_penalty_per_model * len(evicted)
    )
````

## headwaters/core/hardware/state.py

````python
"""``HardwareState`` snapshot (HDW-108): GPU, reserve, profile, battery, GPU-busy.

Detection order is NVML -> ``nvidia-smi`` -> manual config. With no NVIDIA GPU and no manual
config the state says ``cloud_only_suggested`` instead of raising. HDW-109 does the KV/fit math
on top of ``budget_gb``; this module only measures and classifies.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from headwaters.core.hardware.gpu import (
    GpuInfo,
    GpuProbe,
    GpuProcess,
    probe_nvidia_smi,
    probe_nvml,
)
from headwaters.core.rules import LocalTier

Profile = Literal["laptop_12gb", "desktop_16gb", "custom"]
ReserveSource = Literal["override", "display_active", "no_display", "unknown_display"]
LocalSuggestion = Literal["ok", "degraded", "cloud_only_suggested"]
OnBatteryPolicy = Literal["prefer_cloud", "normal", "local_only"]

RESERVE_DISPLAY_GB = 2.0  # monitor on the RTX (v3.1 C1: 2.0-2.5)
RESERVE_UNKNOWN_GB = 2.5  # display status unknown: use the higher value
RESERVE_NO_DISPLAY_GB = 1.0  # iGPU drives the display (Optimus/MUX)

DEFAULT_BUSY_THRESHOLD_PCT = 50.0
DEFAULT_CACHE_TTL_S = 5.0

_LAPTOP_RANGE_GB = (10.0, 13.5)
_DESKTOP_RANGE_GB = (14.0, 17.5)
_OLLAMA_PREFIX = "ollama"


@dataclass(frozen=True)
class ManualGpu:
    """User-supplied GPU description, used only when detection finds nothing."""

    name: str
    total_gb: float


@dataclass(frozen=True)
class BatteryState:
    present: bool
    on_battery: bool
    percent: float | None = None


@dataclass(frozen=True)
class GpuBusy:
    busy: bool
    other_used_mb: float
    other_used_pct: float
    top_process: str | None = None
    reason: str = ""


@dataclass(frozen=True)
class HardwareState:
    gpu: GpuInfo | None
    profile: Profile
    reserve_gb: float
    reserve_source: ReserveSource
    budget_gb: float
    battery: BatteryState
    battery_policy: OnBatteryPolicy  # the effective rule right now ("normal" when on AC)
    gpu_busy: GpuBusy
    local_suggestion: LocalSuggestion
    notes: tuple[str, ...] = ()
    taken_at: float = 0.0

    @property
    def has_gpu(self) -> bool:
        return self.gpu is not None


def select_profile(total_gb: float | None, requested: str = "auto") -> Profile:
    """Pick a profile: an explicit rules value wins, else classify by total VRAM."""
    if requested == "laptop_12gb":
        return "laptop_12gb"
    if requested == "desktop_16gb":
        return "desktop_16gb"
    if requested == "custom" or total_gb is None:
        return "custom"
    if _LAPTOP_RANGE_GB[0] <= total_gb <= _LAPTOP_RANGE_GB[1]:
        return "laptop_12gb"
    if _DESKTOP_RANGE_GB[0] <= total_gb <= _DESKTOP_RANGE_GB[1]:
        return "desktop_16gb"
    return "custom"


def compute_reserve(
    display_active: bool | None, override_gb: float | None
) -> tuple[float, ReserveSource]:
    """Reserve in GB. A user override always wins; unknown display uses the higher value."""
    if override_gb is not None:
        return override_gb, "override"
    if display_active is None:
        return RESERVE_UNKNOWN_GB, "unknown_display"
    if display_active:
        return RESERVE_DISPLAY_GB, "display_active"
    return RESERVE_NO_DISPLAY_GB, "no_display"


def read_battery() -> BatteryState:
    """Battery via ``psutil.sensors_battery()``; no battery (desktop) is not on-battery."""
    try:
        import psutil

        reading = psutil.sensors_battery()
    except Exception:
        return BatteryState(present=False, on_battery=False)
    if reading is None:
        return BatteryState(present=False, on_battery=False)
    return BatteryState(
        present=True,
        on_battery=reading.power_plugged is False,  # None = unknown: treat as plugged in
        percent=float(reading.percent),
    )


def _is_ollama(proc: GpuProcess) -> bool:
    return proc.name.lower().startswith(_OLLAMA_PREFIX)


def detect_gpu_busy(
    gpu: GpuInfo | None,
    threshold_pct: float = DEFAULT_BUSY_THRESHOLD_PCT,
    own_pid: int | None = None,
) -> GpuBusy:
    """Busy = other (non-Ollama, non-us) processes hold more than ``threshold_pct`` of VRAM."""
    if gpu is None or gpu.total_mb <= 0:
        return GpuBusy(False, 0.0, 0.0)
    own = os.getpid() if own_pid is None else own_pid
    others = [p for p in gpu.processes if p.pid != own and not _is_ollama(p)]
    known = [p for p in others if p.mem_mb is not None]
    if known:
        used = sum(p.mem_mb or 0.0 for p in known)
        top = max(known, key=lambda p: p.mem_mb or 0.0)
        top_name: str | None = top.name or str(top.pid)
    elif gpu.free_mb is not None and not any(_is_ollama(p) for p in gpu.processes):
        # Windows hides per-process memory; with no Ollama on the GPU, all used VRAM is "other".
        used = gpu.used_mb or 0.0
        top_name = others[0].name if others and others[0].name else None
    else:
        return GpuBusy(False, 0.0, 0.0)
    pct = 100.0 * used / gpu.total_mb
    busy = pct > threshold_pct
    reason = f"other processes hold {pct:.0f}% of VRAM" if busy else ""
    return GpuBusy(busy, used, pct, top_name, reason)


def build_state(
    gpu: GpuInfo | None,
    local: LocalTier,
    battery: BatteryState,
    busy_threshold_pct: float = DEFAULT_BUSY_THRESHOLD_PCT,
    taken_at: float = 0.0,
) -> HardwareState:
    notes: list[str] = []
    total_gb = None if gpu is None else gpu.total_mb / 1024
    profile = select_profile(total_gb, local.profile)
    display = None if gpu is None else gpu.display_active
    reserve, reserve_src = compute_reserve(display, local.reserve_gb)
    budget = 0.0 if total_gb is None else max(0.0, total_gb - reserve)
    busy = detect_gpu_busy(gpu, busy_threshold_pct)
    policy: OnBatteryPolicy = local.on_battery if battery.on_battery else "normal"

    suggestion: LocalSuggestion
    if gpu is None:
        suggestion = "cloud_only_suggested"
        notes.append("No NVIDIA GPU detected; local models are not suggested.")
    elif busy.busy:
        suggestion = "degraded"
        notes.append(f"GPU busy: {busy.reason}.")
    else:
        suggestion = "ok"
    if gpu is not None and gpu.source == "manual":
        notes.append("GPU taken from manual config; free VRAM is not measured.")
    if reserve_src == "unknown_display":
        notes.append("Display status unknown; using the higher VRAM reserve.")
    if policy == "prefer_cloud":
        notes.append("On battery: prefer free cloud models.")
    elif policy == "local_only":
        notes.append("On battery: local only (per rules).")
    return HardwareState(
        gpu=gpu,
        profile=profile,
        reserve_gb=reserve,
        reserve_source=reserve_src,
        budget_gb=budget,
        battery=battery,
        battery_policy=policy,
        gpu_busy=busy,
        local_suggestion=suggestion,
        notes=tuple(notes),
        taken_at=taken_at,
    )


def _manual_gpu(local: LocalTier, manual: ManualGpu | None) -> GpuInfo | None:
    if manual is not None:
        return GpuInfo(manual.name, manual.total_gb * 1024, None, None, "manual")
    sizes = {"laptop_12gb": 12.0, "desktop_16gb": 16.0}
    if local.profile in sizes:
        label = f"{local.profile} (manual)"
        return GpuInfo(label, sizes[local.profile] * 1024, None, None, "manual")
    return None


class HardwareMonitor:
    """Detects hardware and caches the ``HardwareState`` for a few seconds."""

    def __init__(
        self,
        local: LocalTier | None = None,
        *,
        probes: Sequence[GpuProbe] | None = None,
        battery_reader: Callable[[], BatteryState] = read_battery,
        manual: ManualGpu | None = None,
        busy_threshold_pct: float = DEFAULT_BUSY_THRESHOLD_PCT,
        ttl_s: float = DEFAULT_CACHE_TTL_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._local = local or LocalTier()
        self._probes: tuple[GpuProbe, ...] = (
            tuple(probes) if probes is not None else (probe_nvml, probe_nvidia_smi)
        )
        self._battery_reader = battery_reader
        self._manual = manual
        self._busy_pct = busy_threshold_pct
        self._ttl = ttl_s
        self._clock = clock
        self._lock = threading.Lock()
        self._cached: HardwareState | None = None

    def set_local_rules(self, local: LocalTier) -> None:
        with self._lock:
            self._local = local
            self._cached = None

    def invalidate(self) -> None:
        with self._lock:
            self._cached = None

    def get_state(self, force: bool = False) -> HardwareState:
        with self._lock:
            now = self._clock()
            cached = self._cached
            if not force and cached is not None and now - cached.taken_at < self._ttl:
                return cached
            state = build_state(
                self._detect(), self._local, self._battery_reader(), self._busy_pct, now
            )
            self._cached = state
            return state

    def _detect(self) -> GpuInfo | None:
        for probe in self._probes:
            try:
                info = probe()
            except Exception:
                info = None
            if info is not None:
                return info
        return _manual_gpu(self._local, self._manual)
````

## headwaters/core/hardware/sysmem.py

````python
"""NVIDIA Sysmem Fallback detection (HDW-109, v3.1 C1).

Recent Windows drivers can silently spill VRAM into system RAM, so a model runs very slowly
instead of failing. Signal = measured tokens/s well below the catalog ``expected_tps`` for the
GPU profile, read together with ``/api/ps`` ``size_vram`` vs ``size``.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Literal

from headwaters.core.catalog.schema import ModelEntry

SLOW_RATIO = 0.5  # measured < 50% of expected counts as slow (first-draft threshold)
SPILL_TOLERANCE = 0.02  # size_vram may be 2% under size before we call it a spill

SYSMEM_HINT = (
    "The model looks like it is running partly out of system RAM. In the NVIDIA Control Panel "
    "open Manage 3D settings > Global Settings and set 'CUDA - Sysmem Fallback Policy' to "
    "'Prefer No Sysmem Fallback', then reload the model. Closing other GPU-heavy apps also helps."
)

Evidence = Literal["spilled", "resident_but_slow", "none", "no_data", "gpu_busy"]


@dataclass(frozen=True)
class SysmemVerdict:
    detected: bool
    evidence: Evidence
    measured_tps: float | None
    expected_tps: float | None
    ratio: float | None = None
    hint: str | None = None


def expected_tps_for(entry: ModelEntry, profile: str) -> float | None:
    return entry.expected_tps.get(profile)


def detect_sysmem_fallback(
    measured_tps: float | None,
    expected_tps: float | None,
    size_bytes: int | None,
    size_vram_bytes: int | None,
    gpu_busy: bool = False,
    slow_ratio: float = SLOW_RATIO,
) -> SysmemVerdict:
    """Slow generation plus ``/api/ps`` evidence. Another busy GPU process explains slowness."""
    if not measured_tps or measured_tps <= 0 or not expected_tps or expected_tps <= 0:
        return SysmemVerdict(False, "no_data", measured_tps, expected_tps)
    ratio = measured_tps / expected_tps
    if ratio >= slow_ratio:
        return SysmemVerdict(False, "none", measured_tps, expected_tps, ratio)
    if gpu_busy:
        return SysmemVerdict(False, "gpu_busy", measured_tps, expected_tps, ratio)
    spilled = bool(
        size_bytes
        and size_vram_bytes is not None
        and size_vram_bytes < size_bytes * (1 - SPILL_TOLERANCE)
    )
    evidence: Evidence = "spilled" if spilled else "resident_but_slow"
    return SysmemVerdict(True, evidence, measured_tps, expected_tps, ratio, SYSMEM_HINT)


class SysmemTracker:
    """Remembers which models were flagged so the fit step marks them ``offload``."""

    def __init__(self) -> None:
        self._flagged: dict[str, SysmemVerdict] = {}
        self._lock = threading.Lock()

    def record(self, model_id: str, verdict: SysmemVerdict) -> None:
        with self._lock:
            if verdict.detected:
                self._flagged[model_id] = verdict
            else:
                self._flagged.pop(model_id, None)

    def is_flagged(self, model_id: str) -> bool:
        with self._lock:
            return model_id in self._flagged

    def hints(self) -> dict[str, str]:
        """For ``doctor``: model id -> explanation."""
        with self._lock:
            return {k: v.hint or SYSMEM_HINT for k, v in self._flagged.items()}
````

## headwaters/core/paths.py

````python
"""Filesystem locations for Headwaters.

Config lives in the roaming dir (``%APPDATA%\\Headwaters`` on Windows); state, cache and logs
live in the local dir (``%LOCALAPPDATA%\\Headwaters``). Setting ``HEADWATERS_HOME`` redirects
everything under one root (used by tests).
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

import platformdirs

APP_NAME = "Headwaters"
HOME_ENV_VAR = "HEADWATERS_HOME"

# Windows legacy MAX_PATH is 260; leave headroom for file names appended later.
_WIN_LONG_PATH_THRESHOLD = 240
_WIN_EXTENDED_PREFIX = "\\\\?\\"
_WIN_UNC_EXTENDED_PREFIX = "\\\\?\\UNC\\"


@dataclass(frozen=True)
class HeadwatersPaths:
    """Resolved directory set. Directories are not created until ``ensure_dirs``."""

    config: Path
    state: Path
    cache: Path
    log: Path

    def ensure_dirs(self) -> HeadwatersPaths:
        for directory in (self.config, self.state, self.cache, self.log):
            long_safe(directory).mkdir(parents=True, exist_ok=True)
        return self


def _home_override() -> Path | None:
    raw = os.environ.get(HOME_ENV_VAR, "").strip()
    if not raw:
        return None
    return Path(raw).expanduser().resolve()


def get_paths() -> HeadwatersPaths:
    """Resolve the config/state/cache/log dirs, honouring ``HEADWATERS_HOME``."""
    root = _home_override()
    if root is not None:
        return HeadwatersPaths(
            config=root / "config",
            state=root / "state",
            cache=root / "cache",
            log=root / "logs",
        )
    return HeadwatersPaths(
        config=Path(platformdirs.user_config_dir(APP_NAME, appauthor=False, roaming=True)),
        state=Path(platformdirs.user_state_dir(APP_NAME, appauthor=False)),
        cache=Path(platformdirs.user_cache_dir(APP_NAME, appauthor=False)),
        log=Path(platformdirs.user_log_dir(APP_NAME, appauthor=False)),
    )


def config_dir() -> Path:
    return get_paths().config


def state_dir() -> Path:
    return get_paths().state


def cache_dir() -> Path:
    return get_paths().cache


def log_dir() -> Path:
    return get_paths().log


def long_safe(path: Path | str) -> Path:
    """Return ``path`` in a form the OS can open even when it exceeds MAX_PATH.

    On Windows, absolute paths longer than the threshold get the ``\\\\?\\`` extended prefix.
    Elsewhere, and for short paths, the path is returned unchanged. Paths with spaces need no
    special handling because we never build shell strings from them.
    """
    candidate = Path(path)
    if sys.platform != "win32":
        return candidate
    text = str(candidate)
    if text.startswith(_WIN_EXTENDED_PREFIX) or len(text) < _WIN_LONG_PATH_THRESHOLD:
        return candidate
    absolute = os.path.abspath(text)
    if absolute.startswith("\\\\"):  # UNC share
        return Path(_WIN_UNC_EXTENDED_PREFIX + absolute[2:])
    return Path(_WIN_EXTENDED_PREFIX + absolute)
````

## headwaters/core/quotas.py

````python
"""Quota tracker and cooldowns (HDW-111, v3 section 4.4).

Turns ``WallEvent``s (HDW-110) into persisted per-scope state (``ok | cooldown | exhausted``) in the
shared SQLite ``walls`` table, keeps rpm/rpd counters per free model in ``quotas``, and answers
``availability(model) -> ok | cooldown(eta) | exhausted | soft``.

* ``rate_limit``: cooldown until the hinted reset; exponential backoff with full jitter on repeats;
  cascade at once when the wait exceeds ``walls.cascade_if_wait_over_s``.
* ``exhausted``: no retry until a balance check passes (``poll_key``) or the reset time.
* ``overloaded``: ``walls.overloaded_max_retries`` short retries, then cascade and cool down.
* rpm/rpd limits come from catalog ``free_limits``; soft-exhausted at ``walls.soft_exhaust_pct``.

Subjects are namespaced (``model:``, ``provider:``, ``key:``) so a model id can never collide with
a provider or key alias. Only counts and timestamps are stored: never prompts, outputs or keys.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from headwaters.core import state_limits
from headwaters.core.catalog.http import HttpClient, SourceError
from headwaters.core.catalog.schema import FreeLimit, WallScope
from headwaters.core.rules import WallsRules
from headwaters.core.state import Database
from headwaters.core.state_misc import kv_get, kv_set
from headwaters.core.walls import WallEvent

OPENROUTER_KEY_URL = "https://openrouter.ai/api/v1/key"
ScopeState = Literal["ok", "cooldown", "exhausted"]
AvailabilityStatus = Literal["ok", "cooldown", "exhausted", "soft"]
Action = Literal["retry", "cascade", "none"]

_TRANSIENT_KINDS = ("rate_limit", "overloaded")
_STALE_AFTER_S = 3600.0  # a wall that ended this long ago no longer counts as "repeated"
_RPM = "rpm:"
_RPD = "rpd:"

__all__ = [
    "OPENROUTER_KEY_URL",
    "Availability",
    "KeyStatus",
    "QuotaTracker",
    "WallDecision",
    "key_subject",
    "model_subject",
    "parse_key_status",
    "provider_subject",
]


def model_subject(model: str) -> str:
    return f"model:{model}"


def provider_subject(provider: str) -> str:
    return f"provider:{provider.lower()}"


def key_subject(alias: str) -> str:
    return f"key:{alias}"


@dataclass(frozen=True, slots=True)
class Availability:
    """Answer to ``availability(model)``. ``eta`` is seconds until retry is sensible, if known."""

    status: AvailabilityStatus
    eta: float | None = None
    subject: str | None = None  # the wall subject that decided it, if any

    @property
    def usable(self) -> bool:
        """``soft`` models are still usable, just ranked down."""
        return self.status in ("ok", "soft")


@dataclass(frozen=True, slots=True)
class WallDecision:
    """What to do after recording a wall: retry the same target after ``wait_s``, or cascade."""

    action: Action
    state: ScopeState
    subject: str | None = None
    wait_s: float = 0.0
    until: float | None = None
    consecutive: int = 0


@dataclass(frozen=True, slots=True)
class KeyStatus:
    """Parsed ``GET /api/v1/key`` answer (only what the tracker needs)."""

    exhausted: bool
    limit_remaining: float | None = None
    usage: float | None = None


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def parse_key_status(payload: Any) -> KeyStatus:
    """OpenRouter key info: exhausted when the key's credit is spent or its blocking budget is."""
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise SourceError("unexpected key-status response")
    remaining = _number(data.get("limit_remaining"))
    exhausted = remaining is not None and remaining <= 0
    budget = data.get("effective_budget")
    if isinstance(budget, dict):
        left = _number(budget.get("remaining_usd"))
        if left is not None and left <= 0:
            exhausted = True
    return KeyStatus(exhausted, remaining, _number(data.get("usage")))


class QuotaTracker:
    """Persisted wall state and request counters. Cheap to create; all state is in ``db``."""

    def __init__(
        self,
        db: Database,
        walls: WallsRules | None = None,
        free_limits: Sequence[FreeLimit] = (),
        *,
        clock: Callable[[], float] | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self._db = db
        self._walls = walls or WallsRules()
        self._limits = list(free_limits)
        # Looked up per call (not bound) so a patched ``time.time`` (freezegun) is honoured.
        self._clock: Callable[[], float] = clock or (lambda: time.time())
        self._rng = rng or random.Random()  # jitter only, not security

    # ------------------------------------------------------------------ walls

    @staticmethod
    def _subject(scope: WallScope, model: str, provider: str, key: str | None) -> str:
        if scope == "provider":
            return provider_subject(provider)
        if scope == "key":
            return key_subject(key) if key else provider_subject(provider)
        return model_subject(model)

    def _jitter(self, base: float, attempt: int, cap: float) -> float:
        """Full jitter: uniform(0, min(cap, base * 2^(attempt-1)))."""
        ceiling = min(cap, base * (2 ** max(0, min(attempt, 32) - 1)))
        return self._rng.uniform(0.0, ceiling)

    def _forget_stale(self, subject: str, kind: str, now: float) -> None:
        """Drop a long-finished wall so an old streak does not inflate the backoff."""
        prior = state_limits.get_wall(self._db, subject, kind)
        if prior and prior.until is not None and now - prior.until > _STALE_AFTER_S:
            state_limits.clear_walls(self._db, [subject], [kind])

    def record_wall(
        self, event: WallEvent, *, model: str, provider: str, key: str | None = None
    ) -> WallDecision:
        """Persist the wall and say what to do. ``context`` and ``other`` are not walls."""
        if not event.is_wall:
            return WallDecision("none", "ok")
        now = self._clock()
        cfg = self._walls
        subject = self._subject(event.scope, model, provider, key)
        if event.kind == "exhausted":
            until = event.reset_at if event.reset_at and event.reset_at > now else None
            row = state_limits.record_wall(self._db, subject, "exhausted", until=until)
            return WallDecision("cascade", "exhausted", subject, 0.0, until, row.consecutive_count)
        self._forget_stale(subject, event.kind, now)
        prior = state_limits.get_wall(self._db, subject, event.kind)
        attempt = (prior.consecutive_count if prior else 0) + 1
        hint = event.retry_after or 0.0
        if event.kind == "rate_limit":
            wait = max(hint, self._jitter(cfg.backoff_base_s, attempt, cfg.backoff_max_s))
            action: Action = "cascade" if wait > cfg.cascade_if_wait_over_s else "retry"
        elif attempt <= cfg.overloaded_max_retries:
            wait = max(hint, self._jitter(cfg.overloaded_backoff_s, attempt, cfg.backoff_max_s))
            action = "cascade" if wait > cfg.cascade_if_wait_over_s else "retry"
        else:
            wait = max(hint, cfg.overloaded_cooldown_s)
            action = "cascade"
        until = now + wait
        row = state_limits.record_wall(self._db, subject, event.kind, until=until)
        return WallDecision(action, "cooldown", subject, wait, until, row.consecutive_count)

    def record_success(self, *, model: str, provider: str, key: str | None = None) -> None:
        """A good call ends transient walls (and their streak) and counts a request.

        An ``exhausted`` wall is only lifted by a balance check or its reset time.
        """
        subjects = [model_subject(model), provider_subject(provider)]
        if key:
            subjects.append(key_subject(key))
        state_limits.clear_walls(self._db, subjects, _TRANSIENT_KINDS)
        self.count_request(model, provider)

    # --------------------------------------------------------------- counters

    def _limit_for(self, model: str, provider: str) -> FreeLimit | None:
        """Catalog limit for a model: model-specific wins, else the provider-wide entry.

        A provider-wide limit (catalog ``model: null``) applies to each model separately,
        since free-tier limits are per model.
        """
        wanted = provider.lower()
        fallback: FreeLimit | None = None
        for entry in self._limits:
            if entry.provider.lower() != wanted:
                continue
            if entry.model == model:
                return entry
            if entry.model is None and fallback is None:
                fallback = entry
        return fallback

    @staticmethod
    def _windows(now: float) -> tuple[str, str]:
        moment = datetime.fromtimestamp(now, UTC)
        return _RPM + moment.strftime("%Y-%m-%dT%H:%M"), _RPD + moment.strftime("%Y-%m-%d")

    def count_request(self, model: str, provider: str) -> None:
        """Count one request against the model's rpm/rpd (no-op without a catalog limit)."""
        limit = self._limit_for(model, provider)
        if limit is None:
            return
        subject = model_subject(model)
        rpm_window, rpd_window = self._windows(self._clock())
        pct = self._walls.soft_exhaust_pct / 100
        for prefix, window, cap in ((_RPM, rpm_window, limit.rpm), (_RPD, rpd_window, limit.rpd)):
            row = state_limits.add_quota_usage(self._db, subject, window)
            soft = cap is not None and row.count >= cap * pct
            state_limits.set_quota_limit(self._db, subject, window, limit=cap, soft_exhausted=soft)
            state_limits.prune_quota_windows(self._db, subject, prefix, window)

    def _is_soft(self, model: str, provider: str, now: float) -> bool:
        limit = self._limit_for(model, provider)
        if limit is None:
            return False
        subject = model_subject(model)
        rpm_window, rpd_window = self._windows(now)
        pct = self._walls.soft_exhaust_pct / 100
        for window, cap in ((rpm_window, limit.rpm), (rpd_window, limit.rpd)):
            row = state_limits.get_quota(self._db, subject, window)
            if cap is not None and row is not None and row.count >= cap * pct:
                return True
        return False

    # ------------------------------------------------------------ availability

    def availability(self, model: str, provider: str, key: str | None = None) -> Availability:
        """``ok | cooldown(eta) | exhausted | soft`` for one model (walls beat counters)."""
        now = self._clock()
        subjects = {model_subject(model), provider_subject(provider)}
        if key:
            subjects.add(key_subject(key))
        rows = [r for r in state_limits.active_walls(self._db, now) if r.subject in subjects]
        hard = [r for r in rows if r.kind == "exhausted"]
        if hard:
            ends = [r.until for r in hard]
            eta = None if None in ends else max(u for u in ends if u is not None) - now
            return Availability("exhausted", eta, hard[0].subject)
        cooling = [r for r in rows if r.kind in _TRANSIENT_KINDS and r.until is not None]
        if cooling:
            last = max(cooling, key=lambda r: r.until or 0.0)
            return Availability("cooldown", max(0.0, (last.until or 0.0) - now), last.subject)
        return Availability("soft") if self._is_soft(model, provider, now) else Availability("ok")

    # ------------------------------------------------------------ key polling

    def mark_balance_ok(self, *, provider: str, key: str | None = None) -> int:
        """A balance check passed: lift exhausted walls on the provider and key scopes."""
        subjects = [provider_subject(provider)]
        if key:
            subjects.append(key_subject(key))
        return state_limits.clear_walls(self._db, subjects, ["exhausted"])

    def _poll_due(self, alias: str, now: float) -> bool:
        last = kv_get(self._db, f"quota.key_poll.{alias}")
        interval = self._walls.key_poll_minutes * 60
        return not isinstance(last, int | float) or now - float(last) >= interval

    def poll_key(
        self,
        http: HttpClient,
        api_key: str,
        *,
        alias: str,
        provider: str = "openrouter",
        force: bool = False,
        airplane: bool = False,
        local_lock: bool = False,
        url: str = OPENROUTER_KEY_URL,
    ) -> KeyStatus | None:
        """Check the OpenRouter key every ``key_poll_minutes`` (``force`` = after a paid call).

        Returns None when skipped (airplane mode, local lock, not yet due) or when the check
        failed; a failed check changes nothing. The key is only sent as a bearer header.
        """
        if airplane or local_lock:
            return None
        now = self._clock()
        if not force and not self._poll_due(alias, now):
            return None
        try:
            status = parse_key_status(
                http.request_json("GET", url, headers={"Authorization": f"Bearer {api_key}"})
            )
        except SourceError:
            return None
        kv_set(self._db, f"quota.key_poll.{alias}", now)
        if status.exhausted:
            state_limits.record_wall(self._db, key_subject(alias), "exhausted", until=None)
        else:
            self.mark_balance_ok(provider=provider, key=alias)
        return status
````

## headwaters/core/rules_io.py

````python
"""Reading, validating, defaulting and schema export for config files."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from headwaters.core import paths
from headwaters.core.rules import Rules

RULES_FILE_NAME = "rules.yaml"
SCHEMA_RELATIVE_PATH = Path("catalog") / "rules.schema.json"


class ConfigError(ValueError):
    """A config file could not be read or failed validation; the message is user-facing."""

    def __init__(self, path: Path, problems: list[str]) -> None:
        self.path = path
        self.problems = problems
        super().__init__(f"Invalid config {path}:\n" + "\n".join(f"  - {p}" for p in problems))


def format_validation_error(error: ValidationError) -> list[str]:
    """One readable line per problem, e.g. ``gateway.port: Input should be ...``."""
    lines: list[str] = []
    for item in error.errors():
        location = ".".join(str(part) for part in item["loc"]) or "(root)"
        lines.append(f"{location}: {item['msg']}")
    return lines


def read_yaml_mapping(path: Path) -> dict[str, Any]:
    """Read ``path`` as a YAML mapping; an empty file is an empty mapping."""
    try:
        text = paths.long_safe(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(path, [f"cannot read file: {exc.strerror or exc}"]) from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        where = f"line {mark.line + 1}, column {mark.column + 1}: " if mark else ""
        problem = getattr(exc, "problem", None) or str(exc)
        raise ConfigError(path, [f"YAML syntax error at {where}{problem}"]) from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(path, ["top level must be a mapping of settings"])
    return data


def rules_path() -> Path:
    return paths.config_dir() / RULES_FILE_NAME


def load_rules(path: Path | None = None) -> Rules:
    """Load and validate ``rules.yaml`` (default: the config dir). Raises ``ConfigError``."""
    target = path or rules_path()
    data = read_yaml_mapping(target)
    try:
        return Rules.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(target, format_validation_error(exc)) from exc


_HEADER = (
    "# Headwaters rules. Edit and save: changes apply without a restart.\n"
    "# An invalid edit is reported and the last good settings stay active.\n"
)


def render_rules(rules: Rules) -> str:
    body = yaml.safe_dump(rules.model_dump(mode="json"), sort_keys=False, allow_unicode=True)
    return _HEADER + body


def ensure_rules_file(path: Path | None = None) -> Path:
    """Write the default ``rules.yaml`` on first run. Never overwrites an existing file."""
    target = path or rules_path()
    safe = paths.long_safe(target)
    if not safe.exists():
        safe.parent.mkdir(parents=True, exist_ok=True)
        with safe.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(render_rules(Rules()))
    return target


def schema_json() -> str:
    """Deterministic JSON Schema text for ``Rules`` (LF endings, trailing newline)."""
    schema = Rules.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["title"] = "Headwaters rules.yaml"
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    """``python -m headwaters.core.rules_io [--write|--check] [PATH]``."""
    parser = argparse.ArgumentParser(prog="headwaters.core.rules_io")
    parser.add_argument("--check", action="store_true", help="fail if PATH differs from generated")
    parser.add_argument("--write", action="store_true", help="write the generated schema to PATH")
    parser.add_argument("path", nargs="?", type=Path, default=SCHEMA_RELATIVE_PATH)
    args = parser.parse_args(argv)
    generated = schema_json()
    if args.check:
        current = None
        if args.path.exists():
            current = args.path.read_bytes().decode("utf-8").replace("\r\n", "\n")
        if current != generated:
            sys.stderr.write(f"{args.path} is out of date; run with --write and commit it.\n")
            return 1
        return 0
    if args.write:
        args.path.parent.mkdir(parents=True, exist_ok=True)
        with args.path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(generated)
        return 0
    sys.stdout.write(generated)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
````

## headwaters/core/rules_watch.py

````python
"""Hot reload of ``rules.yaml``: debounced file watching with last-good fallback.

``RulesManager.current`` always returns a valid ``Rules``. When an edit is invalid the error
is logged and kept in ``last_error``; the previous good settings stay active.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver

from headwaters.core import paths
from headwaters.core.logging import get_logger
from headwaters.core.rules import Rules
from headwaters.core.rules_io import ConfigError, ensure_rules_file, load_rules, rules_path

DEFAULT_DEBOUNCE_S = 0.3

_log = get_logger("rules")


class _Handler(FileSystemEventHandler):
    def __init__(self, target: Path, on_change: Callable[[], None]) -> None:
        self._name = target.name.lower()
        self._on_change = on_change

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        # Editors save by rename/replace, so check the destination as well as the source.
        for raw in (event.src_path, getattr(event, "dest_path", "")):
            if raw and Path(str(raw)).name.lower() == self._name:
                self._on_change()
                return


class RulesManager:
    """Owns the live ``Rules``; reloads on file change after a quiet period."""

    def __init__(
        self,
        path: Path | None = None,
        *,
        debounce_s: float = DEFAULT_DEBOUNCE_S,
        on_reload: Callable[[Rules], None] | None = None,
        on_error: Callable[[ConfigError], None] | None = None,
    ) -> None:
        self.path = path or rules_path()
        self._debounce_s = debounce_s
        self._on_reload = on_reload
        self._on_error = on_error
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self._observer: BaseObserver | None = None
        self._rules = Rules()
        self.last_error: ConfigError | None = None
        self._load_initial()

    def _load_initial(self) -> None:
        ensure_rules_file(self.path)
        try:
            self._rules = load_rules(self.path)
        except ConfigError as exc:
            # No previous good config exists: run on built-in defaults and report.
            self.last_error = exc
            _log.error("rules invalid at startup, using defaults: %s", exc)

    @property
    def current(self) -> Rules:
        with self._lock:
            return self._rules

    def reload(self) -> bool:
        """Re-read the file now. Returns True when new valid rules were applied."""
        try:
            fresh = load_rules(self.path)
        except ConfigError as exc:
            with self._lock:
                self.last_error = exc
            _log.warning("rules reload rejected, keeping last good config: %s", exc)
            if self._on_error:
                self._on_error(exc)
            return False
        with self._lock:
            changed = fresh != self._rules
            self._rules = fresh
            self.last_error = None
        if changed:
            _log.info("rules reloaded")
            if self._on_reload:
                self._on_reload(fresh)
        return True

    def _schedule(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
            timer = threading.Timer(self._debounce_s, self.reload)
            timer.daemon = True
            self._timer = timer
            timer.start()

    def start(self) -> None:
        if self._observer is not None:
            return
        directory = paths.long_safe(self.path.parent)
        directory.mkdir(parents=True, exist_ok=True)
        observer = Observer()
        observer.schedule(_Handler(self.path, self._schedule), str(directory), recursive=False)
        observer.daemon = True
        observer.start()
        self._observer = observer

    def stop(self) -> None:
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        if self._observer is not None:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None
````

## headwaters/core/state_limits.py

````python
"""Repositories for ``walls`` and ``quotas``. Each call is one short transaction."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Sequence
from dataclasses import dataclass

from headwaters.core.state import Database


@dataclass(frozen=True)
class WallRow:
    subject: str  # model id, provider, or key alias
    kind: str
    since: float
    until: float | None
    consecutive_count: int
    last_error: str | None


def _wall(r: sqlite3.Row) -> WallRow:
    return WallRow(
        r["subject"], r["kind"], r["since"], r["until"], r["consecutive_count"], r["last_error"]
    )


def record_wall(
    db: Database, subject: str, kind: str, *, until: float | None, error: str | None = None
) -> WallRow:
    """Create the wall, or bump ``consecutive_count`` when the same wall is hit again."""
    with db.write() as c:
        c.execute(
            "INSERT INTO walls (subject, kind, since, until, consecutive_count, last_error)"
            " VALUES (?,?,?,?,1,?) ON CONFLICT(subject, kind) DO UPDATE SET"
            " until=excluded.until, last_error=excluded.last_error,"
            " consecutive_count=consecutive_count+1",
            (subject, kind, time.time(), until, error),
        )
        r = c.execute(
            "SELECT * FROM walls WHERE subject = ? AND kind = ?", (subject, kind)
        ).fetchone()
    return _wall(r)


def clear_wall(db: Database, subject: str, kind: str | None = None) -> int:
    with db.write() as c:
        if kind is None:
            cur = c.execute("DELETE FROM walls WHERE subject = ?", (subject,))
        else:
            cur = c.execute("DELETE FROM walls WHERE subject = ? AND kind = ?", (subject, kind))
        return cur.rowcount


def clear_walls(db: Database, subjects: Sequence[str], kinds: Sequence[str]) -> int:
    """Delete the walls of ``kinds`` for any of ``subjects`` in one transaction."""
    if not subjects or not kinds:
        return 0
    s_marks = ",".join("?" * len(subjects))
    k_marks = ",".join("?" * len(kinds))
    with db.write() as c:
        return c.execute(
            f"DELETE FROM walls WHERE subject IN ({s_marks}) AND kind IN ({k_marks})",
            (*subjects, *kinds),
        ).rowcount


def get_wall(db: Database, subject: str, kind: str) -> WallRow | None:
    """One wall row, active or expired."""
    with db.read() as c:
        r = c.execute(
            "SELECT * FROM walls WHERE subject = ? AND kind = ?", (subject, kind)
        ).fetchone()
    return _wall(r) if r else None


def active_walls(db: Database, now: float | None = None) -> list[WallRow]:
    """Walls with no end time (hard) or an end time still in the future."""
    at = time.time() if now is None else now
    with db.read() as c:
        rows = c.execute(
            "SELECT * FROM walls WHERE until IS NULL OR until > ? ORDER BY subject, kind", (at,)
        )
        return [_wall(r) for r in rows]


@dataclass(frozen=True)
class QuotaRow:
    subject: str
    window: str
    count: int
    limit: int | None
    soft_exhausted: bool


def _quota(r: sqlite3.Row) -> QuotaRow:
    return QuotaRow(r["subject"], r["window"], r["count"], r["limit"], bool(r["soft_exhausted"]))


def add_quota_usage(db: Database, subject: str, window: str, n: int = 1) -> QuotaRow:
    """Atomic increment (safe across processes)."""
    with db.write() as c:
        c.execute(
            "INSERT INTO quotas (subject, window, count) VALUES (?,?,?)"
            " ON CONFLICT(subject, window) DO UPDATE SET count=count+excluded.count",
            (subject, window, n),
        )
        r = c.execute(
            "SELECT * FROM quotas WHERE subject = ? AND window = ?", (subject, window)
        ).fetchone()
    return _quota(r)


def set_quota_limit(
    db: Database, subject: str, window: str, *, limit: int | None, soft_exhausted: bool
) -> None:
    with db.write() as c:
        c.execute(
            'INSERT INTO quotas (subject, window, "limit", soft_exhausted) VALUES (?,?,?,?)'
            ' ON CONFLICT(subject, window) DO UPDATE SET "limit"=excluded."limit",'
            " soft_exhausted=excluded.soft_exhausted",
            (subject, window, limit, int(soft_exhausted)),
        )


def get_quota(db: Database, subject: str, window: str) -> QuotaRow | None:
    with db.read() as c:
        r = c.execute(
            "SELECT * FROM quotas WHERE subject = ? AND window = ?", (subject, window)
        ).fetchone()
    return _quota(r) if r else None


def prune_quota_windows(db: Database, subject: str, prefix: str, keep: str) -> int:
    """Delete a subject's windows that start with ``prefix`` except ``keep`` (old time buckets)."""
    with db.write() as c:
        return c.execute(
            "DELETE FROM quotas WHERE subject = ? AND substr(window, 1, ?) = ? AND window != ?",
            (subject, len(prefix), prefix, keep),
        ).rowcount


def reset_quota_window(db: Database, window: str) -> int:
    with db.write() as c:
        return c.execute(
            "UPDATE quotas SET count = 0, soft_exhausted = 0 WHERE window = ?", (window,)
        ).rowcount
````

## headwaters/core/state_misc.py

````python
"""Repositories for ``sessions``, ``jobs``, ``handoffs`` and ``kv``."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from typing import Any

from headwaters.core.state import Database, dumps

# ---------------------------------------------------------------- sessions


@dataclass(frozen=True)
class SessionRow:
    session_key: str
    pinned_model: str | None
    tier: str | None
    created: float
    last_switch: float | None
    handoff_packet_id: int | None


def get_session(db: Database, key: str) -> SessionRow | None:
    with db.read() as c:
        r = c.execute("SELECT * FROM sessions WHERE session_key = ?", (key,)).fetchone()
    return SessionRow(**dict(r)) if r else None


def pin_session(
    db: Database,
    key: str,
    model: str | None,
    tier: str | None,
    *,
    handoff_packet_id: int | None = None,
) -> SessionRow:
    """Create the session or switch its pinned model (stamping ``last_switch``)."""
    now = time.time()
    with db.write() as c:
        c.execute(
            "INSERT INTO sessions (session_key, pinned_model, tier, created, handoff_packet_id)"
            " VALUES (?,?,?,?,?) ON CONFLICT(session_key) DO UPDATE SET"
            " pinned_model=excluded.pinned_model, tier=excluded.tier, last_switch=?,"
            " handoff_packet_id=excluded.handoff_packet_id",
            (key, model, tier, now, handoff_packet_id, now),
        )
        r = c.execute("SELECT * FROM sessions WHERE session_key = ?", (key,)).fetchone()
    return SessionRow(**dict(r))


# ---------------------------------------------------------------- jobs


@dataclass(frozen=True)
class JobRow:
    job_id: str
    status: str
    progress: float
    result_ref: str | None
    created: float
    updated: float


def create_job(db: Database, job_id: str, status: str = "queued") -> JobRow:
    now = time.time()
    with db.write() as c:
        c.execute(
            "INSERT INTO jobs (job_id, status, progress, created, updated) VALUES (?,?,0,?,?)",
            (job_id, status, now, now),
        )
    return JobRow(job_id, status, 0.0, None, now, now)


def update_job(
    db: Database,
    job_id: str,
    *,
    status: str | None = None,
    progress: float | None = None,
    result_ref: str | None = None,
) -> bool:
    """Update only the fields given."""
    sets = ["updated = ?"]
    args: list[str | float] = [time.time()]
    for column, value in (("status", status), ("progress", progress), ("result_ref", result_ref)):
        if value is not None:
            sets.append(f"{column} = ?")
            args.append(value)
    with db.write() as c:
        return (
            c.execute(
                f"UPDATE jobs SET {', '.join(sets)} WHERE job_id = ?", (*args, job_id)
            ).rowcount
            > 0
        )


def get_job(db: Database, job_id: str) -> JobRow | None:
    with db.read() as c:
        r = c.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
    return JobRow(**dict(r)) if r else None


# ---------------------------------------------------------------- handoffs


def add_handoff(db: Database, session: str, packet: dict[str, Any]) -> int:
    """Store a handoff packet (metadata/summary only; text-like keys are stripped)."""
    with db.write() as c:
        cur = c.execute(
            "INSERT INTO handoffs (session, packet_json, created) VALUES (?,?,?)",
            (session, dumps(packet), time.time()),
        )
        assert cur.lastrowid is not None
        return cur.lastrowid


def get_handoff(db: Database, handoff_id: int) -> dict[str, Any] | None:
    with db.read() as c:
        r = c.execute("SELECT packet_json FROM handoffs WHERE id = ?", (handoff_id,)).fetchone()
    return json.loads(r["packet_json"]) if r else None


def latest_handoff_id(db: Database, session: str) -> int | None:
    with db.read() as c:
        r: sqlite3.Row | None = c.execute(
            "SELECT MAX(id) AS id FROM handoffs WHERE session = ?", (session,)
        ).fetchone()
    return None if r is None or r["id"] is None else int(r["id"])


# ---------------------------------------------------------------- kv


def kv_set(db: Database, key: str, value: Any) -> None:
    with db.write() as c:
        c.execute(
            "INSERT INTO kv (key, value) VALUES (?,?)"
            " ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, dumps(value)),
        )


def kv_get(db: Database, key: str, default: Any = None) -> Any:
    with db.read() as c:
        r = c.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
    return json.loads(r["value"]) if r else default


def kv_delete(db: Database, key: str) -> bool:
    with db.write() as c:
        return c.execute("DELETE FROM kv WHERE key = ?", (key,)).rowcount > 0
````

## headwaters/core/state_models.py

````python
"""Repositories for ``models`` and ``scores``. Each call is one short transaction."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from typing import Any

from headwaters.core.state import Database, dumps


@dataclass(frozen=True)
class ModelRow:
    id: str
    tier: str
    provider: str
    modalities: list[str]
    tools_ok: bool | None = None
    json_ok: bool | None = None
    ctx: int | None = None
    price_in: float | None = None
    price_out: float | None = None
    size_gb: float | None = None
    arch: dict[str, Any] | None = None
    source: str = ""
    last_seen: float = 0.0
    deprecated_by: str | None = None
    unscored: bool = False
    retains_data: bool = False
    warm: bool = False
    size_vram_gb: float | None = None


def _opt_bool(value: int | None) -> bool | None:
    return None if value is None else bool(value)


def _opt_int(value: bool | None) -> int | None:
    return None if value is None else int(value)


def upsert_model(db: Database, m: ModelRow) -> None:
    with db.write() as c:
        c.execute(
            "INSERT INTO models (id, tier, provider, modalities, tools_ok, json_ok, ctx,"
            " price_in, price_out, size_gb, arch_json, source, last_seen, deprecated_by,"
            " unscored, retains_data, warm, size_vram_gb)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(id) DO UPDATE SET tier=excluded.tier, provider=excluded.provider,"
            " modalities=excluded.modalities, tools_ok=excluded.tools_ok,"
            " json_ok=excluded.json_ok, ctx=excluded.ctx, price_in=excluded.price_in,"
            " price_out=excluded.price_out, size_gb=excluded.size_gb,"
            " arch_json=excluded.arch_json, source=excluded.source,"
            " last_seen=excluded.last_seen, deprecated_by=excluded.deprecated_by,"
            " unscored=excluded.unscored, retains_data=excluded.retains_data,"
            " warm=excluded.warm, size_vram_gb=excluded.size_vram_gb",
            (
                m.id,
                m.tier,
                m.provider,
                dumps(m.modalities),
                _opt_int(m.tools_ok),
                _opt_int(m.json_ok),
                m.ctx,
                m.price_in,
                m.price_out,
                m.size_gb,
                dumps(m.arch or {}),
                m.source,
                m.last_seen or time.time(),
                m.deprecated_by,
                int(m.unscored),
                int(m.retains_data),
                int(m.warm),
                m.size_vram_gb,
            ),
        )


def delete_models(db: Database, model_ids: list[str]) -> int:
    """Remove models (and their scores). Returns how many model rows were removed."""
    if not model_ids:
        return 0
    marks = ",".join("?" * len(model_ids))
    with db.write() as c:
        c.execute(f"DELETE FROM scores WHERE model_id IN ({marks})", model_ids)
        return c.execute(f"DELETE FROM models WHERE id IN ({marks})", model_ids).rowcount


def _model(r: sqlite3.Row) -> ModelRow:
    return ModelRow(
        id=r["id"],
        tier=r["tier"],
        provider=r["provider"],
        modalities=json.loads(r["modalities"]),
        tools_ok=_opt_bool(r["tools_ok"]),
        json_ok=_opt_bool(r["json_ok"]),
        ctx=r["ctx"],
        price_in=r["price_in"],
        price_out=r["price_out"],
        size_gb=r["size_gb"],
        arch=json.loads(r["arch_json"]),
        source=r["source"],
        last_seen=r["last_seen"],
        deprecated_by=r["deprecated_by"],
        unscored=bool(r["unscored"]),
        retains_data=bool(r["retains_data"]),
        warm=bool(r["warm"]),
        size_vram_gb=r["size_vram_gb"],
    )


def get_model(db: Database, model_id: str) -> ModelRow | None:
    with db.read() as c:
        r = c.execute("SELECT * FROM models WHERE id = ?", (model_id,)).fetchone()
    return _model(r) if r else None


def list_models(db: Database, tier: str | None = None) -> list[ModelRow]:
    sql = "SELECT * FROM models"
    args: tuple[str, ...] = ()
    if tier is not None:
        sql += " WHERE tier = ?"
        args = (tier,)
    with db.read() as c:
        return [_model(r) for r in c.execute(sql + " ORDER BY id", args)]


@dataclass(frozen=True)
class ScoreRow:
    model_id: str
    task: str
    seed: float | None = None
    override: float | None = None
    learned_ema: float | None = None
    n_samples: int = 0


def set_score(db: Database, s: ScoreRow) -> None:
    with db.write() as c:
        c.execute(
            "INSERT INTO scores (model_id, task, seed, override, learned_ema, n_samples)"
            " VALUES (?,?,?,?,?,?) ON CONFLICT(model_id, task) DO UPDATE SET"
            " seed=excluded.seed, override=excluded.override,"
            " learned_ema=excluded.learned_ema, n_samples=excluded.n_samples",
            (s.model_id, s.task, s.seed, s.override, s.learned_ema, s.n_samples),
        )


def get_score(db: Database, model_id: str, task: str) -> ScoreRow | None:
    with db.read() as c:
        r = c.execute(
            "SELECT * FROM scores WHERE model_id = ? AND task = ?", (model_id, task)
        ).fetchone()
    return ScoreRow(**dict(r)) if r else None


def effective_scores(db: Database) -> dict[str, dict[str, float]]:
    """Score per model and task (0..100): user override, else learned average, else seed."""
    out: dict[str, dict[str, float]] = {}
    with db.read() as c:
        rows = c.execute("SELECT model_id, task, seed, override, learned_ema FROM scores")
        for r in rows:
            picks = (r["override"], r["learned_ema"], r["seed"])
            value = next((v for v in picks if v is not None), None)
            if value is not None:
                out.setdefault(r["model_id"], {})[r["task"]] = float(value)
    return out
````

## headwaters/core/state_schema.py

````python
"""Versioned DDL for the shared SQLite database.

Never edit an applied entry: append a new one. Only metadata and token counts are stored.
"""

from __future__ import annotations

MIGRATIONS: tuple[tuple[int, str, str], ...] = (
    (
        1,
        "initial schema",
        """
        CREATE TABLE models (
            id TEXT PRIMARY KEY, tier TEXT NOT NULL, provider TEXT NOT NULL,
            modalities TEXT NOT NULL DEFAULT '[]',
            tools_ok INTEGER, json_ok INTEGER, ctx INTEGER,
            price_in REAL, price_out REAL, size_gb REAL,
            arch_json TEXT NOT NULL DEFAULT '{}',
            source TEXT NOT NULL DEFAULT '', last_seen REAL NOT NULL
        );
        CREATE TABLE scores (
            model_id TEXT NOT NULL, task TEXT NOT NULL,
            seed REAL, override REAL, learned_ema REAL,
            n_samples INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (model_id, task)
        );
        CREATE TABLE walls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject TEXT NOT NULL, kind TEXT NOT NULL,
            since REAL NOT NULL, until REAL,
            consecutive_count INTEGER NOT NULL DEFAULT 1, last_error TEXT,
            UNIQUE (subject, kind)
        );
        CREATE TABLE quotas (
            subject TEXT NOT NULL, window TEXT NOT NULL,
            count INTEGER NOT NULL DEFAULT 0, "limit" INTEGER,
            soft_exhausted INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (subject, window)
        );
        CREATE TABLE sessions (
            session_key TEXT PRIMARY KEY, pinned_model TEXT, tier TEXT,
            created REAL NOT NULL, last_switch REAL, handoff_packet_id INTEGER
        );
        CREATE TABLE decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,
            surface TEXT NOT NULL, session TEXT,
            scope TEXT NOT NULL, strategy TEXT NOT NULL,
            locks_applied TEXT NOT NULL DEFAULT '[]',
            requirements_json TEXT NOT NULL DEFAULT '{}',
            chosen TEXT, alternatives TEXT NOT NULL DEFAULT '[]',
            floor REAL, score REAL, below_floor INTEGER NOT NULL DEFAULT 0,
            reasons TEXT NOT NULL DEFAULT '[]'
        );
        CREATE INDEX idx_decisions_ts ON decisions (ts);
        CREATE TABLE outcomes (
            decision_id INTEGER PRIMARY KEY REFERENCES decisions (id) ON DELETE CASCADE,
            status TEXT NOT NULL, latency_ms INTEGER,
            tokens_in INTEGER, tokens_out INTEGER,
            cost_usd REAL, counterfactual_usd REAL,
            verify_result TEXT, escalated_to TEXT, user_feedback TEXT
        );
        CREATE TABLE jobs (
            job_id TEXT PRIMARY KEY, status TEXT NOT NULL,
            progress REAL NOT NULL DEFAULT 0, result_ref TEXT,
            created REAL NOT NULL, updated REAL NOT NULL
        );
        CREATE TABLE handoffs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, session TEXT NOT NULL,
            packet_json TEXT NOT NULL, created REAL NOT NULL
        );
        CREATE TABLE kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """,
    ),
    (
        2,
        "model discovery columns",
        """
        ALTER TABLE models ADD COLUMN deprecated_by TEXT;
        ALTER TABLE models ADD COLUMN unscored INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE models ADD COLUMN retains_data INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE models ADD COLUMN warm INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE models ADD COLUMN size_vram_gb REAL;
        """,
    ),
)
LATEST_VERSION = MIGRATIONS[-1][0]
````

## headwaters/core/stdio.py

````python
"""UTF-8 stdio and a stdout guard for MCP stdio servers.

MCP stdio carries JSON-RPC and nothing else on stdout. ``guard_stdout`` swaps ``sys.stdout``
for a sentinel so a stray ``print`` is caught, and hands the protocol layer a writer bound to
the real stdout.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import IO, Any


class StdoutViolationError(RuntimeError):
    """Something other than the protocol writer wrote to stdout."""


def configure_utf8() -> None:
    """Force UTF-8 on stdin/stdout/stderr and for child Python processes."""
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        if stream is sys.stdin:
            reconfigure(encoding="utf-8", errors="replace")
        else:
            reconfigure(encoding="utf-8", errors="replace", newline="\n")


class _GuardedStdout:
    """Stand-in for ``sys.stdout`` that rejects every write."""

    def __init__(self, real: IO[Any], strict: bool) -> None:
        self._real = real
        self._strict = strict
        self.violations: list[str] = []

    @property
    def encoding(self) -> str:
        return "utf-8"

    def write(self, data: str | bytes) -> int:
        text = data if isinstance(data, str) else data.decode("utf-8", errors="replace")
        if not text:
            return 0
        self.violations.append(text)
        if self._strict:
            raise StdoutViolationError(f"non-protocol write to stdout: {text[:80]!r}")
        sys.stderr.write(text)  # production: divert to stderr instead of corrupting the stream
        return len(text)

    def flush(self) -> None:
        return None

    def isatty(self) -> bool:
        return False

    def writable(self) -> bool:
        return True

    @property
    def buffer(self) -> _GuardedStdout:
        return self


@contextmanager
def guard_stdout(*, strict: bool = True) -> Iterator[Callable[[str], None]]:
    """Guard stdout; yield ``write_protocol(text)``, the only sanctioned way to emit output.

    With ``strict=True`` (tests) any other write raises ``StdoutViolationError``. With
    ``strict=False`` (production) stray writes are diverted to stderr and the stream stays
    clean. Only Python-level writes via ``sys.stdout`` are caught; raw fd-1 writes from C
    extensions or subprocesses inheriting stdout are not.
    """
    real = sys.stdout
    guard = _GuardedStdout(real, strict)

    def write_protocol(text: str) -> None:
        real.write(text)
        real.flush()

    sys.stdout = guard
    try:
        yield write_protocol
    finally:
        sys.stdout = real
````

## headwaters/core/verify_basic.py

````python
"""Basic output verification (HDW-116): the cheap checks that make downgrading safe.

``BasicVerifier`` implements ``VerifierPlugin`` (``verify`` returns rendered reasons) and adds
``verify_report`` for a structured ``VerifyReport`` with machine-readable reason codes.

Checks: truncated fences, placeholder/laziness scan (regexes come from the catalog's
``placeholder_patterns``, none live here), tree-sitter parse (``ast.parse`` too for Python), JSON
parse and JSON Schema, ``git apply --check`` for diffs (CRLF/LF aware) and tool-call arguments.
Line endings are normalized to LF before scanning and parsing. Reports never contain candidate text.
"""

from __future__ import annotations

import ast
import json
import re
import textwrap
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from headwaters.core.catalog.schema import Catalog, NamedPattern
from headwaters.core.plugins import CURRENT_API_VERSION
from headwaters.core.verify_diff import check_diff
from headwaters.core.verify_fences import Block, looks_like_diff, normalize_eol, split_fences
from headwaters.core.verify_schema import check_json_schema, check_tool_calls
from headwaters.core.verify_types import CheckResult, Collector, Reason, VerifyReport

__all__ = [
    "GRAMMARS",
    "BasicVerifier",
    "CheckResult",
    "Reason",
    "VerifyReport",
    "looks_like_diff",
    "normalize_eol",
]

# Canonical language -> tree-sitter grammar. Aliases ("py", "ts", "c++") come from the catalog's
# ``analyzer_hints.fence_aliases``. tsx is a grammar of its own in tree-sitter.
GRAMMARS: dict[str, str] = {
    "python": "python",
    "javascript": "javascript",
    "typescript": "typescript",
    "tsx": "tsx",
    "go": "go",
    "rust": "rust",
    "java": "java",
    "csharp": "csharp",
    "c": "c",
    "cpp": "cpp",
    "json": "json",
    "yaml": "yaml",
}
_DIFF_LANGS = frozenset({"diff", "patch"})


@dataclass(frozen=True)
class _Unit:
    language: str | None  # canonical language, "diff", or None when unknown
    body: str
    offset: int  # add to a 1-based line within ``body`` to get the candidate line


@cache
def _parser(grammar: str) -> Any:
    from tree_sitter_language_pack import get_parser

    return get_parser(grammar)


def _first_error(root: Any) -> tuple[int, str]:
    """1-based line and kind (``error`` or ``missing``) of the first broken node."""
    stack = [root]
    while stack:
        node = stack.pop()
        if node.is_error:
            return int(node.start_point[0]) + 1, "error"
        if node.is_missing:
            return int(node.start_point[0]) + 1, "missing"
        if node.has_error:
            stack.extend(reversed(node.children))
    return 1, "error"


class BasicVerifier:
    """The core verifier. Build it with ``from_catalog`` so patterns come from catalog data."""

    api_version = CURRENT_API_VERSION

    def __init__(
        self,
        placeholder_patterns: Sequence[NamedPattern] = (),
        *,
        fence_aliases: Mapping[str, str] | None = None,
        repo: Path | None = None,
        git_timeout: float = 15.0,
    ) -> None:
        self._patterns = [(p.name, re.compile(p.regex)) for p in placeholder_patterns]
        self._aliases = {k.lower(): v.lower() for k, v in (fence_aliases or {}).items()}
        self._repo = repo
        self._git_timeout = git_timeout

    @classmethod
    def from_catalog(
        cls, catalog: Catalog, *, repo: Path | None = None, git_timeout: float = 15.0
    ) -> BasicVerifier:
        return cls(
            catalog.placeholder_patterns,
            fence_aliases=catalog.analyzer_hints.fence_aliases,
            repo=repo,
            git_timeout=git_timeout,
        )

    def verify(self, candidate: str) -> list[str]:
        """``VerifierPlugin`` entry point: rendered reasons, empty when the candidate passed."""
        return [reason.render() for reason in self.verify_report(candidate).reasons]

    def verify_report(
        self,
        candidate: str,
        *,
        language: str | None = None,
        json_schema: Mapping[str, Any] | None = None,
        tool_calls: Sequence[Mapping[str, Any]] | None = None,
        tools: Sequence[Mapping[str, Any]] | None = None,
        repo: Path | None = None,
    ) -> VerifyReport:
        """Run every applicable check.

        ``language`` treats an unfenced candidate as that language; ``json_schema`` validates the
        JSON in the candidate; ``tool_calls`` (+ ``tools`` definitions) validates tool calls;
        ``repo`` overrides the repository used for ``git apply --check``.
        """
        out = Collector()
        text = normalize_eol(candidate)
        blocks = split_fences(text, self._resolve_language)

        if blocks:
            out.ran("fences")
        for block in blocks:
            if not block.closed:
                out.fail(
                    "fences",
                    "truncated_fence",
                    "code fence is never closed (output looks cut off)",
                    block.start_line,
                )

        units = self._units(text, blocks, language)
        self._scan_placeholders(out, units)
        git_repo = repo or self._repo
        for unit in units:
            if unit.language == "diff":
                check_diff(out, unit.body, git_repo, unit.offset or None, self._git_timeout)
            elif unit.language == "json":
                self._check_json_syntax(out, unit)
            elif unit.language in GRAMMARS:
                self._check_parse(out, unit)
        if json_schema is not None:
            check_json_schema(out, text, blocks, json_schema)
        if tool_calls is not None:
            check_tool_calls(out, tool_calls, tools)
        return out.report()

    def _resolve_language(self, info: str) -> str | None:
        words = info.split()
        token = words[0].strip("{}.").lower() if words else ""
        token = self._aliases.get(token, token)
        if token in _DIFF_LANGS:
            return "diff"
        return token if token in GRAMMARS else None

    def _units(self, text: str, blocks: list[Block], language: str | None) -> list[_Unit]:
        """Code to check. Unclosed fences are reported as truncated, not parsed (only noise)."""
        if blocks:
            return [_Unit(b.language, b.body, b.start_line) for b in blocks if b.closed]
        if language is not None:
            return [_Unit(self._resolve_language(language), text, 0)]
        if looks_like_diff(text):
            return [_Unit("diff", text, 0)]
        return []

    def _scan_placeholders(self, out: Collector, units: list[_Unit]) -> None:
        if not self._patterns:
            out.skip("placeholders", "no placeholder patterns configured")
            return
        if not units:
            out.skip("placeholders", "no code to scan")
            return
        out.ran("placeholders")
        for unit in units:
            for number, line in enumerate(unit.body.split("\n"), start=1):
                if unit.language == "diff" and not (line.startswith("+") and line[:3] != "+++"):
                    continue  # only lines the diff adds
                for name, regex in self._patterns:
                    if regex.search(line):
                        out.fail(
                            "placeholders",
                            "placeholder",
                            f"placeholder or lazy-output pattern {name!r}",
                            unit.offset + number,
                        )
                        break

    def _check_parse(self, out: Collector, unit: _Unit) -> None:
        language = unit.language or ""
        body = textwrap.dedent(unit.body) if language == "python" else unit.body
        if not body.strip():
            return
        if language == "python":
            out.ran("parse")
            try:
                ast.parse(body)
            except SyntaxError as exc:
                out.fail(
                    "parse", "syntax_error", f"python: {exc.msg}", unit.offset + (exc.lineno or 1)
                )
                return
            except ValueError as exc:  # e.g. null bytes
                out.fail("parse", "syntax_error", f"python: {exc}")
                return
        try:
            tree = _parser(GRAMMARS[language]).parse(body.encode("utf-8"))
        except Exception as exc:  # grammar missing or parser crash: never fail the candidate
            out.skip("parse", f"{language}: tree-sitter unavailable ({type(exc).__name__})")
            return
        out.ran("parse")
        if tree.root_node.has_error:
            line, kind = _first_error(tree.root_node)
            out.fail(
                "parse",
                "syntax_error",
                f"{language}: tree-sitter found a {kind} node",
                unit.offset + line,
            )

    @staticmethod
    def _check_json_syntax(out: Collector, unit: _Unit) -> None:
        out.ran("json")
        try:
            json.loads(unit.body)
        except json.JSONDecodeError as exc:
            out.fail("json", "json_invalid", exc.msg, unit.offset + exc.lineno)
````

## headwaters/core/verify_diff.py

````python
"""``git apply --check`` for candidate diffs, with CRLF/LF handling (HDW-116).

Line endings: the candidate is normalized to LF first. The repo's style for the touched files is
read from ``git ls-files --eol`` (which already folds in ``.gitattributes``), falling back to
``core.autocrlf`` for new files. We try that style first and the other one second, so a CRLF
working tree accepts an LF diff (what a model writes) and the reverse. Nothing is written to the
repo: ``git apply --check`` only tests. All git calls use a fixed argv, never a shell.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from headwaters.core import paths
from headwaters.core.verify_types import Collector

_DIFF_PATH = re.compile(r"(?m)^(?:\+\+\+|---) (?:[ab]/)?(\S+)")


def _git(
    repo: Path, args: list[str], timeout: float, data: bytes | None = None
) -> subprocess.CompletedProcess[bytes] | None:
    exe = shutil.which("git")
    if exe is None:
        return None
    try:
        return subprocess.run(
            [exe, "-C", str(paths.long_safe(repo)), *args],
            input=data,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def prefers_crlf(repo: Path, files: list[str], timeout: float) -> bool:
    """True when the working tree uses CRLF for these files (or ``core.autocrlf=true``)."""
    if files:
        listing = _git(repo, ["ls-files", "--eol", "--", *files], timeout)
        if listing is not None and listing.returncode == 0:
            info = listing.stdout.decode("utf-8", "replace")
            if "w/crlf" in info or "eol=crlf" in info:
                return True
            if "w/lf" in info:
                return False
    config = _git(repo, ["config", "--get", "core.autocrlf"], timeout)
    return (
        config is not None
        and config.returncode == 0
        and config.stdout.decode("utf-8", "replace").strip().lower() == "true"
    )


def check_diff(
    out: Collector, diff: str, repo: Path | None, line: int | None, timeout: float = 15.0
) -> None:
    """Record the outcome of ``git apply --check`` for an LF-normalized ``diff``."""
    if repo is None:
        out.skip("diff", "no repository configured for git apply --check")
        return
    if shutil.which("git") is None:
        out.skip("diff", "git is not installed")
        return
    text = diff if diff.endswith("\n") else diff + "\n"
    files = sorted({m for m in _DIFF_PATH.findall(text) if m.lstrip("/") != "dev/null"})
    lf, crlf = text, text.replace("\n", "\r\n")
    variants = [crlf, lf] if prefers_crlf(repo, files, timeout) else [lf, crlf]
    first_error = ""
    for variant in variants:
        result = _git(
            repo, ["apply", "--check", "--whitespace=nowarn", "-"], timeout, variant.encode("utf-8")
        )
        if result is None:
            out.skip("diff", "git apply --check could not run")
            return
        if result.returncode == 0:
            out.ran("diff")
            return
        if not first_error:
            lines = result.stderr.decode("utf-8", "replace").strip().splitlines()
            first_error = lines[0] if lines else "git apply --check failed"
    out.fail("diff", "diff_does_not_apply", first_error, line)
````

## headwaters/core/verify_fences.py

````python
"""Line-ending normalization and Markdown fence splitting for verification (HDW-116)."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_DIFF_START = re.compile(r"(?m)^(?:diff --git |--- \S.*\n\+\+\+ \S)")


@dataclass(frozen=True)
class Block:
    info: str  # raw info string after the opening fence
    language: str | None  # resolved by the caller; None when unknown
    body: str
    start_line: int  # 1-based line of the opening fence
    closed: bool


def normalize_eol(text: str) -> str:
    """CRLF and lone CR become LF."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def looks_like_diff(text: str) -> bool:
    return _DIFF_START.search(text) is not None


def split_fences(text: str, resolve: Callable[[str], str | None]) -> list[Block]:
    """Split LF text into fenced blocks. A block still open at the end is ``closed=False``."""
    blocks: list[Block] = []
    opened: tuple[str, str, int] | None = None
    body: list[str] = []
    for index, line in enumerate(text.split("\n"), start=1):
        match = _FENCE.match(line)
        if opened is None:
            if match is None:
                continue
            marker, info = match.group(1), match.group(2).strip()
            if marker[0] == "`" and "`" in info:  # inline code such as ```x``` on one line
                continue
            opened, body = (marker, info, index), []
            continue
        marker, info, start = opened
        closes = (
            match is not None
            and match.group(1)[0] == marker[0]
            and len(match.group(1)) >= len(marker)
            and not match.group(2).strip()
        )
        if closes:
            blocks.append(Block(info, resolve(info), "\n".join(body), start, True))
            opened = None
        else:
            body.append(line)
    if opened is not None:
        blocks.append(Block(opened[1], resolve(opened[1]), "\n".join(body), opened[2], False))
    return blocks
````

## headwaters/core/verify_schema.py

````python
"""JSON Schema and tool-call argument validation for verification (HDW-116).

Violations are reported by path and keyword only; the offending data is never echoed.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from headwaters.core.verify_fences import Block
from headwaters.core.verify_types import Collector

MAX_SCHEMA_ERRORS = 5


def _validate(
    out: Collector, check: str, code: str, schema: Mapping[str, Any], data: Any, prefix: str = ""
) -> None:
    errors = sorted(
        Draft202012Validator(schema).iter_errors(data),
        key=lambda e: [str(p) for p in e.absolute_path],
    )
    for error in errors[:MAX_SCHEMA_ERRORS]:
        path = "/".join(str(p) for p in error.absolute_path) or "(root)"
        out.fail(check, code, f"{prefix}{path}: failed '{error.validator}' constraint")


def check_json_schema(
    out: Collector, text: str, blocks: Sequence[Block], schema: Mapping[str, Any]
) -> None:
    """Validate the JSON in the candidate (first closed json fence, else the whole text)."""
    out.ran("json")
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        out.fail("json", "json_schema_invalid", f"the supplied schema is invalid: {exc.message}")
        return
    raw = next((b.body for b in blocks if b.closed and b.language == "json"), None)
    if raw is None:
        stripped = text.strip()
        if not stripped.startswith(("{", "[")):
            out.fail("json", "json_not_found", "no JSON found in the output")
            return
        raw = stripped
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        if not out.has_code("json_invalid"):
            out.fail("json", "json_invalid", exc.msg, exc.lineno)
        return
    _validate(out, "json", "json_schema_violation", schema, data)


def _tool_schemas(tools: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    schemas: dict[str, Mapping[str, Any]] = {}
    for tool in tools:
        function = tool.get("function")
        spec = function if isinstance(function, Mapping) else tool
        name = spec.get("name")
        if isinstance(name, str):
            schemas[name] = spec.get("parameters") or spec.get("input_schema") or {}
    return schemas


def check_tool_calls(
    out: Collector,
    tool_calls: Sequence[Mapping[str, Any]],
    tools: Sequence[Mapping[str, Any]] | None,
) -> None:
    """Valid tool name (when ``tools`` is given), arguments are JSON and match the tool schema.

    Accepts OpenAI (``function.name/arguments`` string) and Anthropic (``name/input``) shapes.
    """
    out.ran("tool_calls")
    schemas = _tool_schemas(tools) if tools is not None else {}
    for index, call in enumerate(tool_calls):
        function = call.get("function")
        spec = function if isinstance(function, Mapping) else call
        name = spec.get("name")
        label = f"tool call {index}"
        if not isinstance(name, str) or not name:
            out.fail("tool_calls", "tool_name_missing", f"{label} has no tool name")
            continue
        if tools is not None and name not in schemas:
            out.fail("tool_calls", "tool_unknown", f"{label}: unknown tool {name!r}")
            continue
        arguments = spec.get("arguments", spec.get("input", {}))
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments) if arguments.strip() else {}
            except json.JSONDecodeError as exc:
                out.fail(
                    "tool_calls",
                    "tool_args_invalid_json",
                    f"{label} ({name}): arguments are not valid JSON: {exc.msg}",
                )
                continue
        if not isinstance(arguments, Mapping):
            out.fail(
                "tool_calls",
                "tool_args_not_object",
                f"{label} ({name}): arguments must be an object",
            )
            continue
        schema = schemas.get(name)
        if not schema:
            continue
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError:
            out.skip("tool_calls", f"{name}: tool schema is invalid, arguments not checked")
            continue
        _validate(out, "tool_calls", "tool_args_schema", schema, arguments, f"{label} ({name}) ")
````

## headwaters/core/verify_types.py

````python
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
````

## headwaters/core/walls.py

````python
"""Wall classifier (HDW-110, v3 section 4.4): turn a failed provider response into a ``WallEvent``.

``classify(status, headers, body, provider)`` decides which kind of wall a response is
(``rate_limit``, ``exhausted``, ``overloaded``, ``context`` or ``other``), how long to wait
(``retry_after`` seconds / ``reset_at`` epoch seconds) and how far it reaches (``scope``:
``model``, ``provider`` or ``key``).

Status codes, body regexes, kinds and scopes are DATA from the catalog (``wall_patterns``), never
code. Only the wire format of the reset hints (``Retry-After``, ``x-ratelimit-reset*`` and friends,
Gemini ``RetryInfo``) is parsed here.

Privacy: the body is only matched, never stored or logged; the event carries no body text.
"""

from __future__ import annotations

import json
import math
import re
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

from headwaters.core.catalog.schema import Catalog, WallKind, WallPattern, WallScope

ANY_PROVIDER = "*"
OTHER: WallKind = "other"
_DEFAULT_SCOPE: WallScope = "model"
_BODY_SCAN_CHARS = 32_768  # bodies are matched on their first 32K characters only
_EPOCH_MS_MIN = 1e11  # bare numbers above this are epoch milliseconds
_EPOCH_S_MIN = 1e9  # bare numbers above this (and below the ms limit) are epoch seconds
_DURATION_PART = re.compile(r"(\d+(?:\.\d+)?)(ms|h|m|s)")
_DURATION_UNITS = {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}

__all__ = ["ANY_PROVIDER", "WallClassifier", "WallEvent", "classify", "parse_wait"]


@dataclass(frozen=True, slots=True)
class WallEvent:
    """Result of classifying one response. ``kind == "other"`` means: not a wall."""

    kind: WallKind
    scope: WallScope
    retry_after: float | None = None  # seconds to wait from ``now``
    reset_at: float | None = None  # epoch seconds when the wall is expected to lift
    status: int | None = None
    provider: str = ""
    pattern_index: int | None = None  # index into the catalog's wall_patterns, None if no match

    @property
    def is_wall(self) -> bool:
        """True for kinds that mark a wall; ``context`` re-routes and ``other`` surfaces."""
        return self.kind in ("rate_limit", "exhausted", "overloaded")


def _to_text(body: object) -> str:
    if body is None:
        return ""
    if isinstance(body, bytes | bytearray):
        text = bytes(body[: _BODY_SCAN_CHARS * 4]).decode("utf-8", errors="replace")
    elif isinstance(body, str):
        text = body
    else:
        try:
            text = json.dumps(body, ensure_ascii=False)
        except (TypeError, ValueError):
            text = str(body)
    return text[:_BODY_SCAN_CHARS]


def _parse_duration(value: str) -> float | None:
    """``"30"``, ``"1.5s"``, ``"6m0s"``, ``"20ms"`` -> seconds (None if not a duration)."""
    text = value.strip().lower()
    if not text:
        return None
    try:
        number = float(text)
    except ValueError:
        pass
    else:
        return number if math.isfinite(number) and number >= 0 else None
    total = 0.0
    position = 0
    for match in _DURATION_PART.finditer(text):
        if match.start() != position:
            return None
        total += float(match.group(1)) * _DURATION_UNITS[match.group(2)]
        position = match.end()
    return total if position == len(text) else None


def _parse_timestamp(value: str) -> float | None:
    """RFC 3339 / ISO 8601 -> epoch seconds."""
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


def parse_wait(value: str, now: float, *, reset_header: bool = False) -> float | None:
    """Parse one wait hint into an absolute epoch time.

    Accepts a number of seconds, a Go-style duration, an RFC 3339 timestamp or (for
    ``Retry-After``) an HTTP date. With ``reset_header=True`` a bare number may also be an epoch
    in seconds or milliseconds (OpenRouter sends epoch ms), decided by magnitude.
    """
    text = value.strip()
    if not text:
        return None
    duration = _parse_duration(text)
    if duration is not None:
        if reset_header and duration >= _EPOCH_MS_MIN:
            return duration / 1000.0
        if reset_header and duration >= _EPOCH_S_MIN:
            return duration
        return now + duration
    stamp = _parse_timestamp(text)
    if stamp is not None:
        return stamp
    try:
        return parsedate_to_datetime(text).timestamp()
    except (TypeError, ValueError, IndexError):
        return None


def _lowered(headers: Mapping[str, str] | None) -> dict[str, str]:
    if not headers:
        return {}
    return {str(k).lower(): str(v) for k, v in headers.items()}


def _header_reset(headers: dict[str, str], now: float) -> float | None:
    """Absolute reset time from headers, or None.

    ``Retry-After`` (and ``retry-after-ms``) win because they say when to retry. Otherwise the
    latest of the ``*ratelimit*reset*`` headers is used: several limits (requests, tokens) can
    reset at different times and we do not know which one was hit, so waiting for the last one is
    the safe reading (a long wait only makes the router cascade sooner).
    """
    retry_after = headers.get("retry-after")
    if retry_after is not None:
        parsed = parse_wait(retry_after, now)
        if parsed is not None:
            return parsed
    retry_ms = headers.get("retry-after-ms")
    if retry_ms is not None:
        millis = _parse_duration(retry_ms)
        if millis is not None:
            return now + millis / 1000.0
    candidates = [
        parsed
        for name, value in headers.items()
        if "ratelimit" in name and "reset" in name
        for parsed in [parse_wait(value, now, reset_header=True)]
        if parsed is not None
    ]
    return max(candidates) if candidates else None


def _find_retry_delay(node: Any) -> str | None:
    """Gemini ``google.rpc.RetryInfo``: ``error.details[].retryDelay`` (e.g. ``"30s"``)."""
    if isinstance(node, dict):
        delay = node.get("retryDelay")
        if isinstance(delay, str):
            return delay
        for value in node.values():
            found = _find_retry_delay(value)
            if found is not None:
                return found
    elif isinstance(node, list):
        for item in node:
            found = _find_retry_delay(item)
            if found is not None:
                return found
    return None


def _body_reset(text: str, now: float) -> float | None:
    if "retryDelay" not in text:
        return None
    try:
        data = json.loads(text)
    except ValueError:
        return None
    delay = _find_retry_delay(data)
    return None if delay is None else parse_wait(delay, now)


class WallClassifier:
    """Classify responses against a list of catalog ``WallPattern``s.

    A pattern matches when its provider is the response's provider (case-insensitive) or ``*``,
    its ``status_codes`` (if any) include the status, and its ``body_regex`` (if any) is found in
    the body. When several match, patterns with a body regex beat status-only ones, then
    provider-specific beats ``*``, then catalog order decides.
    """

    def __init__(self, patterns: Sequence[WallPattern]) -> None:
        self._patterns = list(patterns)
        self._regex = [
            re.compile(p.body_regex) if p.body_regex is not None else None for p in self._patterns
        ]

    def _best(self, status: int, provider: str, text: str) -> int | None:
        best: tuple[int, int, int] | None = None
        best_index: int | None = None
        wanted = provider.lower()
        for index, pattern in enumerate(self._patterns):
            name = pattern.provider.lower()
            if name not in (wanted, ANY_PROVIDER):
                continue
            if pattern.status_codes and status not in pattern.status_codes:
                continue
            regex = self._regex[index]
            if regex is not None and regex.search(text) is None:
                continue
            rank = (int(regex is not None), int(name != ANY_PROVIDER), -index)
            if best is None or rank > best:
                best, best_index = rank, index
        return best_index

    def classify(
        self,
        status: int,
        headers: Mapping[str, str] | None,
        body: object,
        provider: str,
        *,
        now: float | None = None,
    ) -> WallEvent:
        """Classify one response. ``now`` is injectable for tests (epoch seconds)."""
        moment = time.time() if now is None else now
        text = _to_text(body)
        index = self._best(status, provider, text)
        if index is None:
            return WallEvent(kind=OTHER, scope=_DEFAULT_SCOPE, status=status, provider=provider)
        pattern = self._patterns[index]
        reset_at: float | None = None
        if pattern.kind in ("rate_limit", "exhausted", "overloaded"):
            reset_at = _header_reset(_lowered(headers), moment)
            if reset_at is None:
                reset_at = _body_reset(text, moment)
        retry_after = None if reset_at is None else max(0.0, reset_at - moment)
        return WallEvent(
            kind=pattern.kind,
            scope=pattern.scope,
            retry_after=retry_after,
            reset_at=reset_at,
            status=status,
            provider=provider,
            pattern_index=index,
        )


def classify(
    status: int,
    headers: Mapping[str, str] | None,
    body: object,
    provider: str,
    *,
    catalog: Catalog | None = None,
    patterns: Sequence[WallPattern] | None = None,
    now: float | None = None,
) -> WallEvent:
    """``classify(status, headers, body, provider) -> WallEvent`` using catalog wall patterns.

    Give either ``catalog`` or ``patterns``. For many calls build one ``WallClassifier`` instead.
    """
    if patterns is None:
        if catalog is None:
            raise ValueError("classify needs a catalog or patterns")
        patterns = catalog.wall_patterns
    return WallClassifier(patterns).classify(status, headers, body, provider, now=now)
````
