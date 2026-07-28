# CUI // SP-CTI
"""Executive view data — ROI summary, agent activity digest, cost breakdown.

Every metric is computed from the ``aisg_roi_events`` table. Metrics with no
real backing data (e.g. an AI-maturity score) are reported as *unavailable*
rather than fabricated. An empty table yields an explicit empty-state payload;
a DB failure yields a degraded error payload — a broken database must never
render as a healthy executive dashboard.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tools.db.storage import get_connection

from tools.logging.icdev_logger import get_logger
logger = get_logger("icdev.aisg.executive_view")

COST_PER_HOUR = 150.0

# Number of days since an agent's last logged event within which it is "active".
_ACTIVE_WINDOW_DAYS = 7

# Human-readable category label per ROI action_type (for cost breakdown).
_CATEGORY_LABELS: dict[str, str] = {
    "self_heal": "Self-Healing",
    "compliance_check": "Compliance Checks",
    "security_scan": "Security Scans",
    "test_run": "Test Automation",
    "evidence_collect": "Evidence Collection",
    "pattern_deploy": "Pattern Deployment",
    "fine_tune_eval": "Fine-Tune Evaluation",
    "genesis_reflex": "Genesis Reflexes",
}


def _fetch_all_events() -> list[dict]:
    """Return every ROI event (newest first). Raises on DB error."""
    conn = get_connection()
    try:
        c = conn.cursor()
        c.execute(
            "SELECT action_type, time_saved_minutes, description, triggered_by, occurred_at "
            "FROM aisg_roi_events ORDER BY occurred_at DESC"
        )
        return [dict(r) for r in c.fetchall()]
    finally:
        conn.close()


def _base_payload(state: str, message: str) -> dict:
    """Skeleton payload with all template keys zeroed / emptied."""
    return {
        "state": state,
        "empty": state == "empty",
        "error": state == "error",
        "message": message,
        "hours_saved": 0.0,
        "cost_avoided": 0.0,
        "roi_events": [],
        "cost_breakdown": [],
        "agent_activity": [],
        "maturity_trend": [],
        "maturity_available": False,
        "maturity_score": None,
        "maturity_level": "Not available",
        "agents_active": 0,
        "total_tasks_automated": 0,
    }


def _parse_dt(occurred_at: str):
    """Best-effort parse of an ISO / SQLite datetime string to aware UTC."""
    s = (occurred_at or "").strip().replace(" ", "T")
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        # Fall back to the date portion only.
        try:
            dt = datetime.fromisoformat(s[:10])
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def get_executive_data() -> dict:
    """Return ROI summary, activity digest, and cost breakdown for the exec view.

    Payload ``state`` is one of ``"ok"`` / ``"empty"`` / ``"error"``.
    """
    try:
        events = _fetch_all_events()
    except Exception as exc:
        logger.warning("get_executive_data: DB error reading aisg_roi_events: %s", exc)
        return _base_payload("error", "Executive ROI data is temporarily unavailable.")

    if not events:
        return _base_payload("empty", "No ROI events recorded yet")

    hours_saved = sum(float(e.get("time_saved_minutes") or 0) / 60.0 for e in events)
    cost_avoided = hours_saved * COST_PER_HOUR
    total_hours = hours_saved

    # Cost breakdown by action-type category (real aggregates).
    by_type: dict[str, float] = {}
    for e in events:
        at = e.get("action_type") or "other"
        by_type[at] = by_type.get(at, 0.0) + float(e.get("time_saved_minutes") or 0) / 60.0
    cost_breakdown = [
        {
            "category": _CATEGORY_LABELS.get(at, at.replace("_", " ").title()),
            "hours": round(hrs, 1),
            "pct": round((hrs / total_hours * 100) if total_hours else 0),
        }
        for at, hrs in sorted(by_type.items(), key=lambda x: -x[1])
    ]

    # Agent activity digest derived from triggered_by (real).
    now = datetime.now(timezone.utc)
    active_cutoff = now - timedelta(days=_ACTIVE_WINDOW_DAYS)
    agents: dict[str, dict] = {}
    for e in events:
        agent = e.get("triggered_by") or "unknown"
        occurred = str(e.get("occurred_at") or "")
        a = agents.setdefault(agent, {"tasks_completed": 0, "last_run": ""})
        a["tasks_completed"] += 1
        if occurred > a["last_run"]:
            a["last_run"] = occurred

    agent_activity = []
    agents_active = 0
    for agent, a in sorted(agents.items(), key=lambda x: -x[1]["tasks_completed"]):
        dt = _parse_dt(a["last_run"])
        is_active = dt is not None and dt >= active_cutoff
        if is_active:
            agents_active += 1
        agent_activity.append(
            {
                "agent": agent,
                "tasks_completed": a["tasks_completed"],
                "last_run": a["last_run"],
                "status": "active" if is_active else "idle",
            }
        )

    return {
        "state": "ok",
        "empty": False,
        "error": False,
        "message": "",
        "hours_saved": round(hours_saved, 1),
        "cost_avoided": round(cost_avoided, 0),
        "roi_events": events[:10],
        "cost_breakdown": cost_breakdown,
        "agent_activity": agent_activity,
        # AI-maturity has no real data source yet — report as unavailable rather
        # than fabricate a score.
        "maturity_trend": [],
        "maturity_available": False,
        "maturity_score": None,
        "maturity_level": "Not available",
        "agents_active": agents_active,
        "total_tasks_automated": len(events),
    }
