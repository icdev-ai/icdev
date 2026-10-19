# CUI // SP-CTI
"""AISG ROI event emitter + real-data ROI summary for the /ai-roi dashboard.

All dashboard metrics are computed from the ``aisg_roi_events`` table. When the
table is empty an explicit empty-state payload is returned (never fabricated
"demo" numbers presented as real), and a DB failure returns a degraded
error payload so a broken database never renders as a healthy dashboard.
"""
from __future__ import annotations

from datetime import datetime, timezone

from tools.aisg.constants import ROI_RATES_MINUTES
from tools.db.storage import get_connection

from tools.logging.icdev_logger import get_logger
logger = get_logger("icdev.aisg.roi_tracker")

# Blended labor rate used to convert saved hours into avoided cost.
COST_PER_HOUR = 150.0


def emit_roi_event(
    action_type: str,
    description: str,
    triggered_by: str = "system",
) -> int | None:
    """Insert one row into aisg_roi_events and return the new row id.

    Args:
        action_type: Must be one of the keys in ROI_RATES_MINUTES / DB CHECK.
        description: Human-readable summary of what triggered this event.
        triggered_by: Module or subsystem that caused the action.

    Returns:
        The inserted row id, or None if the insert failed.
    """
    time_saved = ROI_RATES_MINUTES.get(action_type)
    if time_saved is None:
        return None

    occurred_at = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            INSERT INTO aisg_roi_events
                (action_type, time_saved_minutes, description, triggered_by, occurred_at)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (action_type, time_saved, description, triggered_by, occurred_at),
        )
        conn.commit()
        return cur.lastrowid
    except Exception as exc:  # pragma: no cover - defensive write path
        logger.warning("emit_roi_event: failed to insert ROI event: %s", exc)
        return None
    finally:
        conn.close()


def _fetch_aggregates() -> dict:
    """Return ROI totals, per-type and per-month sums, and the 10 newest events.

    Raises on any DB error so callers can distinguish an empty table (healthy,
    no events yet) from a broken database (degraded). Aggregated in SQL: the
    genesis daemon emits an event per reflex, so the table is NOT low-volume
    (118,466 rows on 2026-10-14) and fetching it whole took ~12s per request
    (qa-fail-1f5a9e0dfee73531). The month bucket is ``SUBSTR(CAST(.. AS TEXT))``,
    which PostgreSQL and SQLite both accept for a TEXT or timestamp column.
    """
    conn = get_connection()
    try:
        c = conn.cursor()
        c.execute(
            "SELECT COUNT(*) AS n, SUM(time_saved_minutes) AS minutes "
            "FROM aisg_roi_events"
        )
        totals = dict(c.fetchone() or {})
        c.execute(
            "SELECT action_type, SUM(time_saved_minutes) AS minutes "
            "FROM aisg_roi_events GROUP BY action_type"
        )
        by_type = [dict(r) for r in c.fetchall()]
        c.execute(
            "SELECT SUBSTR(CAST(occurred_at AS TEXT), 1, 7) AS month, "
            "SUM(time_saved_minutes) AS minutes "
            "FROM aisg_roi_events GROUP BY SUBSTR(CAST(occurred_at AS TEXT), 1, 7)"
        )
        by_month = [dict(r) for r in c.fetchall()]
        c.execute(
            "SELECT action_type, time_saved_minutes, description, triggered_by, occurred_at "
            "FROM aisg_roi_events ORDER BY occurred_at DESC LIMIT 10"
        )
        recent = [dict(r) for r in c.fetchall()]
    finally:
        conn.close()
    return {
        "count": int(totals.get("n") or 0),
        "minutes": float(totals.get("minutes") or 0),
        "by_type": by_type,
        "by_month": by_month,
        "recent": recent,
    }


def _month_key(occurred_at: str) -> str | None:
    """Extract a sortable ``YYYY-MM`` key from an ISO/SQLite datetime string."""
    if not occurred_at or len(occurred_at) < 7:
        return None
    key = occurred_at[:7]
    # Basic shape validation: YYYY-MM
    if len(key) == 7 and key[4] == "-" and key[:4].isdigit() and key[5:].isdigit():
        return key
    return None


def _month_label(key: str) -> str:
    try:
        return datetime.strptime(key, "%Y-%m").strftime("%b %Y")
    except ValueError:
        return key


def _monthly_trend(by_month: list[dict]) -> list[dict]:
    """Hours saved grouped by calendar month, chronological.

    ``by_month`` rows carry a raw ``YYYY-MM`` prefix and a minute sum; the
    shape is validated here, in Python, rather than with dialect date SQL.
    """
    buckets: dict[str, float] = {}
    for r in by_month:
        key = _month_key(str(r.get("month") or ""))
        if not key:
            continue
        buckets[key] = buckets.get(key, 0.0) + float(r.get("minutes") or 0) / 60.0
    return [
        {"month": _month_label(k), "hours": round(v, 1), "key": k}
        for k, v in sorted(buckets.items())
    ]


def _empty_payload(message: str, *, error: bool = False) -> dict:
    return {
        "state": "error" if error else "empty",
        "empty": not error,
        "error": error,
        "message": message,
        "hours_saved": 0.0,
        "cost_avoided": 0.0,
        "total_events": 0,
        "recent_events": [],
        "breakdown": [],
        "monthly_trend": [],
    }


def get_roi_summary() -> dict:
    """Return aggregated ROI metrics for the /ai-roi dashboard.

    Returns one of three payload shapes, distinguished by ``state``:
      * ``"ok"``    — real aggregates computed from aisg_roi_events
      * ``"empty"`` — table exists but has no events yet
      * ``"error"`` — DB error; degraded payload, zeros, no canned numbers
    """
    try:
        agg = _fetch_aggregates()
    except Exception as exc:
        logger.warning("get_roi_summary: DB error reading aisg_roi_events: %s", exc)
        return _empty_payload("ROI data is temporarily unavailable.", error=True)

    if not agg["count"]:
        return _empty_payload("No ROI events recorded yet")

    hours_saved = agg["minutes"] / 60.0
    cost_avoided = hours_saved * COST_PER_HOUR

    by_type: dict[str, float] = {}
    for r in agg["by_type"]:
        at = r.get("action_type") or "other"
        by_type[at] = by_type.get(at, 0.0) + float(r.get("minutes") or 0) / 60.0
    breakdown = [
        {"action_type": k, "hours": round(v, 1)}
        for k, v in sorted(by_type.items(), key=lambda x: -x[1])
    ]

    return {
        "state": "ok",
        "empty": False,
        "error": False,
        "hours_saved": round(hours_saved, 1),
        "cost_avoided": round(cost_avoided, 0),
        "total_events": agg["count"],
        "recent_events": agg["recent"],
        "breakdown": breakdown,
        "monthly_trend": _monthly_trend(agg["by_month"]),
    }
