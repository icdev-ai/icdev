# CUI // SP-CTI
"""Canvas Knowledge Graph Blueprint — REST API for cross-canvas KG queries.

This module is loaded only when ICDEV_CANVAS_KG_ENABLED=true.
If you see an ImportError here, ensure tools/canvas/kg_builder.py and
related modules are present and importable.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request


def create_canvas_kg_blueprint() -> Blueprint:
    """Create and return the Canvas KG Flask blueprint."""
    bp = Blueprint("canvas_kg", __name__, url_prefix="/api/canvas-kg")

    @bp.route("/query", methods=["POST"])
    def canvas_kg_query():
        data = request.get_json(silent=True) or {}
        try:
            from tools.canvas.kg_builder import query_canvas_kg
            result = query_canvas_kg(data)
            return jsonify(result)
        except Exception as exc:
            return jsonify({"error": str(exc), "nodes": [], "edges": []}), 500

    @bp.route("/health", methods=["GET"])
    def canvas_kg_health():
        return jsonify({"status": "ok", "module": "canvas_kg"})

    return bp
