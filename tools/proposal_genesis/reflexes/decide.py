#!/usr/bin/env python3
# CUI // SP-CTI
"""R9: Decide Reflex — Bid/no-bid scoring + win probability estimation.

Evaluates tracked opportunities using a 6-dimension deterministic weighted
average to produce a go/no-go recommendation and win probability estimate.

Pipeline: on_demand (triggered after Map or manually).
GREEN tier (read-only analysis, writes only to pg_bid_decisions).
Scanner-tier LLM only (zero Claude tokens — fully deterministic).
"""

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _generate_id(prefix: str = "pg") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


# ---------------------------------------------------------------------------
# Scoring dimensions and weights (D-PG-9 feedback loop)
# ---------------------------------------------------------------------------

SCORE_WEIGHTS = {
    "capability_fit": 0.25,
    "past_performance": 0.20,
    "competitive_position": 0.15,
    "compliance_readiness": 0.15,
    "resource_availability": 0.15,
    "strategic_alignment": 0.10,
}

# Decision thresholds
BID_THRESHOLD = 0.60       # >= 0.60 → bid
NO_BID_THRESHOLD = 0.35    # < 0.35 → no_bid
# 0.35 - 0.59 → deferred (needs further evaluation)


# ---------------------------------------------------------------------------
# Calibration feedback loop (D-PG-9)
# ---------------------------------------------------------------------------

def _get_calibrated_weights() -> Dict[str, float]:
    """Load calibrated weights from DB, falling back to defaults.

    Calibration computes dimension averages for won vs lost opportunities,
    then adjusts weights toward dimensions that better predict wins.
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT value FROM pg_proposal_genesis_config "
            "WHERE key = 'calibrated_score_weights'"
        ).fetchone()
        if row:
            import json as _json
            calibrated = _json.loads(row["value"])
            # Validate keys match
            if set(calibrated.keys()) == set(SCORE_WEIGHTS.keys()):
                return calibrated
    except Exception:
        pass
    finally:
        conn.close()
    return dict(SCORE_WEIGHTS)


def calibrate_weights() -> Dict[str, Any]:
    """Recalibrate scoring weights using bid decision outcomes.

    Queries pg_bid_decision_outcomes joined to pg_bid_decisions to find
    won/lost outcomes. For each dimension, computes the average score
    for won vs lost. Dimensions where won scores are significantly higher
    than lost scores get increased weight; dimensions where the gap is
    small get decreased weight.

    Weights are normalized to sum to 1.0 and clamped to [0.05, 0.40].
    Stored in pg_proposal_genesis_config for use in future scoring.

    Returns calibration summary dict.
    """
    conn = get_connection()
    try:
        # Get outcomes joined to bid decisions with score breakdowns
        rows = conn.execute("""
            SELECT bdo.outcome, bd.score_breakdown
            FROM pg_bid_decision_outcomes bdo
            JOIN pg_bid_decisions bd ON bd.id = bdo.bid_decision_id
            WHERE bdo.outcome IN ('won', 'lost')
            AND bd.score_breakdown IS NOT NULL
        """).fetchall()
    except Exception:
        return {"calibrated": False, "reason": "query_failed", "outcomes": 0}
    finally:
        conn.close()

    if len(rows) < 5:
        return {
            "calibrated": False,
            "reason": "insufficient_outcomes",
            "outcomes": len(rows),
            "minimum_required": 5,
        }

    # Aggregate dimension scores by outcome
    won_scores = {k: [] for k in SCORE_WEIGHTS}
    lost_scores = {k: [] for k in SCORE_WEIGHTS}

    for row in rows:
        try:
            import json as _json
            breakdown = _json.loads(row["score_breakdown"])
        except (TypeError, ValueError):
            continue

        target = won_scores if row["outcome"] == "won" else lost_scores
        for dim in SCORE_WEIGHTS:
            if dim in breakdown:
                target[dim].append(breakdown[dim])

    if not any(won_scores[k] for k in won_scores):
        return {"calibrated": False, "reason": "no_won_outcomes", "outcomes": len(rows)}

    # Compute discriminative power per dimension
    # Higher gap = dimension better predicts wins → higher weight
    gaps = {}
    for dim in SCORE_WEIGHTS:
        won_avg = sum(won_scores[dim]) / len(won_scores[dim]) if won_scores[dim] else 0.5
        lost_avg = sum(lost_scores[dim]) / len(lost_scores[dim]) if lost_scores[dim] else 0.5
        gap = max(0.01, won_avg - lost_avg + 0.5)  # shift to positive range
        gaps[dim] = gap

    # Normalize gaps to produce new weights (sum to 1.0)
    total_gap = sum(gaps.values())
    raw_weights = {k: gaps[k] / total_gap for k in gaps}

    # Clamp to [0.05, 0.40] and re-normalize
    clamped = {k: max(0.05, min(0.40, v)) for k, v in raw_weights.items()}
    clamp_total = sum(clamped.values())
    new_weights = {k: round(v / clamp_total, 4) for k, v in clamped.items()}

    # Store calibrated weights
    conn = get_connection()
    try:
        import json as _json
        now = _utcnow_iso()
        conn.execute(
            "INSERT OR REPLACE INTO pg_proposal_genesis_config "
            "(key, value, updated_at) VALUES (?, ?, ?)",
            ("calibrated_score_weights", _json.dumps(new_weights), now),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()

    return {
        "calibrated": True,
        "outcomes_used": len(rows),
        "won_count": sum(1 for r in rows if r["outcome"] == "won"),
        "lost_count": sum(1 for r in rows if r["outcome"] == "lost"),
        "original_weights": dict(SCORE_WEIGHTS),
        "calibrated_weights": new_weights,
        "dimension_gaps": {k: round(v, 4) for k, v in gaps.items()},
    }


# ---------------------------------------------------------------------------
# Gather opportunity data for scoring
# ---------------------------------------------------------------------------

def _get_undecided_opportunities() -> List[Dict]:
    """Find tracked opportunities without a bid decision."""
    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT o.id, o.title, o.agency, o.naics_code, o.set_aside,
                   o.response_deadline, o.estimated_value, o.status,
                   o.sol_number
            FROM sam_gov_opportunities o
            WHERE o.status IN ('tracked', 'qualifying', 'active')
            AND o.id NOT IN (
                SELECT opportunity_id FROM pg_bid_decisions
                WHERE decision IN ('bid', 'no_bid')
            )
            ORDER BY o.response_deadline ASC
        """).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        conn.close()


def _get_capability_coverage(opportunity_id: str) -> float:
    """Get capability mapping coverage score for an opportunity."""
    conn = get_connection()
    try:
        row = conn.execute("""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN capability_match IS NOT NULL
                        AND capability_match != '' THEN 1 ELSE 0 END) as matched
            FROM rfp_shall_statements
            WHERE opportunity_id = ?
        """, (opportunity_id,)).fetchone()
        if not row or row["total"] == 0:
            return 0.0
        return min(1.0, row["matched"] / row["total"])
    except Exception:
        return 0.0
    finally:
        conn.close()


def _get_quality_score_avg(opportunity_id: str) -> float:
    """Get average quality score for drafts on this opportunity."""
    conn = get_connection()
    try:
        row = conn.execute("""
            SELECT AVG(composite_score) as avg_score
            FROM pg_proposal_quality_scores
            WHERE opportunity_id = ?
            AND composite_score IS NOT NULL
        """, (opportunity_id,)).fetchone()
        if not row or row["avg_score"] is None:
            return 0.0
        return min(1.0, row["avg_score"] / 100.0)
    except Exception:
        return 0.0
    finally:
        conn.close()


def _get_capture_plan_status(opportunity_id: str) -> Optional[Dict]:
    """Get capture plan data for strategic scoring."""
    conn = get_connection()
    try:
        row = conn.execute("""
            SELECT status, win_strategy, teaming_strategy
            FROM pg_capture_plans
            WHERE opportunity_id = ?
            ORDER BY updated_at DESC LIMIT 1
        """, (opportunity_id,)).fetchone()
        return dict(row) if row else None
    except Exception:
        return None
    finally:
        conn.close()


def _get_engagement_score(opportunity_id: str) -> float:
    """Get CRM engagement score for the agency tied to this opportunity."""
    conn = get_connection()
    try:
        # Get agency from opportunity, then find engagement score
        row = conn.execute("""
            SELECT es.composite_score
            FROM pg_crm_engagement_scores es
            JOIN pg_crm_accounts a ON a.id = es.account_id
            JOIN sam_gov_opportunities o ON LOWER(o.agency) = LOWER(a.agency)
            WHERE o.id = ?
            ORDER BY es.created_at DESC LIMIT 1
        """, (opportunity_id,)).fetchone()
        return row["composite_score"] / 100.0 if row else 0.0
    except Exception:
        return 0.0
    finally:
        conn.close()


def _get_teaming_fit(opportunity_id: str) -> float:
    """Get best teaming partner fit score."""
    conn = get_connection()
    try:
        row = conn.execute("""
            SELECT MAX(fit_score) as best_fit
            FROM pg_teaming_assessments
            WHERE opportunity_id = ?
        """, (opportunity_id,)).fetchone()
        return row["best_fit"] / 100.0 if row and row["best_fit"] else 0.0
    except Exception:
        return 0.0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Score computation
# ---------------------------------------------------------------------------

def score_opportunity(opp: Dict) -> Dict:
    """Score an opportunity across 6 dimensions.

    Returns dict with dimension scores, composite score, decision, rationale.
    """
    opp_id = opp.get("id", "")

    # Dimension 1: Capability Fit (from R6 Map coverage)
    cap_fit = _get_capability_coverage(opp_id)

    # Dimension 2: Past Performance (proxy: quality of existing drafts)
    past_perf = _get_quality_score_avg(opp_id)

    # Dimension 3: Competitive Position (from teaming + engagement)
    teaming = _get_teaming_fit(opp_id)
    engagement = _get_engagement_score(opp_id)
    competitive = min(1.0, (teaming * 0.6 + engagement * 0.4))

    # Dimension 4: Compliance Readiness (set-aside match + NAICS familiarity)
    compliance = 0.5  # baseline
    set_aside = (opp.get("set_aside") or "").lower()
    if set_aside in ("total small business", "8(a)", "hubzone",
                     "sdvosb", "wosb", "edwosb"):
        compliance += 0.2  # small biz set-asides = compliance advantage
    naics = opp.get("naics_code") or ""
    if naics.startswith("5415") or naics.startswith("5112"):
        compliance += 0.15  # core IT NAICS
    compliance = min(1.0, compliance)

    # Dimension 5: Resource Availability (proxy: capture plan exists)
    plan = _get_capture_plan_status(opp_id)
    resource = 0.3  # baseline
    if plan:
        resource = 0.6
        if plan.get("win_strategy"):
            resource += 0.2
        if plan.get("teaming_strategy"):
            resource += 0.1
    resource = min(1.0, resource)

    # Dimension 6: Strategic Alignment (estimated value + agency engagement)
    strategic = 0.5  # baseline
    est_value = opp.get("estimated_value") or 0
    if isinstance(est_value, str):
        try:
            est_value = float(est_value.replace(",", "").replace("$", ""))
        except (ValueError, TypeError):
            est_value = 0
    if est_value > 1_000_000:
        strategic += 0.2
    elif est_value > 500_000:
        strategic += 0.1
    if engagement > 0.5:
        strategic += 0.15
    strategic = min(1.0, strategic)

    # Composite weighted average
    dimensions = {
        "capability_fit": cap_fit,
        "past_performance": past_perf,
        "competitive_position": competitive,
        "compliance_readiness": compliance,
        "resource_availability": resource,
        "strategic_alignment": strategic,
    }
    weights = _get_calibrated_weights()
    composite = sum(
        dimensions[k] * weights[k] for k in weights
    )

    # Decision
    if composite >= BID_THRESHOLD:
        decision = "bid"
    elif composite < NO_BID_THRESHOLD:
        decision = "no_bid"
    else:
        decision = "deferred"

    # Rationale (deterministic template)
    top_dims = sorted(dimensions.items(), key=lambda x: x[1], reverse=True)
    strengths = [f"{k}={v:.0%}" for k, v in top_dims[:2] if v >= 0.5]
    weaknesses = [f"{k}={v:.0%}" for k, v in top_dims if v < 0.4]

    parts = [f"Composite score: {composite:.0%}."]
    if strengths:
        parts.append(f"Strengths: {', '.join(strengths)}.")
    if weaknesses:
        parts.append(f"Gaps: {', '.join(weaknesses)}.")
    if decision == "bid":
        parts.append("Recommend: BID.")
    elif decision == "no_bid":
        parts.append("Recommend: NO BID — insufficient coverage.")
    else:
        parts.append("Recommend: DEFER — needs further evaluation.")

    return {
        "dimensions": dimensions,
        "composite": round(composite, 4),
        "win_probability": round(composite, 4),  # proxy
        "decision": decision,
        "rationale": " ".join(parts),
    }


# ---------------------------------------------------------------------------
# Store decision
# ---------------------------------------------------------------------------

def _store_decision(opportunity_id: str, result: Dict) -> Optional[str]:
    """Write bid decision to pg_bid_decisions."""
    conn = get_connection()
    dec_id = _generate_id("pgdec")
    now = _utcnow_iso()
    try:
        conn.execute(
            "INSERT INTO pg_bid_decisions "
            "(id, opportunity_id, decision, win_probability, "
            "score_breakdown, rationale, decided_by, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                dec_id,
                opportunity_id,
                result["decision"],
                result["win_probability"],
                json.dumps(result["dimensions"]),
                result["rationale"],
                "pg_decide",
                now,
            ),
        )
        conn.commit()
        return dec_id
    except Exception:
        return None
    finally:
        conn.close()


def _audit_decide(event_type: str, opportunity_id: Optional[str],
                  details: Dict, success: bool) -> None:
    """Log decide event to audit trail."""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO pg_proposal_genesis_audit "
            "(id, event_type, reflex_name, risk_tier, opportunity_id, "
            "details, success, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                _generate_id("pgaudit"),
                event_type,
                "decide",
                "green",
                opportunity_id,
                json.dumps(details),
                1 if success else 0,
                _utcnow_iso(),
            ),
        )
        conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run(config: Dict[str, Any], trust: Any) -> Dict[str, Any]:
    """Execute the Decide Reflex (R9).

    Steps:
      1. Find tracked opportunities without bid decisions
      2. Score each opportunity across 6 dimensions
      3. Produce bid/no-bid/deferred recommendation
      4. Store decision in pg_bid_decisions
      5. Audit all decisions

    Returns standard reflex result dict.
    """
    max_decisions = config.get("max_decisions_per_run", 20)

    # D-PG-9: Recalibrate scoring weights from win/loss outcomes
    calibration = calibrate_weights()

    opportunities = _get_undecided_opportunities()
    decided = 0
    decisions_made = []

    for opp in opportunities[:max_decisions]:
        opp_id = opp.get("id", "")
        result = score_opportunity(opp)
        dec_id = _store_decision(opp_id, result)

        if dec_id:
            decided += 1
            decisions_made.append({
                "opportunity_id": opp_id,
                "decision": result["decision"],
                "win_probability": result["win_probability"],
                "decision_id": dec_id,
            })

        _audit_decide(
            f"bid_decision_{result['decision']}",
            opp_id,
            {
                "decision_id": dec_id,
                "composite": result["composite"],
                "decision": result["decision"],
                "dimensions": result["dimensions"],
            },
            success=dec_id is not None,
        )

    return {
        "success": True,
        "metric_value": float(decided),
        "details": {
            "opportunities_evaluated": len(opportunities),
            "decisions_scored": min(len(opportunities), max_decisions),
            "decisions_stored": decided,
            "bid": sum(1 for d in decisions_made if d["decision"] == "bid"),
            "no_bid": sum(1 for d in decisions_made
                         if d["decision"] == "no_bid"),
            "deferred": sum(1 for d in decisions_made
                            if d["decision"] == "deferred"),
            "calibration": calibration,
            "decisions": decisions_made,
        },
    }
