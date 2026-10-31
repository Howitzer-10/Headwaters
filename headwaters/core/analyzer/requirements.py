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
KNOWN_TASKS = _TASK_ORDER  # every task that has a floor in rules.yaml
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
    reasoning: bool = False  # set by the caller (HDW-140), never by keyword analysis


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

    def _complexity(self, est_tokens: int, *, reasoning: bool) -> int:
        # Agentic requests are not counted here: the floor already adds ``agentic_bonus`` (F4).
        level = 1 + sum(1 for step in self.hints.complexity_token_steps if est_tokens > step)
        level += int(reasoning)
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
            complexity=self._complexity(est_tokens, reasoning=reasoning_hint),
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
