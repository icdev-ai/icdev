
from tools.logging.icdev_logger import get_logger
# CUI // SP-CTI
"""Canvas Projects API — cross-canvas project management.

Wires the canvas orchestrator into the dashboard REST API.
"""


from flask import Blueprint, jsonify, request

logger = get_logger(__name__)

canvas_projects_api = Blueprint("canvas_projects_api", __name__)


@canvas_projects_api.route("/api/canvas-projects", methods=["GET"])
def list_canvas_projects():
    """List canvas design projects."""
    try:
        from tools.canvas.orchestrator import list_projects

        projects = list_projects()
        return jsonify({"projects": projects, "total": len(projects)})
    except Exception as exc:
        logger.warning("canvas_projects list error: %s", exc)
        return jsonify({"projects": [], "total": 0})


@canvas_projects_api.route("/api/canvas-projects", methods=["POST"])
def create_canvas_project():
    """Create a new canvas design project."""
    try:
        from tools.canvas.orchestrator import create_project

        data = request.get_json(force=True, silent=True) or {}
        project = create_project(
            name=data.get("name", "Untitled Project"),
            description=data.get("description", ""),
            classification=data.get("classification", "CUI"),
        )
        return jsonify(project), 201
    except Exception as exc:
        logger.warning("canvas_projects create error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@canvas_projects_api.route("/api/canvas-projects/<project_id>", methods=["GET"])
def get_canvas_project(project_id):
    """Get a single canvas project."""
    try:
        from tools.canvas.orchestrator import get_project

        project = get_project(project_id)
        if not project:
            return jsonify({"error": "Project not found"}), 404
        return jsonify(project)
    except Exception as exc:
        logger.warning("canvas_projects get error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@canvas_projects_api.route("/api/canvas-projects/<project_id>", methods=["PATCH"])
def update_canvas_project(project_id):
    """Update a canvas project."""
    try:
        from tools.canvas.orchestrator import update_project

        data = request.get_json(force=True, silent=True) or {}
        project = update_project(project_id, **data)
        return jsonify(project)
    except Exception as exc:
        logger.warning("canvas_projects update error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@canvas_projects_api.route("/api/canvas-projects/<project_id>", methods=["DELETE"])
def delete_canvas_project(project_id):
    """Delete a canvas project."""
    try:
        from tools.canvas.orchestrator import delete_project

        deleted = delete_project(project_id)
        if not deleted:
            return jsonify({"error": "Project not found"}), 404
        return jsonify({"status": "deleted", "id": project_id})
    except Exception as exc:
        logger.warning("canvas_projects delete error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@canvas_projects_api.route("/api/canvas-projects/<project_id>/link", methods=["POST"])
def link_canvas_design(project_id):
    """Link a canvas design to a project."""
    try:
        from tools.canvas.orchestrator import link_design

        data = request.get_json(force=True, silent=True) or {}
        canvas_key = data.get("canvas_key")
        design_id = data.get("design_id")
        if not canvas_key or not design_id:
            return jsonify({"error": "canvas_key and design_id required"}), 400
        result = link_design(project_id, canvas_key, design_id)
        return jsonify(result)
    except Exception as exc:
        logger.warning("canvas_projects link error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@canvas_projects_api.route("/api/canvas-projects/<project_id>/unlink", methods=["POST"])
def unlink_canvas_design(project_id):
    """Unlink a canvas design from a project."""
    try:
        from tools.canvas.orchestrator import unlink_design

        data = request.get_json(force=True, silent=True) or {}
        canvas_key = data.get("canvas_key")
        if not canvas_key:
            return jsonify({"error": "canvas_key required"}), 400
        result = unlink_design(project_id, canvas_key)
        return jsonify(result)
    except Exception as exc:
        logger.warning("canvas_projects unlink error: %s", exc)
        return jsonify({"error": str(exc)}), 500


@canvas_projects_api.route("/api/canvas-projects/compliance", methods=["GET"])
def canvas_compliance_summary():
    """Aggregate compliance scores across canvases for a project."""
    project_id = request.args.get("project_id")
    if not project_id:
        try:
            from tools.canvas.orchestrator import list_projects

            projects = list_projects()
            if projects:
                project_id = projects[0]["id"]
            else:
                return jsonify({"canvases": {}, "overall_score": 0})
        except Exception:
            return jsonify({"canvases": {}, "overall_score": 0})
    try:
        from tools.canvas.orchestrator import get_compliance_summary

        summary = get_compliance_summary(project_id)
        return jsonify(summary)
    except Exception as exc:
        logger.warning("canvas_projects compliance error: %s", exc)
        return jsonify({"canvases": {}, "overall_score": 0})


@canvas_projects_api.route("/api/canvas-projects/<project_id>/readiness", methods=["GET"])
def canvas_project_readiness(project_id):
    """Compute project readiness across all linked canvases."""
    try:
        from tools.canvas.orchestrator import compute_readiness

        readiness = compute_readiness(project_id)
        return jsonify(readiness)
    except Exception as exc:
        logger.warning("canvas_projects readiness error: %s", exc)
        return jsonify({"error": str(exc)}), 500
