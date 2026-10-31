"""``python -m headwaters.mcp``: the stdio MCP shim (same as ``headwaters-mcp``)."""

import sys

from headwaters.mcp.shim import main

if __name__ == "__main__":
    sys.exit(main())
