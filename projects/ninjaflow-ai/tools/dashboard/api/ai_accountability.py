#!/usr/bin/env python3
# CUI // SP-CTI
"""AI Accountability API Blueprint — REST endpoints for Phase 49 dashboard."""

import json
import os
import sqlite3
import sys
from pathlib import Path

from flask import Blueprint, jsonify, request

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

ai_accountability_api = Blueprint("ai_accountability_api", __name__, url_prefix="/api/ai-accountability")


def _get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _safe_count(conn, table, project_id=None, where_extra=""):
    try:
        if project_id:
            sql = f"SELECT COUNT(*) as cnt FROM {table} WHERE project_id = ? {where_extra}"
            row = conn.execute(sql, (project_id,)).fetchone()
        else:
            sql = f"SELECT COUNT(*) as cnt FROM {table}"
            if where_extra:
                sql += f" WHERE 1=1 {where_extra}"
            row = conn.execute(sql).fetchone()
        return row["cnt"] if row else 0
    except Exception:
        return 0


@ai_accountability_api.route("/stats", methods=["GET"])
def get_stats():
    """Summary statistics for AI accountability dashboard."""
    project_id = request.args.get("project_id")
    try:
        conn = _get_db()
        stats = {
            "oversight_plan_count": _safe_count(conn, "ai_oversight_plans", project_id),
            "appeal_count": _safe_count(conn, "ai_accountability_appeals", project_id),
            "open_appeals": _safe_count(conn, "ai_accountability_appeals", project_id,
                                        "AND appeal_status IN ('submitted', 'under_review')"),
            "caio_count": _safe_count(conn, "ai_caio_registry", project_id),
            "incident_count": _safe_count(conn, "ai_incident_log", project_id),
            "open_incidents": _safe_count(conn, "ai_incident_log", project_id,
                                          "AND status IN ('open', 'investigating')"),
            "critical_incidents": _safe_count(conn, "ai_incident_log", project_id,
                                              "AND severity = 'critical' AND status NOT IN ('resolved', 'closed')"),
            "ethics_review_count": _safe_count(conn, "ai_ethics_reviews", project_id),
            "reassessment_count": _safe_count(conn, "ai_reassessment_schedule", project_id),
            "accountability_score": None,
        }

        # Get latest accountability audit score
        try:
            sys.path.insert(0, str(BASE_DIR / "tools" / "compliance"))
            from ai_accountability_audit import run_accountability_audit
            result = run_accountability_audit(project_id or "default", db_path=DB_PATH)
            stats["accountability_score"] = result.get("accountability_score", 0)
        except Exception:
            pass

        conn.close()
        return jsonify(stats)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@ai_accountability_api.route("/appeals", methods=["GET"])
def get_appeals():
    """Appeal listing with status filters."""
    project_id = request.args.get("project_id")
    status = request.args.get("status")
    try:
        conn = _get_db()
        sql = "SELECT * FROM ai_accountability_appeals"
        params = []
        wheres = []
        if project_id:
            wheres.append("project_id = ?")
            params.append(project_id)
        if status:
            wheres.append("appeal_status = ?")
            params.append(status)
        if wheres:
            sql += " WHERE " + " AND ".join(wheres)
        sql += " ORDER BY created_at DESC LIMIT 100"
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return jsonify({"appeals": [dict(r) for r in rows], "total": len(rows)})
    except Exception as e:
        return jsonify({"appeals": [], "total": 0, "error": str(e)})


@ai_accountability_api.route("/incidents", methods=["GET"])
def get_incidents():
    """Incident listing with severity filters."""
    project_id = request.args.get("project_id")
    severity = request.args.get("severity")
    try:
        conn = _get_db()
        sql = "SELECT * FROM ai_incident_log"
        params = []
        wheres = []
        if project_id:
            wheres.append("project_id = ?")
            params.append(project_id)
        if severity:
            wheres.append("severity = ?")
            params.append(severity)
        if wheres:
            sql += " WHERE " + " AND ".join(wheres)
        sql += " ORDER BY CASE severity WHEN 'critical' THEN 1 WHEN 'high' THEN 2 WHEN 'medium' THEN 3 ELSE 4 END, created_at DESC LIMIT 100"
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return jsonify({"incidents": [dict(r) for r in rows], "total": len(rows)})
    except Exception as e:
        return jsonify({"incidents": [], "total": 0, "error": str(e)})


@ai_accountability_api.route("/overdue", methods=["GET"])
def get_overdue():
    """Overdue reassessments."""
    project_id = request.args.get("project_id")
    try:
        conn = _get_db()
        sql = "SELECT * FROM ai_reassessment_schedule WHERE next_due < date('now')"
        params = []
        if project_id:
            sql += " AND project_id = ?"
            params.append(project_id)
        sql += " ORDER BY next_due ASC"
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return jsonify({"overdue": [dict(r) for r in rows], "total": len(rows)})
    except Exception as e:
        return jsonify({"overdue": [], "total": 0, "error": str(e)})


@ai_accountability_api.route("/audit", methods=["POST"])
def run_audit():
    """Run cross-framework accountability audit."""
    data = request.get_json(silent=True) or {}
    project_id = data.get("project_id", "default")
    try:
        sys.path.insert(0, str(BASE_DIR / "tools" / "compliance"))
        from ai_accountability_audit import run_accountability_audit
        result = run_accountability_audit(project_id, db_path=DB_PATH)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
