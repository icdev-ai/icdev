# CUI // SP-CTI
"""TA Patterns Blueprint — Flask routes for chart pattern detection API."""
from __future__ import annotations

from flask import Blueprint, jsonify, request as flask_request


def create_ta_blueprint() -> Blueprint:
    bp = Blueprint("ta_patterns", __name__, url_prefix="/api/ta/patterns")

    @bp.route("/detect", methods=["POST"])
    def detect_patterns_api():
        data = flask_request.get_json(silent=True) or {}
        bars = data.get("bars", [])
        if not bars:
            return jsonify({"patterns": [], "error": "bars required"}), 400
        try:
            from tools.trading.ta.patterns import detect_patterns
            patterns = detect_patterns(bars)
            return jsonify({"patterns": patterns})
        except Exception as exc:
            return jsonify({"patterns": [], "error": str(exc)}), 500

    return bp
