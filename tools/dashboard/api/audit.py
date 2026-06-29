# [TEMPLATE: CUI // SP-CTI]
"""
Flask Blueprint for audit API.
Queries the audit_trail table in icdev.db.
"""

from tools.db.storage import get_connection, sql_placeholder
from flask import Blueprint, jsonify, request

from tools.dashboard.config import DB_PATH

audit_api = Blueprint("audit_api", __name__, url_prefix="/api/audit")


def _get_db():
    conn = get_connection(db_path=str(DB_PATH))
    return conn


@audit_api.route("", methods=["GET"])
def list_audit_entries():
    """
    Return audit trail entries.
    Optional query params: project_id, event_type, actor, limit (default 50).
    """
    conn = _get_db()
    ph = sql_placeholder(conn)
    try:
        project_id = request.args.get("project_id")
        event_type = request.args.get("event_type")
        actor = request.args.get("actor")
        limit = request.args.get("limit", "50", type=str)

        try:
            limit_int = min(int(limit), 500)
        except ValueError:
            limit_int = 50

        query = "SELECT * FROM audit_trail WHERE 1=1"
        params: list = []

        if project_id:
            query += f" AND project_id = {ph}"
            params.append(project_id)
        if event_type:
            query += f" AND event_type = {ph}"
            params.append(event_type)
        if actor:
            query += f" AND actor = {ph}"
            params.append(actor)

        query += f" ORDER BY created_at DESC LIMIT {ph}"
        params.append(limit_int)

        rows = conn.execute(query, params).fetchall()
        return jsonify({"entries": [dict(r) for r in rows], "total": len(rows)})
    finally:
        conn.close()
