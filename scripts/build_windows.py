"""Build the Windows installer and the itch payload (HDW-405).

Thin wrapper so the release steps are discoverable from ``scripts/``:

    uv run python scripts/build_windows.py --version 0.9.0

Signing happens only when ``HEADWATERS_CERT_PATH`` (plus ``HEADWATERS_CERT_PASSWORD`` if the
certificate has one) is set; without it the build says so and finishes unsigned. Nothing here
uploads anything: see ``packaging/butler/push_windows_beta.ps1``.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from headwaters.packaging.build import main

if __name__ == "__main__":
    sys.exit(main())
