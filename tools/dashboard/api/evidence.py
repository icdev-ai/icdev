#!/usr/bin/env python3
# CUI // SP-CTI
"""Dashboard API: Evidence Collection (Phase 56, D347)."""

import json
import sqlite3
import sys
from pathlib import Path

from flask import Blueprint, jsonify, request

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

DB_PATH = BASE_DIR / "data" / "icdev.db"

evidence_api = Blueprint("evidence_api", __name__, url_prefix="/api/evidence")


def _get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


@evidence_api.route("/stats", methods=["GET"])
def evidence_stats():
    """GET /api/evidence/stats — Overall evidence collection statistics."""
    from tools.compliance.evidence_collector import FRAMEWORK_EVIDENCE_MAP, _get_connection, _table_exists

    conn = _get_connection()
    stats = {
        "total_frameworks": len(FRAMEWORK_EVIDENCE_MAP),
        "required_frameworks": sum(1 for f in FRAMEWORK_EVIDENCE_MAP.values() if f["required"]),
        "frameworks": [],
    }

    for fw_id, fw_config in FRAMEWORK_EVIDENCE_MAP.items():
        total = 0
        for table_name in fw_config["tables"]:
            if _table_exists(conn, table_name):
                row = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()
                total += row[0]
        stats["frameworks"].append({
            "id": fw_id,
            "description": fw_config["description"],
            "required": fw_config["required"],
            "total_records": total,
        })

    conn.close()
    return jsonify(stats)


@evidence_api.route("/collect", methods=["POST"])
def trigger_collection():
    """POST /api/evidence/collect — Trigger evidence collection for a project."""
    from tools.compliance.evidence_collector import collect_evidence

    data = request.get_json(force=True, silent=True) or {}
    project_id = data.get("project_id") or request.args.get("project_id", "")
    framework = data.get("framework") or request.args.get("framework")
    project_dir = data.get("project_dir") or request.args.get("project_dir")

    if not project_id:
        return jsonify({"error": "project_id required"}), 400

    result = collect_evidence(
        project_id=project_id,
        project_dir=Path(project_dir) if project_dir else None,
        framework=framework,
    )
    return jsonify(result)


@evidence_api.route("/freshness", methods=["GET"])
def evidence_freshness():
    """GET /api/evidence/freshness — Check evidence freshness for a project."""
    from tools.compliance.evidence_collector import check_freshness

    project_id = request.args.get("project_id", "")
    max_age = float(request.args.get("max_age_hours", "48"))

    if not project_id:
        return jsonify({"error": "project_id required"}), 400

    result = check_freshness(project_id=project_id, max_age_hours=max_age)
    return jsonify(result)


@evidence_api.route("/frameworks", methods=["GET"])
def evidence_frameworks():
    """GET /api/evidence/frameworks — List supported evidence frameworks."""
    from tools.compliance.evidence_collector import list_frameworks

    frameworks = list_frameworks()
    return jsonify({"frameworks": frameworks, "total": len(frameworks)})
