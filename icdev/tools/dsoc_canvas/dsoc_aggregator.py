# CUI // SP-CTI
"""DSOC overview aggregator."""
from __future__ import annotations

from datetime import datetime, timezone


def _safe_fetch(conn, sql_pg: str, sql_sq: str, params: tuple = ()) -> list[dict]:
    """Execute a SELECT with PG-style params; fall back to SQLite-style on failure."""
    def _rows(cur) -> list[dict]:
        rows = cur.fetchall()
        if not rows:
            return []
        if hasattr(rows[0], "keys"):
            return [dict(r) for r in rows]
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in rows]

    try:
        cur = conn.cursor()
        cur.execute(sql_pg, params)
        return _rows(cur)
    except Exception:
        try:
            cur = conn.cursor()
            cur.execute(sql_sq, params)
            return _rows(cur)
        except Exception:
            return []


def _scalar(conn, sql_pg: str, sql_sq: str, params: tuple = (), default=0):
    """Return the first column of the first row, or default on failure."""
    rows = _safe_fetch(conn, sql_pg, sql_sq, params)
    if not rows:
        return default
    first = rows[0]
    if isinstance(first, dict):
        return next(iter(first.values()), default)
    return default


def get_dsoc_overview(conn) -> dict:
    """Return a snapshot of DSOC health metrics."""

    active_mitigations = _scalar(
        conn,
        "SELECT COUNT(*) FROM dsoc_mitigations WHERE status='active'",
        "SELECT COUNT(*) FROM dsoc_mitigations WHERE status='active'",
        default=0,
    )

    active_rtbh = _scalar(
        conn,
        "SELECT COUNT(*) FROM dsoc_rtbh_entries WHERE status='active'",
        "SELECT COUNT(*) FROM dsoc_rtbh_entries WHERE status='active'",
        default=0,
    )

    active_flowspec = _scalar(
        conn,
        "SELECT COUNT(*) FROM dsoc_flowspec_rules WHERE status='active'",
        "SELECT COUNT(*) FROM dsoc_flowspec_rules WHERE status='active'",
        default=0,
    )

    scrubbing_centers = _scalar(
        conn,
        "SELECT COUNT(*) FROM dsoc_scrubbing_centers",
        "SELECT COUNT(*) FROM dsoc_scrubbing_centers",
        default=0,
    )

    cap_rows = _safe_fetch(
        conn,
        "SELECT COALESCE(SUM(capacity_gbps),0) AS cap, COALESCE(SUM(current_load_gbps),0) AS load FROM dsoc_scrubbing_centers",
        "SELECT COALESCE(SUM(capacity_gbps),0) AS cap, COALESCE(SUM(current_load_gbps),0) AS load FROM dsoc_scrubbing_centers",
    )
    if cap_rows:
        scrubbing_capacity_gbps = float(cap_rows[0].get("cap", 0) or 0)
        scrubbing_load_gbps = float(cap_rows[0].get("load", 0) or 0)
    else:
        scrubbing_capacity_gbps = 0.0
        scrubbing_load_gbps = 0.0

    if scrubbing_capacity_gbps > 0:
        scrubbing_utilization_pct = round(
            (scrubbing_load_gbps / scrubbing_capacity_gbps) * 100.0, 2
        )
    else:
        scrubbing_utilization_pct = 0.0

    active_threats = _scalar(
        conn,
        "SELECT COUNT(*) FROM dsoc_threats WHERE is_active=1",
        "SELECT COUNT(*) FROM dsoc_threats WHERE is_active=1",
        default=0,
    )

    high_confidence_threats = _scalar(
        conn,
        "SELECT COUNT(*) FROM dsoc_threats WHERE is_active=1 AND confidence_pct >= 80",
        "SELECT COUNT(*) FROM dsoc_threats WHERE is_active=1 AND confidence_pct >= 80",
        default=0,
    )

    return {
        "active_mitigations": int(active_mitigations),
        "active_rtbh": int(active_rtbh),
        "active_flowspec": int(active_flowspec),
        "scrubbing_centers": int(scrubbing_centers),
        "scrubbing_capacity_gbps": scrubbing_capacity_gbps,
        "scrubbing_load_gbps": scrubbing_load_gbps,
        "scrubbing_utilization_pct": scrubbing_utilization_pct,
        "active_threats": int(active_threats),
        "high_confidence_threats": int(high_confidence_threats),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
