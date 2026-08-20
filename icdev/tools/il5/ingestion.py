# CUI // SP-CTI
"""ICDEV™ IL5 Data Ingestion — 30-second display SLA.

Accepts data events classified at Impact Level 5 (IL5/CUI), records them with
ingestion timestamps, and computes display latency against source-publication
time to verify the 30-second SLA.

Architecture note: uses HTTP polling (D103) at 3-second intervals, which
provides sub-5-second detection — well within the 30-second SLA requirement.

NIST 800-53: AU-2, AU-12 (audit), SC-28 (protection at rest), SI-12 (info mgmt).

Usage::

    from tools.il5.ingestion import ingest_il5_event, get_il5_events
    event_id = ingest_il5_event("feed://source-a", payload, source_published_at=ts)
    events   = get_il5_events(since="2026-05-16T00:00:00Z")
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.db.storage import get_connection

IL5_TABLE = "il5_ingestion_log"
IL5_CLASSIFICATION = "CUI"
IL5_IMPACT_LEVEL = "IL5"
SLA_SECONDS = 30

_ICDEV_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_DB = _ICDEV_ROOT / "data" / "icdev.db"

_CREATE_TABLE_SQL = f"""
CREATE TABLE IF NOT EXISTS {IL5_TABLE} (
    id                  TEXT PRIMARY KEY,
    source_id           TEXT NOT NULL,
    content_hash        TEXT NOT NULL,
    classification      TEXT NOT NULL DEFAULT '{IL5_CLASSIFICATION}',
    impact_level        TEXT NOT NULL DEFAULT '{IL5_IMPACT_LEVEL}',
    ingested_at         TEXT NOT NULL,
    source_published_at TEXT,
    display_latency_s   REAL,
    sla_met             INTEGER,
    metadata            TEXT NOT NULL DEFAULT '{{}}'
)
"""


def create_il5_schema(conn: sqlite3.Connection) -> None:
    """Create the IL5 ingestion log table in the given connection."""
    conn.execute(_CREATE_TABLE_SQL)
    conn.commit()


def _open_conn(db_path: Optional[Path] = None) -> sqlite3.Connection:
    path = db_path or _DEFAULT_DB
    conn = get_connection(db_path=str(path))
    conn.row_factory = sqlite3.Row
    return conn


def ingest_il5_event(
    source_id: str,
    content: str,
    *,
    source_published_at: Optional[datetime] = None,
    metadata: Optional[Dict[str, Any]] = None,
    db_path: Optional[Path] = None,
) -> str:
    """Ingest a data event classified at IL5/CUI.

    Records classification, CUI marking, ingestion timestamp, and computes
    whether the 30-second display SLA is met relative to source_published_at.

    Args:
        source_id: Identifier for the upstream data source.
        content: Raw payload content (hashed for dedup; not stored verbatim).
        source_published_at: UTC datetime when the source published the event.
        metadata: Optional JSON-serialisable dict for additional context.
        db_path: Override DB path (used in tests).

    Returns:
        Unique event ID (UUID).
    """
    event_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    ingested_at = now.isoformat()
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

    display_latency_s: Optional[float] = None
    sla_met: Optional[int] = None

    if source_published_at is not None:
        if source_published_at.tzinfo is None:
            source_published_at = source_published_at.replace(tzinfo=timezone.utc)
        display_latency_s = (now - source_published_at).total_seconds()
        sla_met = 1 if display_latency_s <= SLA_SECONDS else 0

    conn = _open_conn(db_path)
    try:
        # Ensure table exists (idempotent)
        conn.execute(_CREATE_TABLE_SQL)
        conn.execute(
            f"""
            INSERT INTO {IL5_TABLE}
                (id, source_id, content_hash, classification, impact_level,
                 ingested_at, source_published_at, display_latency_s, sla_met, metadata)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                event_id,
                source_id,
                content_hash,
                IL5_CLASSIFICATION,
                IL5_IMPACT_LEVEL,
                ingested_at,
                source_published_at.isoformat() if source_published_at else None,
                display_latency_s,
                sla_met,
                json.dumps(metadata or {}),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return event_id


def get_il5_events(
    *,
    since: Optional[str] = None,
    limit: int = 25,
    db_path: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    """Retrieve IL5 ingestion events for display.

    Used by the polling endpoint to push new events to authenticated clients.

    Args:
        since: ISO 8601 cursor; only events ingested after this time.
        limit: Max rows to return.
        db_path: Override DB path.

    Returns:
        List of event dicts ordered newest-first.
    """
    conn = _open_conn(db_path)
    try:
        conn.execute(_CREATE_TABLE_SQL)
        query = f"SELECT * FROM {IL5_TABLE} WHERE 1=1"
        params: list = []
        if since:
            query += " AND ingested_at > ?"
            params.append(since)
        query += " ORDER BY ingested_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def check_sla_compliance(
    event_id: str,
    *,
    db_path: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """Return SLA compliance details for a specific event.

    Returns:
        Dict with keys: event_id, sla_met (bool|None), latency_s (float|None).
        Returns None if the event is not found.
    """
    conn = _open_conn(db_path)
    try:
        conn.execute(_CREATE_TABLE_SQL)
        row = conn.execute(
            f"SELECT sla_met, display_latency_s FROM {IL5_TABLE} WHERE id=%s",
            (event_id,),
        ).fetchone()
        if row is None:
            return None
        raw_met = row["sla_met"]
        return {
            "event_id": event_id,
            "sla_met": bool(raw_met) if raw_met is not None else None,
            "latency_s": row["display_latency_s"],
        }
    finally:
        conn.close()


def get_sla_summary(*, db_path: Optional[Path] = None) -> Dict[str, Any]:
    """Aggregate SLA compliance statistics across all IL5 events.

    Returns:
        Dict with total, sla_met, sla_violated, sla_unknown, compliance_pct.
        compliance_pct counts only events where source_published_at is known.
    """
    conn = _open_conn(db_path)
    try:
        conn.execute(_CREATE_TABLE_SQL)
        row = conn.execute(
            f"""
            SELECT
                COUNT(*)                                        AS total,
                SUM(CASE WHEN sla_met = 1 THEN 1 ELSE 0 END)  AS met,
                SUM(CASE WHEN sla_met = 0 THEN 1 ELSE 0 END)  AS violated,
                SUM(CASE WHEN sla_met IS NULL THEN 1 ELSE 0 END) AS unknown
            FROM {IL5_TABLE}
            """
        ).fetchone()
        total = row["total"] or 0
        met = row["met"] or 0
        violated = row["violated"] or 0
        unknown = row["unknown"] or 0
        known = met + violated
        # NOT ASSESSED, never 100.0 (rem-hyg-13). `known` counts only the events
        # whose SLA outcome could be decided at all — the docstring above says
        # so. Zero of them means every ingested event had an unknown
        # source_published_at, or none were ingested, and the old `else 100.0`
        # reported perfect IL5 SLA compliance over exactly that absence.
        pct = round((met / known) * 100, 2) if known > 0 else None
        return {
            "total": total,
            "sla_met": met,
            "sla_violated": violated,
            "sla_unknown": unknown,
            "compliance_pct": pct,
            "sla_threshold_s": SLA_SECONDS,
            "classification": IL5_CLASSIFICATION,
            "impact_level": IL5_IMPACT_LEVEL,
        }
    finally:
        conn.close()
