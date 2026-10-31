"""Accumulates normalised deltas into one result (HDW-115). Reasoning stays apart from content."""

from __future__ import annotations

from dataclasses import dataclass, field

from headwaters.core.executor.types import Delta, ExecResult, ToolCall, Usage
from headwaters.core.scope import TierName


@dataclass
class _CallParts:
    id: str = ""
    name: str = ""
    arguments: list[str] = field(default_factory=list)


@dataclass
class Accumulator:
    model: str
    tier: TierName
    content: list[str] = field(default_factory=list)
    reasoning: list[str] = field(default_factory=list)
    calls: dict[int, _CallParts] = field(default_factory=dict)
    finish_reason: str | None = None
    usage: Usage = field(default_factory=Usage)

    def add(self, delta: Delta) -> None:
        if delta.content:
            self.content.append(delta.content)
        if delta.reasoning:
            self.reasoning.append(delta.reasoning)
        for part in delta.tool_calls:
            slot = self.calls.setdefault(part.index, _CallParts())
            if part.id:
                slot.id = part.id
            if part.name:
                slot.name = part.name
            if part.arguments:
                slot.arguments.append(part.arguments)
        if delta.finish_reason:
            self.finish_reason = delta.finish_reason
        if delta.usage is not None:
            self.usage = Usage(
                prompt_tokens=delta.usage.prompt_tokens or self.usage.prompt_tokens,
                completion_tokens=delta.usage.completion_tokens or self.usage.completion_tokens,
            )

    def tool_calls(self) -> tuple[ToolCall, ...]:
        return tuple(
            ToolCall(id=c.id or f"call_{i}", name=c.name, arguments="".join(c.arguments) or "{}")
            for i, c in sorted(self.calls.items())
        )

    def result(self, first_token_s: float | None) -> ExecResult:
        return ExecResult(
            model=self.model,
            tier=self.tier,
            content="".join(self.content),
            reasoning="".join(self.reasoning),
            tool_calls=self.tool_calls(),
            finish_reason=self.finish_reason or "stop",
            usage=self.usage,
            first_token_s=first_token_s,
        )
