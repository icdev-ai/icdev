#!/usr/bin/env python3

# CUI // SP-CTI
"""ICDEV™ Standalone MCP Server -- Maintenance.

Wrapper script that resolves the ICDEV™ installation directory, sets up
sys.path and environment, then starts the Maintenance MCP server.
Partial capabilities are acceptable -- missing tools are logged, not fatal.
"""

import logging
import os
import sys
from pathlib import Path


def _resolve_base_dir():
    """Resolve ICDEV™ base directory."""
    env_dir = os.environ.get("ICDEV_BASE_DIR")
    if env_dir and Path(env_dir).is_dir():
        return Path(env_dir)
    # Infer from package location (tools/mcp/standalone/maintenance.py -> 4 levels up)
    return Path(__file__).resolve().parent.parent.parent.parent


# Launched by path (``python tools/mcp/standalone/<name>.py``) only this file's
# own directory is on sys.path, so the first-party import below is unresolvable
# until the installation root is added. main() re-inserts it for the server's own
# imports; this earlier insert is what lets THIS module finish importing at all.
_BASE_DIR = _resolve_base_dir()
if str(_BASE_DIR) not in sys.path:
    sys.path.insert(0, str(_BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("icdev.mcp.standalone.maintenance")


def main():
    base_dir = _resolve_base_dir()
    if str(base_dir) not in sys.path:
        sys.path.insert(0, str(base_dir))

    # Set ICDEV_DB_PATH if not already set
    if "ICDEV_DB_PATH" not in os.environ:
        db_path = base_dir / "data" / "icdev.db"
        os.environ["ICDEV_DB_PATH"] = str(db_path)

    logging.basicConfig(level=logging.INFO, format="%(name)s %(levelname)s %(message)s")

    try:
        from tools.mcp.maintenance_server import create_server

        server = create_server()
        logger.info("Starting ICDEV™ Maintenance MCP server (base_dir=%s)", base_dir)
        server.run()
    except ImportError as e:
        logger.warning("Some capabilities unavailable: %s", e)
        logger.info("Server starting with partial capabilities...")
        try:
            from tools.mcp.maintenance_server import create_server

            server = create_server()
            server.run()
        except Exception as exc:
            logger.error("Failed to start Maintenance MCP server: %s", exc)
            sys.exit(1)
    except Exception as exc:
        logger.error("Failed to start Maintenance MCP server: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
