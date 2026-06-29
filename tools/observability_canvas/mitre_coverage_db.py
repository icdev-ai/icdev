#!/usr/bin/env python3
# CUI // SP-CTI
"""MITRE ATT&CK coverage CRUD — append-only, NIST AU.

Each call to record_coverage() inserts a new row; rows are never updated or
deleted (guard enforced by pre_tool_use.py APPEND_ONLY_TABLES).

Current state for a technique is derived from the most-recently inserted row
per (technique_id, signal_source, project_id).

Valid states: none | partial | full | false_positive
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.observability_canvas.db.init_db import get_connection  # noqa: E402

VALID_STATES = {"none", "partial", "full", "false_positive"}


def record_coverage(
    technique_id: str,
    signal_source: str,
    state: str,
    last_observed_at: str,
    project_id: str,
) -> str:
    """Insert a coverage event row; returns the new row id.

    Raises ValueError if state is not in VALID_STATES.
    """
    if state not in VALID_STATES:
        raise ValueError(f"Invalid state {state!r}; must be one of {sorted(VALID_STATES)}")
    row_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO mitre_coverage "
            "(id, technique_id, signal_source, state, last_observed_at, project_id, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (row_id, technique_id, signal_source, state, last_observed_at, project_id, now),
        )
        conn.commit()
    finally:
        conn.close()
    return row_id


def get_coverage(
    technique_id: str,
    project_id: str,
    signal_source: str | None = None,
) -> dict | None:
    """Return the latest coverage state for a technique, or None if no rows exist.

    If signal_source is given, scope the lookup to that source.
    """
    conn = get_connection()
    try:
        if signal_source is not None:
            row = conn.execute(
                "SELECT * FROM mitre_coverage "
                "WHERE technique_id = %s AND project_id = %s AND signal_source = %s "
                "ORDER BY created_at DESC LIMIT 1",
                (technique_id, project_id, signal_source),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM mitre_coverage "
                "WHERE technique_id = %s AND project_id = %s "
                "ORDER BY created_at DESC LIMIT 1",
                (technique_id, project_id),
            ).fetchone()
        if row is None:
            return None
        return dict(row) if hasattr(row, "keys") else {
            "id": row[0],
            "technique_id": row[1],
            "signal_source": row[2],
            "state": row[3],
            "last_observed_at": row[4],
            "project_id": row[5],
            "created_at": row[6],
        }
    finally:
        conn.close()


def list_coverage(
    project_id: str,
    state: str | None = None,
    limit: int = 200,
) -> list[dict]:
    """Return coverage rows for a project, newest first.

    If state is given, filter to that state value only.
    """
    conn = get_connection()
    try:
        if state is not None:
            rows = conn.execute(
                "SELECT * FROM mitre_coverage "
                "WHERE project_id = %s AND state = %s "
                "ORDER BY created_at DESC LIMIT %s",
                (project_id, state, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM mitre_coverage "
                "WHERE project_id = %s "
                "ORDER BY created_at DESC LIMIT %s",
                (project_id, limit),
            ).fetchall()
        if not rows:
            return []
        if hasattr(rows[0], "keys"):
            return [dict(r) for r in rows]
        cols = ["id", "technique_id", "signal_source", "state", "last_observed_at", "project_id", "created_at"]
        return [dict(zip(cols, r)) for r in rows]
    finally:
        conn.close()


def coverage_summary(project_id: str) -> dict[str, int]:
    """Return counts of the latest state per (technique_id, signal_source) pair.

    Uses a subquery to pick the most-recent row per technique+source, then
    groups by state — so each technique slot is counted exactly once.
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT state, COUNT(*) AS cnt
            FROM (
                SELECT technique_id, signal_source,
                       state,
                       ROW_NUMBER() OVER (
                           PARTITION BY technique_id, signal_source
                           ORDER BY created_at DESC
                       ) AS rn
                FROM mitre_coverage
                WHERE project_id = %s
            ) ranked
            WHERE rn = 1
            GROUP BY state
            """,
            (project_id,),
        ).fetchall()
        summary: dict[str, int] = {s: 0 for s in VALID_STATES}
        for row in rows:
            if hasattr(row, "keys"):
                summary[row["state"]] = row["cnt"]
            else:
                summary[row[0]] = row[1]
        return summary
    finally:
        conn.close()
