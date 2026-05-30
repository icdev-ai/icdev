# CUI // SP-CTI
"""NOCC maintenance window planner — scheduling, conflict detection, notifications."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def get_upcoming_windows(conn: Any, days_ahead: int = 7) -> list[dict]:
    """Return scheduled maintenance windows in the next N days."""
    now = _now_utc()
    cutoff = (now + timedelta(days=days_ahead)).isoformat()
    now_str = now.isoformat()

    for sql, params in [
        (
            "SELECT * FROM noc_maintenance_windows "
            "WHERE status = 'scheduled' AND scheduled_start >= %s AND scheduled_start <= %s "
            "ORDER BY scheduled_start ASC LIMIT 50",
            (now_str, cutoff),
        ),
        (
            "SELECT * FROM noc_maintenance_windows "
            "WHERE status = 'scheduled' AND scheduled_start >= ? AND scheduled_start <= ? "
            "ORDER BY scheduled_start ASC LIMIT 50",
            (now_str, cutoff),
        ),
    ]:
        try:
            cur = conn.execute(sql, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        except Exception:
            continue
    return []


def check_window_conflicts(
    conn: Any, start: str, end: str, circuits: list[str]
) -> list[dict]:
    """Return existing windows that overlap the proposed window on the same circuits."""
    conflicts = []
    try:
        for sql, params in [
            (
                "SELECT * FROM noc_maintenance_windows "
                "WHERE status IN ('scheduled', 'in-progress') "
                "AND scheduled_start < %s AND scheduled_end > %s",
                (end, start),
            ),
            (
                "SELECT * FROM noc_maintenance_windows "
                "WHERE status IN ('scheduled', 'in-progress') "
                "AND scheduled_start < ? AND scheduled_end > ?",
                (end, start),
            ),
        ]:
            try:
                cur = conn.execute(sql, params)
                cols = [d[0] for d in cur.description]
                rows = [dict(zip(cols, row)) for row in cur.fetchall()]
                break
            except Exception:
                rows = []

        circuit_set = set(circuits)
        for row in rows:
            affected_raw = row.get("affected_circuits", "[]")
            try:
                affected = json.loads(affected_raw) if isinstance(affected_raw, str) else affected_raw
            except Exception:
                affected = []
            if circuit_set.intersection(set(affected)):
                conflicts.append(row)
    except Exception:
        pass
    return conflicts


def notify_customers(window: dict, customers: list[str]) -> dict:
    """Log a notification event to noc_audit (stub — real delivery wired in Phase 4)."""
    event = {
        "window_number": window.get("window_number", ""),
        "window_title": window.get("title", ""),
        "scheduled_start": window.get("scheduled_start", ""),
        "scheduled_end": window.get("scheduled_end", ""),
        "notified_customers": customers,
        "notified_at": _now_utc().isoformat(),
        "delivery_method": "stub",
    }
    return {"status": "logged", "event": event}


def create_window(conn: Any, window_data: dict) -> str:
    """Insert a new maintenance window and return its id."""
    win_id = str(uuid.uuid4())
    now = _now_utc()
    win_num = f"MW-{now.strftime('%Y')}-{win_id[:6].upper()}"

    circuits_json = json.dumps(window_data.get("affected_circuits", []))
    customers_json = json.dumps(window_data.get("affected_customers", []))

    for sql, params in [
        (
            "INSERT INTO noc_maintenance_windows "
            "(id, window_number, title, rfc_id, scheduled_start, scheduled_end, "
            "status, impact_scope, affected_customers, affected_circuits, classification) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (win_id, win_num, window_data.get("title", "Maintenance"),
             window_data.get("rfc_id"), window_data.get("scheduled_start"),
             window_data.get("scheduled_end"), "scheduled",
             window_data.get("impact_scope", "single-circuit"),
             customers_json, circuits_json,
             window_data.get("classification", "CUI")),
        ),
        (
            "INSERT INTO noc_maintenance_windows "
            "(id, window_number, title, rfc_id, scheduled_start, scheduled_end, "
            "status, impact_scope, affected_customers, affected_circuits, classification) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (win_id, win_num, window_data.get("title", "Maintenance"),
             window_data.get("rfc_id"), window_data.get("scheduled_start"),
             window_data.get("scheduled_end"), "scheduled",
             window_data.get("impact_scope", "single-circuit"),
             customers_json, circuits_json,
             window_data.get("classification", "CUI")),
        ),
    ]:
        try:
            conn.execute(sql, params)
            conn.commit()
            return win_id
        except Exception:
            continue
    raise RuntimeError("Failed to create maintenance window")
