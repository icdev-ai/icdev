"""RFI Response Workbench — Flask blueprint.

Routes:
  GET  /rfi/                                               — upload + session list
  POST /rfi/upload                                         — upload PDF, parse, create session
  GET  /rfi/<session_id>                                   — workbench for a session
  GET  /api/rfi/<session_id>/sections                      — list sections JSON
  POST /api/rfi/<session_id>/sections/<sid>/generate       — AI generate
  POST /api/rfi/<session_id>/sections/<sid>/hitl           — HITL action
  POST /api/rfi/<session_id>/sections/<sid>/writeguard     — WriteGuard
  POST /api/rfi/<session_id>/sections/<sid>/save           — save edits
  POST /api/rfi/<session_id>/export/<fmt>                  — export (docx|md)
  GET  /api/rfi/<session_id>/download/<fmt>                — download exported file
  DELETE /api/rfi/<session_id>                             — delete session
  POST /api/rfi/iqe-query                                  — IQE dispatch
"""
from __future__ import annotations

from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from flask import Blueprint, abort, jsonify, render_template, request, send_file
from tools.logging.icdev_logger import get_logger
import tools.govcon.rfi_workbench as wb

logger = get_logger("icdev.govcon.rfi_canvas_blueprint")

rfi_canvas_bp = Blueprint("rfi_canvas", __name__, template_folder="../../dashboard/templates")

_UPLOAD_DIR = _ROOT / ".tmp" / "rfi_uploads"
_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

_ALLOWED_EXT = {".pdf", ".docx", ".doc"}


def _allowed(filename):
    return Path(filename).suffix.lower() in _ALLOWED_EXT


# ── Pages ─────────────────────────────────────────────────────────────────────

@rfi_canvas_bp.route("/rfi/")
@rfi_canvas_bp.route("/rfi")
def rfi_index():
    sessions = wb.list_sessions()
    profiles = wb.list_profiles()
    return render_template("rfi_canvas/index.html", sessions=sessions, profiles=profiles)


@rfi_canvas_bp.route("/rfi/upload", methods=["POST"])
def rfi_upload():
    if "rfi_file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    f = request.files["rfi_file"]
    if not f.filename or not _allowed(f.filename):
        return jsonify({"error": "Only PDF and DOCX files are accepted"}), 400

    profile_name = request.form.get("profile", "own_company")
    _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    upload_path = _UPLOAD_DIR / f.filename
    f.save(str(upload_path))

    try:
        from tools.govcon.rfi_document_parser import parse_rfi_document
        parsed = parse_rfi_document(str(upload_path))
    except Exception as exc:
        logger.warning("RFI parse failed: %s", exc)
        parsed = {"rfi_number": "RFI-UNKNOWN", "title": f.filename, "objectives": [], "questionnaire_parts": []}

    session_id = wb.create_session(
        rfi_number=parsed.get("rfi_number", "RFI-UNKNOWN"),
        rfi_title=parsed.get("title", f.filename),
        profile_name=profile_name,
        upload_filename=f.filename,
        parsed_data=parsed,
    )
    return jsonify({"session_id": session_id, "redirect": f"/rfi/{session_id}"})


@rfi_canvas_bp.route("/rfi/<session_id>")
def rfi_workbench_page(session_id):
    session = wb.get_session(session_id)
    if not session:
        abort(404)
    sections = wb.get_sections(session_id)
    profiles = wb.list_profiles()
    return render_template("rfi_canvas/workbench.html", session=session, sections=sections, profiles=profiles)


# ── API: sections ─────────────────────────────────────────────────────────────

@rfi_canvas_bp.route("/api/rfi/<session_id>/sections")
def api_list_sections(session_id):
    return jsonify(wb.get_sections(session_id))


@rfi_canvas_bp.route("/api/rfi/<session_id>/sections/<section_id>/generate", methods=["POST"])
def api_generate(session_id, section_id):
    session = wb.get_session(session_id)
    if not session:
        return jsonify({"error": "Session not found"}), 404
    try:
        updated = wb.generate_section_content(
            section_id=section_id,
            profile_name=session["profile_name"],
            parsed_data=session.get("parsed_data") or {},
        )
        return jsonify({"ok": True, "section": updated})
    except Exception as exc:
        logger.exception("AI generation error for section %s", section_id)
        return jsonify({"error": str(exc)}), 500


@rfi_canvas_bp.route("/api/rfi/<session_id>/sections/<section_id>/hitl", methods=["POST"])
def api_hitl(session_id, section_id):
    data = request.get_json(force=True) or {}
    action = data.get("action")
    comment = data.get("comment", "")
    if action not in ("approve", "reject", "accept"):
        return jsonify({"error": "action must be approve | reject | accept"}), 400
    try:
        updated = wb.apply_hitl(section_id, action, comment)
        return jsonify({"ok": True, "section": updated})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@rfi_canvas_bp.route("/api/rfi/<session_id>/sections/<section_id>/writeguard", methods=["POST"])
def api_writeguard(session_id, section_id):
    try:
        result = wb.run_writeguard(section_id)
        return jsonify({"ok": True, "result": result})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@rfi_canvas_bp.route("/api/rfi/<session_id>/sections/<section_id>/save", methods=["POST"])
def api_save(session_id, section_id):
    data = request.get_json(force=True) or {}
    wb.save_section_content(section_id, data.get("content", ""))
    return jsonify({"ok": True})


# ── API: export ───────────────────────────────────────────────────────────────

@rfi_canvas_bp.route("/api/rfi/<session_id>/export/<fmt>", methods=["POST"])
def api_export(session_id, fmt):
    if fmt not in ("docx", "md"):
        return jsonify({"error": "Supported formats: docx, md"}), 400
    try:
        path = wb.assemble_and_export(session_id, fmt)
        return jsonify({"ok": True, "path": path, "download_url": f"/api/rfi/{session_id}/download/{fmt}"})
    except Exception as exc:
        logger.exception("Export error for session %s", session_id)
        return jsonify({"error": str(exc)}), 500


@rfi_canvas_bp.route("/api/rfi/<session_id>/download/<fmt>")
def api_download(session_id, fmt):
    db = wb.get_db()
    row = db.execute(
        "SELECT file_path FROM rfi_workbench_exports WHERE session_id=%s AND export_format=%s ORDER BY exported_at DESC LIMIT 1",
        (session_id, fmt),
    ).fetchone()
    if not row:
        abort(404)
    fpath = list(row)[0] if not hasattr(row, "keys") else row["file_path"]
    if not fpath or not Path(fpath).exists():
        abort(404)
    mime = (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        if fmt == "docx"
        else "text/markdown"
    )
    return send_file(fpath, mimetype=mime, as_attachment=True, download_name=Path(fpath).name)


@rfi_canvas_bp.route("/api/rfi/<session_id>", methods=["DELETE"])
def api_delete_session(session_id):
    try:
        wb.delete_session(session_id)
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── API: style guide ─────────────────────────────────────────────────────────

@rfi_canvas_bp.route("/api/rfi/<session_id>/style-guide")
def api_get_style_guide(session_id):
    try:
        from tools.govcon.rfi_style_engine import get_session_style_guide
        return jsonify(get_session_style_guide(session_id))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@rfi_canvas_bp.route("/api/rfi/<session_id>/style-guide", methods=["PUT"])
def api_set_style_guide(session_id):
    data = request.get_json(force=True) or {}
    page_limit = data.pop("page_limit", None)
    words_per_page = data.pop("words_per_page", None)
    try:
        from tools.govcon.rfi_style_engine import set_session_style_overrides
        set_session_style_overrides(session_id, data, page_limit=page_limit, words_per_page=words_per_page)
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@rfi_canvas_bp.route("/api/rfi/<session_id>/sections/<section_id>/limits")
def api_get_section_limits(session_id, section_id):
    try:
        from tools.govcon.rfi_style_engine import get_section_limits
        return jsonify(get_section_limits(section_id))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@rfi_canvas_bp.route("/api/rfi/<session_id>/sections/<section_id>/limits", methods=["PUT"])
def api_set_section_limits(session_id, section_id):
    data = request.get_json(force=True) or {}
    try:
        from tools.govcon.rfi_style_engine import set_section_limits
        set_section_limits(section_id, data.get("word_limit"), data.get("page_limit"))
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@rfi_canvas_bp.route("/api/rfi/<session_id>/sections/<section_id>/summarize", methods=["POST"])
def api_summarize(session_id, section_id):
    data = request.get_json(force=True) or {}
    word_target = data.get("word_target")
    if not word_target:
        return jsonify({"error": "word_target is required"}), 400
    try:
        result = wb.summarize_section_content(section_id, int(word_target))
        return jsonify({"ok": True, **result})
    except Exception as exc:
        logger.exception("Summarize error for section %s", section_id)
        return jsonify({"error": str(exc)}), 500


# ── API: engine weights ───────────────────────────────────────────────────────

@rfi_canvas_bp.route("/api/rfi/<session_id>/engine-weights")
def api_get_engine_weights(session_id):
    try:
        return jsonify(wb.get_engine_weights(session_id))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@rfi_canvas_bp.route("/api/rfi/<session_id>/engine-weights", methods=["PUT"])
def api_set_engine_weights(session_id):
    data = request.get_json(force=True) or {}
    try:
        wb.set_engine_weights(session_id, data)
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@rfi_canvas_bp.route("/api/rfi/<session_id>/sections/<section_id>/engine-weights")
def api_get_section_engine_weights(session_id, section_id):
    try:
        override = wb.get_section_engine_weights_override(section_id)
        if override is None:
            return jsonify({"inherited": True, "weights": wb.get_engine_weights(session_id)})
        return jsonify({"inherited": False, "weights": override})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@rfi_canvas_bp.route("/api/rfi/<session_id>/sections/<section_id>/engine-weights", methods=["PUT"])
def api_set_section_engine_weights(session_id, section_id):
    data = request.get_json(force=True) or {}
    try:
        wb.set_section_engine_weights_override(section_id, data)
        return jsonify({"ok": True})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── API: uploads ──────────────────────────────────────────────────────────────

_UPLOAD_PAST_PERF_DIR = _ROOT / ".tmp" / "rfi_past_perf"
_UPLOAD_PAST_PERF_DIR.mkdir(parents=True, exist_ok=True)

_ALLOWED_UPLOAD_EXT = {".pdf", ".docx", ".doc", ".txt"}


@rfi_canvas_bp.route("/api/rfi/<session_id>/uploads")
def api_list_uploads(session_id):
    try:
        return jsonify(wb.list_session_uploads(session_id))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@rfi_canvas_bp.route("/api/rfi/<session_id>/uploads", methods=["POST"])
def api_upload_past_perf(session_id):
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    f = request.files["file"]
    if not f.filename or Path(f.filename).suffix.lower() not in _ALLOWED_UPLOAD_EXT:
        return jsonify({"error": "Accepted: PDF, DOCX, DOC, TXT"}), 400
    upload_type = request.form.get("upload_type", "past_performance")
    _UPLOAD_PAST_PERF_DIR.mkdir(parents=True, exist_ok=True)
    dest = _UPLOAD_PAST_PERF_DIR / f"{session_id}_{f.filename}"
    f.save(str(dest))
    try:
        record = wb.save_session_upload(session_id, f.filename, str(dest), upload_type)
        return jsonify({"ok": True, "upload": record}), 201
    except Exception as exc:
        logger.exception("Upload save failed for session %s", session_id)
        return jsonify({"error": str(exc)}), 500


@rfi_canvas_bp.route("/api/rfi/<session_id>/uploads/<upload_id>", methods=["DELETE"])
def api_delete_upload(session_id, upload_id):
    deleted = wb.delete_session_upload(upload_id)
    if not deleted:
        return jsonify({"error": "Upload not found"}), 404
    return jsonify({"ok": True})


# ── API: requirements ─────────────────────────────────────────────────────────

@rfi_canvas_bp.route("/api/rfi/<session_id>/sections/<section_id>/requirements")
def api_get_requirements(session_id, section_id):
    return jsonify(wb.get_requirements(section_id))


@rfi_canvas_bp.route("/api/rfi/<session_id>/sections/<section_id>/requirements", methods=["POST"])
def api_add_requirement(session_id, section_id):
    data = request.get_json(force=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "text is required"}), 400
    try:
        new_req = wb.add_requirement(section_id, text, source="manual")
        return jsonify({"ok": True, "requirement": new_req}), 201
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@rfi_canvas_bp.route(
    "/api/rfi/<session_id>/sections/<section_id>/requirements/check-coverage",
    methods=["POST"],
)
def api_check_coverage(session_id, section_id):
    try:
        reqs = wb.check_requirement_coverage(section_id)
        return jsonify({"ok": True, "requirements": reqs})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@rfi_canvas_bp.route(
    "/api/rfi/<session_id>/sections/<section_id>/requirements/<req_id>",
    methods=["PUT"],
)
def api_update_requirement(session_id, section_id, req_id):
    data = request.get_json(force=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "text is required"}), 400
    updated = wb.update_requirement(section_id, req_id, text)
    if updated is None:
        return jsonify({"error": "Requirement not found"}), 404
    return jsonify({"ok": True, "requirement": updated})


@rfi_canvas_bp.route(
    "/api/rfi/<session_id>/sections/<section_id>/requirements/<req_id>",
    methods=["DELETE"],
)
def api_delete_requirement(session_id, section_id, req_id):
    deleted = wb.delete_requirement(section_id, req_id)
    if not deleted:
        return jsonify({"error": "Requirement not found"}), 404
    return jsonify({"ok": True})


# ── API: ACE team ──────────────────────────────────────────────────────────────

@rfi_canvas_bp.route("/api/rfi/<session_id>/ace/status")
def api_ace_status(session_id):
    try:
        status = wb.get_ace_team_status(session_id)
        return jsonify(status)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@rfi_canvas_bp.route("/api/rfi/<session_id>/ace/launch", methods=["POST"])
def api_ace_launch(session_id):
    try:
        instance_id = wb.launch_ace_team(session_id)
        return jsonify({"ok": True, "ace_instance_id": instance_id})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── IQE dispatch ──────────────────────────────────────────────────────────────

@rfi_canvas_bp.route("/api/rfi/iqe-query", methods=["POST"])
def api_iqe_query():
    data = request.get_json(force=True) or {}
    query = data.get("query", "").lower()
    try:
        db = wb.get_db()
        if "session" in query or "upload" in query or "status" in query:
            rows = db.execute(
                "SELECT rfi_number, rfi_title, profile_name, status, approved_sections, total_sections, created_at "
                "FROM rfi_workbench_sessions ORDER BY created_at DESC LIMIT 20"
            ).fetchall()
        elif "writeguard" in query or "score" in query or "quality" in query:
            rows = db.execute(
                "SELECT s.rfi_number, sec.part, sec.item_number, sec.title, sec.writeguard_score, sec.status "
                "FROM rfi_workbench_sections sec JOIN rfi_workbench_sessions s ON sec.session_id=s.id "
                "WHERE sec.writeguard_score IS NOT NULL ORDER BY sec.updated_at DESC LIMIT 20"
            ).fetchall()
        else:
            rows = db.execute(
                "SELECT s.rfi_number, sec.part, sec.item_number, sec.title, sec.status, sec.generation_count "
                "FROM rfi_workbench_sections sec JOIN rfi_workbench_sessions s ON sec.session_id=s.id "
                "ORDER BY sec.updated_at DESC LIMIT 20"
            ).fetchall()
        results = [dict(r) for r in rows]
        return jsonify({"results": results, "count": len(results)})
    except Exception as exc:
        return jsonify({"error": str(exc), "results": []}), 500
