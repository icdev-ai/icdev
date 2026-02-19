# CUI // SP-CTI
"""
Flask Blueprint for agent API endpoints.
Queries the agents table in icdev.db.
"""

import sqlite3
from flask import Blueprint, jsonify

from tools.dashboard.config import DB_PATH

agents_api = Blueprint("agents_api", __name__, url_prefix="/api/agents")


def _get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@agents_api.route("", methods=["GET"])
def list_agents():
    """Return all agents with their status and capabilities."""
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT id, name, description, url, status, capabilities, "
            "last_heartbeat, created_at FROM agents ORDER BY name"
        ).fetchall()
        agents = []
        for r in rows:
            agent = dict(r)
            # Count active tasks for this agent
            task_count = conn.execute(
                "SELECT COUNT(*) as cnt FROM a2a_tasks "
                "WHERE target_agent_id = ? AND status IN ('submitted', 'working')",
                (agent["id"],),
            ).fetchone()
            agent["active_task_count"] = task_count["cnt"] if task_count else 0
            agents.append(agent)

        # Summary counts
        active = sum(1 for a in agents if a["status"] == "active")
        inactive = len(agents) - active

        return jsonify({
            "agents": agents,
            "total": len(agents),
            "active": active,
            "inactive": inactive,
        })
    finally:
        conn.close()
