# CUI // SP-CTI
"""NDC Standard Operating Procedures — CRUD + approval workflow.

Deterministic, stdlib-only (sqlite3). Lives in the Network Design Canvas DB
(`data/network_canvas.db`). Tables created by `tools/network/db/init_db.py`:

  - ndc_sops                 (SOP records with JSON step/validation/rollback fields)
  - ndc_sop_approval_log     (append-only approval/review audit log)

Workflow states:
    draft → review → approved → deprecated
              ↑ (reject returns to draft)
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ICDEV_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = _ICDEV_ROOT / "data" / "network_canvas.db"

_VALID_STATUSES = {"draft", "review", "approved", "deprecated"}
_CONTENT_FIELDS = {
    "title", "category", "csp", "description", "prerequisites", "steps",
    "validation", "rollback", "escalation", "classification",
}


def _get_conn():
    # PG-primary via the Network Canvas helper (NC_STORAGE_BACKEND); SQLite is a
    # guarded fallback. Returns a StorageConnection so %s placeholders translate.
    from tools.network.db.init_db import get_connection

    return get_connection()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _gen_id(prefix: str = "sop") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def _dumps(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _loads(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return default


def _row_to_sop(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["prerequisites"] = _loads(d.get("prerequisites"), [])
    d["steps"] = _loads(d.get("steps"), [])
    d["validation"] = _loads(d.get("validation"), [])
    d["rollback"] = _loads(d.get("rollback"), {})
    d["escalation"] = _loads(d.get("escalation"), [])
    return d


def _log_action(conn: sqlite3.Connection, sop_id: str, actor: str,
                action: str, comment: str = "") -> None:
    conn.execute(
        "INSERT INTO ndc_sop_approval_log (id, sop_id, actor, action, comment, timestamp) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (f"log-{uuid.uuid4().hex[:10]}", sop_id, actor, action, comment, _now()),
    )


# ── CRUD ──────────────────────────────────────────────────────────────────────


def create_sop(title: str, category: str, description: str, steps: list,
               prerequisites: list | None = None, validation: list | None = None,
               rollback: dict | None = None, escalation: list | None = None,
               author: str = "", classification: str = "CUI",
               csp: str = "multi") -> dict:
    """Create a draft SOP. Returns the new SOP dict."""
    if not title or not category:
        raise ValueError("title and category are required")
    if not isinstance(steps, list) or not steps:
        raise ValueError("steps must be a non-empty list")

    sop_id = _gen_id()
    now = _now()
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO ndc_sops (sop_id, title, category, version, status, "
            "description, prerequisites, steps, validation, rollback, escalation, "
            "classification, author, csp, created_at, updated_at) "
            "VALUES (%s, %s, %s, 1, 'draft', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                sop_id, title, category, description or "",
                _dumps(prerequisites or []),
                _dumps(steps),
                _dumps(validation or []),
                _dumps(rollback or {}),
                _dumps(escalation or []),
                classification, author, csp, now, now,
            ),
        )
        _log_action(conn, sop_id, author or "system", "created",
                    f"SOP draft created: {title}")
        conn.commit()
        return get_sop(sop_id)
    finally:
        conn.close()


def get_sop(sop_id: str) -> dict:
    """Retrieve a single SOP with all fields parsed."""
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM ndc_sops WHERE sop_id = %s", (sop_id,)
        ).fetchone()
        if not row:
            return {}
        return _row_to_sop(row)
    finally:
        conn.close()


def list_sops(category: str | None = None, status: str | None = None,
              limit: int = 50) -> list:
    """List SOPs, filtered by category/status. Most recent first."""
    conn = _get_conn()
    try:
        clauses: list[str] = []
        params: list[Any] = []
        if category:
            clauses.append("category = ?")
            params.append(category)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(int(limit))
        rows = conn.execute(
            f"SELECT * FROM ndc_sops{where} ORDER BY updated_at DESC LIMIT %s",  # nosec B608
            params,
        ).fetchall()
        return [_row_to_sop(r) for r in rows]
    finally:
        conn.close()


def update_sop(sop_id: str, **fields: Any) -> dict:
    """Update fields. Increments version on any content field change."""
    existing = get_sop(sop_id)
    if not existing:
        raise ValueError(f"SOP not found: {sop_id}")
    if existing.get("status") == "approved":
        raise ValueError("Cannot modify an approved SOP; create a new version or deprecate first")

    updates: list[str] = []
    params: list[Any] = []
    bumped = False

    for key, val in fields.items():
        if key not in _CONTENT_FIELDS and key not in ("reviewer", "approver"):
            continue
        if key in ("prerequisites", "steps", "validation", "rollback", "escalation"):
            updates.append(f"{key} = ?")
            params.append(_dumps(val))
            bumped = True
        else:
            updates.append(f"{key} = ?")
            params.append(val)
            if key in _CONTENT_FIELDS:
                bumped = True

    if not updates:
        return existing

    if bumped:
        updates.append("version = version + 1")
    updates.append("updated_at = ?")
    params.append(_now())
    params.append(sop_id)

    conn = _get_conn()
    try:
        conn.execute(
            f"UPDATE ndc_sops SET {', '.join(updates)} WHERE sop_id = %s",  # nosec B608
            params,
        )
        _log_action(conn, sop_id, fields.get("actor", "system"), "updated",
                    f"Fields updated: {', '.join(k for k in fields if k in _CONTENT_FIELDS)}")
        conn.commit()
    finally:
        conn.close()
    return get_sop(sop_id)


def delete_sop(sop_id: str, actor: str = "system") -> dict:
    """Delete a draft SOP. Approved/deprecated SOPs cannot be deleted."""
    existing = get_sop(sop_id)
    if not existing:
        return {"ok": False, "error": "not_found"}
    if existing.get("status") not in ("draft", "review"):
        return {"ok": False, "error": "cannot_delete_approved_sop"}
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM ndc_sops WHERE sop_id = %s", (sop_id,))
        _log_action(conn, sop_id, actor, "deleted", "Draft SOP deleted")
        conn.commit()
    finally:
        conn.close()
    return {"ok": True, "sop_id": sop_id}


# ── Approval workflow ────────────────────────────────────────────────────────


def submit_for_review(sop_id: str, reviewer: str, submitted_by: str) -> dict:
    """Transition draft → review."""
    existing = get_sop(sop_id)
    if not existing:
        raise ValueError(f"SOP not found: {sop_id}")
    if existing["status"] != "draft":
        raise ValueError(f"Only draft SOPs may be submitted; current status: {existing['status']}")
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE ndc_sops SET status='review', reviewer=%s, updated_at=%s WHERE sop_id=%s",
            (reviewer, _now(), sop_id),
        )
        _log_action(conn, sop_id, submitted_by, "submitted",
                    f"Submitted for review by {reviewer}")
        conn.commit()
    finally:
        conn.close()
    return get_sop(sop_id)


def approve_sop(sop_id: str, approver: str, comment: str = "") -> dict:
    """Transition review → approved. Sets approved_at timestamp."""
    existing = get_sop(sop_id)
    if not existing:
        raise ValueError(f"SOP not found: {sop_id}")
    if existing["status"] != "review":
        raise ValueError(f"Only SOPs in review may be approved; current status: {existing['status']}")
    now = _now()
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE ndc_sops SET status='approved', approver=%s, approved_at=%s, updated_at=%s "
            "WHERE sop_id=%s",
            (approver, now, now, sop_id),
        )
        _log_action(conn, sop_id, approver, "approved", comment or "Approved")
        conn.commit()
    finally:
        conn.close()
    return get_sop(sop_id)


def reject_sop(sop_id: str, approver: str, comment: str) -> dict:
    """Transition review → draft. Rejection reason required."""
    if not comment:
        raise ValueError("Rejection requires a comment")
    existing = get_sop(sop_id)
    if not existing:
        raise ValueError(f"SOP not found: {sop_id}")
    if existing["status"] != "review":
        raise ValueError(f"Only SOPs in review may be rejected; current status: {existing['status']}")
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE ndc_sops SET status='draft', updated_at=%s WHERE sop_id=%s",
            (_now(), sop_id),
        )
        _log_action(conn, sop_id, approver, "rejected", comment)
        conn.commit()
    finally:
        conn.close()
    return get_sop(sop_id)


def deprecate_sop(sop_id: str, actor: str, comment: str = "") -> dict:
    """Transition approved → deprecated."""
    existing = get_sop(sop_id)
    if not existing:
        raise ValueError(f"SOP not found: {sop_id}")
    if existing["status"] != "approved":
        raise ValueError(f"Only approved SOPs may be deprecated; current status: {existing['status']}")
    conn = _get_conn()
    try:
        conn.execute(
            "UPDATE ndc_sops SET status='deprecated', updated_at=%s WHERE sop_id=%s",
            (_now(), sop_id),
        )
        _log_action(conn, sop_id, actor, "deprecated", comment or "Deprecated")
        conn.commit()
    finally:
        conn.close()
    return get_sop(sop_id)


def get_approval_history(sop_id: str) -> list:
    """Return full approval_log for a SOP (oldest first)."""
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT id, sop_id, actor, action, comment, timestamp "
            "FROM ndc_sop_approval_log WHERE sop_id=%s ORDER BY timestamp ASC",
            (sop_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
