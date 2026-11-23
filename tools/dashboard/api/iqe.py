# CUI // SP-CTI
"""IQE dashboard routes — list and execute .iqe query files.

GET  /iqe       — list all .iqe files under context/iqe/queries/
POST /iqe/run   — parse and execute a query file; returns JSON rows
                  body: {"query_path": "relative/path.iqe"}
                  404 if file missing, 400 if parse error, 422 if a
                  collection it reads is unavailable, 500 if execution
                  failed -- never ok/true with zero rows for a failure

Both go through tools/iqe/catalog.py: every adapter is loaded, seeds with no
executor data source are withheld from the listing with their reason, and a
seed runs with conn=None so each adapter opens its own database.

NIST 800-53: AU-2 (Audit Events), SI-10 (Information Input Validation)
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

from pathlib import Path

from flask import Blueprint, jsonify, request

from tools.iqe import catalog

logger = get_logger("icdev.dashboard.iqe")

# Resolved once at import time; stays inside the project tree.
_QUERIES_DIR = (Path(__file__).resolve().parents[3] / "context" / "iqe" / "queries")

iqe_api = Blueprint("iqe_api", __name__)


@iqe_api.route("/iqe", methods=["GET"])
def list_queries():
    """Return runnable .iqe seeds, and those withheld with the reason they cannot run."""
    return jsonify(catalog.list_seeds(_QUERIES_DIR))


@iqe_api.route("/iqe/run", methods=["POST"])
def run_query():
    """Parse and execute a .iqe file; return JSON rows, or ok/false with the reason."""
    body = request.get_json(silent=True) or {}
    query_path: str = body.get("query_path", "")
    if not query_path:
        return jsonify({"ok": False, "error": "query_path is required"}), 400

    # Resolve and enforce path stays inside _QUERIES_DIR (path-traversal guard).
    target = (_QUERIES_DIR / query_path).resolve()
    try:
        target.relative_to(_QUERIES_DIR.resolve())
    except ValueError:
        return jsonify({"ok": False, "error": "invalid query_path"}), 400

    if not target.exists() or target.suffix != ".iqe":
        return jsonify({"ok": False, "error": f"query not found: {query_path}"}), 404

    result, status = catalog.run_seed(target, query_path)
    return jsonify(result), status
