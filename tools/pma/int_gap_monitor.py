#!/usr/bin/env python3
# CUI // SP-CTI
"""PMA INT Gap Monitor — data-access layer for intelligence collection gap tracking.

Tables created on first use:
  pma_int_gaps                — exploitation-line intelligence gaps
  pma_collection_requirements — collection requirements generated from gaps

Public API:
  _ensure_tables(conn)                          — idempotent DDL
  get_persistent_gaps(days=14, conn=None)       — open gaps older than N days
  generate_collection_requirements(gap, conn)   — insert requirement if not a duplicate;
                                                  dedup key: coverage_id + discipline + status != 'cancelled'
"""

import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

INT_DISCIPLINES = ("HUMINT", "SIGINT", "IMINT", "OSINT", "GEOINT", "MASINT", "ALL")
GAP_SEVERITIES = ("critical", "high", "medium", "low")
GAP_STATUSES = ("open", "in_progress", "resolved", "cancelled")
REQ_STATUSES = ("open", "tasked", "cancelled", "closed")

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS pma_int_gaps (
    id                    TEXT PRIMARY KEY,
    exploitation_line_id  TEXT NOT NULL,
    exploitation_line_title TEXT NOT NULL,
    discipline            TEXT NOT NULL DEFAULT 'ALL',
    gap_description       TEXT NOT NULL,
    severity              TEXT NOT NULL DEFAULT 'medium',
    status                TEXT NOT NULL DEFAULT 'open',
    contract_id           TEXT,
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pma_collection_requirements (
    id               TEXT PRIMARY KEY,
    gap_id           TEXT NOT NULL,
    coverage_id      TEXT NOT NULL,
    discipline       TEXT NOT NULL,
    priority         TEXT NOT NULL DEFAULT 'medium',
    requirement_text TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'open',
    kanban_task_id   TEXT,
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);
"""


def _ensure_tables(conn) -> None:
    """Create INT gap tables if they do not exist."""
    conn.executescript(_SCHEMA_SQL)
    conn.commit()


def get_persistent_gaps(days: int = 14, conn=None) -> List[Dict[str, Any]]:
    """Return all open/in-progress INT gaps created more than `days` days ago.

    These are "persistent" gaps: unresolved and older than the threshold,
    indicating no timely action has been taken.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = conn.execute(
        "SELECT * FROM pma_int_gaps "
        "WHERE status NOT IN ('resolved', 'cancelled') AND created_at <= %s",
        (cutoff,),
    ).fetchall()
    return [dict(r) for r in rows]


def generate_collection_requirements(
    gap: Dict[str, Any], conn
) -> Dict[str, Any]:
    """Generate a collection requirement for `gap`, inserting only if no non-cancelled
    requirement already exists for the same (coverage_id, discipline) pair.

    coverage_id is the gap's exploitation_line_id — the intelligence collection
    line the requirement targets.

    Returns:
        {"inserted": True, "requirement_id": str, "priority": str}  — new row created
        {"inserted": False, "requirement_id": str}                   — duplicate skipped
    """
    coverage_id = gap["exploitation_line_id"]
    discipline = gap["discipline"]

    existing = conn.execute(
        "SELECT id FROM pma_collection_requirements "
        "WHERE coverage_id = %s AND discipline = %s AND status != 'cancelled'",
        (coverage_id, discipline),
    ).fetchone()

    if existing:
        req_id = existing[0] if not hasattr(existing, "keys") else existing["id"]
        return {"inserted": False, "requirement_id": req_id}

    req_id = f"pma-req-{uuid.uuid4().hex[:10]}"
    priority = gap.get("severity", "medium")
    req_text = (
        f"Collect {discipline} intelligence to address gap on exploitation line "
        f"'{gap['exploitation_line_title']}': {gap['gap_description']}"
    )
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO pma_collection_requirements "
        "(id, gap_id, coverage_id, discipline, priority, requirement_text, "
        " status, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, 'open', %s, %s)",
        (req_id, gap["id"], coverage_id, discipline, priority, req_text, now, now),
    )
    return {"inserted": True, "requirement_id": req_id, "priority": priority}


def get_untasked_critical_gaps_over_30_days(
    conn, gaps: Optional[List[Dict[str, Any]]] = None
) -> List[Dict[str, Any]]:
    """Return critical gaps older than 30 days that have no 'tasked' requirement.

    Used by the reflex to decide when to file a compliance risk.
    `gaps` may be the already-loaded persistent-gap list (avoids a second DB query).
    """
    if gaps is None:
        gaps = get_persistent_gaps(days=30, conn=conn)

    cutoff_30 = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    results = []
    for gap in gaps:
        if gap.get("severity") != "critical":
            continue
        if gap.get("created_at", "") > cutoff_30:
            continue
        tasked = conn.execute(
            "SELECT id FROM pma_collection_requirements "
            "WHERE gap_id = %s AND status = 'tasked'",
            (gap["id"],),
        ).fetchone()
        if not tasked:
            results.append(gap)
    return results
