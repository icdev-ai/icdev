#!/usr/bin/env python3
# CUI // SP-CTI
"""PMA Meeting Coordinator — data-access layer for program meeting logs and action items.

Tables created on first use:
  pma_meeting_logs  — program coordination meeting records
  pma_action_items  — structured action items extracted from meeting notes

Action item statuses: open, in_progress, completed, overdue
HITL gate: action items require hitl_approved=1 before dispatching to collectors.

Public API:
  _ensure_tables(conn)                         — idempotent DDL
  create_meeting(contract_id, data, conn)      — insert meeting record
  list_meetings(contract_id, conn)             — list meetings for a contract
  extract_action_items(meeting_id, notes, conn) — parse notes into action items
  get_overdue_action_items(contract_id, conn)  — open items past due_date
  list_action_items(meeting_id, conn)          — all items for a meeting
  update_action_item(item_id, data, conn)      — HITL approval + status updates
"""

import sys
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402

_ACTION_KEYWORDS = [
    ("follow up", "PM"),
    ("schedule", "Coordinator"),
    ("review", "PM"),
    ("submit", "Contractor"),
    ("update", "PM"),
    ("coordinate", "Coordinator"),
    ("brief", "PM"),
    ("provide", "Contractor"),
    ("complete", "Contractor"),
    ("track", "PM"),
]


def _ensure_tables(conn) -> None:
    """Create PMA meeting tables if they do not exist."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pma_meeting_logs (
            id           TEXT PRIMARY KEY,
            contract_id  TEXT NOT NULL,
            title        TEXT NOT NULL,
            date_held    TEXT,
            attendees    TEXT,
            notes        TEXT,
            status       TEXT NOT NULL DEFAULT 'draft',
            created_at   TEXT NOT NULL,
            updated_at   TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS pma_action_items (
            id             TEXT PRIMARY KEY,
            meeting_id     TEXT NOT NULL,
            contract_id    TEXT NOT NULL,
            description    TEXT NOT NULL,
            owner          TEXT,
            due_date       TEXT,
            status         TEXT NOT NULL DEFAULT 'open',
            hitl_approved  INTEGER NOT NULL DEFAULT 0,
            notes          TEXT,
            created_at     TEXT NOT NULL,
            updated_at     TEXT NOT NULL
        )
        """
    )
    conn.commit()


def create_meeting(contract_id: str, data: Dict[str, Any], conn=None) -> Dict[str, Any]:
    """Insert a new meeting log record."""
    owns = conn is None
    if owns:
        conn = get_connection()
        conn.set_security_context(None)  # rls-bypass: pma tables have no tenant_id/classification columns

    _ensure_tables(conn)

    meeting_id = f"mtg-{uuid.uuid4().hex[:10]}"
    now = datetime.now(timezone.utc).isoformat()
    title = data.get("title") or "Program Coordination Meeting"
    try:
        conn.execute(
            """
            INSERT INTO pma_meeting_logs
                (id, contract_id, title, date_held, attendees, notes, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                meeting_id,
                contract_id,
                title,
                data.get("date_held"),
                data.get("attendees"),
                data.get("notes"),
                data.get("status", "draft"),
                now,
                now,
            ),
        )
        conn.commit()
        return {"status": "ok", "meeting_id": meeting_id, "action": "created"}
    finally:
        if owns:
            conn.close()


def list_meetings(contract_id: str, conn=None) -> List[Dict[str, Any]]:
    """List meeting logs for a contract."""
    owns = conn is None
    if owns:
        conn = get_connection()
        conn.set_security_context(None)  # rls-bypass: pma tables have no tenant_id/classification columns

    _ensure_tables(conn)

    try:
        rows = conn.execute(
            "SELECT * FROM pma_meeting_logs WHERE contract_id = ? ORDER BY date_held DESC, created_at DESC",
            (contract_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        if owns:
            conn.close()


def extract_action_items(
    meeting_id: str, notes: str, conn=None
) -> List[Dict[str, Any]]:
    """Parse meeting notes into structured action items and persist them.

    Uses keyword matching to identify action sentences. Each matched sentence
    becomes one action item with a heuristic owner assignment. Designed as a
    deterministic fallback; replace with LLM call when ICDEV_LLM_PROVIDER is set.
    """
    owns = conn is None
    if owns:
        conn = get_connection()
        conn.set_security_context(None)  # rls-bypass: pma tables have no tenant_id/classification columns

    _ensure_tables(conn)

    meeting_row = conn.execute(
        "SELECT id, contract_id FROM pma_meeting_logs WHERE id = ?", (meeting_id,)
    ).fetchone()
    if not meeting_row:
        if owns:
            conn.close()
        return []

    meeting = dict(meeting_row)
    contract_id = meeting["contract_id"]
    now = datetime.now(timezone.utc).isoformat()

    items = []
    sentences = [s.strip() for s in (notes or "").replace("\n", ". ").split(".") if s.strip()]
    for sentence in sentences:
        lower = sentence.lower()
        owner = None
        for keyword, default_owner in _ACTION_KEYWORDS:
            if keyword in lower:
                owner = default_owner
                break
        if owner is None:
            continue

        item_id = f"act-{uuid.uuid4().hex[:10]}"
        conn.execute(
            """
            INSERT INTO pma_action_items
                (id, meeting_id, contract_id, description, owner, due_date,
                 status, hitl_approved, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, NULL, 'open', 0, ?, ?)
            """,
            (item_id, meeting_id, contract_id, sentence[:500], owner, now, now),
        )
        items.append(
            {
                "id": item_id,
                "meeting_id": meeting_id,
                "description": sentence[:500],
                "owner": owner,
                "due_date": None,
                "status": "open",
                "hitl_approved": False,
            }
        )

    if items:
        conn.commit()

    if owns:
        conn.close()

    return items


def get_overdue_action_items(contract_id: str, conn=None) -> List[Dict[str, Any]]:
    """Return open action items whose due_date is in the past."""
    owns = conn is None
    if owns:
        conn = get_connection()
        conn.set_security_context(None)  # rls-bypass: pma tables have no tenant_id/classification columns

    _ensure_tables(conn)

    today = date.today().isoformat()
    try:
        rows = conn.execute(
            """
            SELECT * FROM pma_action_items
            WHERE contract_id = ?
              AND status NOT IN ('completed', 'overdue')
              AND due_date IS NOT NULL
              AND due_date < ?
            ORDER BY due_date
            """,
            (contract_id, today),
        ).fetchall()
        overdue = [dict(r) for r in rows]

        for item in overdue:
            conn.execute(
                "UPDATE pma_action_items SET status = 'overdue', updated_at = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), item["id"]),
            )
        if overdue:
            conn.commit()

        return overdue
    finally:
        if owns:
            conn.close()


def list_action_items(meeting_id: str, conn=None) -> List[Dict[str, Any]]:
    """List all action items for a meeting."""
    owns = conn is None
    if owns:
        conn = get_connection()
        conn.set_security_context(None)  # rls-bypass: pma tables have no tenant_id/classification columns

    _ensure_tables(conn)

    try:
        rows = conn.execute(
            "SELECT * FROM pma_action_items WHERE meeting_id = ? ORDER BY created_at",
            (meeting_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        if owns:
            conn.close()


def update_action_item(item_id: str, data: Dict[str, Any], conn=None) -> Dict[str, Any]:
    """Update action item status or HITL approval.

    HITL gate: callers set hitl_approved=True to approve an action item
    before tasking. Status transitions: open → in_progress → completed.
    """
    owns = conn is None
    if owns:
        conn = get_connection()
        conn.set_security_context(None)  # rls-bypass: pma tables have no tenant_id/classification columns

    _ensure_tables(conn)

    try:
        row = conn.execute(
            "SELECT * FROM pma_action_items WHERE id = ?", (item_id,)
        ).fetchone()
        if not row:
            return {"status": "error", "message": "Action item not found"}

        now = datetime.now(timezone.utc).isoformat()
        updates: Dict[str, Any] = {"updated_at": now}

        allowed_statuses = ("open", "in_progress", "completed", "overdue")
        if "status" in data and data["status"] in allowed_statuses:
            updates["status"] = data["status"]
        if "hitl_approved" in data:
            updates["hitl_approved"] = 1 if data["hitl_approved"] else 0
        if "owner" in data:
            updates["owner"] = data["owner"]
        if "due_date" in data:
            updates["due_date"] = data["due_date"]
        if "notes" in data:
            updates["notes"] = data["notes"]

        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [item_id]
        conn.execute(
            f"UPDATE pma_action_items SET {set_clause} WHERE id = ?", values
        )
        conn.commit()

        updated = dict(
            conn.execute(
                "SELECT * FROM pma_action_items WHERE id = ?", (item_id,)
            ).fetchone()
        )
        return {"status": "ok", "item_id": item_id, "item": updated}
    finally:
        if owns:
            conn.close()
