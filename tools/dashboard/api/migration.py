#!/usr/bin/env python3
# CUI // SP-CTI
"""Dashboard API: Migration Tracker (7R Assessment, Plans, Tasks, Artifacts, Progress)."""

import sys
from pathlib import Path

from flask import Blueprint, jsonify, request

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection, table_exists  # noqa: E402

DB_PATH = BASE_DIR / "data" / "icdev.db"

migration_api = Blueprint("migration_api", __name__, url_prefix="/api/migration")


def _get_db():
    conn = get_connection(db_path=str(DB_PATH))
    return conn


def _table_exists(conn, name):
    """Check if a table exists (works for both SQLite and PostgreSQL)."""
    return table_exists(conn, name)


# ---------------------------------------------------------------------------
# GET /api/migration/stats
# ---------------------------------------------------------------------------
@migration_api.route("/stats", methods=["GET"])
def migration_stats():
    """Aggregate migration statistics."""
    conn = None
    try:
        conn = _get_db()
        result = {
            "total_assessments": 0,
            "strategy_distribution": {},
            "ato_impact_breakdown": {},
            "plans_by_status": {},
            "tasks_by_status": {},
            "completion_pct": 0.0,
        }

        # --- assessments ---
        if _table_exists(conn, "migration_assessments"):
            row = conn.execute("SELECT COUNT(*) FROM migration_assessments").fetchone()
            result["total_assessments"] = row[0]

            for r in conn.execute(
                "SELECT recommended_strategy, COUNT(*) AS cnt "
                "FROM migration_assessments "
                "WHERE recommended_strategy IS NOT NULL "
                "GROUP BY recommended_strategy"
            ).fetchall():
                result["strategy_distribution"][r[0]] = r[1]

            for r in conn.execute(
                "SELECT ato_impact, COUNT(*) AS cnt "
                "FROM migration_assessments "
                "WHERE ato_impact IS NOT NULL "
                "GROUP BY ato_impact"
            ).fetchall():
                result["ato_impact_breakdown"][r[0]] = r[1]

        # --- plans ---
        if _table_exists(conn, "migration_plans"):
            for r in conn.execute("SELECT status, COUNT(*) AS cnt FROM migration_plans GROUP BY status").fetchall():
                result["plans_by_status"][r[0]] = r[1]

        # --- tasks ---
        if _table_exists(conn, "migration_tasks"):
            total_tasks = 0
            completed_tasks = 0
            for r in conn.execute("SELECT status, COUNT(*) AS cnt FROM migration_tasks GROUP BY status").fetchall():
                result["tasks_by_status"][r[0]] = r[1]
                total_tasks += r[1]
                if r[0] == "completed":
                    completed_tasks = r[1]
            if total_tasks > 0:
                result["completion_pct"] = round(completed_tasks / total_tasks * 100, 1)

        return jsonify(result)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        if conn:
            conn.close()


# ---------------------------------------------------------------------------
# GET /api/migration/assessments
# ---------------------------------------------------------------------------
@migration_api.route("/assessments", methods=["GET"])
def list_assessments():
    """List migration assessments with optional filters."""
    conn = None
    try:
        conn = _get_db()
        if not _table_exists(conn, "migration_assessments"):
            return jsonify({"assessments": [], "total": 0, "page": 1, "per_page": 25})

        strategy = request.args.get("strategy")
        ato_impact = request.args.get("ato_impact")
        page = max(int(request.args.get("page", 1)), 1)
        per_page = min(max(int(request.args.get("per_page", 25)), 1), 100)

        where_clauses = []
        params = []
        if strategy:
            where_clauses.append("recommended_strategy = ?")
            params.append(strategy)
        if ato_impact:
            where_clauses.append("ato_impact = ?")
            params.append(ato_impact)

        where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        total = conn.execute(
            f"SELECT COUNT(*) FROM migration_assessments{where_sql}",
            params,  # nosec B608 -- table/column names are internal constants, not user input
        ).fetchone()[0]

        rows = conn.execute(
            f"SELECT id, legacy_app_id, component_id, assessment_scope, "  # nosec B608 -- table/column names are internal constants, not user input
            f"rehost_score, replatform_score, refactor_score, rearchitect_score, "
            f"repurchase_score, retire_score, retain_score, "
            f"recommended_strategy, cost_estimate_hours, risk_score, "
            f"timeline_weeks, ato_impact, tech_debt_reduction, assessed_at "
            f"FROM migration_assessments{where_sql} "
            f"ORDER BY assessed_at DESC LIMIT %s OFFSET %s",
            params + [per_page, (page - 1) * per_page],
        ).fetchall()

        assessments = []
        for r in rows:
            assessments.append(
                {
                    "id": r[0],
                    "legacy_app_id": r[1],
                    "component_id": r[2],
                    "assessment_scope": r[3],
                    "rehost_score": r[4],
                    "replatform_score": r[5],
                    "refactor_score": r[6],
                    "rearchitect_score": r[7],
                    "repurchase_score": r[8],
                    "retire_score": r[9],
                    "retain_score": r[10],
                    "recommended_strategy": r[11],
                    "cost_estimate_hours": r[12],
                    "risk_score": r[13],
                    "timeline_weeks": r[14],
                    "ato_impact": r[15],
                    "tech_debt_reduction": r[16],
                    "assessed_at": r[17],
                }
            )

        return jsonify(
            {
                "assessments": assessments,
                "total": total,
                "page": page,
                "per_page": per_page,
            }
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        if conn:
            conn.close()


# ---------------------------------------------------------------------------
# GET /api/migration/plans
# ---------------------------------------------------------------------------
@migration_api.route("/plans", methods=["GET"])
def list_plans():
    """List migration plans with optional filters."""
    conn = None
    try:
        conn = _get_db()
        if not _table_exists(conn, "migration_plans"):
            return jsonify({"plans": [], "total": 0, "page": 1, "per_page": 25})

        status = request.args.get("status")
        strategy = request.args.get("strategy")
        page = max(int(request.args.get("page", 1)), 1)
        per_page = min(max(int(request.args.get("per_page", 25)), 1), 100)

        where_clauses = []
        params = []
        if status:
            where_clauses.append("status = ?")
            params.append(status)
        if strategy:
            where_clauses.append("strategy = ?")
            params.append(strategy)

        where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        total = conn.execute(
            f"SELECT COUNT(*) FROM migration_plans{where_sql}",
            params,  # nosec B608 -- table/column names are internal constants, not user input
        ).fetchone()[0]

        rows = conn.execute(
            f"SELECT id, legacy_app_id, plan_name, strategy, target_language, "  # nosec B608 -- table/column names are internal constants, not user input
            f"target_framework, target_database, target_architecture, "
            f"migration_approach, total_tasks, completed_tasks, status, "
            f"estimated_hours, actual_hours, start_date, target_date, "
            f"completion_date, created_at, updated_at "
            f"FROM migration_plans{where_sql} "
            f"ORDER BY created_at DESC LIMIT %s OFFSET %s",
            params + [per_page, (page - 1) * per_page],
        ).fetchall()

        plans = []
        for r in rows:
            plans.append(
                {
                    "id": r[0],
                    "legacy_app_id": r[1],
                    "plan_name": r[2],
                    "strategy": r[3],
                    "target_language": r[4],
                    "target_framework": r[5],
                    "target_database": r[6],
                    "target_architecture": r[7],
                    "migration_approach": r[8],
                    "total_tasks": r[9],
                    "completed_tasks": r[10],
                    "status": r[11],
                    "estimated_hours": r[12],
                    "actual_hours": r[13],
                    "start_date": r[14],
                    "target_date": r[15],
                    "completion_date": r[16],
                    "created_at": r[17],
                    "updated_at": r[18],
                }
            )

        return jsonify(
            {
                "plans": plans,
                "total": total,
                "page": page,
                "per_page": per_page,
            }
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        if conn:
            conn.close()


# ---------------------------------------------------------------------------
# GET /api/migration/plans/<plan_id>
# ---------------------------------------------------------------------------
@migration_api.route("/plans/<plan_id>", methods=["GET"])
def plan_detail(plan_id):
    """Plan detail including tasks, artifacts, and latest progress."""
    conn = None
    try:
        conn = _get_db()

        # --- plan ---
        if not _table_exists(conn, "migration_plans"):
            return jsonify({"error": "migration_plans table not found"}), 404

        plan_row = conn.execute(
            "SELECT id, legacy_app_id, plan_name, strategy, target_language, "
            "target_framework, target_database, target_architecture, "
            "migration_approach, total_tasks, completed_tasks, status, "
            "estimated_hours, actual_hours, start_date, target_date, "
            "completion_date, created_at, updated_at "
            "FROM migration_plans WHERE id = %s",
            (plan_id,),
        ).fetchone()

        if not plan_row:
            return jsonify({"error": "Plan not found"}), 404

        plan = {
            "id": plan_row[0],
            "legacy_app_id": plan_row[1],
            "plan_name": plan_row[2],
            "strategy": plan_row[3],
            "target_language": plan_row[4],
            "target_framework": plan_row[5],
            "target_database": plan_row[6],
            "target_architecture": plan_row[7],
            "migration_approach": plan_row[8],
            "total_tasks": plan_row[9],
            "completed_tasks": plan_row[10],
            "status": plan_row[11],
            "estimated_hours": plan_row[12],
            "actual_hours": plan_row[13],
            "start_date": plan_row[14],
            "target_date": plan_row[15],
            "completion_date": plan_row[16],
            "created_at": plan_row[17],
            "updated_at": plan_row[18],
        }

        # --- tasks ---
        tasks = []
        if _table_exists(conn, "migration_tasks"):
            for r in conn.execute(
                "SELECT id, task_type, title, description, priority, status, "
                "pi_number, assigned_to, estimated_hours, actual_hours, "
                "output_path, created_at, completed_at "
                "FROM migration_tasks WHERE plan_id = %s ORDER BY created_at",
                (plan_id,),
            ).fetchall():
                tasks.append(
                    {
                        "id": r[0],
                        "task_type": r[1],
                        "title": r[2],
                        "description": r[3],
                        "priority": r[4],
                        "status": r[5],
                        "pi_number": r[6],
                        "assigned_to": r[7],
                        "estimated_hours": r[8],
                        "actual_hours": r[9],
                        "output_path": r[10],
                        "created_at": r[11],
                        "completed_at": r[12],
                    }
                )

        # --- artifacts ---
        artifacts = []
        if _table_exists(conn, "migration_artifacts"):
            for r in conn.execute(
                "SELECT id, task_id, artifact_type, file_path, file_hash, "
                "description, created_at "
                "FROM migration_artifacts WHERE plan_id = %s ORDER BY created_at",
                (plan_id,),
            ).fetchall():
                artifacts.append(
                    {
                        "id": r[0],
                        "task_id": r[1],
                        "artifact_type": r[2],
                        "file_path": r[3],
                        "file_hash": r[4],
                        "description": r[5],
                        "created_at": r[6],
                    }
                )

        # --- latest progress ---
        progress = None
        if _table_exists(conn, "migration_progress"):
            pr = conn.execute(
                "SELECT id, pi_number, snapshot_type, tasks_total, tasks_completed, "
                "tasks_in_progress, tasks_blocked, components_migrated, "
                "components_remaining, apis_migrated, tables_migrated, "
                "test_coverage, compliance_score, hours_spent, notes, created_at "
                "FROM migration_progress WHERE plan_id = %s "
                "ORDER BY created_at DESC LIMIT 1",
                (plan_id,),
            ).fetchone()
            if pr:
                progress = {
                    "id": pr[0],
                    "pi_number": pr[1],
                    "snapshot_type": pr[2],
                    "tasks_total": pr[3],
                    "tasks_completed": pr[4],
                    "tasks_in_progress": pr[5],
                    "tasks_blocked": pr[6],
                    "components_migrated": pr[7],
                    "components_remaining": pr[8],
                    "apis_migrated": pr[9],
                    "tables_migrated": pr[10],
                    "test_coverage": pr[11],
                    "compliance_score": pr[12],
                    "hours_spent": pr[13],
                    "notes": pr[14],
                    "created_at": pr[15],
                }

        plan["tasks"] = tasks
        plan["artifacts"] = artifacts
        plan["progress"] = progress
        return jsonify(plan)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        if conn:
            conn.close()


# ---------------------------------------------------------------------------
# GET /api/migration/plans/<plan_id>/progress
# ---------------------------------------------------------------------------
@migration_api.route("/plans/<plan_id>/progress", methods=["GET"])
def plan_progress(plan_id):
    """All progress snapshots for a plan."""
    conn = None
    try:
        conn = _get_db()
        if not _table_exists(conn, "migration_progress"):
            return jsonify({"snapshots": [], "plan_id": plan_id})

        rows = conn.execute(
            "SELECT id, pi_number, snapshot_type, tasks_total, tasks_completed, "
            "tasks_in_progress, tasks_blocked, components_migrated, "
            "components_remaining, apis_migrated, tables_migrated, "
            "test_coverage, compliance_score, hours_spent, notes, created_at "
            "FROM migration_progress WHERE plan_id = %s ORDER BY created_at",
            (plan_id,),
        ).fetchall()

        snapshots = []
        for r in rows:
            snapshots.append(
                {
                    "id": r[0],
                    "pi_number": r[1],
                    "snapshot_type": r[2],
                    "tasks_total": r[3],
                    "tasks_completed": r[4],
                    "tasks_in_progress": r[5],
                    "tasks_blocked": r[6],
                    "components_migrated": r[7],
                    "components_remaining": r[8],
                    "apis_migrated": r[9],
                    "tables_migrated": r[10],
                    "test_coverage": r[11],
                    "compliance_score": r[12],
                    "hours_spent": r[13],
                    "notes": r[14],
                    "created_at": r[15],
                }
            )

        return jsonify({"snapshots": snapshots, "plan_id": plan_id})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        if conn:
            conn.close()


# ---------------------------------------------------------------------------
# POST /api/migration/assess
# ---------------------------------------------------------------------------
@migration_api.route("/assess", methods=["POST"])
def trigger_assessment():
    """Trigger a migration assessment for a project."""
    data = request.get_json(force=True, silent=True) or {}
    project_id = data.get("project_id") or request.args.get("project_id", "")

    if not project_id:
        return jsonify({"error": "project_id required"}), 400

    try:
        from tools.modernization.modernizer import assess_application

        result = assess_application(project_id=project_id)
        return jsonify(result)
    except ImportError as exc:
        return jsonify({"error": f"Modernizer module not available: {exc}"}), 501
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
