"""Build the Linux payload (HDW-406).

Thin wrapper so the release step is discoverable from ``scripts/``:

    uv run python scripts/build_linux.py --version 0.9.0

There is no compiler and no signing step on this target: the payload is ``install.sh`` next to
``runtime/``, and the install script builds the virtualenv with ``uv``. Nothing here uploads
anything: see ``packaging/butler/push_linux_beta.ps1``.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from headwaters.packaging.build import main

if __name__ == "__main__":
    sys.exit(main(["--target", "linux", *sys.argv[1:]]))
