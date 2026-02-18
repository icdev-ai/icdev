"""
Flask Blueprint for metrics API.
Queries metric_snapshots, alerts, and self_healing_events tables.
"""

import sqlite3
from flask import Blueprint, jsonify, request

from tools.dashboard.config import DB_PATH

metrics_api = Blueprint("metrics_api", __name__, url_prefix="/api/metrics")


def _get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@metrics_api.route("/snapshots", methods=["GET"])
def list_metric_snapshots():
    """Return recent metric snapshots, optionally filtered by project_id."""
    conn = _get_db()
    try:
        project_id = request.args.get("project_id")
        limit = min(int(request.args.get("limit", "100")), 1000)

        if project_id:
            rows = conn.execute(
                "SELECT * FROM metric_snapshots WHERE project_id = ? "
                "ORDER BY collected_at DESC LIMIT ?",
                (project_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM metric_snapshots ORDER BY collected_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return jsonify({"snapshots": [dict(r) for r in rows]})
    finally:
        conn.close()


@metrics_api.route("/alerts", methods=["GET"])
def list_alerts():
    """Return alerts, optionally filtered by project_id and/or status."""
    conn = _get_db()
    try:
        project_id = request.args.get("project_id")
        status = request.args.get("status")
        limit = min(int(request.args.get("limit", "50")), 500)

        query = "SELECT * FROM alerts WHERE 1=1"
        params: list = []
        if project_id:
            query += " AND project_id = ?"
            params.append(project_id)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(query, params).fetchall()

        # Summary
        summary = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM alerts GROUP BY status"
        ).fetchall()

        return jsonify({
            "alerts": [dict(r) for r in rows],
            "total": len(rows),
            "summary": {r["status"]: r["cnt"] for r in summary},
        })
    finally:
        conn.close()


@metrics_api.route("/self-healing", methods=["GET"])
def list_self_healing():
    """Return self-healing events."""
    conn = _get_db()
    try:
        project_id = request.args.get("project_id")
        limit = min(int(request.args.get("limit", "50")), 500)

        if project_id:
            rows = conn.execute(
                "SELECT she.*, kp.description as pattern_description "
                "FROM self_healing_events she "
                "LEFT JOIN knowledge_patterns kp ON she.pattern_id = kp.id "
                "WHERE she.project_id = ? ORDER BY she.created_at DESC LIMIT ?",
                (project_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT she.*, kp.description as pattern_description "
                "FROM self_healing_events she "
                "LEFT JOIN knowledge_patterns kp ON she.pattern_id = kp.id "
                "ORDER BY she.created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return jsonify({"events": [dict(r) for r in rows]})
    finally:
        conn.close()


@metrics_api.route("/health", methods=["GET"])
def health_status():
    """Overall health summary from latest metrics and alerts."""
    conn = _get_db()
    try:
        # Firing alerts count
        firing = conn.execute(
            "SELECT COUNT(*) as cnt FROM alerts WHERE status = 'firing'"
        ).fetchone()

        # Recent self-healing events (last 24h approximation: last 20)
        recent_heals = conn.execute(
            "SELECT COUNT(*) as cnt FROM self_healing_events"
        ).fetchone()

        # Unresolved failures
        unresolved = conn.execute(
            "SELECT COUNT(*) as cnt FROM failure_log WHERE resolved = 0"
        ).fetchone()

        # Active agents
        active_agents = conn.execute(
            "SELECT COUNT(*) as cnt FROM agents WHERE status = 'active'"
        ).fetchone()

        # Total projects
        total_projects = conn.execute(
            "SELECT COUNT(*) as cnt FROM projects"
        ).fetchone()

        health = "healthy"
        if (firing and firing["cnt"] > 0) or (unresolved and unresolved["cnt"] > 5):
            health = "degraded"
        if firing and firing["cnt"] > 5:
            health = "critical"

        return jsonify({
            "status": health,
            "firing_alerts": firing["cnt"] if firing else 0,
            "self_healing_events": recent_heals["cnt"] if recent_heals else 0,
            "unresolved_failures": unresolved["cnt"] if unresolved else 0,
            "active_agents": active_agents["cnt"] if active_agents else 0,
            "total_projects": total_projects["cnt"] if total_projects else 0,
        })
    finally:
        conn.close()
