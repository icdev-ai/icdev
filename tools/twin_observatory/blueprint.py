# CUI // SP-CTI — Twin Observatory blueprint (twx-obs-01)
"""Flask blueprint for the cross-canvas Twin Observatory page.

Registry-driven (args/component_registry.yaml key `twin_observatory`). Read-only:
renders the twin_core observer grid + twin_* event stream, and answers IQE
questions over the twin_observatory.* collections.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request

from tools.logging.icdev_logger import get_logger
from tools.twin_observatory.constants import IQE_COLLECTIONS
from tools.twin_observatory.observatory import get_observatory_data

logger = get_logger("icdev.twin_observatory.blueprint")

twin_observatory_bp = Blueprint(
    "twin_observatory",
    __name__,
    url_prefix="/twin-observatory",
    template_folder="../../tools/dashboard/templates",
)


@twin_observatory_bp.route("/")
def index():
    """Render the Twin Observatory: per-twin health grid + drift event stream."""
    try:
        data = get_observatory_data()
    except Exception as exc:  # noqa: BLE001 — page must always render
        logger.warning("twin_observatory index degraded: %s", exc)
        data = {"report": {"twins": [], "twin_count": 0, "summary": {}}, "events": [], "generated_at": None}
    return render_template("twin_observatory/index.html", **data)


@twin_observatory_bp.route("/api/data")
def api_data():
    """JSON: the same observatory payload (for polling / external consumers)."""
    return jsonify(get_observatory_data(
        window_hours=int(request.args.get("window_hours", 24)),
        event_limit=int(request.args.get("event_limit", 50)),
    ))


@twin_observatory_bp.route("/api/iqe-query", methods=["POST"])
def api_iqe_query():
    """Answer a plain-English question over the twin_observatory.* collections.

    Importing the adapter registers the collections on the module-level Executor.
    """
    import importlib

    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()
    execute = data.get("execute", True)
    if not question:
        return jsonify({"error": "question is required"}), 400

    try:
        importlib.import_module("tools.iqe.adapters.twin_observatory")
    except Exception:  # noqa: BLE001
        pass

    iqe_str = ""
    try:
        from tools.iqe.executor import execute_query
        from tools.iqe.nl_to_iqe import nl_to_iqe
        from tools.iqe.parser import parse as _parse

        translation = nl_to_iqe(question, collections=list(IQE_COLLECTIONS))
        iqe_str = translation["iqe"]
        ast = _parse(iqe_str)
        results = execute_query(ast, None) if execute else []
        return jsonify({"iqe": iqe_str, "results": results, "row_count": len(results)})
    except Exception as exc:  # noqa: BLE001
        return jsonify({"error": str(exc), "iqe": iqe_str}), 500
