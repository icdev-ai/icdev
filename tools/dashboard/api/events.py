# CUI // SP-CTI
"""Events API blueprint — SSE streaming, recent events, event ingest.

Endpoints:
    GET  /api/events/recent         — Recent hook events (paginated)
    GET  /api/events/stream         — SSE event stream
    GET  /api/events/filter-options — Available filter values
    POST /api/events/ingest         — Receive events from hooks
"""

import json
import sqlite3
from pathlib import Path

from flask import Blueprint, Response, jsonify, request

events_bp = Blueprint("events_api", __name__)

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"


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


@events_bp.route("/api/events/stream")
def event_stream():
    """SSE endpoint for real-time event streaming."""
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
    """Receive events from hooks and broadcast to SSE clients."""
    from tools.dashboard.sse_manager import sse_manager

    data = request.get_json(force=True)
    if data:
        sse_manager.broadcast(data, event_type="hook_event")
    return jsonify({"status": "ok"}), 200
