"""Fixed user-facing advice lines shared by every engine (HDW-204, FU-123-2).

One wording, so ``headwaters pick`` and the MCP tools warn the same way. No request text here.
"""

from __future__ import annotations

__all__ = ["BELOW_FLOOR_WARNING", "REMOTE_IMAGE_ADVICE"]

BELOW_FLOOR_WARNING = (
    "Warning: no model in scope reaches the quality floor for this request. This is "
    "the best available one; its answer is verified before you see it."
)

# FU-135-3: a local model cannot fetch a remote URL, so a refusal caused by one always says
# what to send instead. Kept here so every engine can use the same words.
REMOTE_IMAGE_ADVICE = (
    "Send the image as a data: URL (data:image/png;base64,<bytes>) instead of a remote "
    "http(s) URL: a local model cannot fetch the network, so only a data: URL reaches it."
)
