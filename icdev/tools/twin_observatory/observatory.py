# CUI // SP-CTI — Twin Observatory backing module (twx-obs-01)
"""Compose the twin_core observer + cross-canvas event feed into a
template-ready shape for the Twin Observatory page.

Read-only. No new tables — reuses:
  * tools.twin_core.observer.observe()          — per-twin health grid
  * tools.twin_core.event_bridge.recent_twin_events() — twin_* drift stream
"""
from __future__ import annotations

from tools.logging.icdev_logger import get_logger
from tools.twin_observatory.constants import TWIN_CANVAS_ROUTES, VERDICT_COLORS

logger = get_logger("icdev.twin_observatory")


def _humanize_age(seconds) -> str:
    if seconds is None:
        return "never"
    try:
        s = float(seconds)
    except (TypeError, ValueError):
        return "unknown"
    if s < 90:
        return f"{int(s)}s"
    if s < 5400:
        return f"{int(s // 60)}m"
    if s < 172800:
        return f"{int(s // 3600)}h"
    return f"{int(s // 86400)}d"


def get_observatory_data(window_hours: int = 24, event_limit: int = 50) -> dict:
    """Return {report, events, generated_at} for the Observatory template.

    Degrades gracefully: any backing failure yields an empty-but-valid payload so
    the page always renders.
    """
    from tools.twin_core.observer import observe
    from tools.twin_core.schema import worst_verdict

    try:
        report = observe(window_hours=window_hours)
    except Exception as exc:  # noqa: BLE001
        logger.warning("observatory: observe() failed: %s", exc)
        report = {"generated_at": None, "twin_count": 0, "twins": [],
                  "summary": {"verdict_distribution": {}, "violation_counts": {},
                              "stale_twins": [], "overdue_reflexes": []}}

    for t in report.get("twins", []):
        verdicts = t.get("verdicts") or {}
        t["latest_verdict"] = worst_verdict(list(verdicts.keys())) if verdicts else "unknown"
        t["verdict_color"] = VERDICT_COLORS.get(t["latest_verdict"], VERDICT_COLORS["unknown"])
        t["age_human"] = _humanize_age(t.get("latest_snapshot_age_seconds"))
        t["canvas_route"] = TWIN_CANVAS_ROUTES.get(t.get("canvas"), "#")
        vc = t.get("violation_counts") or {}
        t["violation_total"] = sum(v for v in vc.values() if isinstance(v, (int, float)))
        rfx = t.get("reflex") or {}
        t["reflex_overdue"] = bool(rfx.get("overdue"))

    try:
        from tools.twin_core.event_bridge import recent_twin_events

        events = recent_twin_events(limit=event_limit)
    except Exception as exc:  # noqa: BLE001
        logger.warning("observatory: recent_twin_events() failed: %s", exc)
        events = []

    return {
        "report": report,
        "events": events,
        "generated_at": report.get("generated_at"),
    }


# ── IQE collection providers (registry-driven) ────────────────────────────────

def twins_collection(conn=None) -> list[dict]:
    """IQE collection: one row per registered twin (health snapshot)."""
    try:
        return get_observatory_data().get("report", {}).get("twins", [])
    except Exception:  # noqa: BLE001
        return []


def events_collection(conn=None) -> list[dict]:
    """IQE collection: recent twin_* cross-canvas events."""
    try:
        from tools.twin_core.event_bridge import recent_twin_events

        return recent_twin_events(limit=100)
    except Exception:  # noqa: BLE001
        return []
