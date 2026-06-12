# CUI // SP-CTI
"""IQE dashboard routes — list and execute .iqe query files.

GET  /iqe       — list all .iqe files under context/iqe/queries/
POST /iqe/run   — parse and execute a query file; returns JSON rows
                  body: {"query_path": "relative/path.iqe"}
                  404 if file missing, 400 if parse error

NIST 800-53: AU-2 (Audit Events), SI-10 (Information Input Validation)
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

from pathlib import Path

from flask import Blueprint, jsonify, request

from tools.iqe import IQESyntaxError, parse
from tools.iqe.executor import execute_query

logger = get_logger("icdev.dashboard.iqe")

# Resolved once at import time; stays inside the project tree.
_QUERIES_DIR = (Path(__file__).resolve().parents[3] / "context" / "iqe" / "queries")

iqe_api = Blueprint("iqe_api", __name__)


@iqe_api.route("/iqe", methods=["GET"])
def list_queries():
    """Return metadata for every .iqe file under context/iqe/queries/."""
    queries = []
    if _QUERIES_DIR.is_dir():
        for path in sorted(_QUERIES_DIR.rglob("*.iqe")):
            rel = path.relative_to(_QUERIES_DIR)
            queries.append({
                "name": path.stem,
                "path": rel.as_posix(),
                "canvas": rel.parts[0] if len(rel.parts) > 1 else "general",
            })
    return jsonify({"ok": True, "queries": queries, "count": len(queries)})


@iqe_api.route("/iqe/run", methods=["POST"])
def run_query():
    """Parse and execute a .iqe file; return JSON rows."""
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

    query_str = target.read_text(encoding="utf-8")
    try:
        ast = parse(query_str)
    except IQESyntaxError as exc:
        return jsonify({"ok": False, "error": f"parse error: {exc}"}), 400

    rows: list[dict] = []
    try:
        from tools.db.storage import get_connection  # noqa: PLC0415
        with get_connection() as conn:
            rows = execute_query(ast, conn)
    except Exception as exc:  # noqa: BLE001
        logger.warning("IQE execute failed for %s: %s", query_path, exc)

    return jsonify({
        "ok": True,
        "query": query_path,
        "rows": rows,
        "count": len(rows),
    })
