"""CUI // SP-CTI -- Network Capacity Exhaustion Predictor (PNA module)"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from tools.network.db.init_db import get_connection

log = logging.getLogger(__name__)

_NQE_INTERFACE_QUERY = (
    "foreach iface in network.interfaces select { "
    "device: iface.device.name, "
    "name: iface.name, "
    "id: iface.id, "
    "utilizationPct: iface.utilizationPercent, "
    "peakUtilizationPct: iface.peakUtilizationPercent }"
)

_SATURATION_THRESHOLD = 90.0  # % at which we consider saturated


def _fetch_interfaces_nqe(network_id=None):
    try:
        from tools.network.nqe_client import FallbackNQEClient
        client = FallbackNQEClient()
        result = client.query(_NQE_INTERFACE_QUERY, network_id=network_id)
        if isinstance(result, list):
            return result
        if isinstance(result, dict) and "items" in result:
            return result["items"]
    except Exception as exc:
        log.warning("NQE interface fetch failed: %s", exc)
    return []


def _get_historical_utilization(conn, device_name, interface_name, lookback_days=7):
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
        cur = conn.execute(
            """
            SELECT current_util_pct, predicted_at FROM nc_capacity_predictions
            WHERE device_name = ? AND interface_name = ? AND predicted_at >= ?
            ORDER BY predicted_at ASC
            """,
            (device_name, interface_name, cutoff),
        )
        rows = cur.fetchall()
        if len(rows) < 2:
            return None, None
        scores = [r[0] for r in rows]
        avg = sum(scores) / len(scores)
        # Linear regression slope
        n = len(rows)
        dates = [(datetime.fromisoformat(r[1]) - datetime.fromisoformat(rows[0][1])).days for r in rows]
        x_mean = sum(dates) / n
        y_mean = avg
        num = sum((dates[i] - x_mean) * (scores[i] - y_mean) for i in range(n))
        den = sum((dates[i] - x_mean) ** 2 for i in range(n))
        slope = (num / den) if den != 0 else 0.0
        return avg, slope
    except Exception:
        return None, None


def _compute_capacity_risk(util_pct: float, trend_slope_pct_per_day: float, days_to_full) -> float:
    """Test-facing risk formula: util×0.50 + slope×0.30 + days_to_full×0.20."""
    util_norm = util_pct / 100.0
    slope_norm = min(abs(trend_slope_pct_per_day or 0) / 5.0, 1.0)
    if days_to_full is not None and days_to_full > 0:
        days_to_full_norm = 1.0 - min(days_to_full / 90.0, 1.0)
    else:
        days_to_full_norm = 0.0
    risk = util_norm * 0.50 + slope_norm * 0.30 + days_to_full_norm * 0.20
    return round(min(1.0, max(0.0, risk)), 4)


def _estimate_days_to_full(util_pct: float, slope: float) -> int | None:
    """Estimate days until interface hits 100% utilization given current util and slope (pct/day)."""
    if slope is None or slope <= 0:
        return None
    remaining = 100.0 - util_pct
    if remaining <= 0:
        return 0
    return int(remaining / slope)


def _risk_tier(score: float) -> str:
    """Map a capacity risk score to a tier label."""
    if score >= 0.80:
        return "critical"
    if score >= 0.60:
        return "high"
    if score >= 0.35:
        return "medium"
    return "low"


def _compute_capacity_score(current_util, trend_slope, days_to_sat):
    # High current utilization
    if current_util >= 90.0:
        base = 0.90
    elif current_util >= 75.0:
        base = 0.65
    elif current_util >= 60.0:
        base = 0.40
    elif current_util >= 45.0:
        base = 0.20
    else:
        base = 0.05

    # Growing trend penalty
    trend_penalty = 0.0
    if trend_slope is not None and trend_slope > 0:
        trend_penalty = min(0.25, trend_slope * 5)

    score = min(1.0, round(base + trend_penalty, 4))

    if score >= 0.80:
        tier = "critical"
    elif score >= 0.60:
        tier = "high"
    elif score >= 0.35:
        tier = "medium"
    else:
        tier = "low"

    confidence = 0.40
    if trend_slope is not None:
        confidence = min(0.90, 0.55 + abs(trend_slope) * 0.1)

    return score, tier, round(confidence, 3)


def _insert_capacity(conn, pred):
    conn.execute(
        """
        INSERT INTO nc_capacity_predictions
            (device_name, interface_name, interface_id,
             current_util_pct, peak_util_pct, avg_util_pct_7d,
             trend_slope, days_to_saturation, saturation_date,
             confidence, risk_score, risk_tier,
             nqe_source, model_version, predicted_at, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
        """,
        (
            pred["device_name"],
            pred["interface_name"],
            pred.get("interface_id"),
            pred["current_util_pct"],
            pred.get("peak_util_pct"),
            pred.get("avg_util_pct_7d"),
            pred["trend_slope"],
            pred.get("days_to_saturation"),
            pred.get("saturation_date"),
            pred["confidence"],
            pred["risk_score"],
            pred["risk_tier"],
            pred.get("nqe_source", "local_mapping"),
            pred.get("model_version", "1.0"),
            pred.get("predicted_at", datetime.now(timezone.utc).isoformat()),
        ),
    )
    conn.commit()


def predict_capacity_exhaustion(device_name=None, network_id=None):
    ifaces = _fetch_interfaces_nqe(network_id)
    if device_name:
        ifaces = [i for i in ifaces if i.get("device") == device_name]

    results = []
    with get_connection() as conn:
        for iface in ifaces:
            dev = iface.get("device", "")
            iface_name = iface.get("name", "")
            iface_id = iface.get("id")
            current = iface.get("utilizationPct", 0.0) or 0.0
            peak = iface.get("peakUtilizationPct")

            avg_7d, slope = _get_historical_utilization(conn, dev, iface_name)
            slope = slope if slope is not None else 0.0

            days_to_sat = None
            sat_date = None
            if slope > 0:
                remaining_headroom = _SATURATION_THRESHOLD - current
                if remaining_headroom > 0:
                    days_to_sat = int(remaining_headroom / slope)
                    sat_date = (
                        datetime.now(timezone.utc).date() + timedelta(days=days_to_sat)
                    ).isoformat()

            score, tier, conf = _compute_capacity_score(current, slope, days_to_sat)

            pred = {
                "device_name": dev,
                "interface_name": iface_name,
                "interface_id": iface_id,
                "current_util_pct": round(current, 2),
                "peak_util_pct": peak,
                "avg_util_pct_7d": round(avg_7d, 2) if avg_7d is not None else None,
                "trend_slope": round(slope, 4),
                "days_to_saturation": days_to_sat,
                "saturation_date": sat_date,
                "confidence": conf,
                "risk_score": score,
                "risk_tier": tier,
                "nqe_source": "nqe_api" if ifaces else "local_mapping",
                "model_version": "1.0",
                "predicted_at": datetime.now(timezone.utc).isoformat(),
            }
            try:
                _insert_capacity(conn, pred)
            except Exception as exc:
                log.warning("Failed to insert capacity prediction for %s/%s: %s", dev, iface_name, exc)
            results.append(pred)

    return results


def get_capacity_predictions(device_name=None, risk_tier=None, limit=50):
    from tools.network.db.init_db import get_connection as _gc
    with _gc() as conn:
        where, params = [], []
        if device_name:
            where.append("device_name = ?")
            params.append(device_name)
        if risk_tier:
            where.append("risk_tier = ?")
            params.append(risk_tier)
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        sql = f"SELECT * FROM nc_capacity_predictions {clause} ORDER BY risk_score DESC LIMIT ?"
        params.append(limit)
        cur = conn.execute(sql, params)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def get_capacity_summary():
    from tools.network.db.init_db import get_connection as _gc
    with _gc() as conn:
        cur = conn.execute(
            "SELECT risk_tier, COUNT(*) FROM nc_capacity_predictions GROUP BY risk_tier"
        )
        by_tier = {row[0]: row[1] for row in cur.fetchall()}
        cur = conn.execute(
            "SELECT COUNT(*) FROM nc_capacity_predictions WHERE days_to_saturation IS NOT NULL AND days_to_saturation <= 30"
        )
        saturating_soon = cur.fetchone()[0] or 0
        return {
            "total_interfaces": sum(by_tier.values()),
            "saturating_within_30d": saturating_soon,
            "by_tier": by_tier,
        }
