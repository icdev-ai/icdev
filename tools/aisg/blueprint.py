#!/usr/bin/env python3
# CUI // SP-CTI
"""AISG Flask Blueprint.

Routes:
  GET /api/explain/<event_id>  — Fetch audit event and return plain-English
                                  explanation via explain_translator.translate().
"""
from __future__ import annotations

import json

from flask import Blueprint, jsonify

from tools.db.storage import get_connection

bp = Blueprint("aisg", __name__)


def _get_audit_event(event_id: int) -> dict | None:
    """Fetch a single audit_trail row by primary key."""
    conn = get_connection()
    try:
        c = conn.cursor()
        c.execute(
            "SELECT id, event_type, actor, action, details, created_at "
            "FROM audit_trail WHERE id = ?",
            (event_id,),
        )
        row = c.fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


@bp.route("/api/explain/<int:event_id>", methods=["GET"])
def explain_event(event_id: int):
    """Return a plain-English explanation for an audit trail event.

    Fetches the event from audit_trail by *event_id*, parses the JSON
    details field as event_data, then delegates to
    explain_translator.translate() for deterministic rule-based rendering.
    """
    from tools.aisg.explain_translator import translate  # local to avoid circular

    row = _get_audit_event(event_id)
    if row is None:
        return jsonify({"error": f"Event {event_id} not found"}), 404

    event_type: str = row.get("event_type", "")
    raw_details = row.get("details") or "{}"
    try:
        event_data: dict = json.loads(raw_details) if isinstance(raw_details, str) else raw_details
    except (json.JSONDecodeError, TypeError):
        event_data = {}

    explanation = translate(event_type, event_data)
    return jsonify({
        "event_id": event_id,
        "event_type": event_type,
        "actor": row.get("actor"),
        "action": row.get("action"),
        "recorded_at": row.get("created_at"),
        "explanation": explanation,
    })
