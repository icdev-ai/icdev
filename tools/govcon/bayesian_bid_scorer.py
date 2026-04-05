#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""Bayesian Bid Scorer -- information-gain weighted bid/no-bid scoring.

Applies Bayesian information-gain analysis to historical bid decisions to identify
which scoring dimensions carry the most predictive power.  Uses optimal ordering
to short-circuit assessment on clear no-bids.  Deterministic (air-gap safe).

DB tables: pg_bid_decisions (read), pg_win_loss_records (read),
           pg_info_gain_weights (write), audit_trail (write)

Usage:
    python tools/govcon/bayesian_bid_scorer.py --opportunity-id "opp-xxx" --compute-weights --json
    python tools/govcon/bayesian_bid_scorer.py --opportunity-id "opp-xxx" --optimal-order --json
    python tools/govcon/bayesian_bid_scorer.py --opportunity-id "opp-xxx" --score --dimensions '{"capability_fit":0.8}' --json
    python tools/govcon/bayesian_bid_scorer.py --opportunity-id "opp-xxx" --teaching-dims --json
    python tools/govcon/bayesian_bid_scorer.py --calibrate --decision-id "bd-xxx" --outcome win --json
"""

import argparse
import json
import math
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.db.storage import get_connection  # noqa: E402

# 6 scoring dimensions (matches existing Proposal Genesis R9)
DIMENSIONS = [
    "capability_fit",
    "past_performance",
    "competitive_position",
    "compliance_readiness",
    "resource_availability",
    "strategic_alignment",
]

# Static default weights (fallback when insufficient history)
DEFAULT_WEIGHTS = {
    "capability_fit": 0.25,
    "past_performance": 0.20,
    "competitive_position": 0.15,
    "compliance_readiness": 0.15,
    "resource_availability": 0.15,
    "strategic_alignment": 0.10,
}

MIN_HISTORY = 10  # minimum records for info-gain computation


def _now():
    return datetime.now(timezone.utc).isoformat()


def _gen_id(prefix="bbs"):
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _get_db():
    return get_connection()


def _audit(conn, event_type, action, details, opportunity_id=None):
    conn.execute(
        "INSERT INTO audit_trail (id, timestamp, event_type, actor, action, details, project_id, session_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (_gen_id("aud"), _now(), event_type, "bayesian_bid_scorer", action,
         json.dumps(details) if isinstance(details, dict) else str(details), opportunity_id, None),
    )


def _ensure_tables(conn):
    """Create info-gain weights table if needed."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pg_info_gain_weights (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            dimension TEXT NOT NULL,
            posterior_shift REAL NOT NULL,
            discriminability REAL NOT NULL,
            info_gain_weight REAL NOT NULL,
            sample_size INTEGER NOT NULL,
            computed_at TEXT NOT NULL
        )
    """)
    conn.commit()


def _entropy(p):
    """Shannon entropy for binary probability."""
    if p <= 0 or p >= 1:
        return 0.0
    return -(p * math.log2(p) + (1 - p) * math.log2(1 - p))


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def compute_info_gain_weights(project_id):
    """Analyze historical bid decisions to compute info-gain weights per dimension."""
    conn = _get_db()
    _ensure_tables(conn)

    # Get historical decisions with outcomes
    try:
        decisions = conn.execute(
            "SELECT id, opportunity_id, recommendation, confidence, scoring_dimensions "
            "FROM pg_bid_decisions WHERE project_id = ? OR ? IS NULL "
            "ORDER BY created_at DESC",
            (project_id, project_id),
        ).fetchall()
    except Exception:
        decisions = []

    try:
        outcomes = conn.execute(
            "SELECT bid_decision_id, outcome FROM pg_win_loss_records "
            "ORDER BY created_at DESC",
        ).fetchall()
    except Exception:
        outcomes = []

    # Build outcome map
    outcome_map = {}
    for o in outcomes:
        bid_id = o["bid_decision_id"] if isinstance(o, dict) else o[0]
        result = o["outcome"] if isinstance(o, dict) else o[1]
        outcome_map[bid_id] = result

    # Match decisions with outcomes
    matched = []
    for d in decisions:
        d_id = d["id"] if isinstance(d, dict) else d[0]
        dims_raw = d["scoring_dimensions"] if isinstance(d, dict) else d[4]
        if d_id in outcome_map and dims_raw:
            try:
                dims = json.loads(dims_raw) if isinstance(dims_raw, str) else dims_raw
                matched.append({"dimensions": dims, "outcome": outcome_map[d_id]})
            except (json.JSONDecodeError, TypeError):
                pass

    n = len(matched)

    if n < MIN_HISTORY:
        conn.close()
        return {
            "status": "ok",
            "method": "static_defaults",
            "message": f"Insufficient history ({n}/{MIN_HISTORY}). Using static default weights.",
            "weights": DEFAULT_WEIGHTS,
            "sample_size": n,
        }

    # Compute base win rate
    wins = sum(1 for m in matched if m["outcome"] == "win")
    base_win_rate = wins / n
    base_entropy = _entropy(base_win_rate)

    # Information gain per dimension
    dim_scores = {}
    for dim in DIMENSIONS:
        # Split on median of this dimension
        values = [m["dimensions"].get(dim, 0.5) for m in matched]
        median_val = sorted(values)[len(values) // 2]

        high_group = [m for m in matched if m["dimensions"].get(dim, 0.5) >= median_val]
        low_group = [m for m in matched if m["dimensions"].get(dim, 0.5) < median_val]

        if not high_group or not low_group:
            dim_scores[dim] = {"posterior_shift": 0.0, "discriminability": 0.0}
            continue

        # Win rates in each group
        high_win_rate = sum(1 for m in high_group if m["outcome"] == "win") / len(high_group)
        low_win_rate = sum(1 for m in low_group if m["outcome"] == "win") / len(low_group)

        # Posterior shift: difference in win rates
        posterior_shift = abs(high_win_rate - low_win_rate)

        # Discriminability: information gain (reduction in entropy)
        p_high = len(high_group) / n
        p_low = len(low_group) / n
        conditional_entropy = p_high * _entropy(high_win_rate) + p_low * _entropy(low_win_rate)
        discriminability = max(0, base_entropy - conditional_entropy)

        dim_scores[dim] = {
            "posterior_shift": round(posterior_shift, 4),
            "discriminability": round(discriminability, 4),
            "high_win_rate": round(high_win_rate, 3),
            "low_win_rate": round(low_win_rate, 3),
        }

    # Compute composite info-gain score per dimension
    # 4-dimension composite: posterior_shift(0.35) + discriminability(0.25) + diversity(0.20) + complexity_match(0.20)
    raw_weights = {}
    for dim in DIMENSIONS:
        ps = dim_scores[dim]["posterior_shift"]
        disc = dim_scores[dim]["discriminability"]
        # diversity and complexity_match approximate as uniform for now
        raw = 0.35 * ps + 0.25 * disc + 0.20 * 0.5 + 0.20 * 0.5
        raw_weights[dim] = max(0.01, raw)  # floor

    # Normalize to sum = 1.0
    total = sum(raw_weights.values())
    weights = {dim: round(w / total, 4) for dim, w in raw_weights.items()}

    # Store weights
    for dim, weight in weights.items():
        wid = _gen_id("igw")
        conn.execute(
            "INSERT INTO pg_info_gain_weights (id, project_id, dimension, posterior_shift, "
            "discriminability, info_gain_weight, sample_size, computed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (wid, project_id or "global", dim,
             dim_scores[dim]["posterior_shift"], dim_scores[dim]["discriminability"],
             weight, n, _now()),
        )

    _audit(conn, "bid_scoring.compute_weights", f"Computed info-gain weights from {n} decisions",
           {"weights": weights, "sample_size": n}, project_id)
    conn.commit()
    conn.close()

    return {
        "status": "ok",
        "method": "info_gain",
        "weights": weights,
        "dimension_scores": dim_scores,
        "sample_size": n,
        "base_win_rate": round(base_win_rate, 3),
    }


def optimal_assessment_order(project_id):
    """Return dimensions sorted by info-gain (most discriminative first)."""
    result = compute_info_gain_weights(project_id)
    weights = result.get("weights", DEFAULT_WEIGHTS)

    ordered = sorted(weights.items(), key=lambda x: x[1], reverse=True)

    # Early termination analysis
    cumulative = 0
    early_term_at = len(ordered)
    for i, (dim, weight) in enumerate(ordered):
        cumulative += weight
        if cumulative >= 0.70:  # 70% of discriminative power captured
            early_term_at = i + 1
            break

    return {
        "status": "ok",
        "ordered_dimensions": [{"rank": i + 1, "dimension": dim, "weight": w} for i, (dim, w) in enumerate(ordered)],
        "early_termination_at": early_term_at,
        "early_termination_coverage": round(cumulative, 3),
        "recommendation": f"Evaluate first {early_term_at} dimensions; skip rest if confidence >90%",
        "method": result.get("method", "static_defaults"),
        "sample_size": result.get("sample_size", 0),
    }


def score_opportunity(opportunity_id, dimensions):
    """Score an opportunity using info-gain weights."""
    conn = _get_db()
    _ensure_tables(conn)

    # Try to load latest info-gain weights
    rows = conn.execute(
        "SELECT dimension, info_gain_weight FROM pg_info_gain_weights "
        "ORDER BY computed_at DESC LIMIT 6",
    ).fetchall()
    conn.close()

    if len(rows) >= 6:
        weights = {r["dimension"] if isinstance(r, dict) else r[0]: r["info_gain_weight"] if isinstance(r, dict) else r[1] for r in rows}
        method = "info_gain"
    else:
        weights = DEFAULT_WEIGHTS.copy()
        method = "static_defaults"

    # Compute weighted score
    composite = 0.0
    contributions = {}
    for dim in DIMENSIONS:
        score = dimensions.get(dim, 0.5)
        weight = weights.get(dim, 1.0 / len(DIMENSIONS))
        contribution = score * weight
        composite += contribution
        contributions[dim] = {"score": score, "weight": round(weight, 4), "contribution": round(contribution, 4)}

    # Decision thresholds
    if composite >= 0.70:
        recommendation = "BID"
        confidence = min(0.95, 0.5 + composite)
    elif composite >= 0.45:
        recommendation = "REVIEW"
        confidence = 0.5
    else:
        recommendation = "NO_BID"
        confidence = min(0.95, 0.5 + (1.0 - composite))

    # Early termination check
    ordered = sorted(contributions.items(), key=lambda x: x[1]["weight"], reverse=True)
    top2_score = sum(item[1]["contribution"] for item in ordered[:2])
    top2_max = sum(item[1]["weight"] for item in ordered[:2])
    early_term = top2_score < top2_max * 0.3  # top 2 dimensions predict NO_BID

    return {
        "status": "ok",
        "opportunity_id": opportunity_id,
        "composite_score": round(composite, 4),
        "recommendation": recommendation,
        "confidence": round(confidence, 3),
        "method": method,
        "contributions": contributions,
        "early_termination": early_term,
        "early_termination_reason": "Top 2 dimensions predict NO_BID with high confidence" if early_term else None,
    }


def teaching_dimensions(project_id):
    """Return minimum distinguishing set that separates wins from losses."""
    result = compute_info_gain_weights(project_id)
    weights = result.get("weights", DEFAULT_WEIGHTS)
    dim_scores = result.get("dimension_scores", {})

    # Find dimensions with highest posterior shift
    teaching_set = []
    for dim in sorted(weights, key=weights.get, reverse=True):
        ps = dim_scores.get(dim, {}).get("posterior_shift", 0)
        high_wr = dim_scores.get(dim, {}).get("high_win_rate", 0.5)
        low_wr = dim_scores.get(dim, {}).get("low_win_rate", 0.5)

        if ps > 0.1:  # meaningful discriminator
            threshold = (high_wr + low_wr) / 2  # midpoint between groups
            teaching_set.append({
                "dimension": dim,
                "rule": f"{dim} > {threshold:.2f}",
                "posterior_shift": ps,
                "weight": weights[dim],
            })

        if len(teaching_set) >= 3:
            break

    if not teaching_set:
        # Fallback: use top 3 by weight
        for dim in sorted(weights, key=weights.get, reverse=True)[:3]:
            teaching_set.append({
                "dimension": dim,
                "rule": f"{dim} > 0.50",
                "posterior_shift": 0,
                "weight": weights[dim],
            })

    return {
        "status": "ok",
        "project_id": project_id,
        "teaching_set": teaching_set,
        "message": f"These {len(teaching_set)} attributes best separate wins from losses",
        "sample_size": result.get("sample_size", 0),
    }


def calibrate_from_outcome(bid_decision_id, outcome):
    """Record win/loss outcome for feedback loop."""
    conn = _get_db()
    _ensure_tables(conn)

    # Ensure win_loss table exists
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pg_win_loss_records (
            id TEXT PRIMARY KEY,
            bid_decision_id TEXT NOT NULL,
            outcome TEXT NOT NULL CHECK (outcome IN ('win', 'loss', 'no_decision', 'protest')),
            debrief_notes TEXT,
            recorded_at TEXT NOT NULL
        )
    """)

    wl_id = _gen_id("wl")
    conn.execute(
        "INSERT INTO pg_win_loss_records (id, bid_decision_id, outcome, debrief_notes, recorded_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (wl_id, bid_decision_id, outcome, None, _now()),
    )
    _audit(conn, "bid_scoring.calibrate", f"Recorded outcome: {outcome} for {bid_decision_id}",
           {"outcome": outcome}, None)
    conn.commit()
    conn.close()

    return {"status": "ok", "id": wl_id, "bid_decision_id": bid_decision_id, "outcome": outcome}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Bayesian Bid Scorer — info-gain weighted bid/no-bid scoring")
    parser.add_argument("--opportunity-id", help="Opportunity UUID")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--compute-weights", action="store_true", help="Compute info-gain weights from history")
    group.add_argument("--optimal-order", action="store_true", help="Optimal dimension assessment order")
    group.add_argument("--score", action="store_true", help="Score an opportunity")
    group.add_argument("--teaching-dims", action="store_true", help="Teaching dimension set")
    group.add_argument("--calibrate", action="store_true", help="Record win/loss outcome")

    parser.add_argument("--dimensions", help="JSON dict of dimension scores (0.0-1.0)")
    parser.add_argument("--decision-id", help="Bid decision ID for calibration")
    parser.add_argument("--outcome", choices=["win", "loss", "no_decision", "protest"], help="Outcome for calibration")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--human", action="store_true")
    args = parser.parse_args()

    project_id = args.opportunity_id
    result = {}

    if args.compute_weights:
        result = compute_info_gain_weights(project_id)
    elif args.optimal_order:
        result = optimal_assessment_order(project_id)
    elif args.score:
        dims = json.loads(args.dimensions) if args.dimensions else {d: 0.5 for d in DIMENSIONS}
        result = score_opportunity(args.opportunity_id, dims)
    elif args.teaching_dims:
        result = teaching_dimensions(project_id)
    elif args.calibrate:
        if not args.decision_id or not args.outcome:
            result = {"status": "error", "message": "Provide --decision-id and --outcome"}
        else:
            result = calibrate_from_outcome(args.decision_id, args.outcome)

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()

# CUI // SP-CTI
