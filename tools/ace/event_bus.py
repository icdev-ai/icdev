# CUI // SP-CTI
"""ACE Event Bus — ecosystem-wide event emission for automatic coworker dispatch.

Any ICDEV canvas emits an event here; the ACEEventDispatcher picks it up
and routes it to the role(s) whose listen_topics match.

Usage (from any canvas blueprint or route):
    from icdev.tools.ace.event_bus import emit as ace_emit
    ace_emit("text.produced", {"content": "...", "route": "/dic/notebook"}, source_canvas="dic")

Topic taxonomy
--------------
text.produced          Any AI-generated text output (generic fallback)
document.ingested      New document added to DIC / WriteGuard
document.analyzed      DIC completed analysis on a document
code.committed         Code changes committed or pushed
task.completed         Kanban task moved to done
security.scan.completed Security/STIG/CVE scan finished
vulnerability.found    A specific CVE/finding was detected
test.failed            Test suite reported failures
report.generated       Any structured report was produced
network.analyzed       Network topology / migration analyzed
compliance.gap.found   Compliance gap or POAM item found
proposal.updated       Proposal content changed
"""
from __future__ import annotations

import json
from tools.logging.icdev_logger import get_logger
from typing import Any

logger = get_logger("icdev.ace.event_bus")

_DB_ENV = "ICDEV_ACE_DB_URL"

# Topics that carry enough content to be worth auto-dispatching
DISPATCH_TOPICS: frozenset[str] = frozenset({
    "text.produced",
    "document.ingested",
    "document.analyzed",
    "code.committed",
    "task.completed",
    "security.scan.completed",
    "vulnerability.found",
    "test.failed",
    "report.generated",
    "network.analyzed",
    "compliance.gap.found",
    "proposal.updated",
})


def emit(
    topic: str,
    payload: dict[str, Any],
    source_canvas: str = "",
    source_id: str = "",
) -> int | None:
    """Insert an event into the DB.  Non-blocking — returns event_id or None on error.

    The ACEEventDispatcher background thread picks this up within seconds.
    """
    if topic not in DISPATCH_TOPICS:
        return None
    try:
        from icdev.tools.db.storage import get_canvas_connection
        conn = get_canvas_connection(_DB_ENV)
        try:
            cur = conn.execute(
                """INSERT INTO ace_events
                   (topic, source_canvas, source_id, payload_json, processed)
                   VALUES (?, ?, ?, ?, 0)""",
                (topic, source_canvas or "", source_id or "", json.dumps(payload)),
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()
    except Exception as exc:
        logger.debug("ace_emit failed (non-fatal): %s", exc)
        return None


def get_pending(limit: int = 30) -> list[dict[str, Any]]:
    """Return unprocessed events ordered by creation time."""
    try:
        from icdev.tools.db.storage import get_canvas_connection
        conn = get_canvas_connection(_DB_ENV)
        try:
            rows = conn.execute(
                """SELECT id, topic, source_canvas, source_id, payload_json, created_at
                   FROM ace_events WHERE processed = 0
                   ORDER BY created_at ASC LIMIT ?""",
                (limit,),
            ).fetchall()
            return [
                {
                    "id": r[0], "topic": r[1], "source_canvas": r[2],
                    "source_id": r[3], "payload": json.loads(r[4] or "{}"),
                    "created_at": r[5],
                }
                for r in rows
            ]
        finally:
            conn.close()
    except Exception as exc:
        logger.debug("get_pending failed: %s", exc)
        return []


def mark_processed(event_id: int) -> None:
    """Mark an event as processed."""
    try:
        from icdev.tools.db.storage import get_canvas_connection
        conn = get_canvas_connection(_DB_ENV)
        try:
            conn.execute("UPDATE ace_events SET processed = 1 WHERE id = ?", (event_id,))
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        logger.debug("mark_processed failed: %s", exc)


def store_result(
    event_id: int,
    role_id: str,
    instance_id: str,
    status: str = "dispatched",
) -> None:
    """Record that a role session was dispatched for an event."""
    try:
        from icdev.tools.db.storage import get_canvas_connection
        conn = get_canvas_connection(_DB_ENV)
        try:
            conn.execute(
                """INSERT INTO ace_event_results
                   (event_id, role_id, instance_id, status)
                   VALUES (?, ?, ?, ?)""",
                (event_id, role_id, instance_id, status),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        logger.debug("store_result failed: %s", exc)


def get_recent_results(limit: int = 50) -> list[dict[str, Any]]:
    """Return recent event dispatch results for the event feed UI."""
    try:
        from icdev.tools.db.storage import get_canvas_connection
        conn = get_canvas_connection(_DB_ENV)
        try:
            rows = conn.execute(
                """SELECT r.id, r.event_id, r.role_id, r.instance_id, r.status,
                          r.created_at, e.topic, e.source_canvas, e.payload_json
                   FROM ace_event_results r
                   LEFT JOIN ace_events e ON e.id = r.event_id
                   ORDER BY r.created_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
            return [
                {
                    "id": r[0], "event_id": r[1], "role_id": r[2],
                    "instance_id": r[3], "status": r[4], "created_at": r[5],
                    "topic": r[6], "source_canvas": r[7],
                    "payload_preview": (json.loads(r[8] or "{}").get("content", "")[:120]
                                        if r[8] else ""),
                }
                for r in rows
            ]
        finally:
            conn.close()
    except Exception as exc:
        logger.debug("get_recent_results failed: %s", exc)
        return []


def pending_count() -> int:
    """Return count of unprocessed events — used for nav badge."""
    try:
        from icdev.tools.db.storage import get_canvas_connection
        conn = get_canvas_connection(_DB_ENV)
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM ace_events WHERE processed = 0"
            ).fetchone()
            return int(row[0]) if row else 0
        finally:
            conn.close()
    except Exception:
        return 0
