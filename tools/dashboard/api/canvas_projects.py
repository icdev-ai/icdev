# CUI // SP-CTI
"""Canvas Projects API — cross-canvas project management."""

from flask import Blueprint, jsonify

canvas_projects_api = Blueprint("canvas_projects_api", __name__)


@canvas_projects_api.route("/api/canvas-projects", methods=["GET"])
def list_canvas_projects():
    """List canvas design projects."""
    return jsonify({"projects": [], "total": 0})


@canvas_projects_api.route("/api/canvas-projects/compliance", methods=["GET"])
def canvas_compliance_summary():
    """Aggregate compliance scores across canvases."""
    return jsonify({"canvases": [], "overall_score": 0})
