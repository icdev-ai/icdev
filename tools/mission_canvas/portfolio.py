# CUI // SP-CTI
"""Mission Canvas — Portfolio Scaling & Optimization wrapper.

Wraps pipeline.twin and network.montecarlo for capacity
planning and portfolio scaling simulation.
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import logging
from typing import Optional

logger = get_logger("icdev.mission_canvas.portfolio")


def optimize_portfolio(mission_id: str, scenarios: Optional[list[dict]] = None) -> dict:
    """Run portfolio optimization simulation for mission workloads.

    Uses pipeline twin state + monte-carlo network capacity simulation
to recommend scaling actions.
    """
    result = {
        "mission_id": mission_id,
        "snapshot": {},
        "simulation": {},
        "recommendations": [],
        "status": "ok",
    }
    try:
        from tools.pipeline.twin import list_snapshots as pipe_list_snapshots

        result["snapshot"] = {"snapshots": pipe_list_snapshots(pipeline_id=mission_id)}
    except Exception as exc:
        logger.warning("Pipeline snapshot failed: %s", exc)
        result["snapshot"] = {"error": str(exc)}

    try:
        from tools.network.montecarlo import run_monte_carlo

        sim = run_monte_carlo(
            graph={"nodes": [{"id": mission_id}]},
            scenario_name=mission_id,
            scenario_type="capacity",
            config={"iterations": 500},
            iterations=500,
        )
        result["simulation"] = sim
    except Exception as exc:
        logger.warning("Monte-carlo simulation failed: %s", exc)
        result["simulation"] = {"error": str(exc)}

    # Derive simple recommendations if both succeeded
    if not result["snapshot"].get("error") and not result["simulation"].get("error"):
        result["recommendations"] = _derive_recommendations(result["snapshot"], result["simulation"])

    return result


def _derive_recommendations(snapshot: dict, simulation: dict) -> list[dict]:
    """Heuristic scaling recommendations from snapshot + simulation."""
    recs = []
    util = simulation.get("peak_utilization", 0)
    if util > 0.85:
        recs.append({
            "action": "scale_out",
            "reason": f"Peak utilization {util:.0%} exceeds 85% threshold",
            "priority": "high",
        })
    elif util < 0.3:
        recs.append({
            "action": "scale_in",
            "reason": f"Peak utilization {util:.0%} below 30% — over-provisioned",
            "priority": "medium",
        })
    return recs
