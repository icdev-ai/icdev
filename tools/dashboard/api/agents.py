# [TEMPLATE: CUI // SP-CTI]
"""
Flask Blueprint for agent API endpoints.
Queries the agents table in icdev.db.
"""

from tools.db.storage import get_connection
from flask import Blueprint, jsonify, request

from tools.dashboard.config import DB_PATH

agents_api = Blueprint("agents_api", __name__, url_prefix="/api/agents")


def _get_db():
    conn = get_connection(db_path=str(DB_PATH))
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

        return jsonify(
            {
                "agents": agents,
                "total": len(agents),
                "active": active,
                "inactive": inactive,
            }
        )
    finally:
        conn.close()


@agents_api.route("/discover", methods=["GET"])
def discover_agents():
    """Discover agents by capability (adapt-iii-02).

    Query params:
        capability (str): Capability name to filter by. Omit to list all.

    Returns:
        {capability, agents: [{name, port, capabilities, tier, base_url}], total}
    """
    capability = request.args.get("capability", "").strip()
    try:
        from tools.agents.a2a_registry import get_registry
        registry = get_registry()
        if capability:
            matches = registry.discover(capability)
        else:
            matches = registry.all_agents()
        return jsonify({
            "capability": capability or None,
            "agents": [
                {
                    "name": a.name,
                    "port": a.port,
                    "capabilities": a.capabilities,
                    "tier": a.tier,
                    "base_url": a.base_url(),
                    "description": a.description,
                }
                for a in matches
            ],
            "total": len(matches),
        })
    except Exception as exc:
        return jsonify({"error": str(exc), "agents": [], "total": 0}), 500
