# [CUI // SP-CTI]
"""Network Infrastructure Intelligence — Flask API routes.

Provides 16 endpoints for diagram ingestion, device management,
13 analysis dimensions, and NL query dispatch.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import tempfile
import uuid
from pathlib import Path

logger = get_logger("icdev.network.routes.intelligence")


def register_intelligence_routes(bp):
    """Register Network Intelligence API routes on the NDC blueprint."""
    from flask import jsonify, request

    from tools.network.db.init_db import get_connection as _get_conn

    # ── Ingest ───────────────────────────────────────────────────────────

    @bp.route("/api/network-intelligence/ingest", methods=["POST"])
    def ni_ingest():
        """Upload and ingest a network diagram file."""
        from tools.network.network_ingester import ingest_diagram

        if "file" not in request.files:
            return jsonify({"error": "No file provided"}), 400

        f = request.files["file"]
        project_id = request.form.get("project_id", "default")
        name = request.form.get("name", f.filename or "uploaded")

        # Save to temp
        tmp = Path(tempfile.gettempdir()) / f"ni_upload_{uuid.uuid4().hex[:8]}_{f.filename}"
        f.save(str(tmp))

        try:
            result = ingest_diagram(str(tmp), project_id, name)
        finally:
            tmp.unlink(missing_ok=True)

        if result.get("error"):
            return jsonify(result), 400
        return jsonify(result)

    # ── Devices ──────────────────────────────────────────────────────────

    @bp.route("/api/network-intelligence/devices/<topology_id>", methods=["GET"])
    def ni_devices(topology_id):
        """List devices with EOL info."""
        from tools.network.device_manager import get_devices

        filters = {}
        if request.args.get("device_type"):
            filters["device_type"] = request.args["device_type"]
        if request.args.get("vendor"):
            filters["vendor"] = request.args["vendor"]
        if request.args.get("site"):
            filters["site"] = request.args["site"]
        return jsonify(get_devices(topology_id, filters or None))

    # ── NL Query ─────────────────────────────────────────────────────────

    @bp.route("/api/network-intelligence/query", methods=["POST"])
    def ni_query():
        """Accept NL question, route through intelligence engine."""
        from tools.network.network_query_router import route_query

        body = request.json or {}
        topology_id = body.get("topology_id", "")
        question = body.get("question", "")
        if not topology_id or not question:
            return jsonify({"error": "topology_id and question required"}), 400
        return jsonify(route_query(topology_id, question))

    # ── Analysis endpoints ───────────────────────────────────────────────

    @bp.route("/api/network-intelligence/redundancy/<topology_id>", methods=["POST"])
    def ni_redundancy(topology_id):
        from tools.network.network_intelligence import analyze_redundancy
        body = request.json or {}
        return jsonify(analyze_redundancy(topology_id, body.get("site")))

    @bp.route("/api/network-intelligence/eol/<topology_id>", methods=["POST"])
    def ni_eol(topology_id):
        from tools.network.network_intelligence import analyze_eol
        body = request.json or {}
        return jsonify(analyze_eol(topology_id, body.get("years", 5)))

    @bp.route("/api/network-intelligence/blast-radius/<topology_id>/<device_id>", methods=["POST"])
    def ni_blast_radius(topology_id, device_id):
        from tools.network.network_intelligence import analyze_blast_radius
        return jsonify(analyze_blast_radius(topology_id, device_id))

    @bp.route("/api/network-intelligence/impact/<topology_id>", methods=["POST"])
    def ni_impact(topology_id):
        from tools.network.network_intelligence import analyze_impact
        body = request.json or {}
        changes = body.get("changes", [])
        return jsonify(analyze_impact(topology_id, changes))

    @bp.route("/api/network-intelligence/capacity/<topology_id>", methods=["POST"])
    def ni_capacity(topology_id):
        from tools.network.network_intelligence import analyze_capacity
        body = request.json or {}
        return jsonify(analyze_capacity(topology_id, body))

    @bp.route("/api/network-intelligence/config/<topology_id>", methods=["POST"])
    def ni_config(topology_id):
        from tools.network.network_intelligence import manage_configs
        body = request.json or {}
        action = body.get("action", "diff")
        return jsonify(manage_configs(topology_id, action, body.get("targets")))

    @bp.route("/api/network-intelligence/compliance/<topology_id>", methods=["POST"])
    def ni_compliance(topology_id):
        from tools.network.network_intelligence import assess_compliance
        body = request.json or {}
        return jsonify(assess_compliance(topology_id, body.get("framework", "nist")))

    @bp.route("/api/network-intelligence/availability/<topology_id>", methods=["GET"])
    def ni_availability(topology_id):
        from tools.network.network_intelligence import assess_availability
        src = request.args.get("src")
        dst = request.args.get("dst")
        return jsonify(assess_availability(topology_id, src, dst))

    @bp.route("/api/network-intelligence/latency/<topology_id>", methods=["GET"])
    def ni_latency(topology_id):
        from tools.network.network_intelligence import assess_latency
        src = request.args.get("src")
        dst = request.args.get("dst")
        return jsonify(assess_latency(topology_id, src, dst))

    @bp.route("/api/network-intelligence/vendor-risk/<topology_id>", methods=["GET"])
    def ni_vendor_risk(topology_id):
        from tools.network.network_intelligence import assess_vendor_risk
        return jsonify(assess_vendor_risk(topology_id))

    @bp.route("/api/network-intelligence/circuits/<topology_id>", methods=["GET"])
    def ni_circuits(topology_id):
        from tools.network.network_intelligence import assess_circuits
        months = request.args.get("months", 12, type=int)
        return jsonify(assess_circuits(topology_id, months))

    @bp.route("/api/network-intelligence/cost-projection/<topology_id>", methods=["POST"])
    def ni_cost_projection(topology_id):
        from tools.network.network_intelligence import project_costs
        body = request.json or {}
        return jsonify(project_costs(topology_id, body.get("years", 5)))

    @bp.route("/api/network-intelligence/analyses/<topology_id>", methods=["GET"])
    def ni_analyses(topology_id):
        """List cached analysis results."""
        conn = _get_conn()
        rows = conn.execute(
            "SELECT id, analysis_type, query_text, result_summary, created_at "
            "FROM ni_analyses WHERE topology_id = %s ORDER BY created_at DESC LIMIT 50",
            (topology_id,),
        ).fetchall()
        conn.close()
        return jsonify([dict(r) if hasattr(r, "keys") else {
            "id": r[0], "analysis_type": r[1], "query_text": r[2],
            "result_summary": r[3], "created_at": r[4],
        } for r in rows])
