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
