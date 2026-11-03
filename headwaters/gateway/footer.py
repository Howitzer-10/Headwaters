"""The optional answer footer (HDW-512, D17): one plain-text line naming what answered.

A host that shows only the message text hides the routing headers, so the same facts are also
offered as a body footer. It is a mechanism, not a string: ``gateway.footer.fields`` in
rules.yaml picks the facts and their order, and ``enabled`` (default ``false``) turns the whole
thing on, so no existing client or test suddenly sees extra body text.

Rules that keep the footer honest and harmless:

* it is never part of ``usage`` and never counted as model output — it is appended after the
  answer and its token counts are already fixed;
* it is never appended to a ``tool_calls`` answer (a tool call is machine input, and a generic
  "use this tool interface" body must stay byte-clean) nor to an error;
* the data comes from :func:`headwaters.gateway.shared.headwaters_meta`, the same dict the
  stream's ``headwaters`` chunk carries, so cost is not re-derived here.

The footer becomes part of the conversation history a host may echo back; ``X-Headwaters-Model``
(and the other routing headers) is the alternative when invisibility is wanted. Only the body
footer is implemented.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from headwaters.core.router_types import RouteResult
from headwaters.core.rules import FooterField, Rules
from headwaters.gateway.shared import headwaters_meta

__all__ = ["FOOTER_PREFIX", "footer_line", "footer_text", "with_footer"]

FOOTER_PREFIX = "Headwaters:"


def _model(meta: Mapping[str, Any]) -> str:
    return str(meta.get("model") or "")


def _tier(meta: Mapping[str, Any]) -> str:
    return str(meta.get("tier") or "")


def _tokens(meta: Mapping[str, Any]) -> str:
    prompt, completion = meta.get("prompt_tokens"), meta.get("completion_tokens")
    if prompt is None and completion is None:
        return ""
    return f"{int(prompt or 0)}+{int(completion or 0)}"


def _cost(meta: Mapping[str, Any]) -> str:
    """``free`` for a local answer, the priced total when the catalog knows the rates."""
    if str(meta.get("tier") or "") == "local":
        return "free"
    cost = meta.get("cost_usd")
    if cost is None:  # no catalog price for this model: say so rather than print 0.00
        return "unknown"
    if not cost:
        return "free"
    return f"${float(cost):.6f}"


def _latency(meta: Mapping[str, Any]) -> str:
    latency = meta.get("latency_ms")
    return "" if latency is None else f"{int(latency)} ms"


_FORMATTERS: dict[FooterField, Any] = {
    "model": _model,
    "tier": _tier,
    "tokens": _tokens,
    "cost": _cost,
    "latency": _latency,
}


def footer_line(meta: Mapping[str, Any], rules: Rules) -> str | None:
    """The footer for these facts, or None when it is off or no enabled field has a value."""
    footer = rules.gateway.footer
    if not footer.enabled:
        return None
    rendered = []
    for name in footer.fields:
        value = _FORMATTERS[name](meta)
        if value:
            rendered.append(f"{name}={value}")
    return f"{FOOTER_PREFIX} {' '.join(rendered)}" if rendered else None


def footer_text(result: RouteResult | None, rules: Rules) -> str | None:
    """The one footer line for this answer, or None when it must not be appended.

    Off, no result, or a tool-call answer: nothing. An error never reaches this function, which
    is why the error bodies stay clean by construction.
    """
    if result is None or not rules.gateway.footer.enabled:
        return None
    if result.result.tool_calls or result.result.finish_reason == "tool_calls":
        return None
    return footer_line(headwaters_meta(result.decision, result, rules), rules)


def with_footer(content: str, result: RouteResult | None, rules: Rules) -> str:
    """``content`` with the footer as its tail, one line after a blank line."""
    line = footer_text(result, rules)
    if line is None:
        return content
    return f"{content}\n\n{line}" if content else line
