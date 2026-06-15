# CUI // SP-CTI
"""Genesis Reflex — DIC Review Cadence (nightly, 1440-min cadence).

For each DIC collection that has review_interval_days set, checks when the
last HITL section approval was recorded. If the gap exceeds the interval, emits:
  - a canvas_events row  (event_type='dic.review_overdue')
  - a notification_log row for the collection's editors/reviewers

Idempotency: only one overdue event per collection per calendar day.
Air-gap safe: no LLM call; pure DB + config logic.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from typing import Any

from tools.db.storage import get_connection
from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

CADENCE_MINUTES = 1440       # nightly
IMPLEMENTATION_STATUS = "full"


# ── Schema helpers ────────────────────────────────────────────────────────────

def _ensure_column(conn) -> None:
    """Lazy-add review_interval_days to dic_collections if absent (migration fallback)."""
    try:
        conn.execute(
            "ALTER TABLE dic_collections ADD COLUMN review_interval_days INTEGER DEFAULT 90"
        )
        conn.commit()
    except Exception:
        pass  # Column already exists — expected on second run


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ── Core logic ────────────────────────────────────────────────────────────────

def _fetch_overdue_collections(conn) -> list[dict]:
    """Return collections whose last HITL approval is older than review_interval_days."""
    try:
        rows = conn.execute(
            "SELECT collection_id, name, review_interval_days "
            "FROM dic_collections "
            "WHERE review_interval_days IS NOT NULL AND review_interval_days > 0"
        ).fetchall()
    except Exception as exc:
        logger.warning("dic_review_cadence: cannot query dic_collections: %s", exc)
        return []

    overdue = []
    for row in rows:
        if isinstance(row, (list, tuple)):
            cid, cname, interval = row[0], row[1], row[2]
        else:
            cid = row["collection_id"]
            cname = row["name"]
            interval = row["review_interval_days"]

        if not interval:
            continue

        # Find date of last approved section edit in this collection
        last_review_date = _last_approval_date(conn, cid)
        threshold = datetime.now(timezone.utc) - timedelta(days=int(interval))

        if last_review_date is None or last_review_date < threshold:
            overdue.append({
                "collection_id": cid,
                "collection_name": cname or cid,
                "review_interval_days": int(interval),
                "last_review_date": last_review_date.isoformat() if last_review_date else None,
                "days_overdue": (
                    (datetime.now(timezone.utc) - last_review_date).days - int(interval)
                    if last_review_date else int(interval)
                ),
            })
    return overdue


def _last_approval_date(conn, collection_id: str):
    """Return the most recent approved-at timestamp for any section in this collection."""
    for table, col in [
        ("dic_edit_history", "edited_at"),
        ("dic_section_approvals", "approved_at"),
    ]:
        try:
            row = conn.execute(
                f"SELECT MAX({col}) FROM {table} WHERE collection_id = %s",
                (collection_id,),
            ).fetchone()
            if row and row[0] is not None:
                # Use positional access — aggregate column names differ between DBs
                val = row[0]
                return _parse_dt(val)
        except Exception:
            continue
    return None


def _parse_dt(val) -> datetime | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _already_emitted_today(conn, collection_id: str) -> bool:
    """Check if we already emitted an overdue event for this collection today."""
    today = _today_str()
    try:
        row = conn.execute(
            "SELECT id FROM canvas_events "
            "WHERE event_type = 'dic.review_overdue' "
            "AND source_canvas = 'dic' "
            "AND payload_json LIKE %s "
            "AND created_at LIKE %s "
            "LIMIT 1",
            (f'%"{collection_id}"%', f"{today}%"),
        ).fetchone()
        return row is not None
    except Exception:
        return False


def _emit_canvas_event(conn, collection_id: str, info: dict, now: str) -> None:
    import uuid as _uuid
    event_id = f"evt_{_uuid.uuid4().hex[:16]}"
    payload = json.dumps({
        "collection_id": collection_id,
        "collection_name": info["collection_name"],
        "review_interval_days": info["review_interval_days"],
        "last_review_date": info["last_review_date"],
        "days_overdue": info["days_overdue"],
    })
    conn.execute(
        "INSERT INTO canvas_events "
        "(id, source_canvas, target_canvas, event_type, payload_json, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (event_id, "dic", "dic", "dic.review_overdue", payload, now),
    )


def _emit_notifications(conn, collection_id: str, info: dict, now: str) -> None:
    """Best-effort: notify editors/reviewers of this collection."""
    try:
        import hashlib
        members = conn.execute(
            "SELECT user_id FROM dic_collection_members "
            "WHERE collection_id = %s AND role IN ('editor', 'reviewer')",
            (collection_id,),
        ).fetchall()
        for member in members:
            uid = (member[0] if isinstance(member, (list, tuple))
                   else member["user_id"] if hasattr(member, "__getitem__") else "")
            if not uid:
                continue
            log_id = f"nlog-{hashlib.sha256(f'{now}{collection_id}{uid}'.encode()).hexdigest()[:12]}"
            title = (
                f"Review overdue: {info['collection_name']} "
                f"({info['days_overdue']}d past {info['review_interval_days']}d interval)"
            )
            conn.execute(
                "INSERT INTO notification_log "
                "(id, event_type, adapter, severity, title, delivered, error, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (log_id, "dic.review_overdue", "dic_review_cadence",
                 "medium", title[:200], False, None, now),
            )
    except Exception as exc:
        logger.debug("dic_review_cadence: notification error: %s", exc)


# ── Kanban HITL task creation (dsyn-suggest-03) ───────────────────────────────

import os as _os


def _open_hitl_task_exists(collection_id: str, collection_name: str) -> bool:
    """Return True if an open kanban task for this collection's review already exists."""
    try:
        from tools.kanban.task_factory import get_connection as _kconn
        with _kconn() as conn:
            row = conn.execute(
                "SELECT id FROM kanban_tasks "
                "WHERE title LIKE %s AND status NOT IN ('done', 'cancelled') LIMIT 1",
                (f"%{collection_id}%",),
            ).fetchone()
            return row is not None
    except Exception:
        pass
    try:
        from tools.db.storage import get_connection as _gconn
        with _gconn() as conn:
            row = conn.execute(
                "SELECT id FROM kanban_tasks "
                "WHERE title LIKE %s AND status NOT IN ('done', 'cancelled') LIMIT 1",
                (f"%{collection_id}%",),
            ).fetchone()
            return row is not None
    except Exception:
        return False


def _create_hitl_task(collection_id: str, info: dict) -> bool:
    """Create a HITL review kanban task via task_factory. Returns True on success."""
    cname = info["collection_name"]
    days_overdue = info["days_overdue"]
    interval = info["review_interval_days"]
    last = info["last_review_date"] or "never"
    project_id = _os.getenv("DSYN_DEFAULT_PROJECT_ID", "dsyn")
    title = f"Review overdue: {cname} ({collection_id})"
    description = (
        f"The DIC collection '{cname}' (ID: {collection_id}) is overdue for its periodic review.\n\n"
        f"Review interval: every {interval} days\n"
        f"Last approved review: {last}\n"
        f"Days overdue: {days_overdue}\n\n"
        f"Action: Review all pending sections in the collection, approve or request revisions, "
        f"and mark sections as reviewed. Navigate to "
        f"/document-intelligence/collections/{collection_id} to begin.\n\n"
        f"Acceptance criteria: All sections in collection {collection_id} reviewed and status "
        f"updated; at least one section approval recorded within the past {interval} days."
    )
    try:
        from tools.kanban.task_factory import create_tasks
        create_tasks([{
            "id": f"dsyn-hitl-{collection_id[:12]}",
            "title": title,
            "description": description,
            "priority": "medium",
            "project_id": project_id,
            "task_type": "hitl_review",
            "status": "backlog",
        }])
        return True
    except Exception as exc:
        logger.debug("dic_review_cadence: task_factory.create_tasks error: %s", exc)
        return False


# ── Entry point ───────────────────────────────────────────────────────────────

def run(config: dict[str, Any], trust) -> dict:
    """Called by Genesis daemon at 1440-min cadence."""
    processed = 0
    errors = 0
    now = _now_iso()

    try:
        with get_connection() as conn:
            _ensure_column(conn)
            overdue = _fetch_overdue_collections(conn)
            logger.info("dic_review_cadence: %d overdue collections", len(overdue))

            for info in overdue:
                cid = info["collection_id"]
                try:
                    if _already_emitted_today(conn, cid):
                        logger.debug("dic_review_cadence: already emitted today for %s", cid)
                        continue
                    _emit_canvas_event(conn, cid, info, now)
                    _emit_notifications(conn, cid, info, now)
                    conn.commit()
                    # dsyn-suggest-03: idempotent HITL kanban task
                    if not _open_hitl_task_exists(cid, info["collection_name"]):
                        _create_hitl_task(cid, info)
                    processed += 1
                    logger.info(
                        "dic_review_cadence: emitted overdue event for %s (%dd overdue)",
                        cid, info["days_overdue"],
                    )
                except Exception as exc:
                    logger.warning("dic_review_cadence: error for %s: %s", cid, exc)
                    errors += 1
    except Exception as exc:
        logger.error("dic_review_cadence: fatal error: %s", exc)
        return {"success": False, "error": str(exc)}

    return {
        "success": True,
        "processed": processed,
        "errors": errors,
    }
