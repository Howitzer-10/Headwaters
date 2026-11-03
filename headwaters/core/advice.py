"""Fixed user-facing advice lines shared by every engine (HDW-204, FU-123-2).

One wording, so ``headwaters pick`` and the MCP tools warn the same way. No request text here.
"""

from __future__ import annotations

__all__ = [
    "BELOW_FLOOR_WARNING",
    "REMOTE_IMAGE_ADVICE",
    "STREAMED_VERIFY_NOTE",
    "UNCHECKED_NOTE",
]

BELOW_FLOOR_WARNING = (
    "Warning: no model in scope reaches the quality floor for this request. This is "
    "the best available one; its answer is verified before you see it."
)

# FU-130-1: a mandatory gate that could not check anything says so, so the warning above is
# never read as a claim that the answer was verified.
UNCHECKED_NOTE = "No check could run on this answer: it is unchecked, not verified."

# FU-130-2: a streamed answer is checked after it was streamed; the final event reports it.
STREAMED_VERIFY_NOTE = (
    "This answer is streamed before the check runs; the final event reports the result."
)

# FU-135-3: a local model cannot fetch a remote URL, so a refusal caused by one always says
# what to send instead. Kept here so every engine can use the same words.
REMOTE_IMAGE_ADVICE = (
    "Send the image as a data: URL (data:image/png;base64,<bytes>) instead of a remote "
    "http(s) URL: a local model cannot fetch the network, so only a data: URL reaches it."
)
