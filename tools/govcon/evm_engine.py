# CUI // SP-CTI
# ICDEV GovProposal — EVM Engine (Phase 60, D-CPMP-2)
# Earned Value Management calculations per ANSI/EIA-748.

"""
EVM Engine — Earned Value Management calculations for CPMP.

Implements ANSI/EIA-748 EVM indicators:
    - CPI, SPI, CV, SV, EAC, ETC, VAC, TCPI
    - Contract-level aggregation across WBS elements
    - S-curve time-series data for charting
    - Monte Carlo EAC forecasting (PERT distribution, stdlib random — D22)
    - IPMDAR-compatible data export (Format 1, 3, 5)

All EVM period records are append-only (D6, NIST AU-2).

Usage:
    python tools/govcon/evm_engine.py --record --contract-id <id> --wbs-id <id> --period-date YYYY-MM --pv 100 --ev 90 --ac 95 --json
    python tools/govcon/evm_engine.py --aggregate --contract-id <id> --json
    python tools/govcon/evm_engine.py --scurve --contract-id <id> --json
    python tools/govcon/evm_engine.py --forecast --contract-id <id> --iterations 10000 --json
    python tools/govcon/evm_engine.py --periods --contract-id <id> [--wbs-id <id>] --json
    python tools/govcon/evm_engine.py --ipmdar --contract-id <id> --json
"""

import argparse
import json
import os
import random
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parent.parent.parent
_DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(_ROOT / "data" / "icdev.db")))
_CONFIG_PATH = _ROOT / "args" / "govcon_config.yaml"


# ── Config ───────────────────────────────────────────────────────────

def _load_config():
    if _CONFIG_PATH.exists():
        with open(_CONFIG_PATH) as f:
            full = yaml.safe_load(f) or {}
            return full.get("cpmp", {}).get("evm", {})
    return {}


_CFG = _load_config()

CPI_YELLOW = _CFG.get("cpi_yellow_threshold", 0.95)
CPI_RED = _CFG.get("cpi_red_threshold", 0.85)
SPI_YELLOW = _CFG.get("spi_yellow_threshold", 0.95)
SPI_RED = _CFG.get("spi_red_threshold", 0.85)
MC_ITERATIONS = _CFG.get("monte_carlo_iterations", 10000)
CONFIDENCE_LEVELS = _CFG.get("forecast_confidence_levels", [0.50, 0.80, 0.95])


# ── Helpers ──────────────────────────────────────────────────────────

def _get_db():
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _now():
    return datetime.now(timezone.utc).isoformat()


def _uuid():
    return str(uuid.uuid4())


def _audit(conn, action, details="", actor="evm_engine"):
    try:
        conn.execute(
            "INSERT INTO audit_trail (id, timestamp, event_type, actor, action, details, session_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (_uuid(), _now(), "cpmp.evm_engine", actor, action, details, "cpmp"),
        )
    except Exception:
        pass


def _pert_sample(optimistic, most_likely, pessimistic, lambd=4):
    """Sample from PERT distribution using stdlib random.betavariate (D22 air-gap safe)."""
    if optimistic >= pessimistic:
        return most_likely
    alpha = 1 + lambd * (most_likely - optimistic) / (pessimistic - optimistic)
    beta_param = 1 + lambd * (pessimistic - most_likely) / (pessimistic - optimistic)
    x = random.betavariate(alpha, beta_param)
    return optimistic + x * (pessimistic - optimistic)


def _status_color(value, yellow_threshold, red_threshold):
    """Return 'green', 'yellow', or 'red' for an EVM index value."""
    if value is None:
        return "green"
    if value >= yellow_threshold:
        return "green"
    if value >= red_threshold:
        return "yellow"
    return "red"


# ── Pure Computation ─────────────────────────────────────────────────

def compute_indicators(bac, pv, ev, ac):
    """
    Pure function: compute all ANSI/EIA-748 EVM indicators.

    Args:
        bac: Budget at Completion
        pv:  Planned Value (BCWS)
        ev:  Earned Value (BCWP)
        ac:  Actual Cost (ACWP)

    Returns:
        Dict with cpi, spi, cost_variance, schedule_variance, eac, etc, vac, tcpi.
        Division-by-zero cases return None for the affected indicator.
    """
    # Cost Performance Index: EV / AC
    cpi = (ev / ac) if ac and ac != 0 else None

    # Schedule Performance Index: EV / PV
    spi = (ev / pv) if pv and pv != 0 else None

    # Variances
    cost_variance = ev - ac
    schedule_variance = ev - pv

    # Estimate at Completion: BAC / CPI
    eac = (bac / cpi) if cpi and cpi != 0 else None

    # Estimate to Complete: EAC - AC
    etc = (eac - ac) if eac is not None else None

    # Variance at Completion: BAC - EAC
    vac = (bac - eac) if eac is not None else None

    # To-Complete Performance Index: (BAC - EV) / (BAC - AC)
    denominator = bac - ac if bac is not None else None
    tcpi = ((bac - ev) / denominator) if denominator and denominator != 0 else None

    return {
        "cpi": round(cpi, 4) if cpi is not None else None,
        "spi": round(spi, 4) if spi is not None else None,
        "cost_variance": round(cost_variance, 2),
        "schedule_variance": round(schedule_variance, 2),
        "eac": round(eac, 2) if eac is not None else None,
        "etc": round(etc, 2) if etc is not None else None,
        "vac": round(vac, 2) if vac is not None else None,
        "tcpi": round(tcpi, 4) if tcpi is not None else None,
    }


# ── Record Period ────────────────────────────────────────────────────

def record_period(contract_id, wbs_id, period_date, pv, ev, ac, source="manual"):
    """
    Record a monthly EVM snapshot in cpmp_evm_periods (append-only).

    Reads BAC from cpmp_wbs.budget_at_completion, auto-computes all indicators,
    and updates cumulative PV/EV/AC on the WBS element.

    Args:
        contract_id: Contract UUID
        wbs_id:      WBS element UUID
        period_date: Period in YYYY-MM format
        pv:          Planned Value for this period
        ev:          Earned Value for this period
        ac:          Actual Cost for this period
        source:      'manual', 'calculated', or 'imported'

    Returns:
        Dict with status and period_id.
    """
    conn = _get_db()

    # Validate contract exists
    contract = conn.execute(
        "SELECT id FROM cpmp_contracts WHERE id = ?", (contract_id,)
    ).fetchone()
    if not contract:
        conn.close()
        return {"status": "error", "message": f"Contract {contract_id} not found"}

    # Validate WBS exists and belongs to contract
    wbs = conn.execute(
        "SELECT id, budget_at_completion, pv_cumulative, ev_cumulative, ac_cumulative "
        "FROM cpmp_wbs WHERE id = ? AND contract_id = ?",
        (wbs_id, contract_id),
    ).fetchone()
    if not wbs:
        conn.close()
        return {"status": "error", "message": f"WBS {wbs_id} not found under contract {contract_id}"}

    bac = wbs["budget_at_completion"] or 0.0
    pv = float(pv)
    ev = float(ev)
    ac = float(ac)

    # Update cumulative values on WBS
    pv_cum = (wbs["pv_cumulative"] or 0.0) + pv
    ev_cum = (wbs["ev_cumulative"] or 0.0) + ev
    ac_cum = (wbs["ac_cumulative"] or 0.0) + ac

    # Compute indicators using cumulative values
    indicators = compute_indicators(bac, pv_cum, ev_cum, ac_cum)

    # Compute percent complete
    pct_complete = (ev_cum / bac * 100.0) if bac and bac != 0 else 0.0

    # Insert EVM period record (append-only, D6)
    period_id = _uuid()
    conn.execute(
        "INSERT INTO cpmp_evm_periods "
        "(id, contract_id, wbs_id, period_date, bac, "
        "pv, ev, ac, "
        "bcws, bcwp, acwp, "
        "cpi, spi, cv, sv, "
        "eac, etc, vac, tcpi, "
        "source, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            period_id, contract_id, wbs_id, period_date, bac,
            pv_cum, ev_cum, ac_cum,
            pv_cum, ev_cum, ac_cum,   # BCWS=PV, BCWP=EV, ACWP=AC (synonyms)
            indicators["cpi"], indicators["spi"],
            indicators["cost_variance"], indicators["schedule_variance"],
            indicators["eac"], indicators["etc"], indicators["vac"], indicators["tcpi"],
            source, _now(),
        ),
    )

    # Update WBS cumulative values
    conn.execute(
        "UPDATE cpmp_wbs SET pv_cumulative = ?, ev_cumulative = ?, ac_cumulative = ?, "
        "percent_complete = ?, updated_at = ? WHERE id = ?",
        (pv_cum, ev_cum, ac_cum, round(pct_complete, 2), _now(), wbs_id),
    )

    _audit(conn, "record_period",
           f"EVM period {period_date} for WBS {wbs_id}: PV={pv_cum}, EV={ev_cum}, AC={ac_cum}, "
           f"CPI={indicators['cpi']}, SPI={indicators['spi']}")
    conn.commit()
    conn.close()

    return {
        "status": "ok",
        "period_id": period_id,
        "period_date": period_date,
        "bac": bac,
        "pv_cumulative": pv_cum,
        "ev_cumulative": ev_cum,
        "ac_cumulative": ac_cum,
        "indicators": indicators,
        "cpi_status": _status_color(indicators["cpi"], CPI_YELLOW, CPI_RED),
        "spi_status": _status_color(indicators["spi"], SPI_YELLOW, SPI_RED),
    }


# ── Aggregate Contract EVM ───────────────────────────────────────────

def aggregate_contract_evm(contract_id):
    """
    Aggregate EVM across all WBS elements for the latest period.

    Sums PV, EV, AC from each WBS element's most recent period record,
    sums BAC from cpmp_wbs, and computes contract-level indicators.

    Returns:
        Dict with contract-level BAC, PV, EV, AC, and all indicators.
    """
    conn = _get_db()

    contract = conn.execute(
        "SELECT id, contract_number, title FROM cpmp_contracts WHERE id = ?",
        (contract_id,),
    ).fetchone()
    if not contract:
        conn.close()
        return {"status": "error", "message": f"Contract {contract_id} not found"}

    # Get total BAC from all WBS elements
    bac_row = conn.execute(
        "SELECT COALESCE(SUM(budget_at_completion), 0) AS total_bac "
        "FROM cpmp_wbs WHERE contract_id = ?",
        (contract_id,),
    ).fetchone()
    total_bac = bac_row["total_bac"]

    # Get latest period per WBS element and sum
    # Use a subquery to find the max period_date per wbs_id, then join
    rows = conn.execute(
        "SELECT e.wbs_id, e.pv, e.ev, e.ac, "
        "       e.period_date, e.cpi, e.spi "
        "FROM cpmp_evm_periods e "
        "INNER JOIN ("
        "    SELECT wbs_id, MAX(period_date) AS max_period "
        "    FROM cpmp_evm_periods WHERE contract_id = ? "
        "    GROUP BY wbs_id"
        ") latest ON e.wbs_id = latest.wbs_id AND e.period_date = latest.max_period "
        "WHERE e.contract_id = ?",
        (contract_id, contract_id),
    ).fetchall()

    if not rows:
        conn.close()
        return {
            "status": "ok",
            "contract_id": contract_id,
            "contract_number": contract["contract_number"],
            "message": "No EVM period data found",
            "total_bac": total_bac,
            "wbs_count": 0,
        }

    total_pv = sum(r["pv"] or 0 for r in rows)
    total_ev = sum(r["ev"] or 0 for r in rows)
    total_ac = sum(r["ac"] or 0 for r in rows)

    indicators = compute_indicators(total_bac, total_pv, total_ev, total_ac)

    pct_complete = (total_ev / total_bac * 100.0) if total_bac and total_bac != 0 else 0.0

    conn.close()
    return {
        "status": "ok",
        "contract_id": contract_id,
        "contract_number": contract["contract_number"],
        "title": contract["title"],
        "total_bac": total_bac,
        "total_pv": total_pv,
        "total_ev": total_ev,
        "total_ac": total_ac,
        "percent_complete": round(pct_complete, 2),
        "indicators": indicators,
        "cpi_status": _status_color(indicators["cpi"], CPI_YELLOW, CPI_RED),
        "spi_status": _status_color(indicators["spi"], SPI_YELLOW, SPI_RED),
        "wbs_count": len(rows),
    }


# ── S-Curve Data ─────────────────────────────────────────────────────

def generate_scurve_data(contract_id):
    """
    Generate time-series data for S-curve charting.

    Returns a list of {period_date, pv_cumulative, ev_cumulative, ac_cumulative}
    aggregated across all WBS elements for each period.

    Returns:
        Dict with status and s_curve list.
    """
    conn = _get_db()

    contract = conn.execute(
        "SELECT id, contract_number FROM cpmp_contracts WHERE id = ?",
        (contract_id,),
    ).fetchone()
    if not contract:
        conn.close()
        return {"status": "error", "message": f"Contract {contract_id} not found"}

    # Aggregate PV, EV, AC per period across all WBS
    rows = conn.execute(
        "SELECT period_date, "
        "       SUM(pv) AS pv_cumulative, "
        "       SUM(ev) AS ev_cumulative, "
        "       SUM(ac) AS ac_cumulative "
        "FROM cpmp_evm_periods "
        "WHERE contract_id = ? "
        "GROUP BY period_date "
        "ORDER BY period_date ASC",
        (contract_id,),
    ).fetchall()

    s_curve = [
        {
            "period_date": r["period_date"],
            "pv_cumulative": round(r["pv_cumulative"] or 0, 2),
            "ev_cumulative": round(r["ev_cumulative"] or 0, 2),
            "ac_cumulative": round(r["ac_cumulative"] or 0, 2),
        }
        for r in rows
    ]

    conn.close()
    return {
        "status": "ok",
        "contract_id": contract_id,
        "contract_number": contract["contract_number"],
        "total_periods": len(s_curve),
        "s_curve": s_curve,
    }


# ── Monte Carlo Forecast ────────────────────────────────────────────

def forecast_monte_carlo(contract_id, iterations=None):
    """
    Monte Carlo EAC forecast using PERT distribution (D22 — stdlib random only).

    For each iteration:
        1. Sample CPI from PERT(optimistic=best_cpi*0.9, most_likely=current_cpi, pessimistic=worst_cpi*1.1)
        2. Compute EAC = BAC / sampled_CPI

    Returns P50, P80, P95 confidence levels (configurable via govcon_config.yaml).

    Args:
        contract_id: Contract UUID
        iterations:  Number of Monte Carlo iterations (default from config)

    Returns:
        Dict with forecast percentiles and distribution summary.
    """
    if iterations is None:
        iterations = MC_ITERATIONS

    conn = _get_db()

    contract = conn.execute(
        "SELECT id, contract_number FROM cpmp_contracts WHERE id = ?",
        (contract_id,),
    ).fetchone()
    if not contract:
        conn.close()
        return {"status": "error", "message": f"Contract {contract_id} not found"}

    # Get total BAC
    bac_row = conn.execute(
        "SELECT COALESCE(SUM(budget_at_completion), 0) AS total_bac "
        "FROM cpmp_wbs WHERE contract_id = ?",
        (contract_id,),
    ).fetchone()
    total_bac = bac_row["total_bac"]
    if total_bac <= 0:
        conn.close()
        return {"status": "error", "message": "BAC is zero or negative; cannot forecast"}

    # Collect all CPI values across periods for this contract
    cpi_rows = conn.execute(
        "SELECT cpi FROM cpmp_evm_periods "
        "WHERE contract_id = ? AND cpi IS NOT NULL AND cpi > 0 "
        "ORDER BY period_date ASC",
        (contract_id,),
    ).fetchall()
    conn.close()

    if not cpi_rows:
        return {"status": "error", "message": "No valid CPI data for Monte Carlo forecast"}

    cpi_values = [r["cpi"] for r in cpi_rows]
    current_cpi = cpi_values[-1]
    best_cpi = max(cpi_values)
    worst_cpi = min(cpi_values)

    # PERT bounds: stretch optimistic and pessimistic slightly beyond observed range
    optimistic_cpi = best_cpi * 0.9    # could be even better
    pessimistic_cpi = worst_cpi * 1.1  # could be even worse

    # Guard: optimistic must be less than pessimistic
    if optimistic_cpi >= pessimistic_cpi:
        # Insufficient variance — use +/- 10% around current
        optimistic_cpi = current_cpi * 0.90
        pessimistic_cpi = current_cpi * 1.10

    # Ensure pessimistic > optimistic after adjustment
    if optimistic_cpi >= pessimistic_cpi:
        pessimistic_cpi = optimistic_cpi + 0.01

    # Run simulations
    eac_samples = []
    for _ in range(iterations):
        sampled_cpi = _pert_sample(optimistic_cpi, current_cpi, pessimistic_cpi)
        if sampled_cpi > 0:
            eac_samples.append(total_bac / sampled_cpi)

    if not eac_samples:
        return {"status": "error", "message": "All sampled CPIs were non-positive"}

    eac_samples.sort()
    n = len(eac_samples)

    # Compute percentiles
    percentiles = {}
    for level in CONFIDENCE_LEVELS:
        idx = int(level * n)
        if idx >= n:
            idx = n - 1
        key = f"P{int(level * 100)}"
        percentiles[key] = round(eac_samples[idx], 2)

    # Distribution summary
    eac_mean = sum(eac_samples) / n
    eac_min = eac_samples[0]
    eac_max = eac_samples[-1]

    return {
        "status": "ok",
        "contract_id": contract_id,
        "contract_number": contract["contract_number"],
        "total_bac": total_bac,
        "current_cpi": current_cpi,
        "best_cpi": best_cpi,
        "worst_cpi": worst_cpi,
        "iterations": iterations,
        "percentiles": percentiles,
        "distribution": {
            "mean": round(eac_mean, 2),
            "min": round(eac_min, 2),
            "max": round(eac_max, 2),
            "median": percentiles.get("P50"),
        },
        "deterministic_eac": round(total_bac / current_cpi, 2) if current_cpi else None,
        "variance_from_bac": {
            k: round(v - total_bac, 2) for k, v in percentiles.items()
        },
    }


# ── List Periods ─────────────────────────────────────────────────────

def get_evm_periods(contract_id, wbs_id=None):
    """
    List EVM period records with optional WBS filter.

    Args:
        contract_id: Contract UUID
        wbs_id:      Optional WBS UUID to filter

    Returns:
        Dict with status and list of period records.
    """
    conn = _get_db()

    query = "SELECT * FROM cpmp_evm_periods WHERE contract_id = ?"
    params = [contract_id]

    if wbs_id:
        query += " AND wbs_id = ?"
        params.append(wbs_id)

    query += " ORDER BY period_date ASC, wbs_id ASC"
    rows = conn.execute(query, params).fetchall()
    conn.close()

    return {
        "status": "ok",
        "contract_id": contract_id,
        "wbs_id": wbs_id,
        "total": len(rows),
        "periods": [dict(r) for r in rows],
    }


# ── IPMDAR Data ──────────────────────────────────────────────────────

def generate_ipmdar_data(contract_id):
    """
    Generate IPMDAR-compatible data structure.

    Returns:
        - Format 1: WBS summary (each WBS element with latest EVM indicators)
        - Format 3: Baseline (BAC and PV baseline per WBS element)
        - Format 5: EAC analysis (current EAC vs BAC, variance narrative)
    """
    conn = _get_db()

    contract = conn.execute(
        "SELECT * FROM cpmp_contracts WHERE id = ?", (contract_id,)
    ).fetchone()
    if not contract:
        conn.close()
        return {"status": "error", "message": f"Contract {contract_id} not found"}

    # All WBS elements for the contract
    wbs_rows = conn.execute(
        "SELECT * FROM cpmp_wbs WHERE contract_id = ? ORDER BY wbs_number",
        (contract_id,),
    ).fetchall()

    # ── Format 1: WBS Summary ───────────────────────────────────────
    format_1 = []
    for wbs in wbs_rows:
        latest = conn.execute(
            "SELECT * FROM cpmp_evm_periods WHERE contract_id = ? AND wbs_id = ? "
            "ORDER BY period_date DESC LIMIT 1",
            (contract_id, wbs["id"]),
        ).fetchone()

        entry = {
            "wbs_number": wbs["wbs_number"],
            "wbs_title": wbs["title"],
            "level": "",
            "bac": wbs["budget_at_completion"],
            "pv_cumulative": wbs["pv_cumulative"],
            "ev_cumulative": wbs["ev_cumulative"],
            "ac_cumulative": wbs["ac_cumulative"],
            "percent_complete": wbs["percent_complete"],
        }
        if latest:
            entry.update({
                "cpi": latest["cpi"],
                "spi": latest["spi"],
                "cost_variance": latest["cv"],
                "schedule_variance": latest["sv"],
                "eac": latest["eac"],
                "etc": latest["etc"],
                "vac": latest["vac"],
                "tcpi": latest["tcpi"],
                "latest_period": latest["period_date"],
            })
        format_1.append(entry)

    # ── Format 3: Baseline ──────────────────────────────────────────
    # Time-phased PV baseline per WBS
    format_3 = []
    for wbs in wbs_rows:
        periods = conn.execute(
            "SELECT period_date, pv FROM cpmp_evm_periods "
            "WHERE contract_id = ? AND wbs_id = ? ORDER BY period_date ASC",
            (contract_id, wbs["id"]),
        ).fetchall()
        format_3.append({
            "wbs_number": wbs["wbs_number"],
            "wbs_title": wbs["title"],
            "bac": wbs["budget_at_completion"],
            "planned_start": wbs["planned_start"],
            "planned_finish": wbs["planned_finish"],
            "baseline_periods": [
                {"period_date": p["period_date"], "planned_value": p["pv"]}
                for p in periods
            ],
        })

    # ── Format 5: EAC Analysis ──────────────────────────────────────
    total_bac = sum((w["budget_at_completion"] or 0) for w in wbs_rows)
    total_ev = sum((w["ev_cumulative"] or 0) for w in wbs_rows)
    total_ac = sum((w["ac_cumulative"] or 0) for w in wbs_rows)
    total_pv = sum((w["pv_cumulative"] or 0) for w in wbs_rows)

    contract_indicators = compute_indicators(total_bac, total_pv, total_ev, total_ac)

    format_5_elements = []
    for wbs in wbs_rows:
        bac = wbs["budget_at_completion"] or 0
        ev_c = wbs["ev_cumulative"] or 0
        ac_c = wbs["ac_cumulative"] or 0
        pv_c = wbs["pv_cumulative"] or 0
        ind = compute_indicators(bac, pv_c, ev_c, ac_c)

        narrative = ""
        if ind["vac"] is not None:
            if ind["vac"] < 0:
                narrative = f"Projected {abs(ind['vac']):.2f} overrun. "
            elif ind["vac"] > 0:
                narrative = f"Projected {ind['vac']:.2f} underrun. "
            else:
                narrative = "On budget. "

        if ind["cpi"] is not None and ind["cpi"] < CPI_RED:
            narrative += "CPI in RED zone — corrective action required."
        elif ind["cpi"] is not None and ind["cpi"] < CPI_YELLOW:
            narrative += "CPI in YELLOW zone — monitoring closely."

        format_5_elements.append({
            "wbs_number": wbs["wbs_number"],
            "wbs_title": wbs["title"],
            "bac": bac,
            "eac": ind["eac"],
            "vac": ind["vac"],
            "variance_narrative": narrative.strip(),
        })

    format_5 = {
        "contract_bac": total_bac,
        "contract_eac": contract_indicators["eac"],
        "contract_vac": contract_indicators["vac"],
        "contract_cpi": contract_indicators["cpi"],
        "contract_spi": contract_indicators["spi"],
        "elements": format_5_elements,
    }

    conn.close()

    return {
        "status": "ok",
        "contract_id": contract_id,
        "contract_number": contract["contract_number"],
        "title": contract["title"],
        "classification": "CUI // SP-CTI",
        "generated_at": _now(),
        "format_1_wbs_summary": format_1,
        "format_3_baseline": format_3,
        "format_5_eac_analysis": format_5,
    }


# ── CLI ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="ICDEV GovProposal EVM Engine — ANSI/EIA-748 (Phase 60, D-CPMP-2)"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--record", action="store_true", help="Record a monthly EVM period snapshot")
    group.add_argument("--aggregate", action="store_true", help="Aggregate EVM across all WBS for a contract")
    group.add_argument("--scurve", action="store_true", help="Generate S-curve time-series data")
    group.add_argument("--forecast", action="store_true", help="Monte Carlo EAC forecast")
    group.add_argument("--periods", action="store_true", help="List EVM period records")
    group.add_argument("--ipmdar", action="store_true", help="Generate IPMDAR-compatible data export")

    parser.add_argument("--contract-id", help="Contract UUID")
    parser.add_argument("--wbs-id", help="WBS element UUID")
    parser.add_argument("--period-date", help="Period date in YYYY-MM format")
    parser.add_argument("--pv", type=float, help="Planned Value for the period")
    parser.add_argument("--ev", type=float, help="Earned Value for the period")
    parser.add_argument("--ac", type=float, help="Actual Cost for the period")
    parser.add_argument("--source", default="manual", help="Data source (manual/calculated/imported)")
    parser.add_argument("--iterations", type=int, help="Monte Carlo iterations (default from config)")
    parser.add_argument("--json", action="store_true", help="JSON output")

    args = parser.parse_args()

    if args.record:
        if not all([args.contract_id, args.wbs_id, args.period_date,
                     args.pv is not None, args.ev is not None, args.ac is not None]):
            result = {"status": "error",
                      "message": "--record requires --contract-id, --wbs-id, --period-date, --pv, --ev, --ac"}
        else:
            result = record_period(
                args.contract_id, args.wbs_id, args.period_date,
                args.pv, args.ev, args.ac, args.source,
            )
    elif args.aggregate:
        if not args.contract_id:
            result = {"status": "error", "message": "--aggregate requires --contract-id"}
        else:
            result = aggregate_contract_evm(args.contract_id)
    elif args.scurve:
        if not args.contract_id:
            result = {"status": "error", "message": "--scurve requires --contract-id"}
        else:
            result = generate_scurve_data(args.contract_id)
    elif args.forecast:
        if not args.contract_id:
            result = {"status": "error", "message": "--forecast requires --contract-id"}
        else:
            result = forecast_monte_carlo(args.contract_id, iterations=args.iterations)
    elif args.periods:
        if not args.contract_id:
            result = {"status": "error", "message": "--periods requires --contract-id"}
        else:
            result = get_evm_periods(args.contract_id, wbs_id=args.wbs_id)
    elif args.ipmdar:
        if not args.contract_id:
            result = {"status": "error", "message": "--ipmdar requires --contract-id"}
        else:
            result = generate_ipmdar_data(args.contract_id)
    else:
        result = {"status": "error", "message": "Unknown command"}

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
