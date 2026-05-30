# CUI // SP-CTI
"""NOCC SLA predictor — burn rate calculation and breach projection."""

from __future__ import annotations

from datetime import datetime, timezone
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


def compute_sla_burn_rate(
    sla_records: list[dict],
    period_start: str,
    now: str | None = None,
) -> dict:
    """Compute per-circuit SLA burn rate against monthly budget.

    Returns:
        {circuit_id: {target_pct, used_min, budget_min, burn_rate, projected_breach}}
    """
    now_dt = _parse_ts(now) or _now_utc()
    start_dt = _parse_ts(period_start) or now_dt

    # Minutes elapsed and total in period (assume 30-day month)
    total_minutes = 30 * 24 * 60
    elapsed_minutes = max(1, (now_dt - start_dt).total_seconds() / 60)

    result = {}
    for rec in sla_records:
        cid = rec.get("circuit_id", "unknown")
        sla_type = rec.get("sla_type", "uptime")
        target = float(rec.get("target_value", 99.9))
        breach_min = int(rec.get("breach_minutes", 0))

        if sla_type == "uptime":
            # budget_min = allowable downtime minutes per period
            budget_min = round(total_minutes * (100.0 - target) / 100.0, 2)
            burn_rate = breach_min / max(elapsed_minutes, 1)
            projected_total = burn_rate * total_minutes
            projected_breach = projected_total > budget_min
            result[cid] = {
                "target_pct": target,
                "used_min": breach_min,
                "budget_min": budget_min,
                "burn_rate": round(burn_rate, 6),
                "projected_final_min": round(projected_total, 2),
                "projected_breach": projected_breach,
                "sla_type": sla_type,
            }
        else:
            # For latency/jitter/loss: just flag if measured > target
            measured = rec.get("measured_value")
            result[cid] = {
                "target_value": target,
                "measured_value": measured,
                "projected_breach": measured is not None and float(measured) > target,
                "sla_type": sla_type,
            }
    return result


def predict_breach(sla_record: dict, current_uptime_pct: float) -> dict:
    """Linear projection to end of period for a single circuit.

    Returns:
        {will_breach, projected_final_pct, credit_exposure_usd}
    """
    target = float(sla_record.get("target_value", 99.9))
    will_breach = current_uptime_pct < target

    # Simplified credit: 10% of assumed $1000/month circuit cost per 0.1% SLA miss
    miss_pct = max(0.0, target - current_uptime_pct)
    credit = round((miss_pct / 0.1) * 100.0, 2)

    return {
        "will_breach": will_breach,
        "projected_final_pct": round(current_uptime_pct, 4),
        "sla_target_pct": target,
        "credit_exposure_usd": credit,
    }


def get_sla_dashboard(conn: Any) -> dict:
    """Return aggregated SLA health for the NOCC overview."""
    try:
        rows = conn.execute(
            "SELECT circuit_id, carrier, sla_type, target_value, measured_value, "
            "breach, breach_minutes, period_start FROM noc_sla_records "
            "ORDER BY breach DESC, breach_minutes DESC LIMIT 200"
        ).fetchall()
    except Exception:
        rows = []

    cols = None
    records = []
    for row in rows:
        if cols is None and hasattr(row, "keys"):
            cols = list(row.keys())
        if cols:
            records.append(dict(zip(cols, list(row))) if not hasattr(row, "keys") else dict(row))
        else:
            records.append({
                "circuit_id": row[0], "carrier": row[1], "sla_type": row[2],
                "target_value": row[3], "measured_value": row[4],
                "breach": row[5], "breach_minutes": row[6], "period_start": row[7],
            })

    breached = [r for r in records if r.get("breach")]
    at_risk = len(breached)
    total = len(records)
    compliance_pct = round((total - at_risk) / max(total, 1) * 100, 1)

    return {
        "total_circuits": total,
        "breached_count": at_risk,
        "compliance_pct": compliance_pct,
        "breached_circuits": breached[:20],
    }
