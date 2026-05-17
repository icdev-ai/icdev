# CUI // SP-CTI
"""Flask Blueprint for IL5 data ingestion and display API.

Exposes the publication feed endpoint polled by il5_ingestion_service.py,
plus query and SLA summary endpoints for dashboard display.

NIST 800-53: AU-2, AU-12, SC-28, SI-12.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

from flask import Blueprint, jsonify, request

from tools.il5.ingestion import (
    IL5_CLASSIFICATION,
    IL5_IMPACT_LEVEL,
    SLA_SECONDS,
    create_il5_schema,
    get_il5_events,
    get_sla_summary,
    ingest_il5_event,
)
from tools.il5.il5_ingestion_service import parse_il5_payload
from tools.il5.il5_display_service import _format_row
from tools.db.storage import get_connection
from tools.dashboard.config import DB_PATH

il5_api = Blueprint("il5_api", __name__, url_prefix="/api/il5")

_CUI_HEADER = f"CUI // {IL5_CLASSIFICATION} // {IL5_IMPACT_LEVEL}"


def _ensure_schema() -> None:
    conn = get_connection(db_path=str(DB_PATH))
    try:
        create_il5_schema(conn)
    except Exception as exc:
        # PostgreSQL can raise UniqueViolation on concurrent CREATE TABLE IF NOT EXISTS
        # when the composite type already exists. Swallow only that race.
        msg = str(exc).lower()
        if "unique violation" not in msg and "already exists" not in msg:
            raise
    finally:
        conn.close()


@il5_api.route("/feed", methods=["GET"])
def get_feed():
    """Publication feed polled by il5_ingestion_service.py.

    Returns the most recent IL5 events as a JSON array.  The ingestion service
    calls this endpoint on a 3-second polling cadence and persists any new
    records to its own database.

    Query params:
        since (str): ISO 8601 cursor — only events published after this time.
        limit (int): Max records to return (default 50, max 200).
    """
    _ensure_schema()
    since = request.args.get("since")
    try:
        limit = min(int(request.args.get("limit", 50)), 200)
    except (TypeError, ValueError):
        limit = 50

    events = get_il5_events(since=since, limit=limit)
    return jsonify(events), 200


@il5_api.route("/events", methods=["GET"])
def list_events():
    """Return IL5 ingestion events for dashboard display.

    Query params:
        since (str): ISO 8601 cursor.
        limit (int): Max records (default 25, max 200).
    """
    _ensure_schema()
    since = request.args.get("since")
    try:
        limit = min(int(request.args.get("limit", 25)), 200)
    except (TypeError, ValueError):
        limit = 25

    events = get_il5_events(since=since, limit=limit)
    events = [_format_row(ev) for ev in events]
    return jsonify({
        "classification": _CUI_HEADER,
        "impact_level": IL5_IMPACT_LEVEL,
        "sla_threshold_s": SLA_SECONDS,
        "events": events,
        "total": len(events),
    }), 200


@il5_api.route("/sla-summary", methods=["GET"])
def sla_summary():
    """Return aggregate SLA compliance statistics for IL5 events."""
    _ensure_schema()
    summary = get_sla_summary()
    summary["classification"] = _CUI_HEADER
    return jsonify(summary), 200


@il5_api.route("/ingest", methods=["POST"])
def ingest_event():
    """Manually ingest a single IL5 event.

    Request body (JSON):
        source_id (str): Identifier for the upstream data source.
        content (str): Raw payload content (hashed for dedup).
        published_at (str, optional): ISO 8601 publication timestamp.
        metadata (dict, optional): Additional context.
    """
    _ensure_schema()
    body: Dict[str, Any] = request.get_json(silent=True) or {}

    try:
        parsed = parse_il5_payload(body)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 400

    event_id = ingest_il5_event(
        parsed["source_id"],
        parsed["content"],
        source_published_at=parsed["source_published_at"],
        metadata=parsed["metadata"],
    )
    return jsonify({
        "event_id": event_id,
        "classification": _CUI_HEADER,
        "impact_level": IL5_IMPACT_LEVEL,
        "sla_threshold_s": SLA_SECONDS,
        "ingested_at": datetime.now(timezone.utc).isoformat(),
    }), 201
