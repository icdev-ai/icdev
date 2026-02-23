# [TEMPLATE: CUI // SP-CTI]
"""Events API blueprint — HTTP poll transport, recent events, event ingest.

Endpoints:
    GET  /api/events/recent         — Recent hook events (paginated)
    GET  /api/events/poll           — HTTP poll for new events since cursor
    GET  /api/events/stream         — SSE event stream (legacy, kept for backward compat)
    GET  /api/events/filter-options — Available filter values
    POST /api/events/ingest         — Receive events from hooks

Decision D103: HTTP polling replaces SSE as primary transport. SSE kept for
backward compatibility. HTTP polling is more proxy/firewall friendly for DoD
networks, works with Flask's synchronous WSGI, and avoids long-lived connections.
"""

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, Response, jsonify, request

events_bp = Blueprint("events_api", __name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

# Default poll interval recommended to clients (ms)
DEFAULT_POLL_INTERVAL_MS = 3000


def _get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


@events_bp.route("/api/events/recent", methods=["GET"])
def get_recent_events():
    """Return recent hook events with pagination."""
    limit = min(int(request.args.get("limit", 50)), 200)
    offset = int(request.args.get("offset", 0))
    hook_type = request.args.get("hook_type")
    tool_name = request.args.get("tool_name")
    severity = request.args.get("severity")

    conn = _get_db()
    try:
        query = "SELECT * FROM hook_events WHERE 1=1"
        params = []

        if hook_type:
            query += " AND hook_type = ?"
            params.append(hook_type)
        if tool_name:
            query += " AND tool_name = ?"
            params.append(tool_name)
        if severity:
            query += " AND severity = ?"
            params.append(severity)

        query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = conn.execute(query, params).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) as cnt FROM hook_events"
        ).fetchone()["cnt"]

        return jsonify({
            "events": [dict(r) for r in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
            "classification": "CUI",
        })
    finally:
        conn.close()


@events_bp.route("/api/events/poll", methods=["GET"])
def poll_events():
    """HTTP polling endpoint for real-time event updates.

    Returns events newer than the given cursor timestamp.
    Clients call this on an interval (recommended: 3s) instead of
    maintaining a long-lived SSE connection.

    Query params:
        since     — ISO 8601 timestamp cursor; returns only events after this time
        limit     — Max events to return (default 25, max 100)
        severity  — Filter by severity (e.g. critical, high, warning, info)
        hook_type — Filter by hook type (e.g. PreToolUse, PostToolUse)
        tool_name — Filter by tool name

    Returns:
        {events: [...], cursor: "<new_cursor>", poll_interval_ms: 3000}
    """
    since = request.args.get("since", "")
    limit = min(int(request.args.get("limit", 25)), 100)
    severity = request.args.get("severity", "")
    hook_type = request.args.get("hook_type", "")
    tool_name = request.args.get("tool_name", "")

    conn = _get_db()
    try:
        query = "SELECT * FROM hook_events WHERE 1=1"
        params: list = []

        if since:
            query += " AND created_at > ?"
            params.append(since)
        if severity:
            query += " AND severity = ?"
            params.append(severity)
        if hook_type:
            query += " AND hook_type = ?"
            params.append(hook_type)
        if tool_name:
            query += " AND tool_name = ?"
            params.append(tool_name)

        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(query, params).fetchall()
        events = [dict(r) for r in rows]
        # New cursor = most recent event timestamp, or pass through old cursor
        new_cursor = events[0]["created_at"] if events else since

        return jsonify({
            "events": events,
            "cursor": new_cursor,
            "count": len(events),
            "poll_interval_ms": DEFAULT_POLL_INTERVAL_MS,
            "classification": "CUI",
        })
    finally:
        conn.close()


@events_bp.route("/api/events/stream")
def event_stream():
    """SSE endpoint for real-time event streaming (legacy — kept for backward compat).

    Prefer /api/events/poll for new integrations (D103).
    """
    from tools.dashboard.sse_manager import sse_manager

    client_queue = sse_manager.add_client()

    def generate():
        try:
            yield from sse_manager.generate_stream(client_queue)
        finally:
            sse_manager.remove_client(client_queue)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@events_bp.route("/api/events/filter-options", methods=["GET"])
def get_filter_options():
    """Return distinct hook types and tool names for filter dropdowns."""
    conn = _get_db()
    try:
        hook_types = [r["hook_type"] for r in conn.execute(
            "SELECT DISTINCT hook_type FROM hook_events ORDER BY hook_type"
        ).fetchall()]
        tool_names = [r["tool_name"] for r in conn.execute(
            "SELECT DISTINCT tool_name FROM hook_events WHERE tool_name IS NOT NULL ORDER BY tool_name"
        ).fetchall()]
        return jsonify({
            "hook_types": hook_types,
            "tool_names": tool_names,
            "classification": "CUI",
        })
    finally:
        conn.close()


@events_bp.route("/api/events/ingest", methods=["POST"])
def ingest_event():
    """Receive events from hooks — persist to DB and broadcast to SSE clients.

    Changed in D103: events are now written to hook_events table so that
    HTTP poll clients can pick them up. SSE broadcast kept for legacy clients.
    """
    from tools.dashboard.sse_manager import sse_manager

    data = request.get_json(force=True)
    if not data:
        return jsonify({"status": "ok"}), 200

    # Persist to DB so HTTP poll clients can retrieve it
    conn = _get_db()
    try:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT OR IGNORE INTO hook_events "
            "(id, hook_type, tool_name, session_id, project_id, "
            " severity, message, payload, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                data.get("id", str(uuid.uuid4())),
                data.get("hook_type", ""),
                data.get("tool_name", data.get("tool", "")),
                data.get("session_id", ""),
                data.get("project_id", ""),
                data.get("severity", "info"),
                data.get("message", ""),
                json.dumps(data),
                data.get("timestamp", data.get("created_at", now)),
            ),
        )
        conn.commit()
    except Exception:
        pass  # Best-effort persist; don't block ingest
    finally:
        conn.close()

    # SSE broadcast for legacy clients
    sse_manager.broadcast(data, event_type="hook_event")

    # WebSocket broadcast for activity feed (D170)
    try:
        from tools.dashboard.websocket import broadcast_activity
        broadcast_activity({
            "source": "hook",
            "id": data.get("id", ""),
            "event_type": data.get("hook_type", ""),
            "actor_or_agent": data.get("agent_id", data.get("tool_name", "")),
            "summary": data.get("tool_name", data.get("message", "")),
            "project_id": data.get("project_id", ""),
            "classification": data.get("classification", ""),
            "created_at": data.get("timestamp", data.get("created_at", "")),
        })
    except Exception:
        pass  # WebSocket broadcast is best-effort

    return jsonify({"status": "ok"}), 200
