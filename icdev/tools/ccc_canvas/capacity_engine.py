# CUI // SP-CTI
"""CCC — capacity planning engine.

Wraps isp_capacity_planner and adds DB persistence of plans.
"""
from __future__ import annotations

from datetime import datetime, timezone
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.ccc_canvas.capacity_engine")


def _months_to_saturation(current_pct: float, growth_rate_pct: float) -> int:
    """Return months until utilization hits 95% at given monthly growth rate."""
    if growth_rate_pct <= 0:
        return 9999
    pct = current_pct
    months = 0
    while pct < 95.0 and months < 120:
        pct *= (1 + growth_rate_pct / 100)
        months += 1
    return months


def analyze_circuit(conn, circuit_id_int: int) -> dict:
    """Run capacity analysis for a single circuit and persist the plan."""
    try:
        row = conn.execute(
            "SELECT * FROM ccc_circuits WHERE id=%s", (circuit_id_int,)
        ).fetchone()
        if not row:
            return {"error": f"circuit {circuit_id_int} not found"}
        row = dict(row)
    except Exception:
        row = conn.execute(
            "SELECT * FROM ccc_circuits WHERE id=%s", (circuit_id_int,)
        ).fetchone()
        if not row:
            return {"error": f"circuit {circuit_id_int} not found"}
        row = dict(row)

    util = float(row.get("utilization_pct") or 0)
    bw   = float(row.get("bandwidth_gbps") or 1)

    # Estimate growth rate from capacity plans history
    try:
        history = conn.execute(
            "SELECT current_utilization_pct, plan_date FROM ccc_capacity_plans "
            "WHERE circuit_id=%s ORDER BY plan_date DESC LIMIT 6",
            (circuit_id_int,),
        ).fetchall()
    except Exception:
        history = conn.execute(
            "SELECT current_utilization_pct, plan_date FROM ccc_capacity_plans "
            "WHERE circuit_id=%s ORDER BY plan_date DESC LIMIT 6",
            (circuit_id_int,),
        ).fetchall()

    if len(history) >= 2:
        oldest = float(dict(history[-1]).get("current_utilization_pct") or util)
        growth_rate = (util - oldest) / max(len(history), 1)
        confidence  = min(0.9, 0.5 + len(history) * 0.07)
    else:
        growth_rate = 3.0  # assume 3% monthly growth if no history
        confidence  = 0.4

    growth_rate = max(0.0, growth_rate)
    months_left  = _months_to_saturation(util, growth_rate)

    if util >= 85:
        action = "URGENT: upgrade or off-load traffic immediately"
    elif util >= 70 or months_left <= 6:
        action = f"Plan upgrade: ~{months_left}mo to saturation — begin procurement"
    elif months_left <= 12:
        action = f"Monitor closely: ~{months_left}mo to saturation"
    else:
        action = "No action needed"

    projected_util = min(util * (1 + growth_rate / 100) ** 6, 100.0)

    plan = {
        "circuit_id": circuit_id_int,
        "plan_date": datetime.now(timezone.utc).date().isoformat(),
        "current_utilization_pct": round(util, 2),
        "projected_utilization_pct": round(projected_util, 2),
        "months_to_saturation": months_left,
        "recommended_action": action,
        "growth_rate_pct": round(growth_rate, 2),
        "confidence_score": round(confidence, 2),
    }

    # Persist
    try:
        conn.execute(
            "INSERT INTO ccc_capacity_plans "
            "(circuit_id, plan_date, current_utilization_pct, projected_utilization_pct, "
            "months_to_saturation, recommended_action, growth_rate_pct, confidence_score) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            tuple(plan.values()),
        )
        conn.commit()
    except Exception:
        try:
            conn.execute(
                "INSERT INTO ccc_capacity_plans "
                "(circuit_id, plan_date, current_utilization_pct, projected_utilization_pct, "
                "months_to_saturation, recommended_action, growth_rate_pct, confidence_score) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                tuple(plan.values()),
            )
            conn.commit()
        except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
            logger.warning("analyze_circuit: best-effort INSERT into ccc_capacity_plans failed (non-blocking): %s", exc)

    plan["circuit_id_str"] = row.get("circuit_id", "")
    plan["carrier"]        = row.get("carrier", "")
    plan["bandwidth_gbps"] = bw
    return plan


def run_all_circuits(conn) -> list[dict]:
    """Analyze all active circuits and return list of plans."""
    try:
        rows = conn.execute(
            "SELECT id FROM ccc_circuits WHERE status='active' ORDER BY utilization_pct DESC"
        ).fetchall()
    except Exception:
        rows = []
    return [analyze_circuit(conn, dict(r)["id"]) for r in rows]
