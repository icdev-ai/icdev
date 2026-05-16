#!/usr/bin/env python3
# CUI // SP-PROPIN
# Controlled by: GovProposal Portal
# CUI Category: PROPIN
# Distribution: D
# POC: GovProposal System Administrator
"""Price-to-Win estimation engine for government proposals.

Analyzes pricing benchmarks, competitor win data, and LCAT rates to
produce competitive price estimates, labor rate analyses, win probability
curves, and comprehensive pricing intelligence summaries.

Usage:
    python tools/competitive/price_to_win.py --estimate --opp-id "OPP-123" --json
    python tools/competitive/price_to_win.py --estimate --opp-id "OPP-123" --strategy aggressive --json
    python tools/competitive/price_to_win.py --labor-rates --opp-id "OPP-123" --json
    python tools/competitive/price_to_win.py --win-probability --opp-id "OPP-123" --json
    python tools/competitive/price_to_win.py --win-probability --opp-id "OPP-123" --price-points 7 --json
    python tools/competitive/price_to_win.py --summary --opp-id "OPP-123" --json
    python tools/competitive/price_to_win.py --batch --status capture --json
"""

import json
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get(
    "GOVPROPOSAL_DB_PATH", str(BASE_DIR / "data" / "govproposal.db")
))

# Strategy modifiers: target percentile ranges within the competitive band.
STRATEGY_MODIFIERS = {
    "aggressive": {"low_pct": 0.25, "high_pct": 0.35, "label": "25th-35th percentile"},
    "balanced":   {"low_pct": 0.35, "high_pct": 0.50, "label": "35th-50th percentile"},
    "premium":    {"low_pct": 0.50, "high_pct": 0.65, "label": "50th-65th percentile"},
}

# Set-aside margin adjustments (small business typically competes on lower margins).
SET_ASIDE_ADJUSTMENTS = {
    "Total Small Business":           -0.05,
    "SBA":                            -0.05,
    "8(a)":                           -0.07,
    "HUBZone":                        -0.06,
    "WOSB":                           -0.05,
    "EDWOSB":                         -0.06,
    "SDVOSB":                         -0.05,
    "Service-Disabled Veteran-Owned": -0.05,
}

# Risk labels for win probability output.
RISK_LABELS = {
    (0.0, 0.20): "very_high",
    (0.20, 0.40): "high",
    (0.40, 0.60): "moderate",
    (0.60, 0.80): "low",
    (0.80, 1.01): "very_low",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now():
    """Return current UTC timestamp as ISO-8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get_db(db_path=None):
    """Open a database connection with WAL mode and foreign keys enabled."""
    path = str(db_path or DB_PATH)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _audit(conn, event_type, action, entity_type=None, entity_id=None,
           details=None):
    """Write an append-only audit trail record."""
    conn.execute(
        "INSERT INTO audit_trail (event_type, actor, action, entity_type, "
        "entity_id, details, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            event_type,
            "price_to_win",
            action,
            entity_type,
            entity_id,
            json.dumps(details) if details else None,
            _now(),
        ),
    )


def _row_to_dict(row):
    """Convert a sqlite3.Row to a plain dict."""
    if row is None:
        return None
    return dict(row)


def _parse_json_field(value):
    """Safely parse a JSON string field."""
    if value is None:
        return None
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return value


def _safe_divide(numerator, denominator, default=0.0):
    """Safely divide, returning default if denominator is zero."""
    if not denominator:
        return default
    return numerator / denominator


def _percentile(sorted_values, pct):
    """Compute a percentile from a pre-sorted list of numbers.

    Uses linear interpolation between the two nearest values.
    """
    if not sorted_values:
        return 0.0
    n = len(sorted_values)
    if n == 1:
        return sorted_values[0]
    k = (n - 1) * pct
    f = int(k)
    c = f + 1 if f + 1 < n else f
    d = k - f
    return sorted_values[f] + d * (sorted_values[c] - sorted_values[f])


def _risk_label(probability):
    """Map a win probability (0-1) to a risk label string."""
    for (lo, hi), label in RISK_LABELS.items():
        if lo <= probability < hi:
            return label
    return "unknown"


def _load_opportunity(conn, opp_id):
    """Load an opportunity row or raise ValueError."""
    row = conn.execute(
        "SELECT * FROM opportunities WHERE id = ?", (opp_id,)
    ).fetchone()
    if row is None:
        raise ValueError(f"Opportunity not found: {opp_id}")
    return _row_to_dict(row)


# ---------------------------------------------------------------------------
# Core Functions
# ---------------------------------------------------------------------------

def estimate_price_to_win(opp_id, strategy="balanced", db_path=None):
    """Estimate the price-to-win for an opportunity.

    Combines pricing benchmarks and competitor win history with a
    strategy modifier (aggressive / balanced / premium) to produce a
    recommended price range.

    Args:
        opp_id: Opportunity ID.
        strategy: One of 'aggressive', 'balanced', 'premium'.
        db_path: Optional database path override.

    Returns:
        dict with estimated_ptw, confidence_level, price_range,
        strategy_rationale, comparable_awards, benchmark_data.
    """
    if strategy not in STRATEGY_MODIFIERS:
        raise ValueError(
            f"Invalid strategy '{strategy}'. "
            f"Choose from: {', '.join(STRATEGY_MODIFIERS)}"
        )

    conn = _get_db(db_path)
    try:
        opp = _load_opportunity(conn, opp_id)
        naics = opp.get("naics_code")
        agency = opp.get("agency")
        set_aside = opp.get("set_aside_type")
        est_low = opp.get("estimated_value_low") or 0.0
        est_high = opp.get("estimated_value_high") or 0.0
        est_value = est_high if est_high else est_low

        # ------------------------------------------------------------------
        # 1. Pricing benchmarks for NAICS / agency
        # ------------------------------------------------------------------
        bench_query = "SELECT * FROM pricing_benchmarks WHERE naics_code = ?"
        bench_params = [naics] if naics else []
        if not naics:
            bench_query = "SELECT * FROM pricing_benchmarks WHERE 1=0"
        if naics and agency:
            bench_query += " AND (agency = ? OR agency IS NULL)"
            bench_params.append(agency)

        bench_rows = conn.execute(bench_query, bench_params).fetchall()
        benchmarks = [_row_to_dict(r) for r in bench_rows]

        bench_p25 = [b["percentile_25"] for b in benchmarks if b.get("percentile_25")]
        bench_med = [b["median_rate"] for b in benchmarks if b.get("median_rate")]
        bench_p75 = [b["percentile_75"] for b in benchmarks if b.get("percentile_75")]

        # ------------------------------------------------------------------
        # 2. Comparable competitor awards
        # ------------------------------------------------------------------
        wins_query = (
            "SELECT * FROM competitor_wins WHERE 1=1 "
        )
        wins_params = []
        if naics:
            wins_query += "AND naics_code = ? "
            wins_params.append(naics)
        if agency:
            wins_query += "AND agency = ? "
            wins_params.append(agency)
        wins_query += "ORDER BY award_date DESC LIMIT 50"

        win_rows = conn.execute(wins_query, wins_params).fetchall()
        comparable_awards = [_row_to_dict(w) for w in win_rows]

        award_amounts = sorted(
            w["award_amount"] for w in comparable_awards
            if w.get("award_amount") and w["award_amount"] > 0
        )

        # ------------------------------------------------------------------
        # 3. Compute competitive range
        # ------------------------------------------------------------------
        if award_amounts:
            comp_floor = _percentile(award_amounts, 0.25)
            comp_median = _percentile(award_amounts, 0.50)
            comp_ceiling = _percentile(award_amounts, 0.75)
        elif est_value > 0:
            # Fall back to estimate-based range when no award data exists.
            comp_floor = est_value * 0.80
            comp_median = est_value
            comp_ceiling = est_value * 1.20
        else:
            comp_floor = comp_median = comp_ceiling = 0.0

        # ------------------------------------------------------------------
        # 4. Apply strategy modifier
        # ------------------------------------------------------------------
        mod = STRATEGY_MODIFIERS[strategy]
        band = comp_ceiling - comp_floor if comp_ceiling > comp_floor else 0.0
        ptw_low = comp_floor + band * mod["low_pct"]
        ptw_high = comp_floor + band * mod["high_pct"]
        ptw_point = (ptw_low + ptw_high) / 2.0

        # ------------------------------------------------------------------
        # 5. Set-aside adjustment
        # ------------------------------------------------------------------
        sa_adj = SET_ASIDE_ADJUSTMENTS.get(set_aside, 0.0)
        if sa_adj:
            ptw_point *= (1.0 + sa_adj)
            ptw_low *= (1.0 + sa_adj)
            ptw_high *= (1.0 + sa_adj)

        # ------------------------------------------------------------------
        # 6. Confidence
        # ------------------------------------------------------------------
        data_points = len(award_amounts) + len(benchmarks)
        if data_points >= 10:
            confidence = "high"
        elif data_points >= 3:
            confidence = "medium"
        elif data_points >= 1:
            confidence = "low"
        else:
            confidence = "insufficient_data"

        rationale_parts = [
            f"Strategy: {strategy} ({mod['label']})",
            f"Data points: {len(award_amounts)} awards, {len(benchmarks)} benchmarks",
        ]
        if sa_adj:
            rationale_parts.append(
                f"Set-aside adjustment: {sa_adj:+.0%} for {set_aside}"
            )
        if not award_amounts and est_value > 0:
            rationale_parts.append(
                "No comparable awards found; range derived from government estimate"
            )

        result = {
            "opportunity_id": opp_id,
            "opportunity_title": opp.get("title"),
            "naics_code": naics,
            "agency": agency,
            "strategy": strategy,
            "estimated_ptw": round(ptw_point, 2),
            "confidence_level": confidence,
            "price_range": {
                "low": round(ptw_low, 2),
                "high": round(ptw_high, 2),
                "competitive_floor": round(comp_floor, 2),
                "competitive_median": round(comp_median, 2),
                "competitive_ceiling": round(comp_ceiling, 2),
            },
            "government_estimate": {
                "low": est_low,
                "high": est_high,
            },
            "set_aside_type": set_aside,
            "strategy_rationale": "; ".join(rationale_parts),
            "comparable_awards_count": len(award_amounts),
            "comparable_awards": comparable_awards[:10],
            "benchmark_data": benchmarks[:10],
            "generated_at": _now(),
        }

        _audit(
            conn, "ptw.estimate",
            f"Price-to-win estimate for {opp.get('title', opp_id)}",
            "opportunity", opp_id,
            {
                "strategy": strategy,
                "estimated_ptw": round(ptw_point, 2),
                "confidence": confidence,
                "data_points": data_points,
            },
        )
        conn.commit()
        return result
    finally:
        conn.close()


def labor_rate_analysis(opp_id, db_path=None):
    """Analyze labor rates for an opportunity against market benchmarks.

    Compares internal LCAT rates (from lcat_rates + lcats tables) to
    pricing benchmarks for the same NAICS/agency and classifies each
    labor category as above/below/competitive relative to the market.

    Args:
        opp_id: Opportunity ID.
        db_path: Optional database path override.

    Returns:
        dict with rate comparisons, blended rate, and competitive
        position assessment.
    """
    conn = _get_db(db_path)
    try:
        opp = _load_opportunity(conn, opp_id)
        naics = opp.get("naics_code")
        agency = opp.get("agency")

        # Pull internal LCAT rates (most recent per LCAT).
        lcat_rows = conn.execute(
            "SELECT lr.*, l.lcat_name, l.lcat_code, l.naics_code AS lcat_naics "
            "FROM lcat_rates lr "
            "JOIN lcats l ON lr.lcat_id = l.id "
            "WHERE lr.end_date IS NULL OR lr.end_date > date('now') "
            "ORDER BY l.lcat_name, lr.effective_date DESC"
        ).fetchall()

        # De-duplicate: keep the most recent rate per LCAT.
        seen_lcats = set()
        lcat_data = []
        for row in lcat_rows:
            d = _row_to_dict(row)
            if d["lcat_id"] not in seen_lcats:
                seen_lcats.add(d["lcat_id"])
                lcat_data.append(d)

        # Pull market benchmarks for matching NAICS/agency.
        bench_by_labor = defaultdict(list)
        if naics:
            bench_query = (
                "SELECT * FROM pricing_benchmarks "
                "WHERE naics_code = ? AND labor_category IS NOT NULL"
            )
            bench_params = [naics]
            if agency:
                bench_query += " AND (agency = ? OR agency IS NULL)"
                bench_params.append(agency)
            for row in conn.execute(bench_query, bench_params).fetchall():
                b = _row_to_dict(row)
                bench_by_labor[b["labor_category"].lower()].append(b)

        rates_above = []
        rates_below = []
        rates_competitive = []
        rate_comparisons = []
        total_wrap = 0.0

        for lcat in lcat_data:
            base = lcat.get("direct_labor_rate") or 0.0
            fringe = lcat.get("fringe_rate") or 0.0
            overhead = lcat.get("overhead_rate") or 0.0
            ga = lcat.get("ga_rate") or 0.0
            fee = lcat.get("fee_rate") or 0.0
            wrap = lcat.get("wrap_rate") or (
                base * (1 + fringe) * (1 + overhead) * (1 + ga) * (1 + fee)
            )
            total_wrap += wrap

            name = lcat.get("lcat_name", "")
            market_matches = bench_by_labor.get(name.lower(), [])
            market_avg = (
                _safe_divide(
                    sum(m.get("average_rate", 0) or 0 for m in market_matches),
                    len(market_matches),
                )
                if market_matches else None
            )
            market_median = (
                _safe_divide(
                    sum(m.get("median_rate", 0) or 0 for m in market_matches),
                    len(market_matches),
                )
                if market_matches else None
            )

            comparison_ref = market_median or market_avg
            if comparison_ref and comparison_ref > 0:
                delta_pct = ((wrap - comparison_ref) / comparison_ref) * 100
                if delta_pct > 10:
                    position = "above_market"
                    rates_above.append(name)
                elif delta_pct < -10:
                    position = "below_market"
                    rates_below.append(name)
                else:
                    position = "competitive"
                    rates_competitive.append(name)
            else:
                position = "no_benchmark"
                delta_pct = None

            rate_comparisons.append({
                "lcat_name": name,
                "lcat_code": lcat.get("lcat_code"),
                "our_base_rate": round(base, 2),
                "our_wrap_rate": round(wrap, 2),
                "market_average": round(market_avg, 2) if market_avg else None,
                "market_median": round(market_median, 2) if market_median else None,
                "delta_pct": round(delta_pct, 1) if delta_pct is not None else None,
                "position": position,
            })

        blended = _safe_divide(total_wrap, len(lcat_data)) if lcat_data else 0.0

        total_compared = len(rates_above) + len(rates_below) + len(rates_competitive)
        if total_compared == 0:
            overall = "no_benchmark_data"
        elif len(rates_above) > total_compared * 0.5:
            overall = "above_market"
        elif len(rates_below) > total_compared * 0.5:
            overall = "below_market"
        else:
            overall = "competitive"

        result = {
            "opportunity_id": opp_id,
            "naics_code": naics,
            "agency": agency,
            "lcat_count": len(lcat_data),
            "blended_wrap_rate": round(blended, 2),
            "competitive_position": overall,
            "rates_above_market": rates_above,
            "rates_below_market": rates_below,
            "rates_competitive": rates_competitive,
            "rate_comparisons": rate_comparisons,
            "generated_at": _now(),
        }

        _audit(
            conn, "ptw.labor_rates",
            f"Labor rate analysis for {opp.get('title', opp_id)}",
            "opportunity", opp_id,
            {"lcat_count": len(lcat_data), "position": overall},
        )
        conn.commit()
        return result
    finally:
        conn.close()


def win_probability_vs_price(opp_id, price_points=5, db_path=None):
    """Model win probability at multiple price points.

    Uses historical competitor award distribution to estimate the
    probability of winning at each price level.  Non-price factors
    (technical evaluation weight from section_m_parsed) shift the
    curve so that a stronger technical approach allows higher pricing.

    Args:
        opp_id: Opportunity ID.
        price_points: Number of price points to model (default 5).
        db_path: Optional database path override.

    Returns:
        dict with price_points array and evaluation context.
    """
    conn = _get_db(db_path)
    try:
        opp = _load_opportunity(conn, opp_id)
        naics = opp.get("naics_code")
        agency = opp.get("agency")

        # Collect competitor award amounts.
        wins_query = "SELECT award_amount FROM competitor_wins WHERE 1=1 "
        wins_params = []
        if naics:
            wins_query += "AND naics_code = ? "
            wins_params.append(naics)
        if agency:
            wins_query += "AND agency = ? "
            wins_params.append(agency)

        amounts = sorted(
            r["award_amount"]
            for r in conn.execute(wins_query, wins_params).fetchall()
            if r["award_amount"] and r["award_amount"] > 0
        )

        # Determine non-price weight from Section M (evaluation criteria).
        proposal_row = conn.execute(
            "SELECT section_m_parsed FROM proposals "
            "WHERE opportunity_id = ? ORDER BY version DESC LIMIT 1",
            (opp_id,),
        ).fetchone()

        non_price_weight = 0.50  # default: equal price/non-price
        if proposal_row and proposal_row["section_m_parsed"]:
            sec_m = _parse_json_field(proposal_row["section_m_parsed"])
            if isinstance(sec_m, dict):
                # Look for explicit price weight or technical weight.
                pw = sec_m.get("price_weight") or sec_m.get("cost_weight")
                if isinstance(pw, (int, float)) and 0 < pw <= 1:
                    non_price_weight = 1.0 - pw

        # Build price point range.
        if amounts:
            p_min = _percentile(amounts, 0.10)
            p_max = _percentile(amounts, 0.90)
        else:
            est = opp.get("estimated_value_high") or opp.get("estimated_value_low") or 0
            if est > 0:
                p_min = est * 0.70
                p_max = est * 1.30
            else:
                return {
                    "opportunity_id": opp_id,
                    "status": "insufficient_data",
                    "message": "No competitor awards or government estimate available",
                    "price_points": [],
                }

        step = (p_max - p_min) / max(price_points - 1, 1)
        points = []
        for i in range(price_points):
            price = p_min + step * i

            # Price-based probability: fraction of competitor awards above
            # this price (i.e., we would undercut that fraction).
            if amounts:
                undercut_count = sum(1 for a in amounts if a > price)
                price_prob = _safe_divide(undercut_count, len(amounts))
            else:
                # Estimate-based linear model.
                midpoint = (p_min + p_max) / 2.0
                price_prob = max(0.0, min(1.0, 0.5 + (midpoint - price) / (p_max - p_min)))

            # Blend price and non-price factors.
            # Assume our non-price score is moderately competitive (0.65).
            non_price_score = 0.65
            blended_prob = (
                (1 - non_price_weight) * price_prob
                + non_price_weight * non_price_score
            )
            blended_prob = max(0.0, min(1.0, blended_prob))

            # Margin estimate (relative to competitive median).
            median_ref = _percentile(amounts, 0.50) if amounts else ((p_min + p_max) / 2)
            margin_pct = _safe_divide(price - median_ref, median_ref) * 100 if median_ref else 0.0

            points.append({
                "price": round(price, 2),
                "win_probability": round(blended_prob, 3),
                "margin_estimate_pct": round(margin_pct, 1),
                "risk_level": _risk_label(blended_prob),
            })

        result = {
            "opportunity_id": opp_id,
            "naics_code": naics,
            "agency": agency,
            "non_price_weight": round(non_price_weight, 2),
            "comparable_awards_used": len(amounts),
            "price_points": points,
            "generated_at": _now(),
        }

        _audit(
            conn, "ptw.win_probability",
            f"Win probability curve for {opp.get('title', opp_id)}",
            "opportunity", opp_id,
            {"price_points": price_points, "awards_used": len(amounts)},
        )
        conn.commit()
        return result
    finally:
        conn.close()


def competitive_pricing_summary(opp_id, db_path=None):
    """Produce a comprehensive pricing intelligence summary.

    Combines the PTW estimate, labor rate analysis, win probability
    curve, competitor pricing patterns, and agency context into a
    single actionable recommendation.

    Args:
        opp_id: Opportunity ID.
        db_path: Optional database path override.

    Returns:
        dict with all analyses and a consolidated recommendation.
    """
    conn = _get_db(db_path)
    try:
        opp = _load_opportunity(conn, opp_id)
    finally:
        conn.close()

    # Run sub-analyses (each manages its own connection).
    ptw = estimate_price_to_win(opp_id, strategy="balanced", db_path=db_path)
    labor = labor_rate_analysis(opp_id, db_path=db_path)
    win_prob = win_probability_vs_price(opp_id, price_points=5, db_path=db_path)

    # Competitor pricing patterns for this NAICS/agency.
    conn = _get_db(db_path)
    try:
        naics = opp.get("naics_code")
        agency = opp.get("agency")

        comp_patterns = []
        if naics:
            pattern_rows = conn.execute(
                "SELECT competitor_name, "
                "COUNT(*) AS win_count, "
                "AVG(award_amount) AS avg_amount, "
                "MIN(award_amount) AS min_amount, "
                "MAX(award_amount) AS max_amount "
                "FROM competitor_wins "
                "WHERE naics_code = ? AND award_amount > 0 "
                "GROUP BY competitor_name "
                "ORDER BY win_count DESC LIMIT 10",
                (naics,),
            ).fetchall()
            comp_patterns = [_row_to_dict(r) for r in pattern_rows]
            for cp in comp_patterns:
                for k in ("avg_amount", "min_amount", "max_amount"):
                    if cp.get(k) is not None:
                        cp[k] = round(cp[k], 2)

        # Build recommendation.
        confidence = ptw.get("confidence_level", "insufficient_data")
        estimated = ptw.get("estimated_ptw", 0)
        labor_pos = labor.get("competitive_position", "unknown")

        if confidence in ("high", "medium") and labor_pos == "competitive":
            rec_text = (
                f"Recommend bidding near ${estimated:,.0f} (balanced strategy). "
                f"Labor rates are market-competitive. Confidence: {confidence}."
            )
            rec_score = 0.75 if confidence == "high" else 0.55
        elif confidence in ("high", "medium") and labor_pos == "above_market":
            rec_text = (
                f"Estimated PTW is ${estimated:,.0f} but labor rates are "
                f"above market. Consider rate reductions or value justification."
            )
            rec_score = 0.45
        elif confidence in ("high", "medium") and labor_pos == "below_market":
            rec_text = (
                f"Estimated PTW is ${estimated:,.0f}. Below-market labor rates "
                f"provide pricing advantage; consider premium strategy."
            )
            rec_score = 0.70
        else:
            rec_text = (
                "Limited pricing intelligence available. Gather more "
                "benchmark data before finalizing pricing."
            )
            rec_score = 0.30

        result = {
            "opportunity_id": opp_id,
            "opportunity_title": opp.get("title"),
            "price_to_win": ptw,
            "labor_rate_analysis": labor,
            "win_probability_curve": win_prob,
            "competitor_pricing_patterns": comp_patterns,
            "recommendation": {
                "text": rec_text,
                "confidence_score": round(rec_score, 2),
                "recommended_price": estimated,
                "labor_position": labor_pos,
            },
            "generated_at": _now(),
        }

        _audit(
            conn, "ptw.summary",
            f"Pricing summary for {opp.get('title', opp_id)}",
            "opportunity", opp_id,
            {"confidence_score": round(rec_score, 2)},
        )
        conn.commit()
        return result
    finally:
        conn.close()


def batch_ptw(status="capture", db_path=None):
    """Run price-to-win estimation for all opportunities at a pipeline stage.

    Args:
        status: Opportunity pipeline status to filter on (default 'capture').
        db_path: Optional database path override.

    Returns:
        dict with batch results array and summary statistics.
    """
    conn = _get_db(db_path)
    try:
        rows = conn.execute(
            "SELECT id, title FROM opportunities WHERE status = ?",
            (status,),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        # Also try filtering proposals by status, since opportunities may
        # not have a status column in all schemas.
        conn = _get_db(db_path)
        try:
            rows = conn.execute(
                "SELECT DISTINCT o.id, o.title "
                "FROM opportunities o "
                "JOIN proposals p ON p.opportunity_id = o.id "
                "WHERE p.status = ?",
                (status,),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        finally:
            conn.close()

    results = []
    errors = []
    for row in rows:
        opp_id = row["id"]
        try:
            ptw = estimate_price_to_win(opp_id, strategy="balanced", db_path=db_path)
            results.append({
                "opportunity_id": opp_id,
                "title": row["title"],
                "estimated_ptw": ptw.get("estimated_ptw"),
                "confidence_level": ptw.get("confidence_level"),
                "price_range": ptw.get("price_range"),
            })
        except Exception as exc:
            errors.append({"opportunity_id": opp_id, "error": str(exc)})

    return {
        "status_filter": status,
        "total_opportunities": len(rows),
        "estimated": len(results),
        "errors": len(errors),
        "results": results,
        "error_details": errors,
        "generated_at": _now(),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser():
    """Build the argument parser for the CLI."""
    import argparse
    parser = argparse.ArgumentParser(
        description="GovProposal Price-to-Win Estimation Engine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s --estimate --opp-id OPP-123 --json\n"
            "  %(prog)s --estimate --opp-id OPP-123 --strategy aggressive --json\n"
            "  %(prog)s --labor-rates --opp-id OPP-123 --json\n"
            "  %(prog)s --win-probability --opp-id OPP-123 --price-points 7 --json\n"
            "  %(prog)s --summary --opp-id OPP-123 --json\n"
            "  %(prog)s --batch --status capture --json\n"
        ),
    )

    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--estimate", action="store_true",
                        help="Estimate price-to-win for an opportunity")
    action.add_argument("--labor-rates", action="store_true",
                        help="Analyze labor rates vs market benchmarks")
    action.add_argument("--win-probability", action="store_true",
                        help="Model win probability at different price points")
    action.add_argument("--summary", action="store_true",
                        help="Comprehensive pricing intelligence summary")
    action.add_argument("--batch", action="store_true",
                        help="Batch PTW for all opportunities at a pipeline stage")

    parser.add_argument("--opp-id", help="Opportunity ID")
    parser.add_argument(
        "--strategy", choices=["aggressive", "balanced", "premium"],
        default="balanced", help="Pricing strategy (default: balanced)",
    )
    parser.add_argument(
        "--price-points", type=int, default=5,
        help="Number of price points for win probability (default: 5)",
    )
    parser.add_argument(
        "--status", default="capture",
        help="Pipeline status filter for --batch (default: capture)",
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--db-path", help="Override database path")

    return parser


def main():
    """CLI entry point."""
    import argparse  # noqa: F811
    parser = _build_parser()
    args = parser.parse_args()
    db = args.db_path

    try:
        if args.estimate:
            if not args.opp_id:
                parser.error("--estimate requires --opp-id")
            result = estimate_price_to_win(
                args.opp_id, strategy=args.strategy, db_path=db,
            )

        elif args.labor_rates:
            if not args.opp_id:
                parser.error("--labor-rates requires --opp-id")
            result = labor_rate_analysis(args.opp_id, db_path=db)

        elif args.win_probability:
            if not args.opp_id:
                parser.error("--win-probability requires --opp-id")
            result = win_probability_vs_price(
                args.opp_id, price_points=args.price_points, db_path=db,
            )

        elif args.summary:
            if not args.opp_id:
                parser.error("--summary requires --opp-id")
            result = competitive_pricing_summary(args.opp_id, db_path=db)

        elif args.batch:
            result = batch_ptw(status=args.status, db_path=db)

        # Output
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            if isinstance(result, dict):
                for key, value in result.items():
                    if isinstance(value, (dict, list)):
                        print(f"  {key}: {json.dumps(value, default=str)}")
                    else:
                        print(f"  {key}: {value}")

    except ValueError as exc:
        if args.json:
            print(json.dumps({"error": str(exc)}, indent=2))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except sqlite3.Error as exc:
        if args.json:
            print(json.dumps({"error": f"Database error: {exc}"}, indent=2))
        else:
            print(f"Database error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
