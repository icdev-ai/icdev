#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""Rate Benchmarker -- wrap rate calculation, CALC+ benchmarking, Price-to-Win analysis.

Calculates fully-loaded labor rates, benchmarks against stored GSA CALC+ data and
FPDS award history, runs Price-to-Win analysis, and generates cost volume templates.
Deterministic (no LLM, air-gap safe).

DB tables: pg_cost_volumes (write), pg_lcat_allocations (read),
           pg_rate_benchmarks (read/write), pg_competitor_awards (read/write),
           audit_trail (write)

Usage:
    python tools/govcon/rate_benchmarker.py --opportunity-id "opp-xxx" --wrap-rates --direct-rate 75 --json
    python tools/govcon/rate_benchmarker.py --opportunity-id "opp-xxx" --benchmark --labor-category "Software Developers" --json
    python tools/govcon/rate_benchmarker.py --opportunity-id "opp-xxx" --ptw --json
    python tools/govcon/rate_benchmarker.py --opportunity-id "opp-xxx" --generate --contract-type ffp --json
    python tools/govcon/rate_benchmarker.py --opportunity-id "opp-xxx" --sensitivity --json
    python tools/govcon/rate_benchmarker.py --opportunity-id "opp-xxx" --gate --json
    python tools/govcon/rate_benchmarker.py --store-benchmark --labor-category "Software Developers" --soc-code "15-1252" --source calc_plus --hourly-rate 95.50 --year 2026 --json
"""

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.db.storage import get_connection  # noqa: E402

# Default indirect rates (DCAA-typical for mid-tier GovCon)
from tools.logging.icdev_logger import get_logger
logger = get_logger("icdev.govcon.rate_benchmarker")

DEFAULT_RATES = {
    "fringe_pct": 0.32,
    "overhead_pct": 0.45,
    "ga_pct": 0.12,
    "fee_pct": 0.08,
}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _gen_id(prefix="rb"):
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _get_db():
    return get_connection()


def _audit(conn, event_type, action, details, opportunity_id=None):
    """Append an audit row (NIST AU-2).

    prem-bid-02: this used to INSERT into a column called ``timestamp``. There is no
    such column on audit_trail — it is ``created_at`` — and there was no try/except
    around it, so this raised. Every path through here raised.

    That is almost certainly WHY generate_cost_volume had no callers: it was not merely
    dead code, it was code that could not run. Verified against live PostgreSQL:
    `column "timestamp" of relation "audit_trail" does not exist`.

    Best-effort now, like every other _audit in tools/govcon: the caller's actual work
    must not fail because the audit table is unavailable.
    """
    try:
        conn.execute(
            "INSERT INTO audit_trail (created_at, event_type, actor, action, details, "
            "project_id, session_id) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                _now(),
                event_type,
                "rate_benchmarker",
                action,
                json.dumps(details) if isinstance(details, dict) else str(details),
                opportunity_id,
                None,
            ),
        )
    except Exception as exc:
        logger.warning("audit write failed for %s: %s", event_type, exc)


def _ensure_tables(conn):
    """Create rate benchmarking tables if they don't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pg_rate_benchmarks (
            id TEXT PRIMARY KEY,
            labor_category TEXT NOT NULL,
            soc_code TEXT,
            source TEXT NOT NULL,
            hourly_rate REAL NOT NULL,
            year INTEGER NOT NULL,
            region TEXT,
            contract_vehicle TEXT,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pg_competitor_awards (
            id TEXT PRIMARY KEY,
            opportunity_id TEXT,
            competitor_name TEXT NOT NULL,
            contract_number TEXT,
            award_amount REAL,
            period_of_performance TEXT,
            naics_code TEXT,
            labor_categories TEXT,
            source TEXT,
            created_at TEXT NOT NULL,
            tenant_id TEXT,
            classification TEXT DEFAULT 'CUI'
        )
    """)
    conn.commit()


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------


def calculate_wrap_rates(direct_rate, fringe_pct=None, overhead_pct=None, ga_pct=None, fee_pct=None):
    """Calculate fully-loaded labor rate from components."""
    fringe = fringe_pct if fringe_pct is not None else DEFAULT_RATES["fringe_pct"]
    overhead = overhead_pct if overhead_pct is not None else DEFAULT_RATES["overhead_pct"]
    ga = ga_pct if ga_pct is not None else DEFAULT_RATES["ga_pct"]
    fee = fee_pct if fee_pct is not None else DEFAULT_RATES["fee_pct"]

    fringe_cost = direct_rate * fringe
    subtotal_1 = direct_rate + fringe_cost
    overhead_cost = subtotal_1 * overhead
    subtotal_2 = subtotal_1 + overhead_cost
    ga_cost = subtotal_2 * ga
    subtotal_3 = subtotal_2 + ga_cost
    fee_cost = subtotal_3 * fee
    total = subtotal_3 + fee_cost

    wrap_multiplier = total / direct_rate if direct_rate > 0 else 0

    return {
        "status": "ok",
        "direct_rate": round(direct_rate, 2),
        "fringe": {"pct": fringe, "cost": round(fringe_cost, 2)},
        "overhead": {"pct": overhead, "cost": round(overhead_cost, 2)},
        "g_and_a": {"pct": ga, "cost": round(ga_cost, 2)},
        "fee": {"pct": fee, "cost": round(fee_cost, 2)},
        "total_loaded_rate": round(total, 2),
        "wrap_multiplier": round(wrap_multiplier, 3),
    }


def benchmark_rates(labor_category, soc_code=None):
    """Look up stored benchmark data for a labor category."""
    conn = _get_db()
    _ensure_tables(conn)

    params = [f"%{labor_category}%"]
    query = "SELECT hourly_rate, source, year, region FROM pg_rate_benchmarks WHERE labor_category LIKE ?"
    if soc_code:
        query += " AND soc_code = ?"
        params.append(soc_code)
    query += " ORDER BY year DESC"

    rows = conn.execute(query, params).fetchall()
    conn.close()

    if not rows:
        return {
            "status": "ok",
            "labor_category": labor_category,
            "message": "No benchmark data found. Use --store-benchmark to add rates.",
            "median": None,
            "p25": None,
            "p75": None,
            "sample_size": 0,
        }

    rates = sorted([r[0] if isinstance(r, (tuple, list)) else r["hourly_rate"] for r in rows])
    n = len(rates)
    median = rates[n // 2] if n % 2 == 1 else (rates[n // 2 - 1] + rates[n // 2]) / 2
    p25 = rates[max(0, n // 4)]
    p75 = rates[min(n - 1, 3 * n // 4)]

    return {
        "status": "ok",
        "labor_category": labor_category,
        "soc_code": soc_code,
        "median": round(median, 2),
        "p25": round(p25, 2),
        "p75": round(p75, 2),
        "sample_size": n,
        "year_range": f"{min(r[2] if isinstance(r, (tuple, list)) else r['year'] for r in rows)}-{max(r[2] if isinstance(r, (tuple, list)) else r['year'] for r in rows)}",
    }


def store_benchmark(labor_category, soc_code, source, hourly_rate, year, region=None, vehicle=None):
    """Store a rate benchmark data point."""
    conn = _get_db()
    _ensure_tables(conn)
    bid = _gen_id("bmk")
    conn.execute(
        "INSERT INTO pg_rate_benchmarks (id, labor_category, soc_code, source, hourly_rate, year, region, contract_vehicle, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (bid, labor_category, soc_code, source, hourly_rate, year, region, vehicle, _now()),
    )
    _audit(conn, "benchmark.store", f"Stored benchmark: {labor_category} ${hourly_rate}/hr", {"id": bid}, None)
    conn.commit()
    conn.close()
    return {"status": "ok", "id": bid, "labor_category": labor_category, "hourly_rate": hourly_rate}


def ptw_analysis(opportunity_id):
    """Price-to-Win analysis using competitor award data."""
    conn = _get_db()
    _ensure_tables(conn)

    rows = conn.execute(
        "SELECT competitor_name, award_amount, period_of_performance, naics_code "
        "FROM pg_competitor_awards WHERE opportunity_id = %s OR opportunity_id IS NULL "
        "ORDER BY created_at DESC LIMIT 20",
        (opportunity_id,),
    ).fetchall()
    conn.close()

    if not rows:
        return {
            "status": "ok",
            "opportunity_id": opportunity_id,
            "message": "No competitor award data available for PTW analysis",
            "recommendation": "competitive",
            "confidence": 0.1,
        }

    amounts = [
        r[1] if isinstance(r, (tuple, list)) else r["award_amount"]
        for r in rows
        if (r[1] if isinstance(r, (tuple, list)) else r["award_amount"])
    ]
    if not amounts:
        return {
            "status": "ok",
            "opportunity_id": opportunity_id,
            "message": "No award amounts in competitor data",
            "recommendation": "competitive",
            "confidence": 0.1,
        }

    amounts.sort()
    n = len(amounts)
    median = amounts[n // 2]
    low = amounts[max(0, n // 4)]
    high = amounts[min(n - 1, 3 * n // 4)]

    # Recommendation based on position
    recommendation = "competitive"  # default: price near median
    if n >= 5:
        recommendation = "competitive"
    confidence = min(0.9, n / 20)

    return {
        "status": "ok",
        "opportunity_id": opportunity_id,
        "competitor_count": len(rows),
        "award_amounts_analyzed": n,
        "ptw_range": {"low": round(low, 2), "median": round(median, 2), "high": round(high, 2)},
        "recommendation": recommendation,
        "confidence": round(confidence, 2),
        "strategies": {
            "aggressive": {"target": round(low * 0.95, 2), "risk": "high", "win_probability": "higher if LPTA"},
            "competitive": {"target": round(median, 2), "risk": "moderate", "win_probability": "balanced"},
            "premium": {"target": round(high * 1.05, 2), "risk": "low", "win_probability": "higher if best-value"},
        },
    }


def generate_cost_volume(opportunity_id, contract_type="ffp", *, allow_unrated=False):
    """Assemble cost volume from LCAT allocations and wrap rates.

    prem-bid-01/02. Two things were wrong here, and they compounded.

    **It guessed the price.** An LCAT with no rate was silently priced at
    ``rate = 85.0  # default if no rate set``. That is a made-up number on a bid. It
    does not fail, it does not warn — it produces a total that looks exactly like a
    real one, and the wrap rates and the price-to-win band are all computed on top of
    it. compass holds the actual merged supplier rate cards; guessing was never
    necessary, only easier.

    **And then it hid the guess.** ``line_items`` was computed, returned in the
    response dict, and never stored. Only the TOTALS went into pg_cost_volumes. So the
    $85 could not be found afterwards even if you went looking: the price could not be
    audited line by line.

    Now an unrated LCAT is SURFACED, never defaulted. The volume is not priced unless
    every allocation carries a rate; the unrated ones come back in ``unrated`` with
    enough detail to go and fetch them. ``allow_unrated=True`` exists for the estimating
    path that genuinely wants a partial answer — it prices only the rated lines, reports
    the rest, and marks the volume ``partial`` so nothing downstream can mistake it for
    a complete price.

    Priced line items are persisted back onto pg_lcat_allocations (hourly_rate,
    annual_cost, basis_of_estimate) — columns that already existed and were never
    filled in.
    """
    conn = _get_db()
    _ensure_tables(conn)

    # Get LCAT allocations
    allocs = conn.execute(
        "SELECT id, labor_category, bls_soc_code, fte_count, hourly_rate "
        "FROM pg_lcat_allocations WHERE cost_volume_id = %s",
        (opportunity_id,),
    ).fetchall()

    if not allocs:
        conn.close()
        return {
            "status": "ok",
            "opportunity_id": opportunity_id,
            "message": "No LCAT allocations found. Run lcat_mapper.py first.",
        }

    # Calculate costs per LCAT
    line_items = []
    unrated = []
    total_direct = 0
    for a in allocs:
        d = dict(a) if not isinstance(a, (list, tuple)) else None
        alloc_id = d["id"] if d else a[0]
        lc = d["labor_category"] if d else a[1]
        fte = d["fte_count"] if d else a[3]
        rate = d["hourly_rate"] if d else a[4]

        if rate is None:
            # `is None`, NOT `not rate`. A rate of 0.0 is REAL DATA — customer-furnished
            # or government-furnished labour, a no-charge resource — and `not rate` is
            # falsy for it. The original bug replaced a legitimate $0.00 line with the
            # $85 default: the one case where the data was RIGHT is the case it corrupted.
            # My first fix inherited the same test and would have flagged a valid zero-rate
            # line as unrated. Missing and zero are different things.
            #
            # NEVER guess. A made-up rate on a bid is a silently wrong price — it looks
            # exactly like a real one all the way through the wrap rates and the PTW
            # band. Surface it so someone can go and get the real number.
            unrated.append({
                "allocation_id": alloc_id,
                "labor_category": lc,
                "fte": fte,
                "reason": (
                    f"no hourly rate for LCAT '{lc}'. Load the supplier rate card "
                    f"(compass tools/dataops/rate_card_merger) or set the rate on the "
                    f"allocation. This line is NOT priced — it is not guessed either."
                ),
            })
            continue

        annual_hours = (fte or 0) * 2080
        annual_cost = annual_hours * rate
        total_direct += annual_cost
        line_items.append(
            {
                "allocation_id": alloc_id,
                "labor_category": lc,
                "fte": fte,
                "hourly_rate": rate,
                "annual_hours": round(annual_hours),
                "annual_cost": round(annual_cost, 2),
            }
        )

    if unrated and not allow_unrated:
        conn.close()
        return {
            "status": "unpriced",
            "opportunity_id": opportunity_id,
            "unrated": unrated,
            "unrated_count": len(unrated),
            "priced_count": len(line_items),
            "message": (
                f"{len(unrated)} labor category/categories have no rate. Refusing to "
                f"price the volume: a defaulted rate is a wrong price that looks like a "
                f"right one. Pass allow_unrated=True for a partial estimate."
            ),
        }

    # Apply wrap rates
    wrap = calculate_wrap_rates(total_direct)
    total_loaded = wrap["total_loaded_rate"]

    # Persist the PRICED line items. They used to be computed, returned in the response
    # dict, and thrown away — only the totals were stored. So the price could not be
    # audited line by line: you could see a number, but not which LCAT at which rate
    # produced it (and, before this, not which of them had been silently defaulted to
    # $85). hourly_rate / annual_cost / basis_of_estimate are columns pg_lcat_allocations
    # has always had and nothing ever filled in.
    for item in line_items:
        try:
            conn.execute(
                "UPDATE pg_lcat_allocations SET hourly_rate = %s, annual_cost = %s, "
                "basis_of_estimate = %s WHERE id = %s",
                (
                    item["hourly_rate"],
                    item["annual_cost"],
                    f"{item['fte']} FTE x 2080 hrs x ${item['hourly_rate']}/hr "
                    f"(rate from allocation; NOT defaulted)",
                    item["allocation_id"],
                ),
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("could not persist line item %s: %s", item["allocation_id"], exc)
    conn.commit()
    conn.close()

    # Store cost volume
    conn2 = _get_db()
    _ensure_tables(conn2)
    cv_id = _gen_id("cv")
    conn2.execute(
        "INSERT OR REPLACE INTO pg_cost_volumes (id, opportunity_id, contract_type, pricing_strategy, "
        "total_evaluated_price, direct_labor_cost, fringe_rate, overhead_rate, g_and_a_rate, fee_rate, "
        "subcontractor_cost, odc_cost, ptw_estimate_low, ptw_estimate_high, calc_benchmark_median, "
        "status, created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            cv_id,
            opportunity_id,
            contract_type,
            "best_value",
            total_loaded,
            total_direct,
            DEFAULT_RATES["fringe_pct"],
            DEFAULT_RATES["overhead_pct"],
            DEFAULT_RATES["ga_pct"],
            DEFAULT_RATES["fee_pct"],
            0,
            0,
            None,
            None,
            None,
            "draft",
            _now(),
            _now(),
        ),
    )
    _audit(
        conn2,
        "cost_volume.generate",
        f"Generated {contract_type} cost volume",
        {"id": cv_id, "total": total_loaded},
        opportunity_id,
    )
    conn2.commit()
    conn2.close()

    return {
        # "partial" is not "ok". A volume priced with some lines missing must never be
        # mistaken downstream for a complete price — that is how a hole in a bid gets
        # to the customer.
        "status": "partial" if unrated else "ok",
        "cost_volume_id": cv_id,
        "opportunity_id": opportunity_id,
        "contract_type": contract_type,
        "line_items": line_items,
        "unrated": unrated,
        "unrated_count": len(unrated),
        "direct_labor_total": round(total_direct, 2),
        "wrap_rates": wrap,
        "total_evaluated_price": round(total_loaded, 2),
    }


def sensitivity_analysis(opportunity_id):
    """Run +/-10% sensitivity on key rates."""
    conn = _get_db()
    row = conn.execute(
        "SELECT direct_labor_cost, fringe_rate, overhead_rate, g_and_a_rate, fee_rate, total_evaluated_price "
        "FROM pg_cost_volumes WHERE opportunity_id = %s ORDER BY created_at DESC LIMIT 1",
        (opportunity_id,),
    ).fetchone()
    conn.close()

    if not row:
        return {
            "status": "ok",
            "opportunity_id": opportunity_id,
            "message": "No cost volume found. Run --generate first.",
        }

    base_total = row["total_evaluated_price"] if isinstance(row, dict) else row[5]

    scenarios = []
    for var_name, var_pct in [("fringe", 0.10), ("overhead", 0.10), ("g_and_a", 0.10), ("direct_labor", 0.10)]:
        for direction in ["+10%", "-10%"]:
            mult = 1.10 if direction == "+10%" else 0.90
            if var_name == "direct_labor":
                new_total = base_total * mult
            else:
                # Approximate: the variable affects the final total proportionally
                rate_contribution = base_total * var_pct  # rough estimate
                delta = rate_contribution * (mult - 1.0)
                new_total = base_total + delta

            scenarios.append(
                {
                    "variable": var_name,
                    "change": direction,
                    "new_total": round(new_total, 2),
                    "delta": round(new_total - base_total, 2),
                    "delta_pct": round((new_total - base_total) / base_total * 100, 1) if base_total else 0,
                }
            )

    return {
        "status": "ok",
        "opportunity_id": opportunity_id,
        "base_total": round(base_total, 2),
        "scenarios": scenarios,
    }


def gate_evaluate(opportunity_id):
    """Gate evaluation for cost volume completeness."""
    conn = _get_db()
    row = conn.execute(
        "SELECT status, total_evaluated_price, direct_labor_cost "
        "FROM pg_cost_volumes WHERE opportunity_id = %s ORDER BY created_at DESC LIMIT 1",
        (opportunity_id,),
    ).fetchone()

    alloc_count = conn.execute(
        "SELECT COUNT(*) FROM pg_lcat_allocations WHERE cost_volume_id = %s",
        (opportunity_id,),
    ).fetchone()
    alloc_n = alloc_count[0] if alloc_count else 0
    conn.close()

    if not row:
        return {
            "status": "ok",
            "gate": "fail",
            "opportunity_id": opportunity_id,
            "message": "No cost volume exists",
            "blocking_issues": ["cost_volume_missing"],
        }

    cv_status = row["status"] if isinstance(row, dict) else row[0]
    total = row["total_evaluated_price"] if isinstance(row, dict) else row[1]

    blocking = []
    warnings = []

    if cv_status == "draft":
        warnings.append("cost_volume_in_draft")
    if not total or total <= 0:
        blocking.append("total_evaluated_price_zero")
    if alloc_n == 0:
        blocking.append("no_lcat_allocations")

    gate = "fail" if blocking else ("warn" if warnings else "pass")
    return {
        "status": "ok",
        "gate": gate,
        "opportunity_id": opportunity_id,
        "blocking_issues": blocking,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Rate Benchmarker — wrap rates, CALC+ benchmarking, PTW analysis")
    parser.add_argument("--opportunity-id", help="Opportunity UUID")

    group = parser.add_mutually_exclusive_group()
    group.add_argument("--wrap-rates", action="store_true", help="Calculate wrap rates")
    group.add_argument("--benchmark", action="store_true", help="Benchmark labor category rates")
    group.add_argument("--ptw", action="store_true", help="Price-to-Win analysis")
    group.add_argument("--generate", action="store_true", help="Generate cost volume")
    group.add_argument("--sensitivity", action="store_true", help="Sensitivity analysis")
    group.add_argument("--gate", action="store_true", help="Gate evaluation")
    group.add_argument("--store-benchmark", action="store_true", help="Store a rate benchmark")

    parser.add_argument("--direct-rate", type=float, help="Direct labor hourly rate")
    parser.add_argument("--fringe-pct", type=float, help="Fringe rate as decimal, e.g., 0.32")
    parser.add_argument("--overhead-pct", type=float, help="Overhead rate as decimal, e.g., 0.55")
    parser.add_argument("--ga-pct", type=float, help="General and administrative rate as decimal, e.g., 0.12")
    parser.add_argument("--fee-pct", type=float, help="Fee/profit rate as decimal, e.g., 0.08")
    parser.add_argument("--labor-category", help="Labor category name")
    parser.add_argument("--soc-code", help="BLS SOC code")
    parser.add_argument("--source", help="Data source (calc_plus, fpds, manual)")
    parser.add_argument("--hourly-rate", type=float, help="Hourly rate for benchmark")
    parser.add_argument("--year", type=int, help="Rate year")
    parser.add_argument("--contract-type", default="ffp", help="Contract type: ffp, t_and_m, cpff, cpaf, idiq")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable output")
    args = parser.parse_args()

    result = {}

    if args.wrap_rates:
        if args.direct_rate is None:
            print(json.dumps({"status": "error", "message": "Provide --direct-rate"}), file=sys.stderr)
            sys.exit(1)
        result = calculate_wrap_rates(
            args.direct_rate, args.fringe_pct, args.overhead_pct, args.ga_pct, args.fee_pct
        )

    elif args.benchmark:
        if not args.labor_category:
            result = {"status": "error", "message": "Provide --labor-category"}
        else:
            result = benchmark_rates(args.labor_category, args.soc_code)

    elif args.store_benchmark:
        if not all([args.labor_category, args.source, args.hourly_rate, args.year]):
            result = {"status": "error", "message": "Provide --labor-category, --source, --hourly-rate, --year"}
        else:
            result = store_benchmark(args.labor_category, args.soc_code, args.source, args.hourly_rate, args.year)

    elif args.ptw:
        if not args.opportunity_id:
            result = {"status": "error", "message": "Provide --opportunity-id"}
        else:
            result = ptw_analysis(args.opportunity_id)

    elif args.generate:
        if not args.opportunity_id:
            result = {"status": "error", "message": "Provide --opportunity-id"}
        else:
            result = generate_cost_volume(args.opportunity_id, args.contract_type)

    elif args.sensitivity:
        if not args.opportunity_id:
            result = {"status": "error", "message": "Provide --opportunity-id"}
        else:
            result = sensitivity_analysis(args.opportunity_id)

    elif args.gate:
        if not args.opportunity_id:
            result = {"status": "error", "message": "Provide --opportunity-id"}
        else:
            result = gate_evaluate(args.opportunity_id)
            if result.get("gate") == "fail":
                print(json.dumps(result, indent=2, default=str))
                sys.exit(1)

    else:
        parser.print_help()
        return

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()

# CUI // SP-CTI
