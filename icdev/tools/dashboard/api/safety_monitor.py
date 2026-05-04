# CUI // SP-CTI
"""tools/dashboard/api/safety_monitor — Circuit Breaker API.

D146: Exposes in-memory circuit breaker stats via REST.
"""
from __future__ import annotations

import time

from flask import Blueprint, jsonify

safety_monitor_api = Blueprint("safety_monitor_api", __name__)


@safety_monitor_api.route("/circuit-breaker", methods=["GET"])
def get_circuit_breakers():
    """Return stats for all registered circuit breakers."""
    try:
        from tools.resilience.circuit_breaker import get_all_breakers

        raw = get_all_breakers()
        now = time.time()
        breakers = []
        for name, stats in raw.items():
            state = stats.get("state", "closed")
            last_failure = stats.get("last_failure_time", 0)
            last_change = stats.get("last_state_change", 0)
            breakers.append(
                {
                    "service": name,
                    "state": state,
                    "failure_count": stats.get("failure_count", 0),
                    "success_count": stats.get("success_count", 0),
                    "half_open_calls": stats.get("half_open_calls", 0),
                    "failure_threshold": stats.get("failure_threshold", 5),
                    "recovery_timeout_seconds": stats.get("recovery_timeout_seconds", 30),
                    "last_failure_ago_seconds": round(now - last_failure, 1) if last_failure else None,
                    "last_state_change_ago_seconds": round(now - last_change, 1) if last_change else None,
                    "color": "green" if state == "closed" else ("red" if state == "open" else "yellow"),
                }
            )
        # Sort: OPEN first, then HALF_OPEN, then CLOSED
        order = {"open": 0, "half_open": 1, "closed": 2}
        breakers.sort(key=lambda b: order.get(b["state"], 3))
        summary = {
            "total": len(breakers),
            "open": sum(1 for b in breakers if b["state"] == "open"),
            "half_open": sum(1 for b in breakers if b["state"] == "half_open"),
            "closed": sum(1 for b in breakers if b["state"] == "closed"),
        }
        return jsonify({"breakers": breakers, "summary": summary, "timestamp": now})
    except Exception as exc:
        return jsonify({"error": str(exc), "breakers": [], "summary": {"total": 0, "open": 0, "half_open": 0, "closed": 0}}), 200
