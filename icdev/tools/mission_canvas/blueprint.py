# CUI // SP-CTI
"""Mission Canvas — Flask Blueprint.

Mounted at /mission-canvas/ inside the ICDEV dashboard.
Feature flag: ICDEV_MISSION_CANVAS_ENABLED.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import hmac
import os
from functools import wraps
from pathlib import Path

from flask import Blueprint, jsonify, redirect, render_template, request, session

logger = get_logger("icdev.mission_canvas")

_MC_DIR = Path(__file__).resolve().parent
_ICDEV_ROOT = _MC_DIR.parent.parent.parent
_TEMPLATE_DIR = _ICDEV_ROOT / "tools" / "dashboard" / "templates"


def _get_conn():
    # cnr-mc-01: use the Mission Canvas connection (RLS disabled, dedicated
    # mission_* tables), NOT the main icdev DB — otherwise canvas data is
    # unreachable and the global RLS predicate raises UndefinedColumn on PG.
    from tools.mission_canvas.db.init_db import get_connection

    return get_connection()


# ── Blueprint Factory ──────────────────────────────────────────────────────────


def create_mission_canvas_blueprint():
    """Create and return the Mission Canvas Blueprint."""
    enabled = os.environ.get("ICDEV_MISSION_CANVAS_ENABLED", "true").lower()
    if enabled not in ("true", "1", "yes"):
        logger.info("Mission Canvas disabled (ICDEV_MISSION_CANVAS_ENABLED=%s)", enabled)
        return None

    bp = Blueprint(
        "mission_canvas",
        __name__,
        template_folder=str(_TEMPLATE_DIR),
    )

    # ── Auth decorator ───────────────────────────────────────────────────
    def mc_login_required(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if not session.get("user_id"):
                # ICDEV_AUTH_BYPASS: explicit test-only opt-in — bypass unchanged.
                if os.environ.get("ICDEV_AUTH_BYPASS"):
                    session["user_id"] = "e2e-bypass"
                    return f(*args, **kwargs)
                # ICDEV_DASHBOARD_API_KEY: presented-key semantics. Authenticate
                # ONLY if the request presents the key (header X-ICDEV-API-Key or
                # Authorization: Bearer), compared with constant-time
                # hmac.compare_digest. A merely-set env var does NOT auto-auth.
                api_key = os.environ.get("ICDEV_DASHBOARD_API_KEY", "")
                if api_key:
                    presented = request.headers.get("X-ICDEV-API-Key", "")
                    if not presented:
                        auth_header = request.headers.get("Authorization", "")
                        if auth_header.startswith("Bearer "):
                            presented = auth_header[len("Bearer "):].strip()
                    if presented and hmac.compare_digest(presented, api_key):
                        session["user_id"] = "api-key"
                        return f(*args, **kwargs)
                if request.is_json or request.path.startswith("/api/"):
                    return jsonify({"error": "Authentication required"}), 401
                return redirect("/login")
            return f(*args, **kwargs)

        return decorated

    # ════════════════════════════════════════════════════════════════════
    # PAGE ROUTES
    # ════════════════════════════════════════════════════════════════════

    @bp.route("/")
    @mc_login_required
    def mc_index():
        # cnr-mc-01: query the real Mission Canvas tables (mission_designs and
        # friends), not the nonexistent mission_canvas_sessions table. Table
        # existence is handled gracefully so a not-yet-initialized canvas renders.
        conn = _get_conn()
        designs: list[dict] = []
        counts = {"designs": 0, "twins": 0, "evidence": 0, "posture": 0}
        try:
            designs = [
                dict(r)
                for r in conn.execute(
                    "SELECT id, name, description, design_type, classification, "
                    "created_at, updated_at "
                    "FROM mission_designs ORDER BY updated_at DESC LIMIT 20"
                ).fetchall()
            ]
        except Exception:
            designs = []
        for label, table in (
            ("designs", "mission_designs"),
            ("twins", "mission_twin_snapshots"),
            ("evidence", "mission_evidence"),
            ("posture", "mission_security_posture"),
        ):
            try:
                row = conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()
                counts[label] = (row["c"] if row else 0) or 0
            except Exception:
                counts[label] = 0
        try:
            conn.close()
        except Exception:
            pass

        from tools.mission_canvas.constants import OBJECT_TYPES

        return render_template(
            "mission_canvas/index.html",
            designs=designs,
            counts=counts,
            objects=OBJECT_TYPES,
        )

    @bp.route("/detail/<mission_id>")
    @mc_login_required
    def mc_detail(mission_id):
        return render_template(
            "mission_canvas/detail.html",
            mission_id=mission_id,
        )

    # ════════════════════════════════════════════════════════════════════
    # API ROUTES — 13 capabilities
    # ════════════════════════════════════════════════════════════════════

    @bp.route("/api/orchestrate", methods=["POST"])
    @mc_login_required
    def api_orchestrate():
        payload = request.get_json(force=True) or {}
        from tools.mission_canvas.orchestrator import run_mission_team

        result = run_mission_team(
            mission_id=payload.get("mission_id", ""),
            objective=payload.get("objective", ""),
            agents=payload.get("agents"),
            context=payload.get("context"),
        )
        return jsonify(result)

    @bp.route("/api/twin", methods=["GET"])
    @mc_login_required
    def api_twin():
        mission_id = request.args.get("mission_id", "")
        from tools.mission_canvas.twin import take_mission_twin

        return jsonify(take_mission_twin(mission_id))

    @bp.route("/api/evidence", methods=["GET"])
    @mc_login_required
    def api_evidence():
        artifact_id = request.args.get("artifact_id", "")
        from tools.mission_canvas.evidence import get_evidence_chain

        return jsonify({"chain": get_evidence_chain(artifact_id)})

    @bp.route("/api/correlate", methods=["POST"])
    @mc_login_required
    def api_correlate():
        payload = request.get_json(force=True) or {}
        from tools.mission_canvas.correlator import correlate_events

        result = correlate_events(
            mission_id=payload.get("mission_id", ""),
            event_stream=payload.get("events", []),
            lookback_hours=payload.get("lookback_hours", 24),
        )
        return jsonify(result)

    @bp.route("/api/discover", methods=["GET"])
    @mc_login_required
    def api_discover():
        mission_id = request.args.get("mission_id", "")
        scope = request.args.get("scope")
        from tools.mission_canvas.discovery import discover_components

        return jsonify(discover_components(mission_id, scope))

    @bp.route("/api/conflicts", methods=["POST"])
    @mc_login_required
    def api_conflicts():
        payload = request.get_json(force=True) or {}
        from tools.mission_canvas.conflict_resolver import detect_conflicts

        result = detect_conflicts(
            mission_id=payload.get("mission_id", ""),
            sources=payload.get("sources", []),
            rules=payload.get("rules"),
        )
        return jsonify(result)

    @bp.route("/api/portfolio", methods=["GET"])
    @mc_login_required
    def api_portfolio():
        mission_id = request.args.get("mission_id", "")
        from tools.mission_canvas.portfolio import optimize_portfolio

        return jsonify(optimize_portfolio(mission_id))

    @bp.route("/api/security-posture", methods=["GET"])
    @mc_login_required
    def api_security_posture():
        mission_id = request.args.get("mission_id", "")
        from tools.mission_canvas.security_posture import get_security_posture

        return jsonify(get_security_posture(mission_id))

    @bp.route("/api/ai-trust", methods=["GET"])
    @mc_login_required
    def api_ai_trust():
        mission_id = request.args.get("mission_id", "")
        model_id = request.args.get("model_id")
        use_cot = request.args.get("use_cot", "").lower() in ("true", "1", "yes")
        chain_mode = "cot" if use_cot else ""
        from tools.mission_canvas.ai_trust import assess_ai_trust

        result = assess_ai_trust(mission_id, model_id)
        result["chain_mode"] = chain_mode
        return jsonify(result)

    @bp.route("/api/narrative", methods=["POST"])
    @mc_login_required
    def api_narrative():
        payload = request.get_json(force=True) or {}
        from tools.mission_canvas.narrative import generate_narrative

        result = generate_narrative(
            mission_id=payload.get("mission_id", ""),
            topic=payload.get("topic", ""),
            sources=payload.get("sources"),
            classification=payload.get("classification", "CUI"),
            max_words=payload.get("max_words", 250),
        )
        return jsonify(result)

    @bp.route("/api/cicd", methods=["GET"])
    @mc_login_required
    def api_cicd():
        mission_id = request.args.get("mission_id", "")
        from tools.mission_canvas.cicd_bridge import get_cicd_status

        return jsonify(get_cicd_status(mission_id))

    @bp.route("/api/cicd/deploy", methods=["POST"])
    @mc_login_required
    def api_cicd_deploy():
        payload = request.get_json(force=True) or {}
        from tools.mission_canvas.cicd_bridge import trigger_deployment

        result = trigger_deployment(
            mission_id=payload.get("mission_id", ""),
            artifact_id=payload.get("artifact_id", ""),
            env=payload.get("environment", "staging"),
        )
        return jsonify(result)

    @bp.route("/api/iqe-query", methods=["POST"])
    @mc_login_required
    def api_iqe_query():
        """IQE structured query — translate NL to IQE and optionally execute against mission collections."""
        from tools.iqe.nl_to_iqe import nl_to_iqe
        from tools.iqe.parser import IQESyntaxError, parse
        from tools.iqe.executor import execute_query
        import tools.iqe.adapters.mission_canvas  # noqa: F401 — registers mission.* collections

        data = request.get_json(silent=True) or {}
        question = (data.get("question") or "").strip()
        if not question:
            return jsonify({"error": "question is required"}), 400
        execute = data.get("execute", True)

        collections = ["mission.sessions", "mission.twins", "mission.evidence", "mission.alerts"]
        translation = nl_to_iqe(question, collections)
        iqe_str = translation.get("iqe", "")
        explanation = translation.get("explanation", "")

        if not execute:
            return jsonify({"iqe": iqe_str, "explanation": explanation}), 200

        try:
            ast = parse(iqe_str)
        except IQESyntaxError as exc:
            return jsonify({"error": f"IQE parse error: {exc}", "iqe": iqe_str, "explanation": explanation}), 400

        # cnr-mc-02: adapters open their own Mission Canvas connection (conn=None),
        # so mission.* collections resolve against the real canvas tables.
        try:
            rows = execute_query(ast, conn=None)
        except Exception as exc:
            logger.warning("IQE execution failed: %s", exc)
            return jsonify({"error": str(exc), "iqe": iqe_str, "explanation": explanation}), 500

        return jsonify({
            "iqe": iqe_str,
            "explanation": explanation,
            "results": rows,
            "row_count": len(rows),
        }), 200

    return bp
