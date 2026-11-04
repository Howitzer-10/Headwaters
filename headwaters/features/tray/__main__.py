"""``python -m headwaters.features.tray``: how the daemon and ``headwaters tray`` start it."""

from __future__ import annotations

import sys

from headwaters.features.tray import run

if __name__ == "__main__":
    sys.exit(run())
