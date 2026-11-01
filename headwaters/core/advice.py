"""Fixed user-facing advice lines shared by every engine (HDW-204, FU-123-2).

One wording, so ``headwaters pick`` and the MCP tools warn the same way. No request text here.
"""

from __future__ import annotations

__all__ = ["BELOW_FLOOR_WARNING"]

BELOW_FLOOR_WARNING = (
    "Warning: no model in scope reaches the quality floor for this request. This is "
    "the best available one; its answer is verified before you see it."
)
