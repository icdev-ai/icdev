# CUI // SP-CTI
"""ICDEV™ Studio — WNE (Workflow Narrative Engine) Blueprint.

Mounts at root (url_prefix="") to serve:
  POST /api/wne/session                       — create WNE chat session
  POST /api/wne/<session_id>/message          — send message, get reply + progress
  GET  /api/wne/<session_id>/artifact?type=   — retrieve generated artifact
  GET  /api/wne/<session_id>/status           — session status + artifacts_ready
  GET  /studio/narrate                        — narrate page UI

Register in app.py:
    from tools.studio.studio import create_studio_wne_blueprint
    bp = create_studio_wne_blueprint()
    if bp:
        app.register_blueprint(bp)
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

from pathlib import Path

from flask import Blueprint, Response, jsonify, render_template, request

logger = get_logger("icdev.studio.wne")

_STUDIO_DIR = Path(__file__).resolve().parent
_ICDEV_ROOT = _STUDIO_DIR.parent.parent
_TEMPLATE_DIR = _ICDEV_ROOT / "tools" / "dashboard" / "templates"

_ARTIFACT_TYPES = frozenset(
    {"exec_brief", "coa_comparison", "budget_table", "roi_analysis", "slide_outline", "zip_bundle"}
)


def _get_db():
    from tools.dashboard.config import DB_PATH
    from tools.db.storage import get_connection
    return get_connection(db_path=str(DB_PATH))


def create_studio_wne_blueprint() -> Blueprint:
    bp = Blueprint(
        "studio_wne",
        __name__,
        template_folder=str(_TEMPLATE_DIR),
        url_prefix="",
    )

    @bp.route("/studio/narrate")
    def studio_narrate():
        return render_template("studio/narrate.html")

    @bp.route("/api/wne/session", methods=["POST"])
    def wne_create_session():
        try:
            from tools.studio.wne.chat_engine import WNEChatEngine
            data = request.get_json(silent=True) or {}
            workflow_id = data.get("workflow_id") or ""
            template_slug = data.get("template_slug") or ""
            if not workflow_id and not template_slug:
                return jsonify({"error": "workflow_id or template_slug is required"}), 400
            engine = WNEChatEngine()
            result = engine.create_session(
                workflow_id=workflow_id or None,
                template_slug=template_slug or None,
            )
            return jsonify(result), 201
        except Exception as exc:
            logger.exception("wne_create_session failed")
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/wne/<session_id>/message", methods=["POST"])
    def wne_send_message(session_id: str):
        try:
            from tools.studio.wne.chat_engine import WNEChatEngine
            data = request.get_json(silent=True) or {}
            message = (data.get("message") or "").strip()
            if not message:
                return jsonify({"error": "message is required"}), 400
            engine = WNEChatEngine()
            result = engine.send_message(session_id=session_id, message=message)
            return jsonify(result)
        except Exception as exc:
            logger.exception("wne_send_message failed: session=%s", session_id)
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/wne/<session_id>/artifact")
    def wne_get_artifact(session_id: str):
        try:
            artifact_type = (request.args.get("type") or "").strip()
            if not artifact_type:
                return jsonify({"error": "type query parameter is required"}), 400
            if artifact_type not in _ARTIFACT_TYPES:
                return jsonify({"error": f"unknown artifact type '{artifact_type}'"}), 400

            from tools.studio.wne.chat_engine import WNEChatEngine
            engine = WNEChatEngine()
            result = engine.get_artifact(session_id=session_id, artifact_type=artifact_type)

            if artifact_type == "zip_bundle":
                content = result.get("content") or b""
                if isinstance(content, str):
                    content = content.encode("utf-8")
                return Response(
                    content,
                    mimetype="application/zip",
                    headers={
                        "Content-Disposition": f"attachment; filename=narrative-{session_id[:8]}.zip"
                    },
                )

            text = result.get("content") or result.get("text") or ""
            return Response(text, mimetype="text/markdown; charset=utf-8")
        except Exception as exc:
            logger.exception("wne_get_artifact failed: session=%s type=%s", session_id, request.args.get("type"))
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/wne/<session_id>/status")
    def wne_get_status(session_id: str):
        try:
            from tools.studio.wne.chat_engine import WNEChatEngine
            engine = WNEChatEngine()
            result = engine.get_status(session_id=session_id)
            return jsonify(result)
        except Exception as exc:
            logger.exception("wne_get_status failed: session=%s", session_id)
            return jsonify({"error": str(exc)}), 500

    return bp
