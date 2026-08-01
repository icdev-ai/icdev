# CUI // SP-CTI
"""Industry Research Engine API.

Inline-route blueprint extracted verbatim from tools/dashboard/app.py
(nav-misc-03). Routes keep their exact /api/research/... paths. Registered via
_mount_inline(research_api). Pure mechanical extraction - no logic changes.
"""
from __future__ import annotations

import sys
from pathlib import Path

from flask import Blueprint, jsonify, request as flask_request

BASE_DIR = Path(__file__).resolve().parents[3]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

research_api = Blueprint("research_api", __name__)


@research_api.route("/api/research/sessions", methods=["POST"])
def api_research_create_session():
    """Create a new research session."""
    data = flask_request.get_json(silent=True) or {}
    try:
        from tools.research.session_manager import create_session

        result = create_session(
            name=data.get("name", ""),
            vertical_slug=data.get("vertical", ""),
            focus_areas=data.get("focus_areas", []),
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@research_api.route("/api/research/sessions")
def api_research_list_sessions():
    """List research sessions."""
    try:
        from tools.research.session_manager import list_sessions

        status = flask_request.args.get("status")
        return jsonify(list_sessions(status=status))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@research_api.route("/api/research/sessions/<session_id>/run", methods=["POST"])
def api_research_run_pipeline(session_id):
    """Run research pipeline for a session."""
    try:
        from tools.research.research_engine import run_pipeline

        result = run_pipeline(session_id=session_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@research_api.route("/api/research/sessions/<session_id>/status")
def api_research_session_status(session_id):
    """Get session status."""
    try:
        from tools.research.research_engine import get_status

        return jsonify(get_status(session_id=session_id))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@research_api.route("/api/research/sessions/<session_id>/dossier")
def api_research_session_dossier(session_id):
    """Get dossier by session ID."""
    try:
        from tools.research.dossier_generator import get_dossier

        return jsonify(get_dossier(session_id=session_id))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@research_api.route("/api/research/sessions/<session_id>/run-stage", methods=["POST"])
def api_research_run_stage(session_id):
    """Run a single pipeline stage for a session."""
    data = flask_request.get_json(silent=True) or {}
    stage = data.get("stage", "").upper()
    if not stage:
        return jsonify({"ok": False, "error": "Missing 'stage' parameter"}), 400
    try:
        from tools.research.research_engine import run_stage

        result = run_stage(session_id=session_id, stage=stage)
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@research_api.route("/api/research/sessions/<session_id>/regulatory")
def api_research_regulatory_landscape(session_id):
    """Get regulatory landscape for a session."""
    try:
        from tools.research.regulatory_mapper import get_regulatory_landscape

        return jsonify(get_regulatory_landscape(session_id=session_id))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@research_api.route("/api/research/sessions/<session_id>/retry", methods=["POST"])
def api_research_retry_session(session_id):
    """Retry a failed research session pipeline."""
    try:
        import threading
        from tools.research.research_engine import run_pipeline

        t = threading.Thread(target=run_pipeline, kwargs={"session_id": session_id}, daemon=True)
        t.start()
        return jsonify({"ok": True, "message": "Pipeline retry started"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@research_api.route("/api/research/dossiers/<dossier_id>")
def api_research_get_dossier(dossier_id):
    """Get a dossier by dossier ID."""
    try:
        from tools.research.dossier_generator import get_dossier

        return jsonify(get_dossier(dossier_id=dossier_id))
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@research_api.route("/api/research/dossiers/<dossier_id>/review", methods=["POST"])
def api_research_review_dossier(dossier_id):
    """Review a dossier."""
    data = flask_request.get_json(silent=True) or {}
    try:
        from tools.research.dossier_generator import review_dossier

        result = review_dossier(
            dossier_id=dossier_id,
            reviewer=data.get("reviewer", "dashboard"),
            status=data.get("decision", "approved"),
            review_notes=data.get("notes", ""),
        )
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@research_api.route("/api/research/verticals")
def api_research_list_verticals():
    """List available verticals."""
    try:
        from tools.research.vertical_loader import list_verticals

        return jsonify(list_verticals())
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@research_api.route("/api/research/verticals/load", methods=["POST"])
def api_research_load_verticals():
    """Load verticals from config files into DB."""
    try:
        from tools.research.vertical_loader import load_verticals_to_db

        result = load_verticals_to_db()
        return jsonify(result)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
