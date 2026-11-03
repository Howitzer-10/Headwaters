"""Windows packaging for Headwaters (HDW-405): install layout, runtime, autostart, itch payload.

Nothing in this package is imported by the daemon. The modules build *plans* (argv lists and
paths) that the Inno Setup script, the itch manifest and the uninstaller act on, so every decision
is testable without registering a scheduled task, creating a virtualenv or touching
``%APPDATA%``.
"""

from __future__ import annotations

from headwaters.packaging.layout import Install

__all__ = ["Install"]
