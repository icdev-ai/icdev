# CUI // SP-CTI
"""Oracle API — anticipatory intelligence predictions endpoint."""

from flask import Blueprint, jsonify

oracle_api = Blueprint("oracle_api", __name__)


@oracle_api.route("/api/oracle/predictions", methods=["GET"])
def list_predictions():
    """List Oracle predictions."""
    try:
        from tools.oracle.lenses.lens_quality import QualityLens

        lens = QualityLens()
        predictions = lens.run()
        return jsonify({"predictions": [p.to_dict() for p in predictions], "count": len(predictions)})
    except Exception as exc:
        return jsonify({"predictions": [], "count": 0, "error": str(exc)})


@oracle_api.route("/api/oracle/proposals/stats", methods=["GET"])
def oracle_proposals_stats():
    """Oracle proposal stats (stub — populated as proposals accumulate)."""
    return jsonify({"total": 0, "pending": 0, "approved": 0, "rejected": 0})


@oracle_api.route("/api/oracle/proposals/unified", methods=["GET"])
def oracle_proposals_unified():
    """Oracle unified proposal view (stub)."""
    return jsonify({"proposals": [], "total": 0})


@oracle_api.route("/api/oracle/proposals/history", methods=["GET"])
def oracle_proposals_history():
    """Oracle proposal history (stub)."""
    return jsonify({"history": [], "total": 0})
