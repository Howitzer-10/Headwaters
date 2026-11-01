"""Request ``_meta`` keys the shim sends to the daemon (HDW-204, HDW-205, D9).

Kept apart from the tools so the shim imports nothing heavy for a few strings.
"""

from __future__ import annotations

__all__ = ["META_CLIENT", "META_CWD", "META_ROOTS", "MORE_TOOL"]

META_CWD = "headwaters/cwd"  # the shim's working directory: the project folder a host opened
META_ROOTS = "headwaters/roots"  # host roots (paths or file:// URIs); they may only narrow
# One random id per shim process, so the daemon can tell its hosts apart (progressive disclosure).
META_CLIENT = "headwaters/client"
MORE_TOOL = "headwaters_more"  # a successful call makes the shim tell its host the list changed
