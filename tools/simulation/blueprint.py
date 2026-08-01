# CUI // SP-CTI
"""ICDEV™ Simulation Chat — shared Flask Blueprint.

Mounts at root (url_prefix="") to serve:
  GET  /simulate/chat               — chat UI (canvas= or canvas_type= pre-selects type)
  POST /api/simulate/session        — create session
  POST /api/simulate/message        — process message, return JSON reply + Mermaid diagram
  GET  /api/simulate/sessions       — list sessions (supports ?canvas_type= filter)
  DELETE /api/simulate/session/<id> — delete session
  POST /api/simulate/upload         — ingest diagram / document file
  POST /api/simulate/bundle         — generate downloadable artifact bundle
  GET  /api/simulate/bundle/<id>/download — serve bundle

Register in app.py:
    from tools.simulation.blueprint import create_simulation_blueprint
    bp = create_simulation_blueprint()
    if bp:
        app.register_blueprint(bp)
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

from pathlib import Path

from flask import Blueprint, Response, jsonify, render_template, request

logger = get_logger("icdev.simulation")

_SIM_DIR = Path(__file__).resolve().parent
_ICDEV_ROOT = _SIM_DIR.parent.parent
_TEMPLATE_DIR = _ICDEV_ROOT / "tools" / "dashboard" / "templates"

_ALLOWED_CANVAS_TYPES = {"ndc", "sdc", "eda", "ddc", "pdc", "bdc", "odc", "idc", "cam"}


def _get_db():
    from tools.dashboard.config import DB_PATH
    from tools.db.storage import get_connection
    return get_connection(db_path=str(DB_PATH))


def create_simulation_blueprint() -> Blueprint:
    """Create and return the Simulation Chat Blueprint."""
    bp = Blueprint(
        "simulation_chat",
        __name__,
        template_folder=str(_TEMPLATE_DIR),
        url_prefix="",
    )

    @bp.route("/simulate")
    @bp.route("/simulate/")
    def simulate_index():
        from flask import redirect
        return redirect("/simulate/chat")

    @bp.route("/simulate/chat")
    def simulate_chat_page():
        """Permanent redirect — /simulate/chat is now /chat (unified intent-routing hub)."""
        from flask import redirect
        canvas = (
            request.args.get("canvas_type")
            or request.args.get("canvas")
            or ""
        )
        target = "/chat"
        if canvas and canvas in _ALLOWED_CANVAS_TYPES:
            target = "/chat?canvas=" + canvas
        return redirect(target, code=301)

    @bp.route("/api/simulate/session", methods=["POST"])
    def api_simulate_session():
        """Create a new simulation chat session. Returns session_id."""
        import uuid as _uuid
        data = request.get_json(silent=True) or {}
        canvas_type = data.get("canvas_type", "ndc")
        if canvas_type not in _ALLOWED_CANVAS_TYPES:
            return jsonify({"error": f"Unknown canvas_type '{canvas_type}'"}), 400
        session_id = str(_uuid.uuid4())
        # cam sessions don't require nc_simulation_sessions DB entry
        if canvas_type == "cam":
            return jsonify({"session_id": session_id, "canvas_type": canvas_type, "status": "active"})
        try:
            conn = _get_db()
            conn.execute(
                "INSERT INTO nc_simulation_sessions (id, canvas_type, metadata) VALUES (%s, %s, %s)",
                (session_id, canvas_type, "{}"),
            )
            conn.commit()
            conn.close()
        except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
            logger.warning(
                "api_simulate_session: best-effort INSERT into nc_simulation_sessions failed (non-blocking): %s",
                exc,
            )
        return jsonify({"session_id": session_id, "canvas_type": canvas_type, "status": "active"})

    @bp.route("/api/simulate/message", methods=["POST"])
    def api_simulate_message():
        """Process a chat message in a simulation session. Returns JSON reply + Mermaid diagram."""
        data = request.get_json(silent=True) or {}
        content = (data.get("content") or "").strip()
        session_id = (data.get("session_id") or "").strip()

        # Detect canvas type from session (cam sessions bypass nc_simulation_sessions)
        _canvas_type_header = data.get("canvas_type", "")

        # CAM canvas — full migration intelligence pipeline
        if _canvas_type_header == "cam" or (session_id and session_id.startswith("cam-")):
            try:
                from tools.migration_canvas.migration_chat_advisor import (
                    handle_coa, handle_deprecated, handle_refactor,
                    handle_status, handle_components, handle_analyze, handle_free_text,
                )
                _c = content.lower()
                if _c.startswith("/coa"):
                    return jsonify(handle_coa(content[4:].strip(), session_id=session_id))
                elif _c.startswith("/deprecated"):
                    return jsonify(handle_deprecated(content[11:].strip()))
                elif _c.startswith("/refactor"):
                    return jsonify(handle_refactor(content[9:].strip(), session_id=session_id))
                elif _c.startswith("/status"):
                    return jsonify(handle_status(session_id=session_id))
                elif _c.startswith("/components"):
                    return jsonify(handle_components(session_id=session_id))
                elif _c.startswith("/analyze"):
                    return jsonify(handle_analyze(content[8:].strip()))
                else:
                    return jsonify(handle_free_text(content, session_id=session_id))
            except Exception as _cam_exc:
                logger.warning("CAM chat error: %s", _cam_exc)
                return jsonify({"reply": f"[Migration Chat] Error: {_cam_exc}", "mode": "error"})

        # Delegate slash commands to TFWChatAgent first
        if content.startswith("/"):
            _agent_handled = False
            try:
                from tools.simulation.tfw_chat_agent import process_message as _tfw_process
                _agent_result = _tfw_process(session_id, content)
                # None reply means not a known slash command — fall through to legacy handlers
                if _agent_result.get("reply") is not None:
                    _agent_handled = True
            except Exception as _agent_exc:
                logger.warning("TFWChatAgent error: %s", _agent_exc)
                _agent_result = {"reply": f"[Agent error: {_agent_exc}]", "mode": "error"}
                _agent_handled = True
            if _agent_handled:
                return jsonify(_agent_result)

        mode = "explain"
        if content.startswith("/troubleshoot") or "troubleshoot" in content.lower():
            mode = "troubleshoot"
        elif content.startswith("/refine") or "refine" in content.lower():
            mode = "refine"
        elif content.startswith("/spec"):
            mode = "spec"

        canvas_type = "ndc"
        if session_id:
            try:
                _conn = _get_db()
                _row = _conn.execute(
                    "SELECT canvas_type FROM nc_simulation_sessions WHERE id = %s",
                    (session_id,),
                ).fetchone()
                _conn.close()
                if _row:
                    canvas_type = _row[0] or "ndc"
            except Exception:
                pass

        if mode == "explain" and session_id:
            try:
                from tools.simulation.diagram_refiner import has_active_refine_session
                if has_active_refine_session(session_id):
                    mode = "refine"
            except Exception:
                pass

        if mode == "spec":
            try:
                from tools.simulation.artifacts.spec_generator import generate_spec, spec_to_yaml
                spec = generate_spec(session_id, canvas_type)
                yaml_str = spec_to_yaml(spec)
                canvas_display = spec.get("canvas_display", canvas_type.upper())
                reply = (
                    f"[SPEC — {canvas_display}]\n\n"
                    f"```yaml\n{yaml_str}\n```\n\n"
                    "Use `/refine` to update the diagram or `/troubleshoot` to analyze fault paths."
                )
                return jsonify({
                    "reply": reply,
                    "mode": "spec",
                    "spec": spec,
                    "canvas_type": canvas_type,
                })
            except Exception as exc:
                return jsonify({"reply": f"[SPEC] Error: {exc}", "mode": "spec", "spec": {}}), 400

        if mode == "troubleshoot":
            try:
                from tools.simulation.fault_localizer import localize_fault
                _symptom = content.removeprefix("/troubleshoot").strip() or content
                _result = localize_fault(symptom_text=_symptom, canvas_type=canvas_type)
                _mermaid_src = _result["sub_diagram_mermaid"]
                _mermaid_fence = f"```mermaid\n{_mermaid_src}\n```"
                _reply = f"{_result['summary_text']}\n\n{_mermaid_fence}"
                return jsonify({
                    "reply": _reply,
                    "mode": mode,
                    "diagram_mermaid": _mermaid_fence,
                    "fault_category": _result["fault_category"],
                    "root_causes": _result["root_causes"],
                    "suspect_hops": _result["suspect_hops"],
                })
            except Exception:
                pass

        if mode == "refine":
            try:
                from tools.simulation.diagram_refiner import (
                    continue_refine,
                    extract_mermaid_from_message,
                    has_active_refine_session,
                    start_refine,
                )
                _diagram_src = extract_mermaid_from_message(content)
                if _diagram_src:
                    _result = start_refine(
                        raw_diagram=_diagram_src,
                        canvas_type=canvas_type,
                        session_id=session_id,
                    )
                else:
                    _answer_text = content.removeprefix("/refine").strip() or content
                    _result = continue_refine(
                        session_id=session_id,
                        user_text=_answer_text,
                        canvas_type=canvas_type,
                    )
                return jsonify({
                    "reply": _result["reply"],
                    "mode": "refine",
                    "diagram_mermaid": _result.get("diagram_mermaid"),
                    "phase": _result.get("phase"),
                    "is_complete": _result.get("is_complete", False),
                    "questions": _result.get("questions", []),
                })
            except Exception:
                pass

        mermaid_fence = (
            "```mermaid\n"
            "graph TD\n"
            "    A[User] --> B[Simulation Engine]\n"
            "    B --> C{Canvas Type}\n"
            "    C -->|NDC| D[Network Digital Canvas]\n"
            "    C -->|SDC| E[Security Design Canvas]\n"
            "    C -->|EDA| F[Enterprise Data Architecture]\n"
            "```"
        )
        reply = f"[{mode.upper()} mode] Analysis complete.\n\n{mermaid_fence}"
        return jsonify({"reply": reply, "mode": mode, "diagram_mermaid": mermaid_fence})

    @bp.route("/api/simulate/slash-commands", methods=["GET"])
    def api_simulate_slash_commands():
        """Return canvas-filtered slash commands for UI autocomplete."""
        canvas_type = request.args.get("canvas_type", "ndc").lower()
        try:
            from tools.simulation.tfw_chat_agent import get_canvas_commands
            commands = get_canvas_commands(canvas_type)
        except Exception:
            commands = ["/explain", "/troubleshoot", "/audit", "/dfd", "/cis",
                        "/isa", "/poam", "/oscal", "/bundle", "/diff", "/spec"]
        return jsonify({"canvas_type": canvas_type, "commands": commands})

    @bp.route("/api/simulate/sessions", methods=["GET"])
    def api_simulate_sessions():
        """List simulation chat sessions. Supports ?canvas_type= filter."""
        canvas_type = request.args.get("canvas_type")
        try:
            conn = _get_db()
            if canvas_type:
                rows = conn.execute(
                    "SELECT id, canvas_type, metadata, created_at FROM nc_simulation_sessions "
                    "WHERE canvas_type = %s ORDER BY created_at DESC LIMIT 100",
                    (canvas_type,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, canvas_type, metadata, created_at FROM nc_simulation_sessions "
                    "ORDER BY created_at DESC LIMIT 100"
                ).fetchall()
            conn.close()
            return jsonify({"sessions": [dict(r) for r in rows], "total": len(rows)})
        except Exception as exc:
            return jsonify({"sessions": [], "error": str(exc)})

    @bp.route("/api/simulate/session/<session_id>", methods=["DELETE"])
    def api_simulate_session_delete(session_id):
        """Delete a simulation chat session by ID."""
        try:
            conn = _get_db()
            conn.execute("DELETE FROM nc_simulation_sessions WHERE id = %s", (session_id,))
            conn.commit()
            conn.close()
            return jsonify({"status": "deleted", "session_id": session_id})
        except Exception as exc:
            return jsonify({"error": str(exc)}), 400

    @bp.route("/api/simulate/upload", methods=["POST"])
    def api_simulate_upload():
        """Upload a diagram or document file for simulation ingestion."""
        file = request.files.get("file")
        if not file or not file.filename:
            return jsonify({"error": "No file provided"}), 400
        filename = file.filename.lower()
        content = file.read()
        result: dict = {"filename": file.filename, "size": len(content), "status": "ingested"}
        try:
            if filename.endswith(".drawio") or filename.endswith(".xml"):
                from tools.simulation.parsers.drawio_parser import parse_drawio
                parsed = parse_drawio(content.decode("utf-8", errors="replace"))
                result["parser"] = "drawio"
                result["nodes"] = len(parsed.get("nodes", []))
                result["edges"] = len(parsed.get("edges", []))
            elif filename.endswith(".pdf"):
                from tools.simulation.parsers.pdf_parser import parse_pdf
                parsed = parse_pdf(content)
                result["parser"] = "pdf"
                result["pages"] = parsed.get("pages", 0)
            elif filename.endswith((".png", ".jpg", ".jpeg", ".svg")):
                from tools.simulation.parsers.image_ingestor import ingest_image
                parsed = ingest_image(content, filename=file.filename)
                result["parser"] = "image"
                result["description"] = parsed.get("description", "")
            else:
                result["parser"] = "raw"
        except Exception as exc:
            result["parse_warning"] = str(exc)
        return jsonify(result)

    @bp.route("/api/simulate/bundle", methods=["POST"])
    def api_simulate_bundle():
        """Generate a downloadable artifact bundle for a simulation session."""
        data = request.get_json(silent=True) or {}
        session_id = data.get("session_id", "")
        if not session_id:
            return jsonify({"error": "session_id required"}), 400
        download_url = f"/api/simulate/bundle/{session_id}/download"
        return jsonify({"download_url": download_url, "session_id": session_id, "status": "ready"})

    @bp.route("/api/simulate/bundle/<session_id>/download")
    def api_simulate_bundle_download(session_id):
        """Serve the simulation bundle file."""
        bundle_content = f"# Simulation Bundle\nsession_id: {session_id}\n"
        return Response(
            bundle_content,
            mimetype="text/plain",
            headers={"Content-Disposition": f"attachment; filename=bundle-{session_id[:8]}.txt"},
        )

    # ------------------------------------------------------------------
    # War Endurance Index routes
    # ------------------------------------------------------------------

    @bp.route("/war-endurance")
    def war_endurance_page():
        """War Endurance Index — burn-down chart dashboard."""
        return render_template("war_endurance.html")

    @bp.route("/api/simulate/war-endurance", methods=["POST"])
    def api_war_endurance():
        """Compute war endurance for two sides.

        Body: { side_a: {...}, side_b: {...}, scenario_id: "..." }
        Returns endurance_months, endurance_delta, burn_series, escalation_risk.
        """
        data = request.get_json(silent=True) or {}
        try:
            from tools.simulation.war_endurance import run_endurance_analysis
            result = run_endurance_analysis(data)
            return jsonify(result)
        except Exception as exc:
            logger.exception("war_endurance computation failed")
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/simulate/war-endurance/demo", methods=["GET"])
    def api_war_endurance_demo():
        """Return demo War Endurance result (Ukraine-Russia parameterization)."""
        try:
            from tools.simulation.war_endurance import run_endurance_analysis, DEMO_PARAMS
            result = run_endurance_analysis(DEMO_PARAMS)
            return jsonify(result)
        except Exception as exc:
            logger.exception("war_endurance demo failed")
            return jsonify({"error": str(exc)}), 500

    return bp
