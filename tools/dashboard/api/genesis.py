#!/usr/bin/env python3
# CUI // SP-CTI
"""Dashboard API: Genesis Reflex Coverage and Health.

Provides endpoints for monitoring which Genesis reflexes are real
implementations versus stubs, plus daemon health and coverage metrics.

Read-only against daemon state and reflex modules.
"""

import os
import sys
from pathlib import Path

from flask import Blueprint, jsonify

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

genesis_api = Blueprint("genesis_api", __name__, url_prefix="/api/genesis")


def _load_daemon_config():
    """Load genesis config if present."""
    config_path = BASE_DIR / "args" / "genesis_config.yaml"
    if not config_path.exists():
        return {}
    try:
        import yaml
        with open(config_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


@genesis_api.route("/reflex-coverage", methods=["GET"])
def reflex_coverage():
    """Return reflex implementation coverage statistics.

    Example response:
    {
      "total": 25,
      "real": 12,
      "partial": 3,
      "stubs": 8,
      "missing": 2,
      "coverage_percent": 48.0,
      "details": [
        {"reflex": "research", "exists": true, "has_run": true,
         "implementation_status": "full", "is_stub": false, "loc": 120},
        ...
      ]
    }
    """
    try:
        from tools.genesis.daemon import GenesisDaemon

        config = _load_daemon_config()
        daemon = GenesisDaemon(config)
        coverage = daemon.get_reflex_coverage()
        return jsonify(coverage)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@genesis_api.route("/daemon-status", methods=["GET"])
def daemon_status():
    """Return high-level Genesis daemon status (running, enabled, version)."""
    try:
        from tools.genesis.daemon import DAEMON_VERSION, PID_FILE

        running = PID_FILE.exists()
        pid = None
        if running:
            try:
                pid = int(PID_FILE.read_text(encoding="utf-8").strip())
            except Exception:
                pass

        return jsonify(
            {
                "daemon": "Genesis",
                "version": DAEMON_VERSION,
                "enabled": os.environ.get("ICDEV_GENESIS_ENABLED", "false").lower() == "true",
                "running": running,
                "pid": pid,
            }
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
