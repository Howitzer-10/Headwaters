"""Put Headwaters into a host's own config files (HDW-403).

The package is deliberately split so importing one small piece does not drag in the rest: ``names``
is imported by the MCP tool that suggests switch commands (FU-205-3), ``engine`` by the CLI, and
``verify`` by the doctor. Nothing here imports the MCP SDK, the web framework or the daemon (D14).
"""
