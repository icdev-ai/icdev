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
