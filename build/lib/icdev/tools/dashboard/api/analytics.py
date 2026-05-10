# CUI // SP-CTI
"""Analytics API — funnel analytics and conversion metrics."""

from flask import Blueprint, jsonify

analytics_api = Blueprint("analytics_api", __name__)


@analytics_api.route("/api/analytics/funnel", methods=["GET"])
def get_funnel():
    """Get funnel analytics data."""
    return jsonify({"stages": [], "total": 0})
