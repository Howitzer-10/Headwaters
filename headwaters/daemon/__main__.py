"""``python -m headwaters.daemon [--port N]``: run the shared daemon in the foreground."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from headwaters.core.stdio import configure_utf8
from headwaters.daemon.runner import run

EXIT_ALREADY_RUNNING = 3


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="headwaters-daemon", description=__doc__)
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Preferred port of the internal API and MCP endpoint (a free one is used if taken).",
    )
    args = parser.parse_args(argv)
    configure_utf8()
    return run(preferred_port=args.port)


if __name__ == "__main__":
    sys.exit(main())
