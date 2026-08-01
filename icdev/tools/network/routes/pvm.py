# CUI // SP-CTI
"""Network Canvas — PVM Predictive Vulnerability Management routes.

Provides API and page routes for:
  - Attack surface mapping (NQE-correlated)
  - Risk trajectory prediction (time-series)
  - Automated triage queue with HITL approval
  - AI-assisted patch planning
  - Vulnerability Intelligence dashboard page
"""

from __future__ import annotations

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.network.routes.pvm")


def register_pvm_routes(bp):
    """Register PVM Predictive Vulnerability Management routes on the NDC blueprint."""
    from flask import jsonify, render_template, request

    from tools.network.db.init_db import get_connection as _nc_conn  # noqa: F401

    def _now() -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat()

    # ── Page ─────────────────────────────────────────────────────────────

    @bp.route("/network/vulnerability-intelligence")
    def pvm_dashboard():
        """Vulnerability Intelligence dashboard — 4-panel predictive UI."""
        try:
            from tools.network.advisory import list_advisories
            advisories_count = len(list_advisories(status="open", limit=1000))
        except Exception:
            advisories_count = 0

        try:
            conn = _nc_conn()
            try:
                pending_triage = conn.execute(
                    "SELECT COUNT(*) FROM nc_triage_queue WHERE status='pending'"
                ).fetchone()[0]
            finally:
                conn.close()
        except Exception:
            pending_triage = 0

        try:
            conn = _nc_conn()
            try:
                open_predictions = conn.execute(
                    "SELECT COUNT(DISTINCT advisory_id) FROM nc_vuln_predictions"
                ).fetchone()[0]
            finally:
                conn.close()
        except Exception:
            open_predictions = 0

        return render_template(
            "network/vuln_intelligence.html",
            title="Vulnerability Intelligence",
            advisories_count=advisories_count,
            pending_triage=pending_triage,
            open_predictions=open_predictions,
        )

    # ── Attack Surface ────────────────────────────────────────────────────

    @bp.route("/api/nqe/attack-surface", methods=["POST"])
    def pvm_map_attack_surface():
        """Trigger a full attack surface mapping pass."""
        try:
            from tools.network.attack_surface_mapper import map_attack_surface
            body = request.json or {}
            network_id = body.get("network_id")
            result = map_attack_surface(network_id=network_id)
            return jsonify(result)
        except Exception as exc:
            logger.exception("attack surface mapping failed")
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/nqe/attack-surface", methods=["GET"])
    def pvm_get_attack_surface():
        """Query the attack surface table."""
        try:
            from tools.network.attack_surface_mapper import get_attack_surface
            cve_id = request.args.get("cve_id")
            device_name = request.args.get("device_name")
            min_score = float(request.args.get("min_score", 0.0))
            rows = get_attack_surface(cve_id=cve_id, device_name=device_name, min_score=min_score)
            return jsonify(rows)
        except Exception as exc:
            logger.exception("get attack surface failed")
            return jsonify({"error": str(exc)}), 500

    # ── Risk Prediction ───────────────────────────────────────────────────

    @bp.route("/api/nqe/predict-risks", methods=["POST"])
    def pvm_predict_risks():
        """Compute risk predictions (one advisory or all open)."""
        try:
            from tools.network.vuln_predictor import predict_advisory_risk, predict_all_open_advisories
            body = request.json or {}
            advisory_id = body.get("advisory_id")
            if advisory_id is not None:
                result = predict_advisory_risk(int(advisory_id))
            else:
                result = predict_all_open_advisories()
            return jsonify(result)
        except Exception as exc:
            logger.exception("risk prediction failed")
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/nqe/risk-trajectory/<int:advisory_id>", methods=["GET"])
    def pvm_risk_trajectory(advisory_id):
        """Return prediction history for one advisory."""
        try:
            from tools.network.vuln_predictor import get_risk_trajectory
            limit = int(request.args.get("limit", 10))
            rows = get_risk_trajectory(advisory_id, limit=limit)
            return jsonify(rows)
        except Exception as exc:
            logger.exception("risk trajectory failed")
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/nqe/top-risks", methods=["GET"])
    def pvm_top_risks():
        """Return latest highest-risk advisories."""
        try:
            from tools.network.vuln_predictor import get_top_risks
            limit = int(request.args.get("limit", 20))
            rows = get_top_risks(limit=limit)
            return jsonify(rows)
        except Exception as exc:
            logger.exception("top risks failed")
            return jsonify({"error": str(exc)}), 500

    # ── Triage ────────────────────────────────────────────────────────────

    @bp.route("/api/nqe/triage-queue", methods=["GET"])
    def pvm_triage_queue():
        """Return the triage queue, optionally filtered by status."""
        try:
            from tools.network.vuln_triage_engine import get_triage_queue
            status = request.args.get("status")
            limit = int(request.args.get("limit", 100))
            rows = get_triage_queue(status=status, limit=limit)
            return jsonify(rows)
        except Exception as exc:
            logger.exception("triage queue failed")
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/nqe/score-triage", methods=["POST"])
    def pvm_score_triage():
        """Score advisories and populate the triage queue."""
        try:
            from tools.network.vuln_triage_engine import score_advisories
            body = request.json or {}
            advisory_ids = body.get("advisory_ids")  # list[int] or None
            result = score_advisories(advisory_ids=advisory_ids)
            return jsonify(result)
        except Exception as exc:
            logger.exception("score triage failed")
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/nqe/triage-approve", methods=["POST"])
    def pvm_triage_approve():
        """HITL approve a triage queue entry."""
        try:
            from tools.network.vuln_triage_engine import approve_advisory
            body = request.json or {}
            advisory_id = body.get("advisory_id")
            approved_by = body.get("approved_by", "").strip()
            if advisory_id is None or not approved_by:
                return jsonify({"error": "advisory_id and approved_by are required"}), 400

            row = approve_advisory(int(advisory_id), approved_by)

            # Append HITL approval to audit log
            try:
                conn = _nc_conn()
                try:
                    conn.execute(
                        """INSERT INTO nc_nqe_audit_log
                           (action, advisory_id, input_text, data_source, confidence, created_at)
                           VALUES (%s,%s,%s,%s,%s,%s)""",
                        ("triage_approve", advisory_id,
                         f"HITL approve by {approved_by}", "hitl", 1.0, _now()),
                    )
                    conn.commit()
                finally:
                    conn.close()
            except Exception as _exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
                # audit failure is non-fatal
                logger.warning(
                    "pvm_triage_approve: best-effort INSERT into nc_nqe_audit_log failed (non-blocking): %s",
                    _exc,
                )

            return jsonify(row)
        except Exception as exc:
            logger.exception("triage approve failed")
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/nqe/triage-defer", methods=["POST"])
    def pvm_triage_defer():
        """Defer a triage queue entry."""
        try:
            from tools.network.vuln_triage_engine import defer_advisory
            body = request.json or {}
            advisory_id = body.get("advisory_id")
            approved_by = body.get("approved_by", "analyst").strip()
            if advisory_id is None:
                return jsonify({"error": "advisory_id is required"}), 400
            row = defer_advisory(int(advisory_id), approved_by)
            return jsonify(row)
        except Exception as exc:
            logger.exception("triage defer failed")
            return jsonify({"error": str(exc)}), 500

    # ── Patch Planning ────────────────────────────────────────────────────

    @bp.route("/api/nqe/patch-plan", methods=["POST"])
    def pvm_create_patch_plan():
        """Generate a new patch plan from approved triage queue."""
        try:
            from tools.network.patch_planner import create_patch_plan
            body = request.json or {}
            approved_by = body.get("approved_by")
            result = create_patch_plan(approved_by=approved_by)
            return jsonify(result)
        except Exception as exc:
            logger.exception("patch plan creation failed")
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/nqe/patch-plans", methods=["GET"])
    def pvm_get_patch_plans():
        """List patch plan rows."""
        try:
            from tools.network.patch_planner import get_patch_plans
            plan_id = request.args.get("plan_id")
            advisory_id = request.args.get("advisory_id")
            advisory_id_int = int(advisory_id) if advisory_id else None
            rows = get_patch_plans(plan_id=plan_id, advisory_id=advisory_id_int)
            return jsonify(rows)
        except Exception as exc:
            logger.exception("get patch plans failed")
            return jsonify({"error": str(exc)}), 500

    @bp.route("/api/nqe/patch-plan-summary/<plan_id>", methods=["GET"])
    def pvm_patch_plan_summary(plan_id):
        """Return aggregate summary for a patch plan."""
        try:
            from tools.network.patch_planner import get_plan_summary
            result = get_plan_summary(plan_id)
            return jsonify(result)
        except Exception as exc:
            logger.exception("patch plan summary failed")
            return jsonify({"error": str(exc)}), 500

    logger.info("PVM Predictive Vulnerability Management routes registered")
