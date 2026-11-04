"""Tool-schema attribution and the honest context diagnosis (HDW-511).

``Analyzer.analyze`` counts the OpenAI ``tools`` array as code, so every MCP server a host
enables inflates ``est_tokens`` — and therefore ``hard.min_ctx`` — before the user types
anything. Two things live here:

* ``attribute`` maps a tool name to the server it came from. The wire format carries no
  server field, so this is a heuristic: a ``__`` split when the host supplies namespaced
  names, otherwise the leading segment of a flattened name (``summer_add_node`` ->
  ``summer``), falling back to the longest shared prefix when there is only one server.
  Because the result is used to drop schemas, anything a name cannot be attributed to
  raises instead of guessing.
* ``measure`` splits a request's ``est_tokens`` into tools, message/system text and images,
  and ``diagnose`` turns that into a message that states the real composition and the fixes
  that actually work. It never suggests pulling a model when the problem is context.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from headwaters.core.analyzer._extract import collect, dumps
from headwaters.core.analyzer.requirements import AnalyzerInput, Requirements

# The front-door tools are the product, so prune-to-fit never drops them (HDW-511).
HEADWATERS_NAMESPACE = "headwaters"
PRUNE_KEY = "tools.prune_to_fit"


class ToolEstimator(Protocol):
    """The part of ``Analyzer`` the composition needs; a fake in tests may implement less."""

    def estimate_tokens(self, chunks: Iterable[str], code_extra_chars: int, images: int) -> int: ...


class AmbiguousTools(ValueError):
    """The tools array cannot be attributed to servers without guessing."""

    def __init__(self, names: Sequence[str]) -> None:
        self.names = tuple(names)
        super().__init__(f"cannot attribute tool schemas to servers: {', '.join(self.names)}")


def tool_name(spec: Mapping[str, Any]) -> str:
    """The name of one OpenAI (or Anthropic) function tool; empty when it has none."""
    function = spec.get("function")
    if isinstance(function, Mapping) and isinstance(function.get("name"), str):
        return str(function["name"])
    name = spec.get("name")
    return name if isinstance(name, str) else ""


def _shared_prefix(names: Sequence[str]) -> str:
    """The longest leading string every name shares, character by character."""
    shortest = min(names, key=len)
    for index, char in enumerate(shortest):
        if any(name[index] != char for name in names):
            return shortest[:index]
    return shortest


def attribute(names: Sequence[str]) -> dict[str, str]:
    """Map every tool name to a namespace, or raise ``AmbiguousTools``.

    Namespaced names (``server__tool``) name their server directly. The remaining names are
    the flattened kind an MCP host produces, so the leading segment is the server
    (``summer_add_node``, ``summer_write_file`` -> ``summer``). A name with no segment to
    read, or a lone tool among larger groups, is a guess — and pruning must not guess.
    """
    explicit: dict[str, str] = {}
    loose: list[str] = []
    for name in names:
        head, separator, _ = name.partition("__")
        if separator and head:
            explicit[name] = head
        else:
            loose.append(name)
    if not loose:
        return explicit
    groups: dict[str, list[str]] = {}
    for name in loose:
        segment = name.partition("_")[0]
        if not segment or segment == name:
            raise AmbiguousTools([name])
        groups.setdefault(segment, []).append(name)
    if len(groups) == 1:
        # One server: report its longest shared prefix rather than a guessed segment.
        prefix = _shared_prefix(loose).rstrip("_-.")
        if not prefix:
            raise AmbiguousTools(loose)
        for name in loose:
            explicit[name] = prefix
        return explicit
    lonely = [group[0] for group in groups.values() if len(group) == 1]
    if lonely:
        raise AmbiguousTools(lonely)
    for segment, group in groups.items():
        for name in group:
            explicit[name] = segment
    return explicit


def _allocate(total: int, weights: Sequence[int]) -> list[int]:
    """Split ``total`` across ``weights`` so the parts sum to ``total`` exactly."""
    if not weights or total <= 0:
        return [0 for _ in weights]
    positive = [max(1, weight) for weight in weights]
    scale = total / sum(positive)
    exact = [weight * scale for weight in positive]
    parts = [math.floor(value) for value in exact]
    remainder = total - sum(parts)
    order = sorted(range(len(parts)), key=lambda i: (-(exact[i] - parts[i]), i))
    for index in order[:remainder]:
        parts[index] += 1
    return parts


@dataclass(frozen=True)
class Composition:
    """Where a request's ``est_tokens`` went; the parts sum to ``total``."""

    total: int
    messages: int
    tools: int
    images: int
    tool_count: int
    namespaces: tuple[tuple[str, int, int], ...]  # (namespace, tokens, tool count), largest first
    largest_tools: tuple[tuple[str, int], ...]  # (tool name, tokens), largest first
    ambiguous: bool

    def namespace_tokens(self, namespace: str) -> int:
        return next((tokens for name, tokens, _ in self.namespaces if name == namespace), 0)


def measure(estimator: Any, inp: AnalyzerInput, reqs: Requirements) -> Composition | None:
    """Split ``reqs.est_tokens`` into its parts; ``None`` when the analyzer cannot estimate.

    The total is the analyzer's own reading, and the text and image parts use the same
    estimator, so ``tools`` is the remainder rather than a second guess at the rate.
    """
    estimate = getattr(estimator, "estimate_tokens", None)
    if estimate is None:
        return None
    collected = collect(inp.messages, inp.system)
    messages = int(estimate(collected.all_text, collected.code_extra_chars, 0))
    images = int(estimate([], 0, collected.image_count))
    total = reqs.est_tokens
    tools = max(0, total - messages - images)

    specs = list(inp.tools or ())
    names = [tool_name(spec) for spec in specs]
    chars = [len(dumps(spec)) for spec in specs]
    shares = _allocate(tools, chars)
    pairs = tuple(zip(names, shares, strict=True))

    try:
        mapping: dict[str, str] | None = attribute(names)
    except AmbiguousTools:
        mapping = None
    by_namespace: dict[str, list[int]] = {}
    if mapping is not None:
        for name, tokens in pairs:
            bucket = by_namespace.setdefault(mapping[name], [0, 0])
            bucket[0] += tokens
            bucket[1] += 1
    namespaces = tuple(
        sorted(
            ((name, tokens[0], tokens[1]) for name, tokens in by_namespace.items()),
            key=lambda item: (-item[1], item[0]),
        )
    )
    largest = tuple(sorted(pairs, key=lambda item: (-item[1], item[0]))[:3])
    return Composition(
        total=total,
        messages=messages,
        tools=tools,
        images=images,
        tool_count=len(specs),
        namespaces=namespaces,
        largest_tools=largest,
        ambiguous=mapping is None,
    )


@dataclass(frozen=True)
class ContextDiagnosis:
    """The user-facing message and the one-line fix hint for a context shortfall."""

    message: str
    fix_hint: str


def _joined(items: Sequence[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " or " + items[-1]


def diagnose(
    composition: Composition,
    *,
    needed: int,
    headroom: float,
    local_ctx: Sequence[int],
    prune_enabled: bool,
    widen: bool,
) -> ContextDiagnosis:
    """Say what the request is made of and what the user can do about it.

    ``widen`` is false when a lock (private lock, project rule, secret, airplane mode) keeps
    the request local, because suggesting ``headwaters/auto`` there would not work.
    """
    largest = max(local_ctx, default=None)
    message = (
        f"The request needs {composition.total:,} tokens of context; with the "
        f"{headroom:g}x context headroom a model must hold {needed:,}, and no local model can."
    )
    if largest:
        message += f" The largest local window is {largest:,}."
    message += (
        f" Composition: tools {composition.tools:,} tokens ({composition.tool_count} schemas), "
        f"message/system {composition.messages:,}, images {composition.images:,}."
    )
    if composition.namespaces:
        listed = ", ".join(
            f"{name} {tokens:,} ({count} tools)"
            for name, tokens, count in composition.namespaces[:3]
        )
        message += f" Largest tool namespaces: {listed}."
    elif composition.tool_count:
        listed = ", ".join(
            f"{name or '(unnamed)'} {tokens:,}" for name, tokens in composition.largest_tools
        )
        message += (
            " Tool names could not be attributed to servers"
            + (", so prune-to-fit cannot act" if prune_enabled else "")
            + f"; the largest schemas are {listed}."
        )
    if not widen:
        message += " The scope is locked to local, so a cloud model is not available."

    fixes: list[str] = []
    if widen:
        fixes.append("use a wider scope such as headwaters/auto")
    if prune_enabled:
        fixes.append(f"{PRUNE_KEY} is already on and cannot help here")
    else:
        fixes.append(f"set {PRUNE_KEY}: true in rules.yaml")
    fixes.append("remove a large MCP server from the host")
    message += f" Fix: {_joined(fixes)}."
    hint = _joined(fixes)
    return ContextDiagnosis(message, hint[:1].upper() + hint[1:] + ".")
