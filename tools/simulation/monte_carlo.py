#!/usr/bin/env python3
# CUI // SP-CTI
"""RICOAS Digital Program Twin — Monte Carlo simulation using PERT distribution.

Uses Python stdlib `random` with Beta-distribution approximation to PERT.
NO numpy, NO scipy. Supports schedule, cost, and risk dimensions with
configurable iterations and confidence levels.

Part of the ICDEV RICOAS Phase 20C simulation subsystem.
"""

import argparse
import json
import math
import os
import random
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

DEFAULT_ITERATIONS = 10000
DEFAULT_CONFIDENCE_LEVELS = [0.10, 0.50, 0.80, 0.90]

# T-shirt size to hours mapping (consistent with simulation_engine.py)
TSHIRT_HOURS = {
    "XS": 8,
    "S": 40,
    "M": 80,
    "L": 200,
    "XL": 400,
    "XXL": 800,
}

DEFAULT_HOURLY_RATE = 150
HISTOGRAM_BINS = 20
CDF_POINTS = 100


# ---------------------------------------------------------------------------
# PERT distribution
# ---------------------------------------------------------------------------

def pert_sample(optimistic, most_likely, pessimistic, lambd=4):
    """Sample from PERT distribution using Beta distribution approximation.

    The PERT distribution is a reparameterization of the Beta distribution
    commonly used in project management for schedule and cost estimation.

    Args:
        optimistic: Best-case value.
        most_likely: Most likely value (mode).
        pessimistic: Worst-case value.
        lambd: Shape parameter (default 4, standard PERT).

    Returns:
        A single sample from the PERT distribution.
    """
    if optimistic >= pessimistic:
        return most_likely
    alpha = 1 + lambd * (most_likely - optimistic) / (pessimistic - optimistic)
    beta_param = 1 + lambd * (pessimistic - most_likely) / (pessimistic - optimistic)
    # Use stdlib random.betavariate
    x = random.betavariate(alpha, beta_param)
    return optimistic + x * (pessimistic - optimistic)


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def _get_connection(db_path=None):
    """Get a database connection."""
    path = db_path or DB_PATH
    if not Path(path).exists():
        raise FileNotFoundError(
            f"Database not found: {path}\n"
            "Run: python tools/db/init_icdev_db.py"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _log_audit(conn, project_id, action, details):
    """Append an audit trail entry (immutable)."""
    try:
        conn.execute(
            """INSERT INTO audit_trail
               (project_id, event_type, actor, action, details, classification)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                project_id,
                "monte_carlo_completed",
                "icdev-simulation-engine",
                action,
                json.dumps(details),
                "CUI",
            ),
        )
        conn.commit()
    except Exception as exc:
        print(f"Warning: audit log failed: {exc}", file=sys.stderr)


def _safe_query(conn, sql, params=()):
    """Execute query, return list of dicts. Returns [] on error."""
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []


# ---------------------------------------------------------------------------
# Statistics helpers (stdlib only)
# ---------------------------------------------------------------------------

def _mean(values):
    """Compute arithmetic mean."""
    if not values:
        return 0.0
    return sum(values) / len(values)


def _std_dev(values, mean_val=None):
    """Compute population standard deviation."""
    if len(values) < 2:
        return 0.0
    if mean_val is None:
        mean_val = _mean(values)
    variance = sum((x - mean_val) ** 2 for x in values) / len(values)
    return math.sqrt(variance)


def _percentile(sorted_values, p):
    """Compute the p-th percentile from a sorted list (0 <= p <= 1)."""
    if not sorted_values:
        return 0.0
    n = len(sorted_values)
    idx = p * (n - 1)
    lower = int(math.floor(idx))
    upper = int(math.ceil(idx))
    if lower == upper:
        return sorted_values[lower]
    # Linear interpolation
    frac = idx - lower
    return sorted_values[lower] * (1 - frac) + sorted_values[upper] * frac


def _build_histogram(values, num_bins=HISTOGRAM_BINS):
    """Build histogram data (bin edges and counts)."""
    if not values:
        return {"bins": [], "counts": [], "bin_width": 0}
    min_val = min(values)
    max_val = max(values)
    if min_val == max_val:
        return {"bins": [min_val], "counts": [len(values)], "bin_width": 0}

    bin_width = (max_val - min_val) / num_bins
    bins = []
    counts = []
    for i in range(num_bins):
        edge = min_val + i * bin_width
        bins.append(round(edge, 2))
        count = 0
        for v in values:
            if i < num_bins - 1:
                if edge <= v < edge + bin_width:
                    count += 1
            else:
                # Last bin is inclusive on both sides
                if edge <= v <= max_val:
                    count += 1
        counts.append(count)

    return {"bins": bins, "counts": counts, "bin_width": round(bin_width, 2)}


def _build_cdf(sorted_values, num_points=CDF_POINTS):
    """Build cumulative distribution function data points."""
    if not sorted_values:
        return []
    n = len(sorted_values)
    cdf = []
    for i in range(num_points):
        p = i / (num_points - 1) if num_points > 1 else 0.5
        val = _percentile(sorted_values, p)
        cdf.append({"probability": round(p, 4), "value": round(val, 2)})
    return cdf


# ---------------------------------------------------------------------------
# Dimension-specific input generators
# ---------------------------------------------------------------------------

def _get_schedule_inputs(conn, project_id):
    """Gather SAFe items and build PERT parameters for schedule simulation.

    For each story/item, the estimate is the t-shirt-mapped hours:
        optimistic = 0.5 * estimate
        most_likely = estimate
        pessimistic = 2.5 * estimate
    """
    items = _safe_query(
        conn,
        "SELECT id, title, t_shirt_size, story_points, wsjf_score "
        "FROM safe_decomposition WHERE project_id = ? AND level = 'story'",
        (project_id,),
    )
    if not items:
        # Fallback: generate synthetic items based on any SAFe decomposition
        items = _safe_query(
            conn,
            "SELECT id, title, t_shirt_size, story_points, wsjf_score "
            "FROM safe_decomposition WHERE project_id = ?",
            (project_id,),
        )

    inputs = []
    for item in items:
        size = item.get("t_shirt_size") or "M"
        base_hours = TSHIRT_HOURS.get(size, 80)
        inputs.append({
            "item_id": item["id"],
            "title": item.get("title", ""),
            "estimate_hours": base_hours,
            "optimistic": base_hours * 0.5,
            "most_likely": float(base_hours),
            "pessimistic": base_hours * 2.5,
        })

    # If no items at all, provide a synthetic single-item baseline
    if not inputs:
        inputs.append({
            "item_id": "synthetic-baseline",
            "title": "Baseline estimate",
            "estimate_hours": 80,
            "optimistic": 40.0,
            "most_likely": 80.0,
            "pessimistic": 200.0,
        })

    return inputs


def _get_cost_inputs(conn, project_id):
    """Gather components and build PERT parameters for cost simulation.

    For each component:
        optimistic = 0.7 * estimate
        most_likely = estimate
        pessimistic = 2.0 * estimate
    """
    items = _safe_query(
        conn,
        "SELECT id, title, t_shirt_size FROM safe_decomposition WHERE project_id = ?",
        (project_id,),
    )

    inputs = []
    for item in items:
        size = item.get("t_shirt_size") or "M"
        base_hours = TSHIRT_HOURS.get(size, 80)
        base_cost = base_hours * DEFAULT_HOURLY_RATE
        inputs.append({
            "item_id": item["id"],
            "title": item.get("title", ""),
            "estimate_cost": base_cost,
            "optimistic": base_cost * 0.7,
            "most_likely": float(base_cost),
            "pessimistic": base_cost * 2.0,
        })

    if not inputs:
        base_cost = 80 * DEFAULT_HOURLY_RATE
        inputs.append({
            "item_id": "synthetic-baseline",
            "title": "Baseline cost",
            "estimate_cost": base_cost,
            "optimistic": base_cost * 0.7,
            "most_likely": float(base_cost),
            "pessimistic": base_cost * 2.0,
        })

    return inputs


def _get_risk_inputs(conn, project_id):
    """Gather risk events and their probabilities for risk simulation.

    Each risk event has a probability (bernoulli) and an impact value.
    """
    risk_events = []

    # Critical CVEs
    cves = _safe_query(
        conn,
        "SELECT cve_id, severity, cvss_score FROM cve_triage "
        "WHERE project_id = ? AND triage_decision NOT IN ('false_positive', 'not_applicable')",
        (project_id,),
    )
    for cve in cves:
        severity = cve.get("severity", "medium")
        prob_map = {"critical": 0.4, "high": 0.3, "medium": 0.2, "low": 0.1}
        impact_map = {"critical": 200, "high": 100, "medium": 50, "low": 20}
        risk_events.append({
            "event_id": cve.get("cve_id", "unknown"),
            "event_type": "cve",
            "probability": prob_map.get(severity, 0.2),
            "impact_hours": impact_map.get(severity, 50),
        })

    # RED boundary impacts
    reds = _safe_query(
        conn,
        "SELECT id, impact_description FROM boundary_impact_assessments "
        "WHERE project_id = ? AND impact_tier = 'RED'",
        (project_id,),
    )
    for r in reds:
        risk_events.append({
            "event_id": r.get("id", "unknown"),
            "event_type": "boundary_red",
            "probability": 0.3,
            "impact_hours": 160,
        })

    # Expired ISAs
    isas = _safe_query(
        conn,
        "SELECT id, partner_system FROM isa_agreements "
        "WHERE project_id = ? AND status = 'expired'",
        (project_id,),
    )
    for isa in isas:
        risk_events.append({
            "event_id": isa.get("id", "unknown"),
            "event_type": "expired_isa",
            "probability": 0.2,
            "impact_hours": 80,
        })

    # CAT1 STIG findings
    cat1s = _safe_query(
        conn,
        "SELECT stig_id, title FROM stig_findings "
        "WHERE project_id = ? AND severity = 'CAT1' AND status = 'Open'",
        (project_id,),
    )
    for s in cat1s:
        risk_events.append({
            "event_id": s.get("stig_id", "unknown"),
            "event_type": "cat1_stig",
            "probability": 0.35,
            "impact_hours": 120,
        })

    # If no risk events, add a baseline low risk
    if not risk_events:
        risk_events.append({
            "event_id": "baseline-risk",
            "event_type": "baseline",
            "probability": 0.05,
            "impact_hours": 40,
        })

    return risk_events


# ---------------------------------------------------------------------------
# Monte Carlo runners
# ---------------------------------------------------------------------------

def _run_schedule_mc(inputs, iterations):
    """Run Monte Carlo for schedule dimension.

    Each iteration samples PERT for each item and sums total hours.
    """
    results = []
    for _ in range(iterations):
        total = 0.0
        for item in inputs:
            sampled = pert_sample(
                item["optimistic"], item["most_likely"], item["pessimistic"]
            )
            total += sampled
        results.append(total)
    return results


def _run_cost_mc(inputs, iterations):
    """Run Monte Carlo for cost dimension.

    Each iteration samples PERT for cost of each component and sums.
    """
    results = []
    for _ in range(iterations):
        total = 0.0
        for item in inputs:
            sampled = pert_sample(
                item["optimistic"], item["most_likely"], item["pessimistic"]
            )
            total += sampled
        results.append(total)
    return results


def _run_risk_mc(inputs, iterations):
    """Run Monte Carlo for risk dimension.

    Each iteration: for each risk event, sample bernoulli (uniform < prob),
    if triggered, add impact hours.
    """
    results = []
    for _ in range(iterations):
        total_impact = 0.0
        for event in inputs:
            if random.random() < event["probability"]:
                total_impact += event["impact_hours"]
        results.append(total_impact)
    return results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_monte_carlo(scenario_id, dimension, iterations=DEFAULT_ITERATIONS,
                    confidence_levels=None, db_path=None):
    """Execute Monte Carlo simulation for a scenario dimension.

    Args:
        scenario_id: UUID of the simulation scenario.
        dimension: One of 'schedule', 'cost', 'risk'.
        iterations: Number of iterations (default 10000).
        confidence_levels: List of percentiles (default [0.10, 0.50, 0.80, 0.90]).
        db_path: Optional database path override.

    Returns:
        dict with run_id, dimension, iterations, mean, std_dev, percentiles,
        histogram, and cdf.
    """
    if confidence_levels is None:
        confidence_levels = list(DEFAULT_CONFIDENCE_LEVELS)

    if dimension not in ("schedule", "cost", "risk"):
        raise ValueError(f"Invalid dimension: {dimension}. Must be schedule, cost, or risk.")

    conn = _get_connection(db_path)
    try:
        # Load scenario
        row = conn.execute(
            "SELECT * FROM simulation_scenarios WHERE id = ?", (scenario_id,),
        ).fetchone()
        if not row:
            raise ValueError(f"Scenario not found: {scenario_id}")

        scenario = dict(row)
        project_id = scenario["project_id"]

        # Gather inputs
        if dimension == "schedule":
            inputs = _get_schedule_inputs(conn, project_id)
            runner = _run_schedule_mc
        elif dimension == "cost":
            inputs = _get_cost_inputs(conn, project_id)
            runner = _run_cost_mc
        else:  # risk
            inputs = _get_risk_inputs(conn, project_id)
            runner = _run_risk_mc

        # Run simulation
        start_time = time.time()
        raw_results = runner(inputs, iterations)
        duration_ms = int((time.time() - start_time) * 1000)

        # Sort for percentile calculations
        raw_results.sort()

        # Statistics
        mean_val = _mean(raw_results)
        std_val = _std_dev(raw_results, mean_val)
        min_val = raw_results[0] if raw_results else 0.0
        max_val = raw_results[-1] if raw_results else 0.0

        # Percentiles
        percentiles = {}
        for p in confidence_levels:
            key = f"p{int(p * 100)}"
            percentiles[key] = round(_percentile(raw_results, p), 2)

        # Histogram and CDF
        histogram = _build_histogram(raw_results, HISTOGRAM_BINS)
        cdf = _build_cdf(raw_results, CDF_POINTS)

        # Build summary
        results_summary = {
            "mean": round(mean_val, 2),
            "std_dev": round(std_val, 2),
            "min": round(min_val, 2),
            "max": round(max_val, 2),
            "percentiles": percentiles,
        }

        # Persist to DB
        run_id = str(uuid4())
        conn.execute(
            """INSERT INTO monte_carlo_runs
               (id, scenario_id, iterations, dimension, distribution_type,
                input_parameters,
                p10_value, p50_value, p80_value, p90_value,
                mean_value, std_deviation,
                histogram_data, cdf_data, confidence_intervals,
                run_duration_ms, completed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id,
                scenario_id,
                iterations,
                dimension,
                "pert",
                json.dumps(inputs, default=str),
                percentiles.get("p10"),
                percentiles.get("p50"),
                percentiles.get("p80"),
                percentiles.get("p90"),
                round(mean_val, 2),
                round(std_val, 2),
                json.dumps(histogram),
                json.dumps(cdf),
                json.dumps({"confidence_levels": confidence_levels, "percentiles": percentiles}),
                duration_ms,
                datetime.utcnow().isoformat(),
            ),
        )
        conn.commit()

        _log_audit(conn, project_id,
                   f"Monte Carlo ({dimension}) completed: {iterations} iterations",
                   {"run_id": run_id, "scenario_id": scenario_id,
                    "dimension": dimension, "iterations": iterations,
                    "mean": round(mean_val, 2), "duration_ms": duration_ms})

        return {
            "run_id": run_id,
            "dimension": dimension,
            "iterations": iterations,
            "mean": round(mean_val, 2),
            "std_dev": round(std_val, 2),
            "min": round(min_val, 2),
            "max": round(max_val, 2),
            "percentiles": percentiles,
            "histogram": histogram,
            "cdf": cdf,
            "duration_ms": duration_ms,
        }
    finally:
        conn.close()


def get_run(run_id, db_path=None):
    """Get a Monte Carlo run by ID.

    Args:
        run_id: UUID of the run.
        db_path: Optional database path override.

    Returns:
        dict with full run details.
    """
    conn = _get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM monte_carlo_runs WHERE id = ?", (run_id,),
        ).fetchone()
        if not row:
            raise ValueError(f"Monte Carlo run not found: {run_id}")

        result = dict(row)
        # Parse JSON fields
        for field in ("input_parameters", "histogram_data", "cdf_data", "confidence_intervals"):
            if result.get(field):
                try:
                    result[field] = json.loads(result[field])
                except (json.JSONDecodeError, TypeError):
                    pass
        return result
    finally:
        conn.close()


def list_runs(scenario_id, db_path=None):
    """List all Monte Carlo runs for a scenario.

    Args:
        scenario_id: UUID of the scenario.
        db_path: Optional database path override.

    Returns:
        dict with scenario_id and list of runs.
    """
    conn = _get_connection(db_path)
    try:
        rows = conn.execute(
            "SELECT id, dimension, iterations, mean_value, std_deviation, "
            "p10_value, p50_value, p80_value, p90_value, run_duration_ms, completed_at "
            "FROM monte_carlo_runs WHERE scenario_id = ? ORDER BY completed_at DESC",
            (scenario_id,),
        ).fetchall()

        runs = [dict(r) for r in rows]
        return {"scenario_id": scenario_id, "runs": runs, "count": len(runs)}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="RICOAS Digital Program Twin — Monte Carlo simulation (PERT distribution)"
    )
    parser.add_argument("--scenario-id", help="Scenario UUID")
    parser.add_argument("--run-id", help="Monte Carlo run UUID")
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    # Run args
    parser.add_argument("--dimension", choices=["schedule", "cost", "risk"],
                        help="Dimension to simulate")
    parser.add_argument("--iterations", type=int, default=DEFAULT_ITERATIONS,
                        help=f"Number of iterations (default {DEFAULT_ITERATIONS})")
    parser.add_argument("--confidence-levels",
                        help="Comma-separated confidence levels (e.g. 0.10,0.50,0.80,0.90,0.95)")

    # Query args
    parser.add_argument("--get", action="store_true", help="Get run details")
    parser.add_argument("--list", action="store_true", help="List runs for a scenario")

    # DB override
    parser.add_argument("--db", help="Database path override")

    args = parser.parse_args()
    db_path = Path(args.db) if args.db else None

    try:
        if args.get:
            if not args.run_id:
                parser.error("--run-id is required for --get")
            result = get_run(run_id=args.run_id, db_path=db_path)

        elif args.list:
            if not args.scenario_id:
                parser.error("--scenario-id is required for --list")
            result = list_runs(scenario_id=args.scenario_id, db_path=db_path)

        elif args.scenario_id and args.dimension:
            # Run Monte Carlo
            conf_levels = None
            if args.confidence_levels:
                conf_levels = [float(x.strip()) for x in args.confidence_levels.split(",")]
            result = run_monte_carlo(
                scenario_id=args.scenario_id,
                dimension=args.dimension,
                iterations=args.iterations,
                confidence_levels=conf_levels,
                db_path=db_path,
            )

        else:
            parser.print_help()
            sys.exit(0)

        # Output
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            _print_human(result)

    except (FileNotFoundError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    except json.JSONDecodeError as exc:
        print(f"ERROR: Invalid JSON: {exc}", file=sys.stderr)
        sys.exit(1)


def _print_human(result):
    """Pretty-print results for human consumption."""
    if "run_id" in result and "dimension" in result and "mean" in result:
        # run_monte_carlo result
        print(f"Monte Carlo Simulation — {result['dimension'].upper()}")
        print(f"  Run ID:     {result['run_id']}")
        print(f"  Iterations: {result['iterations']:,}")
        print(f"  Duration:   {result.get('duration_ms', 0)} ms")
        print()
        print(f"  Mean:    {result['mean']:,.2f}")
        print(f"  Std Dev: {result['std_dev']:,.2f}")
        print(f"  Min:     {result.get('min', 0):,.2f}")
        print(f"  Max:     {result.get('max', 0):,.2f}")
        print()
        print("  Percentiles:")
        for k, v in result.get("percentiles", {}).items():
            print(f"    {k}: {v:,.2f}")
        print()
        # Mini ASCII histogram
        hist = result.get("histogram", {})
        counts = hist.get("counts", [])
        if counts:
            max_count = max(counts) if counts else 1
            print("  Histogram:")
            bins = hist.get("bins", [])
            for i, c in enumerate(counts):
                bar_len = int((c / max_count) * 40) if max_count > 0 else 0
                label = f"{bins[i]:>10,.0f}" if i < len(bins) else ""
                print(f"    {label} | {'#' * bar_len} ({c})")

    elif "runs" in result:
        # list_runs result
        print(f"Scenario: {result['scenario_id']}  |  Runs: {result['count']}")
        for r in result["runs"]:
            print(
                f"  [{r['dimension']}] {r['id'][:8]}... "
                f"mean={r.get('mean_value', 0):,.2f} "
                f"p50={r.get('p50_value', 0):,.2f} "
                f"p90={r.get('p90_value', 0):,.2f} "
                f"({r.get('iterations', 0):,} iter)"
            )

    elif "id" in result and "scenario_id" in result:
        # get_run result
        print(f"Run ID:     {result['id']}")
        print(f"Scenario:   {result['scenario_id']}")
        print(f"Dimension:  {result.get('dimension')}")
        print(f"Iterations: {result.get('iterations', 0):,}")
        print(f"Mean:       {result.get('mean_value', 0):,.2f}")
        print(f"Std Dev:    {result.get('std_deviation', 0):,.2f}")
        print(f"P10:        {result.get('p10_value', 0):,.2f}")
        print(f"P50:        {result.get('p50_value', 0):,.2f}")
        print(f"P80:        {result.get('p80_value', 0):,.2f}")
        print(f"P90:        {result.get('p90_value', 0):,.2f}")
        print(f"Duration:   {result.get('run_duration_ms', 0)} ms")

    else:
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
# CUI // SP-CTI
