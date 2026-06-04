"""ZTA canvas Flask blueprint — LAC Simulator routes."""

from __future__ import annotations
import json
import logging
from datetime import datetime, timezone

from flask import Blueprint, render_template, request, jsonify, session

from tools.zta.lac_simulator import PrincipalContext, ResourceMetadata, simulate_access
from tools.zta.lac_scenarios import list_scenarios, get_scenario
from tools.zta.db.init_db import init_db
from tools.db.storage import get_canvas_connection

log = logging.getLogger(__name__)

bp = Blueprint("zta", __name__, template_folder="../../tools/dashboard/templates")

_DB_CONN = None


def _get_conn():
    global _DB_CONN
    if _DB_CONN is None:
        try:
            _DB_CONN = get_canvas_connection()
            init_db(_DB_CONN)
        except Exception as exc:
            log.warning("ZTA DB connection failed: %s", exc)
    return _DB_CONN


def create_zta_blueprint() -> Blueprint:
    return bp


@bp.route("/lac-simulator")
def lac_simulator_page():
    scenarios = list_scenarios()
    return render_template("zta/lac_simulator.html", scenarios=scenarios)


@bp.route("/lac/scenarios")
def lac_scenarios():
    return jsonify(list_scenarios())


@bp.route("/lac/scenarios/<scenario_id>")
def lac_scenario_detail(scenario_id):
    scenario = get_scenario(scenario_id)
    if not scenario:
        return jsonify({"error": "not found"}), 404
    return jsonify(scenario)


@bp.route("/lac/simulate", methods=["POST"])
def lac_simulate():
    data = request.get_json(force=True) or {}
    principal_cfg = data.get("principal", {})
    resource_cfg = data.get("resource", {})
    action = data.get("action", "read")
    environment = data.get("environment", {})
    scenario_id = data.get("scenario_id", "ad_hoc")

    # Canonical scenarios always use their predefined values
    if scenario_id != "ad_hoc":
        scenario = get_scenario(scenario_id)
        if scenario:
            principal_cfg = scenario.get("principal_config", principal_cfg)
            resource_cfg = scenario.get("resource_config", resource_cfg)
            environment = scenario.get("environment", environment) or environment

    principal = PrincipalContext(
        citizenship=principal_cfg.get("citizenship", "US"),
        clearance_level=principal_cfg.get("clearance_level", "CUI"),
        cois=principal_cfg.get("cois", []),
        roles=principal_cfg.get("roles", []),
    )
    resource = ResourceMetadata(
        resource_id=resource_cfg.get("resource_id", "res-unknown"),
        is_eci=bool(resource_cfg.get("is_eci", False)),
        cois=resource_cfg.get("cois", []),
        classification=resource_cfg.get("classification", "CUI"),
        ownership=resource_cfg.get("ownership", "US"),
    )

    result = simulate_access(principal, resource, action, environment, scenario_name=scenario_id)

    # Persist audit trail
    try:
        conn = _get_conn()
        if conn:
            conn.execute(
                """INSERT INTO zta_lac_audit
                   (scenario_name, decision, deny_reasons,
                    principal_citizenship, principal_clearance, principal_cois,
                    resource_id, resource_is_eci, action, environment,
                    break_glass_activated, audit_json, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    result["scenario_name"],
                    result["decision"],
                    json.dumps(result["deny_reasons"]),
                    principal.citizenship,
                    principal.clearance_level,
                    json.dumps(principal.cois),
                    resource.resource_id,
                    int(resource.is_eci),
                    action,
                    json.dumps(environment),
                    int(result.get("break_glass_activated", False)),
                    json.dumps(result["audit_trail"]),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.commit()
    except Exception as exc:
        log.warning("ZTA audit write failed: %s", exc)

    return jsonify(result)


@bp.route("/lac/audit")
def lac_audit():
    page = int(request.args.get("page", 1))
    per_page = min(int(request.args.get("per_page", 20)), 200)
    offset = (page - 1) * per_page
    rows = []
    try:
        conn = _get_conn()
        if conn:
            cursor = conn.execute(
                "SELECT * FROM zta_lac_audit ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (per_page, offset),
            )
            cols = [d[0] for d in cursor.description]
            rows = [dict(zip(cols, row)) for row in cursor.fetchall()]
    except Exception as exc:
        log.warning("ZTA audit read failed: %s", exc)
    return jsonify({"rows": rows, "page": page, "per_page": per_page})
