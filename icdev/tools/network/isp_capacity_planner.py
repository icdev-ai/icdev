# CUI // SP-CTI
"""ISP capacity planning — traffic growth modeling, DWDM analysis, dark fiber ROI.

Pure Python stdlib (statistics, math). No external dependencies.
All inputs are dicts/lists; all outputs are plain dicts suitable for JSON serialization.
"""

from __future__ import annotations

import math
import statistics
from datetime import datetime, timezone
from typing import Any


# ── Traffic growth modeling ────────────────────────────────────────────────────

def model_traffic_growth(
    historical_gbps: list[float],
    forecast_months: int = 12,
    method: str = "cagr",
) -> dict[str, Any]:
    """Model traffic growth from historical monthly data points.

    Args:
        historical_gbps: Monthly average traffic in Gbps (oldest first)
        forecast_months: How many months to forecast
        method: "cagr" | "linear" | "exponential"

    Returns:
        {
          forecast: [{month, gbps}],
          cagr_pct: float,
          doubling_years: float,
          peak_forecast_gbps: float,
          recommended_capacity_gbps: float,  # 40% overhead headroom
          method: str,
        }
    """
    if not historical_gbps or len(historical_gbps) < 2:
        raise ValueError("At least 2 historical data points required")

    n = len(historical_gbps)
    first = historical_gbps[0]
    last = historical_gbps[-1]

    if first <= 0:
        raise ValueError("First data point must be > 0")

    # CAGR: (end/start)^(1/n_months) - 1  → monthly rate
    monthly_cagr = (last / first) ** (1.0 / (n - 1)) - 1.0
    annual_cagr = ((1 + monthly_cagr) ** 12 - 1) * 100  # percent

    # Doubling time: log(2) / log(1 + monthly_rate) / 12
    if monthly_cagr > 0:
        doubling_years = math.log(2) / math.log(1 + monthly_cagr) / 12
    else:
        doubling_years = float("inf")

    # Forecast
    forecast: list[dict] = []
    base = last
    for m in range(1, forecast_months + 1):
        if method == "linear":
            # Fit linear slope on historical data
            mean_x = (n - 1) / 2
            mean_y = statistics.mean(historical_gbps)
            slope = sum(
                (i - mean_x) * (v - mean_y)
                for i, v in enumerate(historical_gbps)
            ) / sum((i - mean_x) ** 2 for i in range(n))
            projected = last + slope * m
        elif method == "exponential":
            projected = last * math.exp(math.log(last / first) / (n - 1) * m)
        else:  # cagr (default)
            projected = base * (1 + monthly_cagr) ** m
        forecast.append({"month": m, "gbps": round(max(projected, 0), 2)})

    peak = max(f["gbps"] for f in forecast)
    recommended = round(peak * 1.4, 2)  # 40% headroom

    return {
        "method": method,
        "historical_points": n,
        "historical_latest_gbps": last,
        "cagr_pct": round(annual_cagr, 2),
        "monthly_growth_pct": round(monthly_cagr * 100, 3),
        "doubling_years": round(doubling_years, 2) if doubling_years != float("inf") else None,
        "forecast": forecast,
        "peak_forecast_gbps": peak,
        "recommended_capacity_gbps": recommended,
        "headroom_ratio": 1.4,
    }


# ── DWDM capacity analysis ─────────────────────────────────────────────────────

# Standard DWDM channel baud rates and modulation capacities
_DWDM_MODULATIONS = {
    "100G-DP-QPSK": 100,
    "200G-DP-16QAM": 200,
    "400G-DP-16QAM": 400,
    "400G-DP-64QAM": 400,
    "800G-DP-64QAM": 800,
    "1.2T-DP-64QAM": 1200,
}

_DWDM_GRID_CHANNELS = {
    "C-band-50GHz": 96,    # 96 channels at 50 GHz spacing
    "C-band-37.5GHz": 128,  # flex grid
    "L-band-50GHz": 96,
    "S-band-50GHz": 80,
}


def dwdm_capacity_analysis(
    fiber_pairs: int,
    modulation: str = "400G-DP-16QAM",
    grid: str = "C-band-50GHz",
    current_channels_used: int = 0,
    amplifier_spans: int = 1,
    span_loss_db: float = 20.0,
) -> dict[str, Any]:
    """Analyze DWDM fiber capacity and headroom.

    Args:
        fiber_pairs: Number of fiber pairs (each pair = 1 Tx + 1 Rx)
        modulation: DWDM modulation scheme
        grid: DWDM channel grid type
        current_channels_used: Currently lit channels
        amplifier_spans: Number of amplifier spans in the route
        span_loss_db: Loss per span in dB

    Returns:
        {
          total_capacity_tbps: float,
          used_capacity_gbps: float,
          available_capacity_gbps: float,
          utilization_pct: float,
          max_channels: int,
          available_channels: int,
          reach_km_estimate: float,
          upgrade_options: [str],
        }
    """
    channel_gbps = _DWDM_MODULATIONS.get(modulation, 100)
    max_channels_per_pair = _DWDM_GRID_CHANNELS.get(grid, 96)
    total_channels = max_channels_per_pair * fiber_pairs
    used_channels = current_channels_used
    available_channels = max(0, total_channels - used_channels)

    total_capacity_gbps = total_channels * channel_gbps
    used_capacity_gbps = used_channels * channel_gbps
    available_capacity_gbps = available_channels * channel_gbps
    utilization_pct = round(used_capacity_gbps / total_capacity_gbps * 100, 1) if total_capacity_gbps > 0 else 0.0

    # Rough reach estimate: OSNR budget / span loss
    # Simplified: 100G QPSK reaches ~2000 km; halve per doubling of baud
    reach_map = {
        "100G-DP-QPSK": 2000,
        "200G-DP-16QAM": 1000,
        "400G-DP-16QAM": 600,
        "400G-DP-64QAM": 400,
        "800G-DP-64QAM": 250,
        "1.2T-DP-64QAM": 150,
    }
    base_reach = reach_map.get(modulation, 600)
    # Derate by actual span loss vs assumed 20 dB
    reach_km = round(base_reach * (20.0 / max(span_loss_db, 1.0)), 0)

    # Upgrade options when utilization > 70%
    upgrade_options: list[str] = []
    if utilization_pct > 70:
        if channel_gbps < 400:
            upgrade_options.append(f"Upgrade to 400G-DP-16QAM ({4 * channel_gbps / channel_gbps:.0f}x capacity per channel)")
        if channel_gbps < 800:
            upgrade_options.append("Deploy 800G/1.2T coherent transponders")
        if fiber_pairs < 8:
            upgrade_options.append(f"Add {2} more fiber pairs ({(fiber_pairs + 2) * max_channels_per_pair * channel_gbps / 1000:.1f} Tbps total)")
        upgrade_options.append("Evaluate dark fiber lease or IRU on parallel routes")

    return {
        "fiber_pairs": fiber_pairs,
        "modulation": modulation,
        "grid": grid,
        "max_channels": total_channels,
        "used_channels": used_channels,
        "available_channels": available_channels,
        "channel_capacity_gbps": channel_gbps,
        "total_capacity_tbps": round(total_capacity_gbps / 1000, 2),
        "used_capacity_gbps": used_capacity_gbps,
        "available_capacity_gbps": available_capacity_gbps,
        "utilization_pct": utilization_pct,
        "reach_km_estimate": reach_km,
        "upgrade_options": upgrade_options,
        "spans": amplifier_spans,
        "span_loss_db": span_loss_db,
    }


# ── Dark fiber ROI analysis ───────────────────────────────────────────────────

def dark_fiber_roi(
    route_km: float,
    fiber_pairs: int,
    *,
    iru_cost_usd: float = 0.0,
    annual_lease_usd: float = 0.0,
    lease_term_years: int = 20,
    capex_per_km_usd: float = 15_000.0,
    opex_annual_usd: float = 50_000.0,
    current_lit_cost_annual_usd: float = 500_000.0,
    discount_rate_pct: float = 8.0,
) -> dict[str, Any]:
    """Calculate dark fiber build/lease ROI versus buying lit capacity.

    Supports two modes:
    - IRU (Indefeasible Right of Use): one-time capex + opex
    - Lease: annual recurring cost

    Args:
        route_km: Route distance in km
        fiber_pairs: Number of fiber pairs
        iru_cost_usd: One-time IRU purchase cost (0 if leasing)
        annual_lease_usd: Annual lease cost (0 if IRU/build)
        lease_term_years: Analysis horizon in years
        capex_per_km_usd: Build capex per km (if building own fiber)
        opex_annual_usd: Annual opex (amplifier maintenance, site, monitoring)
        current_lit_cost_annual_usd: Current annual spend on lit wavelengths
        discount_rate_pct: Discount rate for NPV calculation

    Returns:
        {
          mode: "iru" | "lease" | "build",
          break_even_years: float | None,
          npv_usd: float,
          irr_pct: float | None,
          total_cost_usd: float,
          total_savings_usd: float,
          recommendation: str,
          annual_cash_flows: [{year, cost, savings, net}],
        }
    """
    r = discount_rate_pct / 100.0

    if iru_cost_usd > 0:
        mode = "iru"
        initial_cost = iru_cost_usd
        annual_cost = opex_annual_usd
    elif annual_lease_usd > 0:
        mode = "lease"
        initial_cost = 0.0
        annual_cost = annual_lease_usd + opex_annual_usd
    else:
        mode = "build"
        initial_cost = capex_per_km_usd * route_km * fiber_pairs
        annual_cost = opex_annual_usd

    cash_flows: list[dict] = []
    cumulative_net = -initial_cost
    break_even_year: float | None = None
    npv = -initial_cost

    for year in range(1, lease_term_years + 1):
        savings = current_lit_cost_annual_usd
        cost = annual_cost
        net = savings - cost
        cumulative_net += net
        if break_even_year is None and cumulative_net >= 0:
            # Linear interpolation within year
            prev = cumulative_net - net
            fraction = -prev / net if net > 0 else 0
            break_even_year = round(year - 1 + fraction, 2)
        npv += net / (1 + r) ** year
        cash_flows.append({
            "year": year,
            "cost_usd": round(cost, 0),
            "savings_usd": round(savings, 0),
            "net_usd": round(net, 0),
            "cumulative_usd": round(cumulative_net, 0),
        })

    total_cost = initial_cost + annual_cost * lease_term_years
    total_savings = current_lit_cost_annual_usd * lease_term_years

    # Rough IRR estimation via binary search
    irr: float | None = None
    try:
        flows = [-initial_cost] + [current_lit_cost_annual_usd - annual_cost] * lease_term_years
        irr = _estimate_irr(flows)
    except Exception:
        pass

    if npv > 0 and break_even_year is not None and break_even_year <= lease_term_years:
        rec = f"FAVORABLE — break-even in {break_even_year:.1f} years, NPV ${npv:,.0f}"
    elif npv > 0:
        rec = f"MARGINAL — positive NPV (${npv:,.0f}) but no break-even within {lease_term_years}y"
    else:
        rec = f"UNFAVORABLE — negative NPV (${npv:,.0f}); reconsider or renegotiate pricing"

    return {
        "mode": mode,
        "route_km": route_km,
        "fiber_pairs": fiber_pairs,
        "initial_cost_usd": round(initial_cost, 0),
        "annual_cost_usd": round(annual_cost, 0),
        "analysis_years": lease_term_years,
        "break_even_years": break_even_year,
        "npv_usd": round(npv, 0),
        "irr_pct": round(irr * 100, 2) if irr is not None else None,
        "total_cost_usd": round(total_cost, 0),
        "total_savings_usd": round(total_savings, 0),
        "net_benefit_usd": round(total_savings - total_cost, 0),
        "recommendation": rec,
        "annual_cash_flows": cash_flows,
    }


def _estimate_irr(cash_flows: list[float], tol: float = 1e-6, max_iter: int = 200) -> float:
    """Estimate IRR via Newton-Raphson / bisection."""
    def npv_at(r: float) -> float:
        return sum(cf / (1 + r) ** t for t, cf in enumerate(cash_flows))

    lo, hi = -0.99, 10.0
    if npv_at(lo) * npv_at(hi) > 0:
        raise ValueError("Cannot bracket IRR")
    for _ in range(max_iter):
        mid = (lo + hi) / 2
        if abs(hi - lo) < tol:
            return mid
        if npv_at(mid) * npv_at(lo) < 0:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


# ── Capacity planning summary ─────────────────────────────────────────────────

def capacity_planning_summary(
    site_name: str,
    current_traffic_gbps: float,
    peak_traffic_gbps: float,
    installed_capacity_gbps: float,
    forecast_12m_gbps: float,
    forecast_36m_gbps: float,
) -> dict[str, Any]:
    """Generate a structured capacity planning summary for a site/link.

    Returns utilization, headroom, and upgrade urgency classification.
    """
    current_util = round(current_traffic_gbps / installed_capacity_gbps * 100, 1) if installed_capacity_gbps > 0 else 0.0
    peak_util = round(peak_traffic_gbps / installed_capacity_gbps * 100, 1) if installed_capacity_gbps > 0 else 0.0
    headroom_gbps = max(0, installed_capacity_gbps - peak_traffic_gbps)

    months_to_saturation: float | None = None
    if forecast_12m_gbps > installed_capacity_gbps:
        months_to_saturation = 6.0  # within 12m window
    elif forecast_36m_gbps > installed_capacity_gbps:
        months_to_saturation = 24.0  # within 36m window

    # Urgency classification
    if peak_util >= 85 or (months_to_saturation and months_to_saturation <= 6):
        urgency = "CRITICAL"
        urgency_color = "#ef4444"
    elif peak_util >= 70 or (months_to_saturation and months_to_saturation <= 24):
        urgency = "HIGH"
        urgency_color = "#f59e0b"
    elif peak_util >= 50:
        urgency = "MEDIUM"
        urgency_color = "#3b82f6"
    else:
        urgency = "LOW"
        urgency_color = "#22c55e"

    return {
        "site": site_name,
        "current_traffic_gbps": current_traffic_gbps,
        "peak_traffic_gbps": peak_traffic_gbps,
        "installed_capacity_gbps": installed_capacity_gbps,
        "current_utilization_pct": current_util,
        "peak_utilization_pct": peak_util,
        "headroom_gbps": round(headroom_gbps, 1),
        "forecast_12m_gbps": forecast_12m_gbps,
        "forecast_36m_gbps": forecast_36m_gbps,
        "months_to_saturation": months_to_saturation,
        "upgrade_urgency": urgency,
        "urgency_color": urgency_color,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
