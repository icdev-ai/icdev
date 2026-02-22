# CUI // SP-CTI
"""
Activity feed API blueprint (Phase 30 — D174).

Merges audit_trail + hook_events via UNION ALL for a unified activity feed.
Read-only, preserves append-only contract (D6).
"""

import sqlite3

from flask import Blueprint, jsonify, request

from tools.dashboard.config import DB_PATH

activity_api = Blueprint("activity_api", __name__, url_prefix="/api/activity")


def _get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Merged activity feed query (D174)
# ---------------------------------------------------------------------------

MERGED_QUERY = """
SELECT * FROM (
    SELECT
        'audit' AS source,
        id,
        event_type,
        actor AS actor_or_agent,
        action AS summary,
        project_id,
        classification,
        created_at
    FROM audit_trail

    UNION ALL

    SELECT
        'hook' AS source,
        id,
        hook_type AS event_type,
        session_id AS actor_or_agent,
        tool_name AS summary,
        project_id,
        classification,
        created_at
    FROM hook_events
) merged
WHERE 1=1
"""


@activity_api.route("/feed")
def activity_feed():
    """Merged activity feed with filters and pagination."""
    # Filters
    source = request.args.get("source")  # audit, hook
    event_type = request.args.get("event_type")
    actor = request.args.get("actor")
    project_id = request.args.get("project_id")
    since = request.args.get("since")  # ISO timestamp
    limit = min(int(request.args.get("limit", "100")), 500)
    offset = int(request.args.get("offset", "0"))

    query = MERGED_QUERY
    params = []

    if source:
        query += " AND source = ?"
        params.append(source)
    if event_type:
        query += " AND event_type = ?"
        params.append(event_type)
    if actor:
        query += " AND actor_or_agent LIKE ?"
        params.append(f"%{actor}%")
    if project_id:
        query += " AND project_id = ?"
        params.append(project_id)
    if since:
        query += " AND created_at >= ?"
        params.append(since)

    query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    conn = _get_db()
    try:
        rows = conn.execute(query, params).fetchall()
        events = [dict(r) for r in rows]
        return jsonify({"events": events, "count": len(events), "offset": offset, "limit": limit})
    finally:
        conn.close()


@activity_api.route("/poll")
def activity_poll():
    """Cursor-based polling — returns events newer than the given cursor."""
    cursor = request.args.get("cursor", "")
    limit = min(int(request.args.get("limit", "50")), 200)

    query = MERGED_QUERY
    params = []

    if cursor:
        query += " AND created_at > ?"
        params.append(cursor)

    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    conn = _get_db()
    try:
        rows = conn.execute(query, params).fetchall()
        events = [dict(r) for r in rows]
        new_cursor = events[0]["created_at"] if events else cursor
        return jsonify({"events": events, "cursor": new_cursor, "count": len(events)})
    finally:
        conn.close()


@activity_api.route("/filter-options")
def activity_filter_options():
    """Return unique values for filter dropdowns."""
    conn = _get_db()
    try:
        # Event types from both tables
        audit_types = conn.execute(
            "SELECT DISTINCT event_type FROM audit_trail ORDER BY event_type"
        ).fetchall()
        hook_types = conn.execute(
            "SELECT DISTINCT hook_type AS event_type FROM hook_events ORDER BY hook_type"
        ).fetchall()

        # Actors / agents
        actors = conn.execute(
            "SELECT DISTINCT actor FROM audit_trail WHERE actor IS NOT NULL ORDER BY actor"
        ).fetchall()
        agents = conn.execute(
            "SELECT DISTINCT session_id AS agent_id FROM hook_events WHERE session_id IS NOT NULL ORDER BY session_id"
        ).fetchall()

        # Projects
        projects = conn.execute(
            "SELECT DISTINCT project_id FROM audit_trail WHERE project_id IS NOT NULL ORDER BY project_id"
        ).fetchall()

        return jsonify({
            "event_types": sorted(set(
                [r["event_type"] for r in audit_types if r["event_type"]] +
                [r["event_type"] for r in hook_types if r["event_type"]]
            )),
            "actors": sorted(set(
                [r["actor"] for r in actors if r["actor"]] +
                [r["agent_id"] for r in agents if r["agent_id"]]
            )),
            "projects": [r["project_id"] for r in projects if r["project_id"]],
            "sources": ["audit", "hook"],
        })
    finally:
        conn.close()


@activity_api.route("/stats")
def activity_stats():
    """Activity statistics for stat cards."""
    conn = _get_db()
    try:
        audit_count = conn.execute("SELECT COUNT(*) as cnt FROM audit_trail").fetchone()["cnt"]
        hook_count = conn.execute("SELECT COUNT(*) as cnt FROM hook_events").fetchone()["cnt"]

        # Today's events
        audit_today = conn.execute(
            "SELECT COUNT(*) as cnt FROM audit_trail WHERE DATE(created_at) = DATE('now')"
        ).fetchone()["cnt"]
        hook_today = conn.execute(
            "SELECT COUNT(*) as cnt FROM hook_events WHERE DATE(created_at) = DATE('now')"
        ).fetchone()["cnt"]

        # Last hour
        audit_hour = conn.execute(
            "SELECT COUNT(*) as cnt FROM audit_trail WHERE created_at >= datetime('now', '-1 hour')"
        ).fetchone()["cnt"]
        hook_hour = conn.execute(
            "SELECT COUNT(*) as cnt FROM hook_events WHERE created_at >= datetime('now', '-1 hour')"
        ).fetchone()["cnt"]

        return jsonify({
            "total": audit_count + hook_count,
            "audit_total": audit_count,
            "hook_total": hook_count,
            "today": audit_today + hook_today,
            "last_hour": audit_hour + hook_hour,
        })
    except Exception:
        return jsonify({
            "total": 0, "audit_total": 0, "hook_total": 0,
            "today": 0, "last_hour": 0,
        })
    finally:
        conn.close()
