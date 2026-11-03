"""Packaging for Headwaters (HDW-405, HDW-406): install layouts, runtime, autostart, payloads.

Nothing in this package is imported by the daemon. The modules build *plans* (argv lists and
paths) that the Inno Setup script, the itch manifest, the systemd user unit and the uninstallers
act on, so every decision is testable without registering a scheduled task, creating a
virtualenv, starting a service or touching ``%APPDATA%``.
"""

from __future__ import annotations

from headwaters.packaging.layout import Install
from headwaters.packaging.linux import LinuxInstall

__all__ = ["Install", "LinuxInstall"]
