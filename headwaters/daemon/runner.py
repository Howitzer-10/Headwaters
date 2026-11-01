"""Process entry of the daemon: lock, rules, ports, run, clean up (HDW-201)."""

from __future__ import annotations

import asyncio
import os

from headwaters.core import netports, paths
from headwaters.core.daemon_info import DEFAULT_API_PORT, load_or_create_token, remove_info
from headwaters.core.instance_lock import InstanceLock
from headwaters.core.logging import configure_logging, get_logger, shutdown_logging
from headwaters.core.rules import Rules
from headwaters.core.rules_io import ConfigError, load_rules, rules_path
from headwaters.core.rules_watch import RulesManager
from headwaters.core.state import open_database
from headwaters.daemon.service import Daemon, pick_ports

__all__ = ["EXIT_ALREADY_RUNNING", "EXIT_CONFIG", "EXIT_NO_PORT", "EXIT_OK", "run"]

EXIT_OK = 0
EXIT_CONFIG = 2
EXIT_ALREADY_RUNNING = 3
EXIT_NO_PORT = 4
_log = get_logger("daemon")


def _load_rules() -> Rules:
    path = rules_path()
    return load_rules(path) if path.exists() else Rules()


def run(*, preferred_port: int | None = None) -> int:
    """Run the daemon in the foreground until it is told to stop. Returns an exit code."""
    paths.get_paths().ensure_dirs()
    configure_logging()
    lock = InstanceLock()
    try:
        if not lock.acquire():
            _log.info("another daemon already holds the lock")
            return EXIT_ALREADY_RUNNING
        try:
            rules = _load_rules()
        except ConfigError as exc:
            # Starting on defaults could drop a Private lock the user set; refuse instead.
            _log.error("rules.yaml is invalid, daemon not started: %s", exc)
            return EXIT_CONFIG
        try:
            sock, ports = pick_ports(rules, preferred_port, DEFAULT_API_PORT)
        except netports.PortError as exc:
            _log.error("no port available: %s", exc)
            return EXIT_NO_PORT
        # Watch rules.yaml so ``headwaters private on`` reaches the running router (HDW-131).
        manager = RulesManager()
        manager.start()
        daemon = Daemon(
            rules,
            open_database(),
            sock,
            ports,
            load_or_create_token(),
            rules_source=lambda: manager.current,
            reload_rules=manager.reload,
        )
        try:
            daemon.rebuild_engine()
            asyncio.run(daemon.serve())
        finally:
            manager.stop()
            sock.close()
            remove_info(os.getpid())
        return EXIT_OK
    finally:
        lock.release()
        shutdown_logging()
