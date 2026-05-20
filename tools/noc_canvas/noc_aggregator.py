# CUI // SP-CTI
"""NOCC overview aggregator — combined health snapshot for the dashboard."""

from __future__ import annotations

from typing import Any


def get_noc_overview(conn: Any) -> dict:
    """Return a combined overview dict for the NOCC index page.

    Fields:
        alarm_summary       — counts by severity
        incident_counts     — counts by priority for open incidents
        sla_health          — compliance % and breach count
        upcoming_maintenance — next 3 windows
        open_p1_count       — quick-glance P1 count
    """
    from tools.noc_canvas.alarm_correlator import get_alarm_summary
    from tools.noc_canvas.sla_predictor import get_sla_dashboard
    from tools.noc_canvas.maintenance_planner import get_upcoming_windows

    alarm_summary = get_alarm_summary(conn)
    sla_health = get_sla_dashboard(conn)
    upcoming = get_upcoming_windows(conn, days_ahead=3)

    incident_counts = {p: 0 for p in ["p1", "p2", "p3", "p4"]}
    try:
        rows = conn.execute(
            "SELECT severity, COUNT(*) AS cnt FROM noc_incidents "
            "WHERE status NOT IN ('resolved','closed') GROUP BY severity"
        ).fetchall()
        for row in rows:
            sev = row[0] if not hasattr(row, "keys") else row["severity"]
            cnt = row[1] if not hasattr(row, "keys") else row["cnt"]
            if sev in incident_counts:
                incident_counts[sev] = cnt
    except Exception:
        pass

    return {
        "alarm_summary": alarm_summary,
        "incident_counts": incident_counts,
        "open_p1_count": incident_counts.get("p1", 0),
        "sla_health": sla_health,
        "upcoming_maintenance": upcoming[:3],
        "total_active_alarms": sum(alarm_summary.values()),
    }
