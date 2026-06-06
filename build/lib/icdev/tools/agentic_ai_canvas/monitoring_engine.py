# CUI // SP-CTI
"""Agentic AI Design Canvas — Monitoring Engine (Phase 10).

Tracks assessment score trends per design and surfaces drift alerts:
  CRITICAL  — score dropped ≥ 20 pts since last baseline
  HIGH      — score dropped ≥ 10 pts
  MEDIUM    — score dropped ≥  5 pts
  OK        — score stable or improving

Portfolio summary: alert counts, designs at risk, latest score per design.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

_DRIFT_CRITICAL = 20
_DRIFT_HIGH = 10
_DRIFT_MEDIUM = 5

_ALERT_COLORS = {
    "CRITICAL": "danger",
    "HIGH": "warning",
    "MEDIUM": "info",
    "OK": "success",
}


def compute_monitoring(designs: list[dict], assessments: list[dict]) -> dict:
    """
    Compute monitoring status for all designs.

    Returns:
        {
          design_alerts: [{design_id, design_name, current_score, baseline_score,
                           drift, alert_level, color, history}],
          summary: {critical, high, medium, ok, total_designs, assessed},
          generated_at: str,
        }
    """
    design_names = {d["id"]: d.get("name", d["id"][:8]) for d in designs}
    by_design: dict[str, list[dict]] = defaultdict(list)
    for a in assessments:
        by_design[a["design_id"]].append(a)

    design_alerts = []
    for design in designs:
        did = design["id"]
        rows = sorted(by_design.get(did, []), key=lambda x: x.get("created_at", ""))
        if not rows:
            design_alerts.append({
                "design_id": did,
                "design_name": design_names.get(did, did[:8]),
                "current_score": None,
                "baseline_score": None,
                "drift": 0,
                "alert_level": "OK",
                "color": "success",
                "history": [],
                "assessed": False,
            })
            continue

        current = float(rows[-1].get("score", 0))
        baseline = float(rows[0].get("score", 0)) if len(rows) >= 2 else current
        drift = round(current - baseline, 1)
        alert = _alert_level(drift)

        history = [
            {"score": float(r.get("score", 0)), "created_at": r.get("created_at", "")}
            for r in rows[-10:]  # last 10 assessments
        ]

        design_alerts.append({
            "design_id": did,
            "design_name": design_names.get(did, did[:8]),
            "current_score": current,
            "baseline_score": baseline,
            "drift": drift,
            "alert_level": alert,
            "color": _ALERT_COLORS.get(alert, "secondary"),
            "history": history,
            "assessed": True,
        })

    # Sort: worst alert first, then by drift ascending
    alert_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "OK": 3}
    design_alerts.sort(key=lambda x: (alert_order.get(x["alert_level"], 9), x.get("drift", 0)))

    summary = {
        "critical": sum(1 for d in design_alerts if d["alert_level"] == "CRITICAL"),
        "high": sum(1 for d in design_alerts if d["alert_level"] == "HIGH"),
        "medium": sum(1 for d in design_alerts if d["alert_level"] == "MEDIUM"),
        "ok": sum(1 for d in design_alerts if d["alert_level"] == "OK"),
        "total_designs": len(designs),
        "assessed": sum(1 for d in design_alerts if d.get("assessed")),
    }

    return {
        "design_alerts": design_alerts,
        "summary": summary,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "alert_colors": _ALERT_COLORS,
    }


def _alert_level(drift: float) -> str:
    if drift <= -_DRIFT_CRITICAL:
        return "CRITICAL"
    if drift <= -_DRIFT_HIGH:
        return "HIGH"
    if drift <= -_DRIFT_MEDIUM:
        return "MEDIUM"
    return "OK"
