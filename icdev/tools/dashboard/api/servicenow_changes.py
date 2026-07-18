#!/usr/bin/env python3
# CUI // SP-CTI
"""Dashboard API: ServiceNow Change Management Tickets.

CRUD + sync endpoints for ServiceNow change_request records.
Leverages existing integration_connections table (system_type='servicenow').
"""

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, jsonify, request

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection, table_exists  # noqa: E402

DB_PATH = BASE_DIR / "data" / "icdev.db"

servicenow_changes_api = Blueprint("servicenow_changes_api", __name__, url_prefix="/api/servicenow")


def _get_db():
    conn = get_connection(db_path=str(DB_PATH))
    return conn


def _table_exists(conn, name):
    """Check if a table exists (works for both SQLite and PostgreSQL)."""
    return table_exists(conn, name)


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# GET /api/servicenow/connections
# ---------------------------------------------------------------------------
@servicenow_changes_api.route("/connections", methods=["GET"])
def list_connections():
    """List active ServiceNow integration connections."""
    conn = None
    try:
        conn = _get_db()
        if not _table_exists(conn, "integration_connections"):
            return jsonify({"connections": []})

        rows = conn.execute(
            "SELECT id, project_id, instance_url, sync_status, last_sync, created_at "
            "FROM integration_connections WHERE system_type = 'servicenow' "
            "ORDER BY created_at DESC"
        ).fetchall()

        connections = []
        for r in rows:
            connections.append(
                {
                    "id": r[0],
                    "project_id": r[1],
                    "instance_url": r[2],
                    "sync_status": r[3],
                    "last_sync": r[4],
                    "created_at": r[5],
                }
            )

        return jsonify({"connections": connections, "count": len(connections)})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        if conn:
            conn.close()


# ---------------------------------------------------------------------------
# GET /api/servicenow/changes
# ---------------------------------------------------------------------------
@servicenow_changes_api.route("/changes", methods=["GET"])
def list_changes():
    """List change management tickets with filters."""
    conn = None
    try:
        conn = _get_db()
        if not _table_exists(conn, "servicenow_change_tickets"):
            return jsonify({"tickets": [], "total": 0, "page": 1, "per_page": 25})

        state = request.args.get("state")
        priority = request.args.get("priority")
        project_id = request.args.get("project_id")
        page = max(int(request.args.get("page", 1)), 1)
        per_page = min(max(int(request.args.get("per_page", 25)), 1), 100)

        where_clauses = []
        params = []
        if state:
            where_clauses.append("state = ?")
            params.append(state)
        if priority:
            where_clauses.append("priority = ?")
            params.append(priority)
        if project_id:
            where_clauses.append("project_id = ?")
            params.append(project_id)

        where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

        total = conn.execute(
            f"SELECT COUNT(*) FROM servicenow_change_tickets{where_sql}",
            params,
        ).fetchone()[0]

        rows = conn.execute(
            f"SELECT id, number, short_description, type, state, priority, risk, impact, "
            f"assignment_group, assigned_to, start_date, end_date, approval, close_code, "
            f"project_id, migration_plan_id, sync_status, created_at, updated_at "
            f"FROM servicenow_change_tickets{where_sql} "
            f"ORDER BY updated_at DESC LIMIT %s OFFSET %s",
            params + [per_page, (page - 1) * per_page],
        ).fetchall()

        tickets = []
        for r in rows:
            tickets.append(
                {
                    "id": r[0],
                    "number": r[1],
                    "short_description": r[2],
                    "type": r[3],
                    "state": r[4],
                    "priority": r[5],
                    "risk": r[6],
                    "impact": r[7],
                    "assignment_group": r[8],
                    "assigned_to": r[9],
                    "start_date": r[10],
                    "end_date": r[11],
                    "approval": r[12],
                    "close_code": r[13],
                    "project_id": r[14],
                    "migration_plan_id": r[15],
                    "sync_status": r[16],
                    "created_at": r[17],
                    "updated_at": r[18],
                }
            )

        return jsonify({"tickets": tickets, "total": total, "page": page, "per_page": per_page})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        if conn:
            conn.close()


# ---------------------------------------------------------------------------
# GET /api/servicenow/changes/<ticket_id>
# ---------------------------------------------------------------------------
@servicenow_changes_api.route("/changes/<ticket_id>", methods=["GET"])
def get_change(ticket_id):
    """Get a single change ticket detail."""
    conn = None
    try:
        conn = _get_db()
        if not _table_exists(conn, "servicenow_change_tickets"):
            return jsonify({"error": "Table not found"}), 404

        row = conn.execute(
            "SELECT id, connection_id, sys_id, number, short_description, description, "
            "type, state, priority, risk, impact, category, assignment_group, assigned_to, "
            "requested_by, start_date, end_date, approval, close_code, close_notes, "
            "project_id, migration_plan_id, sync_status, raw_json, created_at, updated_at, last_synced "
            "FROM servicenow_change_tickets WHERE id = %s",
            (ticket_id,),
        ).fetchone()

        if not row:
            return jsonify({"error": "Ticket not found"}), 404

        ticket = {
            "id": row[0],
            "connection_id": row[1],
            "sys_id": row[2],
            "number": row[3],
            "short_description": row[4],
            "description": row[5],
            "type": row[6],
            "state": row[7],
            "priority": row[8],
            "risk": row[9],
            "impact": row[10],
            "category": row[11],
            "assignment_group": row[12],
            "assigned_to": row[13],
            "requested_by": row[14],
            "start_date": row[15],
            "end_date": row[16],
            "approval": row[17],
            "close_code": row[18],
            "close_notes": row[19],
            "project_id": row[20],
            "migration_plan_id": row[21],
            "sync_status": row[22],
            "raw_json": row[23],
            "created_at": row[24],
            "updated_at": row[25],
            "last_synced": row[26],
        }

        return jsonify(ticket)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        if conn:
            conn.close()


# ---------------------------------------------------------------------------
# POST /api/servicenow/changes/sync
# ---------------------------------------------------------------------------
@servicenow_changes_api.route("/changes/sync", methods=["POST"])
def sync_changes():
    """Simulate a sync pull from ServiceNow change_request table.

    In production this would call the ServiceNow Table API.
    For now, generates representative demo records if none exist.
    """
    data = request.get_json(force=True, silent=True) or {}
    connection_id = data.get("connection_id")
    project_id = data.get("project_id")

    conn = None
    try:
        conn = _get_db()
        if not _table_exists(conn, "servicenow_change_tickets"):
            return jsonify({"error": "servicenow_change_tickets table not found"}), 501

        now = _now()

        # If no connection_id provided, find the latest active ServiceNow connection
        if not connection_id:
            row = conn.execute(
                "SELECT id FROM integration_connections WHERE system_type = 'servicenow' "
                "AND sync_status != 'disabled' ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if row:
                connection_id = row[0]

        if not connection_id:
            return jsonify({"error": "No active ServiceNow connection found"}), 400

        # Check existing count
        existing = conn.execute(
            "SELECT COUNT(*) FROM servicenow_change_tickets WHERE connection_id = %s",
            (connection_id,),
        ).fetchone()[0]

        created = 0
        if existing == 0:
            # Seed demo data
            demo_states = ["new", "assess", "authorize", "scheduled", "implement", "review", "closed"]
            demo_types = ["normal", "standard", "emergency"]
            demo_risks = ["low", "moderate", "high", "very_high"]
            demo_priorities = ["1", "2", "3", "4", "5"]
            demo_approvals = ["not requested", "requested", "approved", "rejected"]

            import random

            for i in range(12):
                ticket_id = f"sn-{uuid.uuid4().hex[:8]}"
                state = random.choice(demo_states)
                approval = "approved" if state in ("scheduled", "implement", "review", "closed") else random.choice(demo_approvals)
                close_code = "Successful" if state == "closed" else None

                conn.execute(
                    """INSERT INTO servicenow_change_tickets
                       (id, connection_id, sys_id, number, short_description, description,
                        type, state, priority, risk, impact, category, assignment_group,
                        assigned_to, requested_by, start_date, end_date, approval, close_code,
                        close_notes, project_id, migration_plan_id, sync_status, raw_json,
                        created_at, updated_at, last_synced)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        ticket_id,
                        connection_id,
                        f"{uuid.uuid4().hex[:32]}",
                        f"CHG{10000 + i}",
                        f"Demo change request {i + 1}",
                        "Automated demo change ticket for testing ServiceNow integration.",
                        random.choice(demo_types),
                        state,
                        random.choice(demo_priorities),
                        random.choice(demo_risks),
                        random.choice(demo_risks),
                        "Application Migration",
                        "Change Management",
                        "admin",
                        "admin",
                        now,
                        now,
                        approval,
                        close_code,
                        None,
                        project_id,
                        None,
                        "synced",
                        json.dumps({"demo": True}),
                        now, now, now,
                    ),
                )
                created += 1

            conn.commit()

        # Update connection last_sync
        conn.execute(
            "UPDATE integration_connections SET last_sync = %s, updated_at = %s WHERE id = %s",
            (now, now, connection_id),
        )
        conn.commit()

        return jsonify({
            "synced": True,
            "connection_id": connection_id,
            "created": created,
            "existing": existing,
            "synced_at": now,
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        if conn:
            conn.close()


# ---------------------------------------------------------------------------
# GET /api/servicenow/changes/stats
# ---------------------------------------------------------------------------
@servicenow_changes_api.route("/changes/stats", methods=["GET"])
def change_stats():
    """Aggregate stats for change tickets."""
    conn = None
    try:
        conn = _get_db()
        if not _table_exists(conn, "servicenow_change_tickets"):
            return jsonify({
                "total": 0,
                "by_state": {},
                "by_risk": {},
                "by_priority": {},
                "approval_pending": 0,
            })

        total = conn.execute("SELECT COUNT(*) FROM servicenow_change_tickets").fetchone()[0]

        by_state = {}
        for r in conn.execute("SELECT state, COUNT(*) FROM servicenow_change_tickets GROUP BY state").fetchall():
            by_state[r[0]] = r[1]

        by_risk = {}
        for r in conn.execute("SELECT risk, COUNT(*) FROM servicenow_change_tickets GROUP BY risk").fetchall():
            by_risk[r[0]] = r[1]

        by_priority = {}
        for r in conn.execute("SELECT priority, COUNT(*) FROM servicenow_change_tickets GROUP BY priority").fetchall():
            by_priority[r[0]] = r[1]

        pending = conn.execute(
            "SELECT COUNT(*) FROM servicenow_change_tickets WHERE approval = 'requested'"
        ).fetchone()[0]

        return jsonify({
            "total": total,
            "by_state": by_state,
            "by_risk": by_risk,
            "by_priority": by_priority,
            "approval_pending": pending,
        })
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        if conn:
            conn.close()


# ---------------------------------------------------------------------------
# POST /api/servicenow/changes/<ticket_id>/link
# ---------------------------------------------------------------------------
@servicenow_changes_api.route("/changes/<ticket_id>/link", methods=["POST"])
def link_change(ticket_id):
    """Link a change ticket to a project or migration plan."""
    data = request.get_json(force=True, silent=True) or {}
    project_id = data.get("project_id")
    migration_plan_id = data.get("migration_plan_id")

    if not project_id and not migration_plan_id:
        return jsonify({"error": "project_id or migration_plan_id required"}), 400

    conn = None
    try:
        conn = _get_db()
        if not _table_exists(conn, "servicenow_change_tickets"):
            return jsonify({"error": "Table not found"}), 404

        now = _now()
        updates = []
        params = []
        if project_id:
            updates.append("project_id = ?")
            params.append(project_id)
        if migration_plan_id:
            updates.append("migration_plan_id = ?")
            params.append(migration_plan_id)
        updates.append("updated_at = ?")
        params.append(now)
        params.append(ticket_id)

        conn.execute(
            f"UPDATE servicenow_change_tickets SET {', '.join(updates)} WHERE id = %s",
            params,
        )
        conn.commit()

        return jsonify({"ok": True, "ticket_id": ticket_id, "updated_at": now})
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        if conn:
            conn.close()
