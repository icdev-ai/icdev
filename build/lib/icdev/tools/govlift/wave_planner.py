# CUI // SP-CTI
"""GovLift — Wave Planner.

Manages migration waves: create, list, update status, and summarize.
All DB access via get_connection() — never sqlite3.connect().
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

_ICDEV_ROOT = Path(__file__).resolve().parents[2]
if str(_ICDEV_ROOT) not in sys.path:
    sys.path.insert(0, str(_ICDEV_ROOT))

from tools.db.storage import get_connection, translate_sql
from tools.govlift.constants import WAVE_STATUSES


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _wave_id() -> str:
    return "wave-" + uuid4().hex[:8]


def _row_to_dict(row) -> dict:
    if row is None:
        return {}
    try:
        return dict(row)
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def list_waves(status: str | None = None) -> list[dict]:
    """Return all waves ordered by sequence_num, filtered by optional status."""
    try:
        conn = get_connection()
        try:
            if status:
                sql = translate_sql(
                    "SELECT * FROM govlift_waves WHERE status = ? ORDER BY sequence_num ASC"
                )
                rows = conn.execute(sql, (status,)).fetchall()
            else:
                sql = translate_sql(
                    "SELECT * FROM govlift_waves ORDER BY sequence_num ASC"
                )
                rows = conn.execute(sql).fetchall()
            return [_row_to_dict(r) for r in rows]
        finally:
            conn.close()
    except Exception as exc:
        print(f"wave_planner.list_waves error: {exc}", file=sys.stderr)
        return []


def get_wave(wave_id: str) -> dict | None:
    """Return a single wave by ID, or None if not found."""
    try:
        conn = get_connection()
        try:
            sql = translate_sql("SELECT * FROM govlift_waves WHERE id = ?")
            row = conn.execute(sql, (wave_id,)).fetchone()
            return _row_to_dict(row) if row else None
        finally:
            conn.close()
    except Exception as exc:
        print(f"wave_planner.get_wave error: {exc}", file=sys.stderr)
        return None


def create_wave(
    name: str,
    sequence_num: int,
    planned_start: str | None = None,
    planned_end: str | None = None,
    notes: str = "",
) -> dict:
    """Insert a new migration wave and return the created record."""
    wv_id = _wave_id()
    now = _now()
    try:
        conn = get_connection()
        try:
            sql = translate_sql(
                "INSERT INTO govlift_waves "
                "(id, name, sequence_num, status, planned_start, planned_end, "
                " workload_count, notes, created_at) "
                "VALUES (?,?,?,'planned',?,?,0,?,?)"
            )
            conn.execute(
                sql,
                (wv_id, name, sequence_num, planned_start, planned_end, notes, now),
            )
            conn.commit()
            return get_wave(wv_id) or {}
        finally:
            conn.close()
    except Exception as exc:
        print(f"wave_planner.create_wave error: {exc}", file=sys.stderr)
        raise


def update_wave_status(wave_id: str, status: str) -> dict:
    """Update the status of a wave."""
    if status not in WAVE_STATUSES:
        raise ValueError(f"Invalid wave status '{status}'. Valid: {WAVE_STATUSES}")
    try:
        conn = get_connection()
        try:
            sql = translate_sql(
                "UPDATE govlift_waves SET status = ? WHERE id = ?"
            )
            conn.execute(sql, (status, wave_id))
            conn.commit()
            return get_wave(wave_id) or {}
        finally:
            conn.close()
    except Exception as exc:
        print(f"wave_planner.update_wave_status error: {exc}", file=sys.stderr)
        raise


def get_wave_summary() -> dict:
    """Return total/by-status counts and per-wave workload breakdown."""
    try:
        conn = get_connection()
        try:
            total_row = conn.execute(
                translate_sql("SELECT COUNT(*) AS cnt FROM govlift_waves")
            ).fetchone()
            status_rows = conn.execute(
                translate_sql(
                    "SELECT status, COUNT(*) AS cnt FROM govlift_waves GROUP BY status"
                )
            ).fetchall()
            wave_rows = conn.execute(
                translate_sql(
                    "SELECT id, name, sequence_num, status, workload_count "
                    "FROM govlift_waves ORDER BY sequence_num ASC"
                )
            ).fetchall()

            total = _row_to_dict(total_row).get("cnt", 0) if total_row else 0
            by_status = {r["status"]: r["cnt"] for r in status_rows}
            waves = [_row_to_dict(r) for r in wave_rows]

            return {
                "total": total,
                "planned": by_status.get("planned", 0),
                "ready": by_status.get("ready", 0),
                "in_progress": by_status.get("in_progress", 0),
                "completed": by_status.get("completed", 0),
                "paused": by_status.get("paused", 0),
                "by_status": by_status,
                "by_wave": waves,
                "workload_breakdown": waves,
            }
        finally:
            conn.close()
    except Exception as exc:
        print(f"wave_planner.get_wave_summary error: {exc}", file=sys.stderr)
        return {
            "total": 0,
            "planned": 0,
            "ready": 0,
            "in_progress": 0,
            "completed": 0,
            "paused": 0,
            "by_status": {},
            "workload_breakdown": [],
        }
