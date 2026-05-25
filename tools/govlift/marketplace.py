# CUI // SP-CTI
"""GovLift — Runbook Marketplace.

Provides submit/approve/reject/download/stats operations for
govlift_marketplace_items backed by govlift_runbook_templates.
All DB access via get_connection() — never sqlite3.connect().
SQL uses ? placeholders; StorageCursor translates to %s for PostgreSQL.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

_ICDEV_ROOT = Path(__file__).resolve().parents[2]
if str(_ICDEV_ROOT) not in sys.path:
    sys.path.insert(0, str(_ICDEV_ROOT))

from tools.db.storage import get_connection


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _mp_id() -> str:
    return "mp-" + uuid4().hex[:10]


def _row_to_dict(row) -> dict:
    if row is None:
        return {}
    try:
        return dict(row)
    except Exception:
        return {}


def _fetch_item(conn, item_id: str) -> dict:
    row = conn.execute(
        "SELECT * FROM govlift_marketplace_items WHERE id = ?",
        (item_id,),
    ).fetchone()
    return _row_to_dict(row)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def publish_template(template_id: str, author: str, description: str = "") -> dict:
    """Submit a runbook template to the marketplace.

    Raises ValueError if the template is not found.
    """
    conn = get_connection()
    try:
        tpl_row = conn.execute(
            "SELECT id, name FROM govlift_runbook_templates WHERE id = ?",
            (template_id,),
        ).fetchone()
        if not tpl_row:
            raise ValueError(f"Template '{template_id}' not found")
        tpl = _row_to_dict(tpl_row)

        mp_id = _mp_id()
        now = _now()
        conn.execute(
            "INSERT INTO govlift_marketplace_items "
            "(id, template_id, title, author, description, status, created_at) "
            "VALUES (?,?,?,?,?,'submitted',?)",
            (mp_id, template_id, tpl["name"], author, description, now),
        )
        conn.commit()
        return _fetch_item(conn, mp_id)
    finally:
        conn.close()


def get_item(item_id: str) -> dict:
    """Return a marketplace item by ID, or {} if not found."""
    conn = get_connection()
    try:
        return _fetch_item(conn, item_id)
    finally:
        conn.close()


def list_items(status: str | None = None, author: str | None = None) -> list[dict]:
    """Return marketplace items filtered by optional status or author."""
    conn = get_connection()
    try:
        clauses: list[str] = []
        params: list = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if author:
            clauses.append("author = ?")
            params.append(author)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = conn.execute(
            f"SELECT * FROM govlift_marketplace_items {where} ORDER BY created_at DESC",
            params,
        ).fetchall()
        return [_row_to_dict(r) for r in rows]
    finally:
        conn.close()


def approve_item(item_id: str, reviewer: str, notes: str = "") -> dict:
    """Approve a submitted marketplace item.

    Raises ValueError if item not found or not in 'submitted' status.
    """
    conn = get_connection()
    try:
        item = _fetch_item(conn, item_id)
        if not item:
            raise ValueError(f"Marketplace item '{item_id}' not found")
        if item["status"] != "submitted":
            raise ValueError(
                f"Can only approve items in 'submitted' status; current status: {item['status']}"
            )
        now = _now()
        conn.execute(
            "UPDATE govlift_marketplace_items "
            "SET status='approved', reviewed_by=?, review_notes=?, published_at=? "
            "WHERE id=?",
            (reviewer, notes, now, item_id),
        )
        conn.commit()
        return _fetch_item(conn, item_id)
    finally:
        conn.close()


def reject_item(item_id: str, reviewer: str, notes: str = "") -> dict:
    """Reject a marketplace item.

    Raises ValueError if item not found.
    """
    conn = get_connection()
    try:
        item = _fetch_item(conn, item_id)
        if not item:
            raise ValueError(f"Marketplace item '{item_id}' not found")
        conn.execute(
            "UPDATE govlift_marketplace_items "
            "SET status='rejected', reviewed_by=?, review_notes=? "
            "WHERE id=?",
            (reviewer, notes, item_id),
        )
        conn.commit()
        return _fetch_item(conn, item_id)
    finally:
        conn.close()


def download_item(item_id: str) -> dict:
    """Download an approved marketplace item, incrementing its counter.

    Returns {'item': ..., 'template': ...}.
    Raises ValueError if item not found or not approved.
    """
    conn = get_connection()
    try:
        item = _fetch_item(conn, item_id)
        if not item:
            raise ValueError(f"Marketplace item '{item_id}' not found")
        if item["status"] != "approved":
            raise ValueError("Only approved items can be downloaded")

        conn.execute(
            "UPDATE govlift_marketplace_items SET downloads = downloads + 1 WHERE id = ?",
            (item_id,),
        )
        conn.commit()

        tpl_row = conn.execute(
            "SELECT * FROM govlift_runbook_templates WHERE id = ?",
            (item["template_id"],),
        ).fetchone()
        tpl = _row_to_dict(tpl_row)

        return {"item": _fetch_item(conn, item_id), "template": tpl}
    finally:
        conn.close()


def get_stats() -> dict:
    """Return aggregate marketplace statistics."""
    conn = get_connection()
    try:
        total_items = conn.execute(
            "SELECT COUNT(*) FROM govlift_marketplace_items"
        ).fetchone()[0]

        approved_items = conn.execute(
            "SELECT COUNT(*) FROM govlift_marketplace_items WHERE status = 'approved'"
        ).fetchone()[0]

        total_downloads = conn.execute(
            "SELECT COALESCE(SUM(downloads), 0) FROM govlift_marketplace_items"
        ).fetchone()[0]

        return {
            "total_items": total_items,
            "approved_items": approved_items,
            "total_downloads": total_downloads,
        }
    finally:
        conn.close()
