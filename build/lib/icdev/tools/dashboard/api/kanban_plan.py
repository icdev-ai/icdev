# CUI // SP-CTI
"""Kanban Plan API — task decomposition and scheduling endpoints."""

from flask import Blueprint, jsonify

kanban_plan_api = Blueprint("kanban_plan_api", __name__)


@kanban_plan_api.route("/api/kanban/plans", methods=["GET"])
def list_plans():
    """List kanban plans."""
    return jsonify({"plans": [], "total": 0})
