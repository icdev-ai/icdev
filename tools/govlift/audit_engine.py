# CUI // SP-CTI
"""GovLift — Audit Engine.

Append-only audit log for all GovLift operations.
Compliant with NIST 800-53 AU family controls.
All DB access via get_connection() — never sqlite3.connect().
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

_ICDEV_ROOT = Path(__file__).resolve().parents[2]
if str(_ICDEV_ROOT) not in sys.path:
    sys.path.insert(0, str(_ICDEV_ROOT))

from tools.db.storage import get_connection, translate_sql
from tools.govlift.constants import CLASSIFICATION


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _audit_id() -> str:
    return "audit-" + uuid4().hex[:10]


def _row_to_dict(row) -> dict:
    if row is None:
        return {}
    try:
        d = dict(row)
        # Deserialize details JSON string → dict
        raw = d.get("details")
        if isinstance(raw, str):
            try:
                d["details"] = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                pass
        return d
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Public API — append-only (no UPDATE / DELETE)
# ---------------------------------------------------------------------------


def log_action(
    user_id: str,
    action: str,
    resource_type: str = "",
    resource_id: str = "",
    details: dict | None = None,
    ip_address: str = "",
    session_id: str = "",
) -> dict:
    """Append a new audit event and return the created record.

    This table is APPEND-ONLY per NIST AU. Never call UPDATE or DELETE.
    """
    audit_id = _audit_id()
    now = _now()
    details_str = json.dumps(details or {})

    try:
        conn = get_connection()
        try:
            sql = translate_sql(
                "INSERT INTO govlift_audit_log "
                "(id, user_id, action, resource_type, resource_id, details, "
                " classification, ip_address, session_id, timestamp) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)"
            )
            conn.execute(
                sql,
                (
                    audit_id, user_id, action, resource_type, resource_id,
                    details_str, CLASSIFICATION, ip_address, session_id, now,
                ),
            )
            conn.commit()
            row = conn.execute(
                translate_sql("SELECT * FROM govlift_audit_log WHERE id = ?"),
                (audit_id,),
            ).fetchone()
            return _row_to_dict(row) if row else {}
        finally:
            conn.close()
    except Exception as exc:
        print(f"audit_engine.log_action error: {exc}", file=sys.stderr)
        raise


def list_audit_log(
    user_id: str | None = None,
    action: str | None = None,
    resource_type: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """Return audit log entries, newest first, with optional filters."""
    try:
        conn = get_connection()
        try:
            clauses = []
            params: list = []
            if user_id:
                clauses.append("user_id = ?")
                params.append(user_id)
            if action:
                clauses.append("action = ?")
                params.append(action)
            if resource_type:
                clauses.append("resource_type = ?")
                params.append(resource_type)

            where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
            sql = translate_sql(
                f"SELECT * FROM govlift_audit_log {where} "
                f"ORDER BY timestamp DESC LIMIT ?"
            )
            params.append(limit)
            rows = conn.execute(sql, params).fetchall()
            return [_row_to_dict(r) for r in rows]
        finally:
            conn.close()
    except Exception as exc:
        print(f"audit_engine.list_audit_log error: {exc}", file=sys.stderr)
        return []


def get_audit_summary() -> dict:
    """Return total event count, unique user count, and action breakdown."""
    try:
        conn = get_connection()
        try:
            total_row = conn.execute(
                translate_sql("SELECT COUNT(*) AS cnt FROM govlift_audit_log")
            ).fetchone()
            users_row = conn.execute(
                translate_sql(
                    "SELECT COUNT(DISTINCT user_id) AS cnt FROM govlift_audit_log"
                )
            ).fetchone()
            action_rows = conn.execute(
                translate_sql(
                    "SELECT action, COUNT(*) AS cnt "
                    "FROM govlift_audit_log GROUP BY action ORDER BY cnt DESC"
                )
            ).fetchall()

            total = _row_to_dict(total_row).get("cnt", 0) if total_row else 0
            unique_users = _row_to_dict(users_row).get("cnt", 0) if users_row else 0
            action_breakdown = {r["action"]: r["cnt"] for r in action_rows}

            return {
                "total_events": total,
                "unique_users": unique_users,
                "action_breakdown": action_breakdown,
            }
        finally:
            conn.close()
    except Exception as exc:
        print(f"audit_engine.get_audit_summary error: {exc}", file=sys.stderr)
        return {"total_events": 0, "unique_users": 0, "action_breakdown": {}}
