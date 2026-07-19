#!/usr/bin/env python3
# CUI // SP-CTI
"""AI Accountability API Blueprint — REST endpoints for Phase 49 dashboard."""

import os
import sqlite3
import sys
from datetime import datetime, timezone
from tools.db.storage import get_connection, sql_placeholder, table_exists
from pathlib import Path

from flask import Blueprint, jsonify, request

from tools.dashboard.auth import require_role

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# Compliance-posture mutations (audits) are restricted to security/compliance
# roles, mirroring GOVCON_WRITE_ROLES in api/govcon.py. require_role() denies
# unauthenticated callers with 401 and wrong-role callers with 403 + audit log.
COMPLIANCE_WRITE_ROLES = ("admin", "isso", "ciso")

ai_accountability_api = Blueprint("ai_accountability_api", __name__, url_prefix="/api/ai-accountability")


def _get_db() -> sqlite3.Connection:
    conn = get_connection(db_path=str(DB_PATH))
    return conn


def _resolve_project_id(explicit: str = None) -> str:
    """Resolve project ID: explicit > query param > first project in DB > 'icdev-platform'."""
    pid = explicit or request.args.get("project_id")
    if pid:
        return pid
    try:
        conn = _get_db()
        row = conn.execute("SELECT id FROM projects ORDER BY created_at ASC LIMIT 1").fetchone()
        conn.close()
        if row:
            return row["id"]
    except Exception:
        pass
    return "icdev-platform"


def _safe_count(conn, table, project_id=None, where_extra=""):
    """COUNT rows in ``table``.

    A missing table returns 0 (feature not yet migrated — an honest "no data").
    A genuine query failure (backend/dialect error) is NOT swallowed to 0:
    masking it would render a failed compliance read as "0 open incidents /
    0 appeals" — a fabricated-healthy posture. The error propagates so the
    caller (get_stats) returns HTTP 500 instead of fake zeros.
    """
    from tools.db.storage import sql_placeholder as _sqlph
    if not table_exists(conn, table):
        return 0
    ph = _sqlph(conn)
    if project_id:
        sql = f"SELECT COUNT(*) as cnt FROM {table} WHERE project_id = {ph} {where_extra}"  # nosec B608 -- table/column names are internal constants, not user input
        row = conn.execute(sql, (project_id,)).fetchone()
    else:
        sql = f"SELECT COUNT(*) as cnt FROM {table}"  # nosec B608 -- table/column names are internal constants, not user input
        if where_extra:
            sql += f" WHERE 1=1 {where_extra}"
        row = conn.execute(sql).fetchone()
    return row["cnt"] if row else 0


@ai_accountability_api.route("/stats", methods=["GET"])
def get_stats():
    """Summary statistics for AI accountability dashboard."""
    project_id = request.args.get("project_id")
    try:
        conn = _get_db()
        stats = {
            "oversight_plan_count": _safe_count(conn, "ai_oversight_plans", project_id),
            "appeal_count": _safe_count(conn, "ai_accountability_appeals", project_id),
            "open_appeals": _safe_count(
                conn, "ai_accountability_appeals", project_id, "AND appeal_status IN ('submitted', 'under_review')"
            ),
            "caio_count": _safe_count(conn, "ai_caio_registry", project_id),
            "incident_count": _safe_count(conn, "ai_incident_log", project_id),
            "open_incidents": _safe_count(
                conn, "ai_incident_log", project_id, "AND status IN ('open', 'investigating')"
            ),
            "critical_incidents": _safe_count(
                conn,
                "ai_incident_log",
                project_id,
                "AND severity = 'critical' AND status NOT IN ('resolved', 'closed')",
            ),
            "ethics_review_count": _safe_count(conn, "ai_ethics_reviews", project_id),
            "reassessment_count": _safe_count(conn, "ai_reassessment_schedule", project_id),
            "accountability_score": None,
        }

        # Get latest accountability audit score
        try:
            sys.path.insert(0, str(BASE_DIR / "tools" / "compliance"))
            from ai_accountability_audit import run_accountability_audit

            resolved_pid = _resolve_project_id(project_id)
            result = run_accountability_audit(resolved_pid, db_path=DB_PATH)
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
        ph = sql_placeholder(conn)
        sql = "SELECT * FROM ai_accountability_appeals"
        params = []
        wheres = []
        if project_id:
            wheres.append(f"project_id = {ph}")
            params.append(project_id)
        if status:
            wheres.append(f"appeal_status = {ph}")
            params.append(status)
        if wheres:
            sql += " WHERE " + " AND ".join(wheres)
        sql += " ORDER BY created_at DESC LIMIT 100"
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return jsonify({"appeals": [dict(r) for r in rows], "total": len(rows)})
    except Exception as e:
        # Fail loud: a read error must not render as an empty (all-clear)
        # appeal list on a compliance page.
        return jsonify({"error": str(e)}), 500


@ai_accountability_api.route("/incidents", methods=["GET"])
def get_incidents():
    """Incident listing with severity filters."""
    project_id = request.args.get("project_id")
    severity = request.args.get("severity")
    try:
        conn = _get_db()
        ph = sql_placeholder(conn)
        sql = "SELECT * FROM ai_incident_log"
        params = []
        wheres = []
        if project_id:
            wheres.append(f"project_id = {ph}")
            params.append(project_id)
        if severity:
            wheres.append(f"severity = {ph}")
            params.append(severity)
        if wheres:
            sql += " WHERE " + " AND ".join(wheres)
        sql += " ORDER BY CASE severity WHEN 'critical' THEN 1 WHEN 'high' THEN 2 WHEN 'medium' THEN 3 ELSE 4 END, created_at DESC LIMIT 100"
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return jsonify({"incidents": [dict(r) for r in rows], "total": len(rows)})
    except Exception as e:
        # Fail loud: a read error must not render as an empty (all-clear)
        # incident list on a compliance page.
        return jsonify({"error": str(e)}), 500


@ai_accountability_api.route("/overdue", methods=["GET"])
def get_overdue():
    """Overdue reassessments."""
    project_id = request.args.get("project_id")
    try:
        conn = _get_db()
        ph = sql_placeholder(conn)
        # The old SQLite-only current-date SQL function raised on PostgreSQL
        # (the primary backend), which — under the old empty-as-success except
        # — silently hid every overdue reassessment. Bind a Python-computed
        # cutoff so the query is backend-agnostic.
        today = datetime.now(timezone.utc).date().isoformat()
        sql = f"SELECT * FROM ai_reassessment_schedule WHERE next_due < {ph}"
        params = [today]
        if project_id:
            sql += f" AND project_id = {ph}"
            params.append(project_id)
        sql += " ORDER BY next_due ASC"
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return jsonify({"overdue": [dict(r) for r in rows], "total": len(rows)})
    except Exception as e:
        # Fail loud: hiding overdue reassessments behind an empty list would
        # fabricate a "nothing overdue" posture.
        return jsonify({"error": str(e)}), 500


@ai_accountability_api.route("/audit", methods=["POST"])
@require_role(*COMPLIANCE_WRITE_ROLES)
def run_audit():
    """Run cross-framework accountability audit."""
    data = request.get_json(silent=True) or {}
    project_id = _resolve_project_id(data.get("project_id"))
    try:
        sys.path.insert(0, str(BASE_DIR / "tools" / "compliance"))
        from ai_accountability_audit import run_accountability_audit

        result = run_accountability_audit(project_id, db_path=DB_PATH)
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
