#!/usr/bin/env python3
# CUI // SP-CTI
"""ICDEV Standalone MCP Server -- Core.

Wrapper script that resolves the ICDEV installation directory, sets up
sys.path and environment, then starts the Core MCP server.
Partial capabilities are acceptable -- missing tools are logged, not fatal.
"""
import logging
import os
import sys
from pathlib import Path

logger = logging.getLogger("icdev.mcp.standalone.core")


def _resolve_base_dir():
    """Resolve ICDEV base directory."""
    env_dir = os.environ.get("ICDEV_BASE_DIR")
    if env_dir and Path(env_dir).is_dir():
        return Path(env_dir)
    # Infer from package location (tools/mcp/standalone/core.py -> 4 levels up)
    return Path(__file__).resolve().parent.parent.parent.parent


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
        from tools.mcp.core_server import create_server
        server = create_server()
        logger.info("Starting ICDEV Core MCP server (base_dir=%s)", base_dir)
        server.run()
    except ImportError as e:
        logger.warning("Some capabilities unavailable: %s", e)
        logger.info("Server starting with partial capabilities...")
        try:
            from tools.mcp.core_server import create_server
            server = create_server()
            server.run()
        except Exception as exc:
            logger.error("Failed to start Core MCP server: %s", exc)
            sys.exit(1)
    except Exception as exc:
        logger.error("Failed to start Core MCP server: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
