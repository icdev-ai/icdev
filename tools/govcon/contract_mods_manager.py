"""Contract Modification Manager — request/approval workflow for cpmp_contract_mods."""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import Any

from tools.db.storage import get_connection

DB_PATH = os.environ.get("ICDEV_DB_PATH", "data/icdev.db")

# Valid status transitions for contract modifications
MOD_TRANSITIONS: dict[str, list[str]] = {
    "requested": ["in_review", "rejected"],
    "in_review": ["approved", "rejected"],
    "approved": ["executed"],
    "rejected": [],
    "executed": [],
}

MOD_TYPES = ("admin", "funding", "scope", "pop")
MOD_STATUSES = ("requested", "in_review", "approved", "rejected", "executed")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get_conn(db_path: str | None = None):
    conn = get_connection(db_path=db_path or DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def list_mods(contract_id: str, *, db_path: str | None = None) -> list[dict]:
    conn = _get_conn(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM cpmp_contract_mods WHERE contract_id = %s ORDER BY mod_number",
            (contract_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_mod(mod_id: str, *, db_path: str | None = None) -> dict | None:
    conn = _get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM cpmp_contract_mods WHERE id = %s", (mod_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Mutations
# ---------------------------------------------------------------------------

def create_mod(
    contract_id: str,
    type_: str,
    description: str,
    value_delta: float = 0.0,
    requested_by: str | None = None,
    effective_date: str | None = None,
    classification: str = "CUI",
    tenant_id: str | None = None,
    metadata: str = "{}",
    *,
    db_path: str | None = None,
) -> dict:
    if type_ not in MOD_TYPES:
        raise ValueError(f"type must be one of {MOD_TYPES}")
    conn = _get_conn(db_path)
    try:
        # Auto-increment mod_number within contract
        row = conn.execute(
            "SELECT COALESCE(MAX(mod_number), 0) + 1 FROM cpmp_contract_mods WHERE contract_id = %s",
            (contract_id,),
        ).fetchone()
        mod_number = row[0]
        mod_id = str(uuid.uuid4())
        now = _now()
        conn.execute(
            """INSERT INTO cpmp_contract_mods
               (id, contract_id, mod_number, type, description, value_delta, status,
                requested_by, requested_at, classification, tenant_id, metadata, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, 'requested', %s, %s, %s, %s, %s, %s, %s)""",
            (mod_id, contract_id, mod_number, type_, description, value_delta,
             requested_by, now, classification, tenant_id, metadata, now, now),
        )
        # Record in status history (reuse cpmp_status_history)
        _record_status_history(conn, "contract_mod", mod_id, None, "requested", requested_by)
        conn.commit()
        return get_mod(mod_id, db_path=db_path) or {}
    finally:
        conn.close()


def transition_mod(
    mod_id: str,
    new_status: str,
    actor: str | None = None,
    reason: str | None = None,
    *,
    db_path: str | None = None,
) -> dict:
    conn = _get_conn(db_path)
    try:
        row = conn.execute(
            "SELECT status FROM cpmp_contract_mods WHERE id = %s", (mod_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"Modification {mod_id} not found")
        old_status = row["status"]
        allowed = MOD_TRANSITIONS.get(old_status, [])
        if new_status not in allowed:
            raise ValueError(f"Cannot transition from {old_status!r} to {new_status!r}")
        now = _now()
        extra_fields: dict[str, Any] = {}
        if new_status == "in_review":
            extra_fields = {"reviewed_by": actor, "reviewed_at": now}
        elif new_status == "approved":
            extra_fields = {"approved_by": actor, "approved_at": now}
        elif new_status == "rejected":
            extra_fields = {"rejection_reason": reason}
        elif new_status == "executed":
            extra_fields = {"executed_at": now}
        set_clauses = ["status = ?", "updated_at = ?"]
        params: list[Any] = [new_status, now]
        for k, v in extra_fields.items():
            set_clauses.append(f"{k} = ?")
            params.append(v)
        params.append(mod_id)
        conn.execute(
            f"UPDATE cpmp_contract_mods SET {', '.join(set_clauses)} WHERE id = %s",
            params,
        )
        _record_status_history(conn, "contract_mod", mod_id, old_status, new_status, actor, reason)
        conn.commit()
        return get_mod(mod_id, db_path=db_path) or {}
    finally:
        conn.close()


def _record_status_history(
    conn, entity_type: str, entity_id: str, old_status: str | None,
    new_status: str, changed_by: str | None, reason: str | None = None
) -> None:
    conn.execute(
        """INSERT INTO cpmp_status_history
           (entity_type, entity_id, old_status, new_status, changed_by, reason)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (entity_type, entity_id, old_status, new_status, changed_by, reason),
    )
