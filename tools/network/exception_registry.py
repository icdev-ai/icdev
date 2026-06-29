# [TEMPLATE: CUI // SP-CTI]
"""Network Canvas — Exception registry with ISSO/ISSM/AO approval chain."""
from __future__ import annotations

from datetime import datetime, timezone

from tools.network.db.init_db import get_connection


def list_exceptions(status: str | None = None, limit: int = 500) -> list[dict]:
    conn = get_connection()
    try:
        sql = "SELECT * FROM nc_exceptions WHERE 1=1"
        params: list = []
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def file_exception(data: dict) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    advisory_id = data.get("advisory_id")
    if advisory_id is not None:
        try:
            advisory_id = int(advisory_id)
        except (TypeError, ValueError):
            advisory_id = None
    conn = get_connection()
    try:
        cur = conn.execute(
            """INSERT INTO nc_exceptions
               (device_id, device_name, exception_type, risk_acceptance_level,
                justification, expiry_date, advisory_id, status, updated_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                data.get("device_id", ""),
                data.get("device_name", ""),
                data.get("exception_type", "risk-acceptance"),
                data.get("risk_level", "medium"),
                data.get("justification", ""),
                data.get("expiry_date") or None,
                advisory_id,
                "pending",
                now,
            ),
        )
        conn.commit()
        new_id = cur.lastrowid
        row = conn.execute("SELECT * FROM nc_exceptions WHERE id = %s", (new_id,)).fetchone()
        return dict(row)
    finally:
        conn.close()


_APPROVAL_LEVELS = {
    "isso": ("isso_approved", "isso_approved_by", "isso_approved_at", "isso-approved"),
    "issm": ("issm_approved", "issm_approved_by", "issm_approved_at", "issm-approved"),
    "ao":   ("ao_approved",   "ao_approved_by",   "ao_approved_at",   "fully-approved"),
}


def approve_exception(exc_id: int | str, level: str, approver: str) -> dict:
    if level not in _APPROVAL_LEVELS:
        raise ValueError(f"Unknown approval level: {level}")
    col_flag, col_by, col_at, new_status = _APPROVAL_LEVELS[level]
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    try:
        conn.execute(
            f"""UPDATE nc_exceptions
                SET {col_flag}=1, {col_by}=%s, {col_at}=%s, status=%s, updated_at=%s
                WHERE id=%s""",
            (approver, now, new_status, now, exc_id),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM nc_exceptions WHERE id = %s", (exc_id,)).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()
