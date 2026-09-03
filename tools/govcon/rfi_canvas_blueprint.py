"""RFI Response Workbench — Flask blueprint.

Routes:
  GET  /rfi/                                               — upload + session list
  POST /rfi/upload                                         — upload PDF, parse, create session
  POST /rfp/upload                                         — upload a solicitation; Section L seeds the
                                                             session; opportunity_id -> L/M matrix (rmf-rfp-01)
  GET  /rfi/<session_id>                                   — workbench for a session
  GET  /api/rfi/<session_id>/sections                      — list sections JSON
  POST /api/rfi/<session_id>/sections/<sid>/generate       — AI generate
  POST /api/rfi/<session_id>/sections/<sid>/hitl           — HITL action
  POST /api/rfi/<session_id>/sections/<sid>/writeguard     — WriteGuard
  POST /api/rfi/<session_id>/sections/<sid>/save           — save edits
  POST /api/rfi/<session_id>/export/<fmt>                  — export (docx|md); 409 if aggregation guard blocks
  POST /api/rfi/<session_id>/aggregation-guard/override     — clear an aggregation guard block (HITL)
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
    opportunities = _list_open_opportunities()
    return render_template(
        "rfi_canvas/index.html", sessions=sessions, profiles=profiles, opportunities=opportunities,
    )


def _list_open_opportunities(limit: int = 100) -> list:
    """Open proposal opportunities an RFP upload can build a matrix for.

    Best-effort: a database without the proposals schema yields [] and the
    upload form still works with a typed opportunity id.
    """
    try:
        db = wb.get_db()
        rows = db.execute(
            "SELECT id, solicitation_number, title FROM proposal_opportunities "
            "WHERE status NOT IN ('won', 'lost', 'no_bid', 'cancelled', 'submitted') "
            "ORDER BY updated_at DESC LIMIT %s",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:  # noqa: BLE001 - the form degrades to a text field
        logger.debug("open opportunities unavailable for the RFP upload form: %s", exc)
        return []


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
    parse_summary = wb.get_parse_summary(wb.get_session(session_id))
    return jsonify({
        "session_id": session_id,
        "redirect": f"/rfi/{session_id}",
        "parse_summary": parse_summary,
    })


@rfi_canvas_bp.route("/rfp/upload", methods=["POST"])
def rfp_upload():
    """Upload a solicitation (RFP/RFQ), seed a workbench session from Section L,
    and -- when an opportunity_id is supplied -- populate that opportunity's
    L/M compliance matrix through compliance_matrix_builder (rmf-rfp-01).

    Mirrors POST /rfi/upload. solicitation_parser had no route and no UI
    before this; its output reached nothing but response_drafter.
    """
    if "rfp_file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    f = request.files["rfp_file"]
    if not f.filename or not _allowed(f.filename):
        return jsonify({"error": "Only PDF and DOCX files are accepted"}), 400

    profile_name = request.form.get("profile", "own_company")
    opportunity_id = (request.form.get("opportunity_id") or "").strip() or None
    _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    upload_path = _UPLOAD_DIR / f.filename
    f.save(str(upload_path))

    parse_error = None
    try:
        from tools.govcon.solicitation_parser import parse_solicitation
        parsed = parse_solicitation(str(upload_path))
    except Exception as exc:
        logger.warning("RFP parse failed: %s", exc)
        parse_error = str(exc)
        parsed = {
            "source": "solicitation_document",
            "solicitation_number": "RFP-UNKNOWN",
            "title": f.filename,
            "section_l_instructions": [],
            "section_m_factors": [],
            "volume_structure": [],
        }

    session_id = wb.create_session(
        rfi_number=parsed.get("solicitation_number") or "RFP-UNKNOWN",
        rfi_title=parsed.get("title") or f.filename,
        profile_name=profile_name,
        upload_filename=f.filename,
        parsed_data=parsed,
    )
    parse_summary = wb.get_parse_summary(wb.get_session(session_id))

    matrix = None
    if opportunity_id:
        try:
            from tools.govcon.compliance_matrix_builder import ingest_solicitation
            matrix = ingest_solicitation(str(upload_path), opportunity_id, parsed=parsed)
        except Exception as exc:
            logger.warning("RFP matrix build failed for %s: %s", opportunity_id, exc)
            matrix = {"status": "error", "opportunity_id": opportunity_id, "error": str(exc)}

    return jsonify({
        "session_id": session_id,
        "redirect": f"/rfi/{session_id}",
        "document_kind": "rfp",
        "parse_summary": parse_summary,
        "parse_error": parse_error,
        "opportunity_id": opportunity_id,
        "matrix": matrix,
    })


@rfi_canvas_bp.route("/rfi/<session_id>")
def rfi_workbench_page(session_id):
    session = wb.get_session(session_id)
    if not session:
        abort(404)
    sections = wb.get_sections(session_id)
    profiles = wb.list_profiles()
    parse_summary = wb.get_parse_summary(session)
    return render_template(
        "rfi_canvas/workbench.html",
        session=session, sections=sections, profiles=profiles, parse_summary=parse_summary,
    )


# ── API: sections ─────────────────────────────────────────────────────────────

@rfi_canvas_bp.route("/api/rfi/<session_id>/sections")
def api_list_sections(session_id):
    return jsonify(wb.get_sections(session_id))


@rfi_canvas_bp.route("/api/rfi/<session_id>/generate-all", methods=["POST"])
def api_generate_all(session_id):
    session = wb.get_session(session_id)
    if not session:
        return jsonify({"error": "Session not found"}), 404
    status = wb.get_generate_all_status(session_id)
    if status.get("running"):
        return jsonify({"ok": False, "error": "Already running"}), 409
    import threading
    threading.Thread(
        target=wb.generate_all_sections,
        args=(session_id, session["profile_name"], session.get("parsed_data") or {}),
        daemon=True,
    ).start()
    return jsonify({"ok": True, "message": "Generate-all started"})


@rfi_canvas_bp.route("/api/rfi/<session_id>/generate-all/status")
def api_generate_all_status(session_id):
    return jsonify(wb.get_generate_all_status(session_id))


@rfi_canvas_bp.route("/api/rfi/<session_id>/generate-all/cancel", methods=["POST"])
def api_generate_all_cancel(session_id):
    wb.cancel_generate_all(session_id)
    return jsonify({"ok": True})


@rfi_canvas_bp.route("/api/rfi/<session_id>/accept-all", methods=["POST"])
def api_accept_all(session_id):
    """Bulk-accept every drafted section as final (skips pending/rejected)."""
    try:
        result = wb.accept_all_drafted(session_id)
        return jsonify({"ok": True, **result})
    except Exception as exc:
        logger.exception("Accept-all error for session %s", session_id)
        return jsonify({"error": str(exc)}), 500


@rfi_canvas_bp.route("/api/rfi/<session_id>/readiness")
def api_readiness(session_id):
    return jsonify(wb.get_session_readiness(session_id))


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
    if fmt not in ("docx", "md", "questions"):
        return jsonify({"error": "Supported formats: docx, md, questions"}), 400
    _body = request.get_json(silent=True) or {}
    force = bool(_body.get("force_placeholders"))
    force_refs = bool(_body.get("force_references"))
    try:
        if fmt == "questions":
            # Part 6 questions to the Government — separate ARC/email
            # submission, never part of the response document.
            path = wb.export_questions(session_id, force_placeholders=force)
        else:
            path = wb.assemble_and_export(
                session_id, fmt, force_placeholders=force, force_references=force_refs
            )
        return jsonify({"ok": True, "path": path, "download_url": f"/api/rfi/{session_id}/download/{fmt}"})
    except wb.ReferenceGateBlocked as exc:
        return (
            jsonify(
                {
                    "error": "Citation gate: section(s) cite RFI references that do not "
                             "exist — fix the citations or force",
                    "gate": "citation_guard",
                    "findings": exc.findings,
                }
            ),
            409,
        )
    except wb.PlaceholderGateBlocked as exc:
        return (
            jsonify(
                {
                    "error": "Placeholder gate: unresolved [PLACEHOLDER] tokens remain — resolve or force",
                    "gate": "placeholder_guard",
                    "findings": exc.findings,
                }
            ),
            409,
        )
    except wb.AggregationGuardBlocked as exc:
        return (
            jsonify(
                {
                    "error": "Aggregation guard: derived classification exceeds surface ceiling — review required",
                    "gate": "aggregation_guard",
                    "derived": exc.derived,
                    "fired_rules": exc.fired_rules,
                }
            ),
            409,
        )
    except Exception as exc:
        logger.exception("Export error for session %s", session_id)
        return jsonify({"error": str(exc)}), 500


@rfi_canvas_bp.route("/api/rfi/<session_id>/aggregation-guard/override", methods=["POST"])
def api_aggregation_guard_override(session_id):
    data = request.get_json(force=True) or {}
    comment = data.get("comment", "")
    try:
        resolved_by = getattr(request, "remote_user", None) or "unknown"
        count = wb.override_aggregation_guard(session_id, comment=comment, resolved_by=resolved_by)
        return jsonify({"ok": True, "resolved_count": count})
    except Exception as exc:
        logger.exception("Aggregation guard override error for session %s", session_id)
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


@rfi_canvas_bp.route("/api/rfi/<session_id>/engine-weights/availability")
def api_engine_availability(session_id):
    """Check which engine source tables exist and have rows."""
    try:
        from tools.govcon.rfi_engine_runner import check_source_availability
        return jsonify(check_source_availability(session_id))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@rfi_canvas_bp.route("/api/rfi/<session_id>/engines/run", methods=["POST"])
def api_engine_run(session_id):
    """Phase C: trigger an engine source population for this RFI session."""
    data = request.get_json(force=True) or {}
    source = data.get("source")
    topic = data.get("topic", "")
    if not source:
        return jsonify({"error": "source is required"}), 400
    try:
        from tools.govcon.rfi_engine_runner import trigger_engine_seed
        import threading
        threading.Thread(
            target=trigger_engine_seed,
            args=(session_id, source, topic),
            daemon=True,
        ).start()
        return jsonify({"ok": True, "message": f"Engine '{source}' seed started in background"})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


# ── Preview (assembled document, in-browser read-only view) ───────────────────

@rfi_canvas_bp.route("/rfi/<session_id>/preview")
def rfi_preview(session_id):
    session = wb.get_session(session_id)
    if not session:
        abort(404)
    # Preview mirrors the exported response document — Part 6 (questions to
    # the Government) is submitted separately and excluded here too.
    sections = [s for s in wb.get_sections(session_id) if s.get("part") != "part6"]
    # trust-cite-03: expose the evidence each section was drafted from as a
    # rendered Sources list (parsed from sources_json here so the template stays simple).
    for s in sections:
        s["source_labels"] = wb._section_source_labels(s)
    profile = wb._load_profile(session.get("profile_name", "own_company"))
    annex = wb.build_compliance_annex(sections)
    return render_template(
        "rfi_canvas/preview.html",
        session=session,
        sections=sections,
        profile=profile,
        annex=annex,
    )


# ── API: requirements ─────────────────────────────────────────────────────────

@rfi_canvas_bp.route("/api/rfi/<session_id>/sections/<section_id>/history")
def api_section_history(session_id, section_id):
    return jsonify(wb.get_section_history(section_id))


@rfi_canvas_bp.route("/api/rfi/<session_id>/sections/<section_id>/ace-feedback")
def api_ace_feedback(session_id, section_id):
    section = wb.get_section(section_id)
    if not section:
        return jsonify({"error": "not found"}), 404
    feedback = section.get("ace_feedback")
    if feedback is None:
        return jsonify({"status": "pending"})
    return jsonify({"status": "ready", "feedback": feedback})


@rfi_canvas_bp.route("/api/rfi/<session_id>/sections/<section_id>/ace-feedback/run", methods=["POST"])
def api_run_ace_feedback(session_id, section_id):
    import threading
    threading.Thread(
        target=wb._ace_editor_review_background, args=(section_id,), daemon=True
    ).start()
    return jsonify({"ok": True, "status": "running"})


@rfi_canvas_bp.route("/api/rfi/<session_id>/sections/<section_id>/ace-reviewer/run", methods=["POST"])
def api_ace_reviewer_run(session_id, section_id):
    import threading
    threading.Thread(target=wb._ace_reviewer_pass_background, args=(section_id,), daemon=True).start()
    return jsonify({"ok": True, "message": "Reviewer pass started"})


@rfi_canvas_bp.route("/api/rfi/<session_id>/consistency")
def api_consistency(session_id):
    return jsonify(wb.check_cross_section_consistency(session_id))


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


# ── API: deadline countdown ───────────────────────────────────────────────────

@rfi_canvas_bp.route("/api/rfi/<session_id>/deadline")
def api_get_deadline(session_id):
    return jsonify(wb.get_deadline_info(session_id))


@rfi_canvas_bp.route("/api/rfi/<session_id>/deadline", methods=["POST"])
def api_save_deadline(session_id):
    data = request.get_json(force=True) or {}
    deadline = (data.get("deadline") or "").strip()
    if not deadline:
        return jsonify({"error": "deadline is required (YYYY-MM-DD or MM/DD/YYYY)"}), 400
    return jsonify(wb.save_session_deadline(session_id, deadline))


# ── API: competitive differentiator ───────────────────────────────────────────

@rfi_canvas_bp.route("/api/rfi/<session_id>/why-us", methods=["POST"])
def api_generate_why_us(session_id):
    data = request.get_json(force=True) or {}
    try:
        session = wb.get_session(session_id)
        profile_name = data.get("profile_name") or (session.get("profile_name") if session else "own_company")
        result = wb.generate_why_us(
            session_id,
            profile_name=profile_name or "own_company",
            competitor_name=data.get("competitor_name", ""),
        )
        return jsonify({"ok": True, **result})
    except Exception as exc:
        logger.exception("Why Us generation failed for session %s", session_id)
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


# ── Capture strategy + evidence library ───────────────────────────────────────
#
# These live on the RFI canvas blueprint rather than in a canvas of their own:
# the strategy and the evidence corpus are shared by /rfi and /proposals, and the
# workflow (Set Strategy -> Attach Evidence -> Generate -> Approve) reads as one
# loop only if the user never leaves it.

_ALLOWED_EVIDENCE_EXT = {".pdf", ".docx", ".doc", ".txt", ".md"}
_EVIDENCE_UPLOAD_DIR = _ROOT / ".tmp" / "govcon_evidence"
_EVIDENCE_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


@rfi_canvas_bp.route("/capture/strategy")
def capture_strategy_page():
    from tools.govcon.capture_strategy import get_company_strategy

    return render_template("rfi_canvas/strategy.html", strategy=get_company_strategy())


@rfi_canvas_bp.route("/capture/evidence")
def capture_evidence_page():
    return render_template("rfi_canvas/evidence.html")


@rfi_canvas_bp.route("/api/capture/strategy")
def api_get_capture_strategy():
    from tools.govcon.capture_strategy import get_company_strategy

    try:
        return jsonify(get_company_strategy())
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@rfi_canvas_bp.route("/api/capture/strategy", methods=["PUT"])
def api_save_capture_strategy():
    from tools.govcon.capture_strategy import save_company_strategy

    data = request.get_json(force=True) or {}
    try:
        return jsonify(save_company_strategy(data, actor=request.remote_addr or "dashboard"))
    except Exception as exc:
        logger.warning("Could not save capture strategy: %s", exc)
        return jsonify({"error": str(exc)}), 500


@rfi_canvas_bp.route("/api/capture/strategy/style-check", methods=["POST"])
def api_capture_strategy_style_check():
    """Check a theme statement against the company style guide as the user types.

    A theme containing a banned phrase would otherwise be injected verbatim into
    every narrative prompt, which is exactly the puffery forbidden_phrases exists
    to suppress. Catching it here is cheaper than catching it in the draft.
    """
    from tools.govcon.rfi_style_engine import check_style_compliance, get_company_style_guide

    text = (request.get_json(force=True) or {}).get("text", "")
    try:
        guide = dict(get_company_style_guide())
        # Only phrase policy applies to a theme statement; headings/classification
        # markings are properties of a rendered section, not of a one-line claim.
        guide.pop("required_headings", None)
        result = check_style_compliance(text, guide)
        result["findings"] = [f for f in result["findings"] if f["type"] == "forbidden_phrase"]
        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc), "findings": []}), 500


@rfi_canvas_bp.route("/api/rfi/<session_id>/theme-coverage")
def api_theme_coverage(session_id):
    """Advisory theme x part coverage. Never blocks; answers 'is my message uniform?'"""
    from tools.govcon.capture_strategy import policy_for, resolve_strategy, theme_coverage

    try:
        strategy = resolve_strategy(session_id=session_id)
        sections = wb.get_sections(session_id)
        report = theme_coverage(sections, strategy)
        report["sections"] = [
            {
                "item_number": s.get("item_number"),
                "title": s.get("title"),
                "policy": policy_for(s.get("item_number", "")),
            }
            for s in sections
        ]
        report["themes"] = [
            {"id": f"win_themes:{i}", "statement": t.get("statement", "")}
            for i, t in enumerate(strategy.get("win_themes") or [])
        ]
        return jsonify(report)
    except Exception as exc:
        logger.warning("theme-coverage failed for %s: %s", session_id, exc)
        return jsonify({"error": str(exc), "findings": [], "matrix": {}}), 500


@rfi_canvas_bp.route("/api/rfi/<session_id>/sections/<section_id>/provenance")
def api_section_provenance(section_id, session_id):
    """The evidence behind a generated section.

    sources_json has been persisted since migration 249 and shown nowhere. A writer
    who can see why the model said something can trust or correct it.
    """
    try:
        section = wb.get_section(section_id) or {}
        return jsonify(
            {
                "sources": wb._section_source_labels(section),
                "evidence": wb.get_section_evidence_status(section_id),
            }
        )
    except Exception as exc:
        return jsonify({"error": str(exc), "sources": []}), 500


@rfi_canvas_bp.route("/api/capture/evidence")
def api_list_evidence():
    from tools.govcon.evidence_corpus import corpus_stats, list_corpus

    try:
        return jsonify({"items": list_corpus(), "stats": corpus_stats()})
    except Exception as exc:
        logger.warning("Could not list evidence corpus: %s", exc)
        return jsonify({"error": str(exc), "items": [], "stats": {}}), 500


@rfi_canvas_bp.route("/api/capture/evidence/upload", methods=["POST"])
def api_upload_evidence():
    """Upload a prior submission. Hashes first so a duplicate is reported, not re-ingested."""
    from tools.govcon.evidence_corpus import ingest_upload

    upload = request.files.get("file")
    if not upload or not upload.filename:
        return jsonify({"error": "no file"}), 400
    if Path(upload.filename).suffix.lower() not in _ALLOWED_EVIDENCE_EXT:
        return jsonify({"error": f"unsupported type; allowed: {sorted(_ALLOWED_EVIDENCE_EXT)}"}), 400

    dest = _EVIDENCE_UPLOAD_DIR / Path(upload.filename).name
    upload.save(dest)
    try:
        result = ingest_upload(
            str(dest),
            title=request.form.get("title") or Path(upload.filename).stem,
            doc_type=request.form.get("doc_type", "proposal"),
            outcome=request.form.get("outcome", "unknown"),
            solicitation_number=request.form.get("solicitation_number", ""),
            uploaded_by=request.remote_addr or "",
        )
        status = 409 if result.get("status") == "duplicate" else 200
        return jsonify(result), status
    except Exception as exc:
        logger.warning("Evidence upload failed: %s", exc)
        return jsonify({"error": str(exc)}), 500
