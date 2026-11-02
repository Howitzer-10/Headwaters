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
from headwaters.core.catalog.schema import AnalyzerHints, Catalog
from headwaters.core.ollama_remote import RemotePatterns, is_remote_name
from headwaters.core.rules import ClassifierRules, LocalTier

SOURCE_CLASSIFIER = "classifier"
AUTO_SMALL = "auto_small"
TASKS = ("code", "reasoning", "chat", "summarize", "extract", "creative", "vision")
RISKS = ("low", "medium", "high")
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


def prompt_text(inp: AnalyzerInput, max_chars: int | None = None) -> str:
    """The last user message as plain text, clipped to head + tail."""
    limit = max_chars or AnalyzerHints().classifier_max_prompt_chars
    text = _extract.collect(inp.messages, None).last_user_text
    if len(text) > limit:
        half = limit // 2
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
        remote_patterns: RemotePatterns = (),
        hints: AnalyzerHints | None = None,
    ) -> None:
        self.model = model
        self.hints = hints or AnalyzerHints()
        # A cloud-proxied model would send the prompt to ollama.com (A-OLL-9): never call it.
        self.refused = is_remote_name(model, remote_patterns)
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
        if self.refused:
            return None
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
        prompt = prompt_text(inp, self.hints.classifier_max_prompt_chars)
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
            confidence=max(current.confidence, self.hints.classifier_min_confidence),
            source=SOURCE_CLASSIFIER,
            ambiguous=False,
        )
