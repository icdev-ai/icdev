# [TEMPLATE: CUI // SP-CTI]
"""
Flask Blueprint for metrics API.
Queries metric_snapshots, alerts, and self_healing_events tables.
"""

from tools.db.storage import get_connection, sql_placeholder
from flask import Blueprint, jsonify, request

from tools.dashboard.config import DB_PATH

metrics_api = Blueprint("metrics_api", __name__, url_prefix="/api/metrics")


def _get_db():
    conn = get_connection(db_path=str(DB_PATH))
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
                "SELECT * FROM metric_snapshots WHERE project_id = %s ORDER BY collected_at DESC LIMIT %s",
                (project_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM metric_snapshots ORDER BY collected_at DESC LIMIT %s",
                (limit,),
            ).fetchall()
        return jsonify({"snapshots": [dict(r) for r in rows]})
    finally:
        conn.close()


@metrics_api.route("/alerts", methods=["GET"])
def list_alerts():
    """Return alerts, optionally filtered by project_id and/or status."""
    conn = _get_db()
    ph = sql_placeholder(conn)
    try:
        project_id = request.args.get("project_id")
        status = request.args.get("status")
        limit = min(int(request.args.get("limit", "50")), 500)

        query = "SELECT * FROM alerts WHERE 1=1"
        params: list = []
        if project_id:
            query += f" AND project_id = {ph}"
            params.append(project_id)
        if status:
            query += f" AND status = {ph}"
            params.append(status)
        query += f" ORDER BY created_at DESC LIMIT {ph}"
        params.append(limit)

        rows = conn.execute(query, params).fetchall()

        # Summary
        summary = conn.execute("SELECT status, COUNT(*) as cnt FROM alerts GROUP BY status").fetchall()

        return jsonify(
            {
                "alerts": [dict(r) for r in rows],
                "total": len(rows),
                "summary": {r["status"]: r["cnt"] for r in summary},
            }
        )
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
                "WHERE she.project_id = %s ORDER BY she.created_at DESC LIMIT %s",
                (project_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT she.*, kp.description as pattern_description "
                "FROM self_healing_events she "
                "LEFT JOIN knowledge_patterns kp ON she.pattern_id = kp.id "
                "ORDER BY she.created_at DESC LIMIT %s",
                (limit,),
            ).fetchall()
        return jsonify({"events": [dict(r) for r in rows]})
    finally:
        conn.close()


@metrics_api.route("/push", methods=["POST"])
def push_container_metrics():
    """
    Accept a batch of container metrics from push_agent.py.
    Body: {"metrics": [{container_id, container_name, host, backend, cpu_percent, ...}]}
    """
    from flask import request as flask_request

    payload = flask_request.get_json(silent=True) or {}
    metrics = payload.get("metrics", [])
    if not metrics:
        return jsonify({"error": "no metrics in payload"}), 400

    conn = _get_db()
    try:
        conn.executemany(
            """INSERT INTO container_metrics
               (container_id, container_name, host, backend,
                cpu_percent, memory_percent, memory_rss_mb, memory_limit_mb,
                disk_read_mb, disk_write_mb, net_rx_mb, net_tx_mb, status,
                pushed, pushed_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1,datetime('now'))""",
            [
                (
                    m.get("container_id", "unknown"),
                    m.get("container_name"),
                    m.get("host", "unknown"),
                    m.get("backend", "unknown"),
                    m.get("cpu_percent"),
                    m.get("memory_percent"),
                    m.get("memory_rss_mb"),
                    m.get("memory_limit_mb"),
                    m.get("disk_read_mb"),
                    m.get("disk_write_mb"),
                    m.get("net_rx_mb"),
                    m.get("net_tx_mb"),
                    m.get("status"),
                )
                for m in metrics
            ],
        )
        conn.commit()
    finally:
        conn.close()

    return jsonify({"accepted": len(metrics)}), 200


@metrics_api.route("/containers", methods=["GET"])
def list_container_metrics():
    """Return recent container metrics, optionally filtered by host or container_name."""
    conn = _get_db()
    ph = sql_placeholder(conn)
    try:
        host = request.args.get("host")
        container = request.args.get("container")
        limit = min(int(request.args.get("limit", "200")), 2000)

        query = "SELECT * FROM container_metrics WHERE 1=1"
        params: list = []
        if host:
            query += f" AND host = {ph}"
            params.append(host)
        if container:
            query += f" AND container_name LIKE {ph}"
            params.append(f"%{container}%")
        query += f" ORDER BY collected_at DESC LIMIT {ph}"
        params.append(limit)

        rows = conn.execute(query, params).fetchall()
        return jsonify({"metrics": [dict(r) for r in rows], "total": len(rows)})
    finally:
        conn.close()


@metrics_api.route("/health", methods=["GET"])
def health_status():
    """Overall health summary from latest metrics and alerts."""
    conn = _get_db()
    try:
        # Firing alerts count
        firing = conn.execute("SELECT COUNT(*) as cnt FROM alerts WHERE status = 'firing'").fetchone()

        # Recent self-healing events (last 24h approximation: last 20)
        recent_heals = conn.execute("SELECT COUNT(*) as cnt FROM self_healing_events").fetchone()

        # Unresolved failures
        unresolved = conn.execute("SELECT COUNT(*) as cnt FROM failure_log WHERE resolved = 0").fetchone()

        # Active agents
        active_agents = conn.execute("SELECT COUNT(*) as cnt FROM agents WHERE status = 'active'").fetchone()

        # Total projects
        total_projects = conn.execute("SELECT COUNT(*) as cnt FROM projects").fetchone()

        health = "healthy"
        if (firing and firing["cnt"] > 0) or (unresolved and unresolved["cnt"] > 5):
            health = "degraded"
        if firing and firing["cnt"] > 5:
            health = "critical"

        return jsonify(
            {
                "status": health,
                "firing_alerts": firing["cnt"] if firing else 0,
                "self_healing_events": recent_heals["cnt"] if recent_heals else 0,
                "unresolved_failures": unresolved["cnt"] if unresolved else 0,
                "active_agents": active_agents["cnt"] if active_agents else 0,
                "total_projects": total_projects["cnt"] if total_projects else 0,
            }
        )
    finally:
        conn.close()
