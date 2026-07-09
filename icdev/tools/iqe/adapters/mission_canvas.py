# CUI // SP-CTI
"""IQE collection adapters for Mission Canvas.

Registers:
  mission.sessions   — mission_canvas_sessions
  mission.twins      — mission_canvas_twins
  mission.evidence   — mission_canvas_evidence
  mission.alerts     — mission_canvas_alerts
"""

from __future__ import annotations

from typing import Any

from tools.iqe.executor import register_collection


def _get_conn():
    from tools.db.storage import get_connection

    return get_connection()


def sessions_adapter(conn: Any) -> list[dict]:
    """Return rows from mission_canvas_sessions."""
    if conn is None:
        conn = _get_conn()
    cur = conn.execute(
        "SELECT id, name, status, classification, metadata, created_at, updated_at "
        "FROM mission_canvas_sessions"
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def twins_adapter(conn: Any) -> list[dict]:
    """Return rows from mission_canvas_twins."""
    if conn is None:
        conn = _get_conn()
    cur = conn.execute(
        "SELECT id, session_id, twin_type, snapshot_json, classification, created_at "
        "FROM mission_canvas_twins"
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def evidence_adapter(conn: Any) -> list[dict]:
    """Return rows from mission_canvas_evidence."""
    if conn is None:
        conn = _get_conn()
    cur = conn.execute(
        "SELECT id, session_id, artifact_id, artifact_type, source, actor, classification, metadata, created_at "
        "FROM mission_canvas_evidence"
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def alerts_adapter(conn: Any) -> list[dict]:
    """Return rows from mission_canvas_alerts."""
    if conn is None:
        conn = _get_conn()
    cur = conn.execute(
        "SELECT id, session_id, alert_type, severity, message, classification, acknowledged, created_at "
        "FROM mission_canvas_alerts"
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


register_collection("mission.sessions", sessions_adapter)
register_collection("mission.twins", twins_adapter)
register_collection("mission.evidence", evidence_adapter)
register_collection("mission.alerts", alerts_adapter)
