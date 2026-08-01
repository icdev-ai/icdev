#!/usr/bin/env python3
# CUI // SP-CTI
"""Dashboard API: Executive Migration Metrics.

High-level aggregate endpoints for executive dashboards.
Pulls from migration_assessments, migration_plans, migration_tasks,
migration_progress, and migration_intelligence tables.
"""

import sys
from pathlib import Path

from flask import Blueprint, jsonify

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection, table_exists  # noqa: E402

DB_PATH = BASE_DIR / "data" / "icdev.db"

executive_api = Blueprint("executive_api", __name__, url_prefix="/api/executive")


def _get_db():
    conn = get_connection(db_path=str(DB_PATH))
    return conn


def _table_exists(conn, name):
    """Check if a table exists (works for both SQLite and PostgreSQL)."""
    return table_exists(conn, name)


# ---------------------------------------------------------------------------
# GET /api/executive/migration-summary
# ---------------------------------------------------------------------------
@executive_api.route("/migration-summary", methods=["GET"])
def migration_summary():
    """High-level migration KPIs for executive dashboard."""
    conn = None
    try:
        conn = _get_db()
        result = {
            "total_assessments": 0,
            "total_plans": 0,
            "active_plans": 0,
            "completed_plans": 0,
            "total_tasks": 0,
            "completed_tasks": 0,
            "completion_pct": 0.0,
            "avg_risk_score": 0.0,
            "avg_timeline_weeks": 0.0,
            "total_estimated_hours": 0,
            "total_actual_hours": 0,
            "ato_high_critical": 0,
            "strategy_distribution": {},
            "plans_by_status": {},
            "monthly_trend": [],
        }

        # Assessments
        if _table_exists(conn, "migration_assessments"):
            row = conn.execute("SELECT COUNT(*) FROM migration_assessments").fetchone()
            result["total_assessments"] = row[0] if row else 0

            row = conn.execute(
                "SELECT AVG(risk_score) FROM migration_assessments WHERE risk_score IS NOT NULL"
            ).fetchone()
            result["avg_risk_score"] = round(row[0], 2) if row and row[0] else 0.0

            row = conn.execute(
                "SELECT COUNT(*) FROM migration_assessments WHERE ato_impact IN ('high','critical')"
            ).fetchone()
            result["ato_high_critical"] = row[0] if row else 0

            for r in conn.execute(
                "SELECT recommended_strategy, COUNT(*) AS cnt FROM migration_assessments "
                "WHERE recommended_strategy IS NOT NULL GROUP BY recommended_strategy"
            ).fetchall():
                result["strategy_distribution"][r[0]] = r[1]

        # Plans
        if _table_exists(conn, "migration_plans"):
            row = conn.execute("SELECT COUNT(*) FROM migration_plans").fetchone()
            result["total_plans"] = row[0] if row else 0

            for r in conn.execute(
                "SELECT status, COUNT(*) AS cnt FROM migration_plans GROUP BY status"
            ).fetchall():
                result["plans_by_status"][r[0]] = r[1]
                if r[0] == "in_progress":
                    result["active_plans"] = r[1]
                if r[0] == "completed":
                    result["completed_plans"] = r[1]

            row = conn.execute(
                "SELECT SUM(estimated_hours), SUM(actual_hours) FROM migration_plans"
            ).fetchone()
            result["total_estimated_hours"] = int(row[0] or 0) if row else 0
            result["total_actual_hours"] = int(row[1] or 0) if row else 0

            row = conn.execute(
                "SELECT AVG(timeline_weeks) FROM migration_plans WHERE timeline_weeks IS NOT NULL"
            ).fetchone()
            result["avg_timeline_weeks"] = round(row[0], 1) if row and row[0] else 0.0

        # Tasks
        if _table_exists(conn, "migration_tasks"):
            row = conn.execute("SELECT COUNT(*) FROM migration_tasks").fetchone()
            result["total_tasks"] = row[0] if row else 0

            row = conn.execute(
                "SELECT COUNT(*) FROM migration_tasks WHERE status = 'completed'"
            ).fetchone()
            result["completed_tasks"] = row[0] if row else 0

            if result["total_tasks"] > 0:
                result["completion_pct"] = round(
                    result["completed_tasks"] / result["total_tasks"] * 100, 1
                )

        # Monthly trend (plans created per month)
        if _table_exists(conn, "migration_plans"):
            rows = conn.execute(
                "SELECT strftime('%Y-%m', created_at) AS month, COUNT(*) AS cnt "
                "FROM migration_plans WHERE created_at IS NOT NULL "
                "GROUP BY month ORDER BY month DESC LIMIT 12"
            ).fetchall()
            result["monthly_trend"] = [{"month": r[0], "plans": r[1]} for r in rows]

        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        if conn:
            conn.close()


# ---------------------------------------------------------------------------
# GET /api/executive/migration-trend
# ---------------------------------------------------------------------------
@executive_api.route("/migration-trend", methods=["GET"])
def migration_trend():
    """Time-series trend of migration progress."""
    conn = None
    try:
        conn = _get_db()
        result = {"snapshots": [], "plans": []}

        if _table_exists(conn, "migration_progress"):
            rows = conn.execute(
                "SELECT plan_id, strftime('%Y-%m-%d', created_at) AS day, "
                "AVG(tasks_completed * 100.0 / NULLIF(tasks_total, 0)) AS pct, "
                "SUM(components_migrated) AS comps, SUM(apis_migrated) AS apis, "
                "SUM(tables_migrated) AS tables "
                "FROM migration_progress WHERE created_at IS NOT NULL "
                "GROUP BY plan_id, day ORDER BY day DESC LIMIT 90"
            ).fetchall()
            result["snapshots"] = [
                {
                    "plan_id": r[0],
                    "day": r[1],
                    "completion_pct": round(r[2] or 0, 1),
                    "components_migrated": r[3] or 0,
                    "apis_migrated": r[4] or 0,
                    "tables_migrated": r[5] or 0,
                }
                for r in rows
            ]

        if _table_exists(conn, "migration_plans"):
            rows = conn.execute(
                "SELECT id, plan_name, status, total_tasks, completed_tasks, "
                "target_date, completion_date FROM migration_plans ORDER BY created_at DESC LIMIT 20"
            ).fetchall()
            result["plans"] = [
                {
                    "id": r[0],
                    "name": r[1],
                    "status": r[2],
                    "total_tasks": r[3] or 0,
                    "completed_tasks": r[4] or 0,
                    "target_date": r[5],
                    "completion_date": r[6],
                }
                for r in rows
            ]

        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        if conn:
            conn.close()


# ---------------------------------------------------------------------------
# GET /api/executive/strategy-summary
# ---------------------------------------------------------------------------
@executive_api.route("/strategy-summary", methods=["GET"])
def strategy_summary():
    """7R strategy breakdown with cost and risk averages."""
    conn = None
    try:
        conn = _get_db()
        result = {"strategies": [], "total_assessed": 0}

        if _table_exists(conn, "migration_assessments"):
            rows = conn.execute(
                "SELECT recommended_strategy, COUNT(*) AS cnt, "
                "AVG(cost_estimate_hours) AS avg_cost, AVG(risk_score) AS avg_risk, "
                "AVG(timeline_weeks) AS avg_weeks "
                "FROM migration_assessments WHERE recommended_strategy IS NOT NULL "
                "GROUP BY recommended_strategy ORDER BY cnt DESC"
            ).fetchall()
            result["strategies"] = [
                {
                    "strategy": r[0],
                    "count": r[1],
                    "avg_cost_hours": round(r[2] or 0, 0),
                    "avg_risk": round(r[3] or 0, 2),
                    "avg_weeks": round(r[4] or 0, 1),
                }
                for r in rows
            ]
            result["total_assessed"] = sum(s["count"] for s in result["strategies"])

        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        if conn:
            conn.close()
