"""The shared daemon process (HDW-201): composition root of the Headwaters engines.

This package wires ``headwaters.core`` (router, catalog, keep-warm), ``headwaters.gateway`` (the
HTTP server) and ``headwaters.mcp`` (the streamable-HTTP MCP endpoint) into one process. Nothing
else imports it: the CLI starts it as a child process (``python -m headwaters.daemon``), which
keeps the web framework and the MCP SDK out of the CLI and out of core (see ``.importlinter``
and D14 in docs/DECISIONS.md).
"""
