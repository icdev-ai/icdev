#!/usr/bin/env python3
# CUI // SP-CTI
"""R9: Decide Reflex — Bid/no-bid scoring + win probability estimation.

Evaluates tracked opportunities using a 6-dimension deterministic weighted
average to produce a go/no-go recommendation and win probability estimate.

Enhancement §3.1: Bayesian Teaching Intelligence integration adds info-gain-
based weight calibration.  Three-tier fallback:
  1. Bayesian info-gain from historical win/loss data (D-BT-1/D-BT-2)
  2. Gap-based calibration from pg_bid_decision_outcomes (D-PG-9)
  3. Static SCORE_WEIGHTS defaults

Pipeline: on_demand (triggered after Map or manually).
GREEN tier (read-only analysis, writes only to pg_bid_decisions).
Scanner-tier LLM only (zero Claude tokens — fully deterministic).
"""

import json
import math
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.proposal_genesis.reflexes.decide")


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
BID_THRESHOLD = 0.60  # >= 0.60 → bid
NO_BID_THRESHOLD = 0.35  # < 0.35 → no_bid
# 0.35 - 0.59 → deferred (needs further evaluation)

# ---------------------------------------------------------------------------
# Module-level constants — Bayesian estimation + dimension scoring parameters.
# All overridable from proposal_genesis_config.yaml under reflexes.decide.
# Change config, not code.
# ---------------------------------------------------------------------------
# Bayesian info-gain weight estimation (§3.1)
_BAYESIAN_MIN_SAMPLES   = 8       # min won/lost examples for Bayesian estimation
_WEIGHT_CLAMP_MIN       = 0.05    # min per-dimension weight after normalization
_WEIGHT_CLAMP_MAX       = 0.40    # max per-dimension weight after normalization
_INFO_GAIN_PS_WEIGHT    = 0.7     # posterior_shift weight in info-gain combination
_INFO_GAIN_DISC_WEIGHT  = 0.3     # discriminability weight in info-gain combination
_DISCRIMINABILITY_MULT  = 2.0     # gap → discriminability scaling factor
_SPREAD_MIN             = 0.01    # min spread to avoid divide-by-zero in likelihood
_LIKELIHOOD_EPSILON     = 0.01    # denominator offset in Gaussian likelihood
_WEIGHT_ROUND_PRECISION = 4       # rounding precision for normalized weights
# Gap-based calibration feedback loop (D-PG-9)
_CALIBRATION_MIN_OUTCOMES = 5     # min won/lost outcomes for gap calibration
_GAP_POSITIVE_SHIFT     = 0.5     # offset to shift won-lost gap into positive range
# Competitive-position blend
_COMP_TEAMING_WEIGHT    = 0.6     # teaming fit weight in competitive position
_COMP_ENGAGEMENT_WEIGHT = 0.4     # engagement weight in competitive position
# Compliance-readiness scoring
_COMPLIANCE_BASELINE    = 0.5     # baseline compliance readiness score
_SETASIDE_BONUS         = 0.2     # bonus for matching set-aside category
_NAICS_BONUS            = 0.15    # bonus for matching NAICS code
# Resource-availability scoring
_RESOURCE_BASELINE      = 0.3     # baseline resource availability score
_RESOURCE_WITH_PLAN     = 0.6     # score when capture plan exists
_RESOURCE_WIN_BONUS     = 0.2     # bonus for win_strategy present
_RESOURCE_TEAMING_BONUS = 0.1     # bonus for teaming_strategy present
# Strategic-alignment scoring
_STRATEGIC_BASELINE     = 0.5     # baseline strategic alignment score
_STRATEGIC_VALUE_HIGH   = 1_000_000  # est_value threshold for high bonus
_STRATEGIC_VALUE_MED    = 500_000    # est_value threshold for medium bonus
_STRATEGIC_VALUE_HIGH_BONUS = 0.2    # bonus for est_value above high threshold
_STRATEGIC_VALUE_MED_BONUS  = 0.1    # bonus for est_value above medium threshold
_STRATEGIC_ENGAGEMENT_THRESH = 0.5   # engagement threshold for strategic bonus
_STRATEGIC_ENGAGEMENT_BONUS  = 0.15  # bonus for high engagement
# Quality-score normalization
_QUALITY_DENOMINATOR    = 100.0   # divisor to normalize 0-100 quality to 0-1
# Early termination + rationale thresholds
_EARLY_TERM_DIMS        = 3       # dimensions assessed before early-termination check
_EARLY_TERM_MULTIPLIER  = 0.7     # NO_BID_THRESHOLD multiplier for early termination
_STRENGTH_THRESHOLD     = 0.5     # dimension score for inclusion in strengths list
_WEAKNESS_THRESHOLD     = 0.4     # dimension score below which it's a weakness
_COMPOSITE_ROUND        = 4       # rounding precision for composite score
_MAX_DECISIONS_PER_RUN  = 20      # default max opportunities scored per run


# ---------------------------------------------------------------------------
# Bayesian Teaching Intelligence (Enhancement §3.1, D-BT-1/D-BT-2)
# ---------------------------------------------------------------------------


def _get_bayesian_weights() -> Optional[Dict[str, float]]:
    """Compute info-gain-weighted dimension weights from historical outcomes.

    Queries pg_bid_decisions joined with pg_bid_decision_outcomes, builds
    teaching examples (dimension scores + won/lost label), then uses
    Bayesian posterior_shift to compute per-dimension info-gain.  Returns
    normalized weights clamped to [0.05, 0.40], or None on any failure
    (graceful degradation — callers fall through to gap-based calibration).
    """
    try:
        # Late import — bayesian_teacher may not be available in all envs
        from tools.intelligence.bayesian_teacher import (  # type: ignore
            _posterior_shift,
        )
    except (ImportError, Exception):
        return None

    conn = get_connection()
    try:
        rows = conn.execute("""
            SELECT bdo.outcome, bd.score_breakdown
            FROM pg_bid_decision_outcomes bdo
            JOIN pg_bid_decisions bd ON bd.id = bdo.bid_decision_id
            WHERE bdo.outcome IN ('won', 'lost')
            AND bd.score_breakdown IS NOT NULL
        """).fetchall()
    except Exception:
        return None
    finally:
        conn.close()

    # Need meaningful sample size for Bayesian estimation
    if len(rows) < _BAYESIAN_MIN_SAMPLES:
        return None

    # Parse score breakdowns into labelled examples
    examples: List[Dict[str, Any]] = []
    for row in rows:
        try:
            breakdown = json.loads(row["score_breakdown"])
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        # Ensure all dimensions present
        if not all(d in breakdown for d in SCORE_WEIGHTS):
            continue
        examples.append(
            {
                "scores": {d: float(breakdown[d]) for d in SCORE_WEIGHTS},
                "outcome": row["outcome"],  # 'won' or 'lost'
            }
        )

    if len(examples) < _BAYESIAN_MIN_SAMPLES:
        return None

    won = [e for e in examples if e["outcome"] == "won"]
    lost = [e for e in examples if e["outcome"] == "lost"]
    if not won or not lost:
        return None

    # Per-dimension info-gain via posterior_shift
    # Prior: uniform over [won, lost] → [0.5, 0.5]
    prior = [0.5, 0.5]
    dim_info_gain: Dict[str, float] = {}

    for dim in SCORE_WEIGHTS:
        # Compute mean score per outcome for this dimension
        won_scores = [e["scores"][dim] for e in won]
        lost_scores = [e["scores"][dim] for e in lost]
        won_mean = sum(won_scores) / len(won_scores)
        lost_mean = sum(lost_scores) / len(lost_scores)

        # Likelihood: Gaussian-like L(outcome | dim_score)
        # Approximate: if won_mean > lost_mean, this dimension is
        # informative.  Use separation as likelihood ratio.
        # L(won | high_score) ∝ exp(-|score - won_mean|)
        # We construct a synthetic observation at the midpoint and
        # measure how much it shifts the prior.
        midpoint = (won_mean + lost_mean) / 2.0
        spread = max(abs(won_mean - lost_mean), _SPREAD_MIN)

        # Likelihood for each hypothesis (won/lost) given the midpoint
        lk_won = math.exp(-((midpoint - won_mean) ** 2) / (2 * spread**2 + _LIKELIHOOD_EPSILON))
        lk_lost = math.exp(-((midpoint - lost_mean) ** 2) / (2 * spread**2 + _LIKELIHOOD_EPSILON))
        z = lk_won + lk_lost
        if z > 0:
            likelihood = [lk_won / z, lk_lost / z]
        else:
            likelihood = [0.5, 0.5]

        ps = _posterior_shift(prior, likelihood)

        # Also compute variance-based discriminability
        # Higher variance within won-vs-lost gap → more discriminative
        gap = abs(won_mean - lost_mean)
        discriminability = min(1.0, gap * _DISCRIMINABILITY_MULT)

        # Combined: posterior_shift + discriminability (info-gain blend)
        dim_info_gain[dim] = _INFO_GAIN_PS_WEIGHT * ps + _INFO_GAIN_DISC_WEIGHT * discriminability

    # Normalize to sum to 1.0 and clamp to [0.05, 0.40]
    total_ig = sum(dim_info_gain.values())
    if total_ig <= 0:
        return None

    raw_weights = {k: dim_info_gain[k] / total_ig for k in dim_info_gain}
    clamped = {k: max(_WEIGHT_CLAMP_MIN, min(_WEIGHT_CLAMP_MAX, v)) for k, v in raw_weights.items()}
    clamp_total = sum(clamped.values())
    if clamp_total <= 0:
        return None
    bayesian_weights = {k: round(v / clamp_total, _WEIGHT_ROUND_PRECISION) for k, v in clamped.items()}

    # Persist for observability (non-blocking)
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO pg_proposal_genesis_config (key, value, updated_at) VALUES (%s, %s, %s)",
            ("bayesian_score_weights", json.dumps(bayesian_weights), _utcnow_iso()),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning(
            "_get_bayesian_weights: best-effort INSERT into pg_proposal_genesis_config failed (non-blocking): %s",
            exc,
        )
    finally:
        conn.close()

    return bayesian_weights


def _compute_teaching_dimension(weights: Dict[str, float]) -> List[str]:
    """Return top 3 most discriminative dimensions (simplified Goldman & Kearns).

    Sorts dimensions by weight (info-gain) descending and returns the top 3.
    These form the minimum distinguishing set for bid/no-bid classification.
    """
    sorted_dims = sorted(weights.items(), key=lambda x: x[1], reverse=True)
    return [dim for dim, _w in sorted_dims[:_EARLY_TERM_DIMS]]


def _optimal_assessment_order(weights: Dict[str, float]) -> List[str]:
    """Return dimensions sorted by weight (highest info-gain first).

    Used for early termination: if the first 2-3 dimensions predict NO_BID
    with high confidence, skip remaining dimensions to save computation.
    """
    sorted_dims = sorted(weights.items(), key=lambda x: x[1], reverse=True)
    return [dim for dim, _w in sorted_dims]


# ---------------------------------------------------------------------------
# Calibration feedback loop (D-PG-9)
# ---------------------------------------------------------------------------


def _get_calibrated_weights() -> Dict[str, float]:
    """Load calibrated weights via three-tier fallback.

    Tier 1: Bayesian info-gain from historical win/loss data (§3.1).
    Tier 2: Gap-based calibration stored in pg_proposal_genesis_config.
    Tier 3: Static SCORE_WEIGHTS defaults.

    Returns weights dict (always sums to ~1.0) and never raises.
    """
    # Tier 1: Bayesian info-gain (best — data-driven, principled)
    bayesian = _get_bayesian_weights()
    if bayesian is not None:
        return bayesian

    # Tier 2: Gap-based calibration (good — empirical)
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT value FROM pg_proposal_genesis_config WHERE key = 'calibrated_score_weights'"
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

    # Tier 3: Static defaults
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

    if len(rows) < _CALIBRATION_MIN_OUTCOMES:
        return {
            "calibrated": False,
            "reason": "insufficient_outcomes",
            "outcomes": len(rows),
            "minimum_required": _CALIBRATION_MIN_OUTCOMES,
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
        gap = max(_SPREAD_MIN, won_avg - lost_avg + _GAP_POSITIVE_SHIFT)  # shift to positive range
        gaps[dim] = gap

    # Normalize gaps to produce new weights (sum to 1.0)
    total_gap = sum(gaps.values())
    raw_weights = {k: gaps[k] / total_gap for k in gaps}

    # Clamp and re-normalize
    clamped = {k: max(_WEIGHT_CLAMP_MIN, min(_WEIGHT_CLAMP_MAX, v)) for k, v in raw_weights.items()}
    clamp_total = sum(clamped.values())
    new_weights = {k: round(v / clamp_total, _WEIGHT_ROUND_PRECISION) for k, v in clamped.items()}

    # Store calibrated weights
    conn = get_connection()
    try:
        import json as _json

        now = _utcnow_iso()
        conn.execute(
            "INSERT OR REPLACE INTO pg_proposal_genesis_config (key, value, updated_at) VALUES (%s, %s, %s)",
            ("calibrated_score_weights", _json.dumps(new_weights), now),
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning(
            "calibrate_weights: best-effort INSERT into pg_proposal_genesis_config failed (non-blocking): %s",
            exc,
        )
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
        row = conn.execute(
            """
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN capability_match IS NOT NULL
                        AND capability_match != '' THEN 1 ELSE 0 END) as matched
            FROM rfp_shall_statements
            WHERE opportunity_id = %s
        """,
            (opportunity_id,),
        ).fetchone()
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
        row = conn.execute(
            """
            SELECT AVG(composite_score) as avg_score
            FROM pg_proposal_quality_scores
            WHERE opportunity_id = %s
            AND composite_score IS NOT NULL
        """,
            (opportunity_id,),
        ).fetchone()
        if not row or row["avg_score"] is None:
            return 0.0
        return min(1.0, row["avg_score"] / _QUALITY_DENOMINATOR)
    except Exception:
        return 0.0
    finally:
        conn.close()


def _get_capture_plan_status(opportunity_id: str) -> Optional[Dict]:
    """Get capture plan data for strategic scoring."""
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT status, win_strategy, teaming_strategy
            FROM pg_capture_plans
            WHERE opportunity_id = %s
            ORDER BY updated_at DESC LIMIT 1
        """,
            (opportunity_id,),
        ).fetchone()
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
        row = conn.execute(
            """
            SELECT es.composite_score
            FROM pg_crm_engagement_scores es
            JOIN pg_crm_accounts a ON a.id = es.account_id
            JOIN sam_gov_opportunities o ON LOWER(o.agency) = LOWER(a.agency)
            WHERE o.id = %s
            ORDER BY es.created_at DESC LIMIT 1
        """,
            (opportunity_id,),
        ).fetchone()
        return row["composite_score"] / _QUALITY_DENOMINATOR if row else 0.0
    except Exception:
        return 0.0
    finally:
        conn.close()


def _get_teaming_fit(opportunity_id: str) -> float:
    """Get best teaming partner fit score."""
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT MAX(fit_score) as best_fit
            FROM pg_teaming_assessments
            WHERE opportunity_id = %s
        """,
            (opportunity_id,),
        ).fetchone()
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

    Enhancement §3.1: uses Bayesian-optimized dimension ordering for early
    termination.  If the top 3 dimensions predict NO_BID with high
    confidence (partial_composite < NO_BID_THRESHOLD * 0.7), remaining
    dimensions are skipped and the decision is marked ``early_no_bid``.

    Returns dict with dimension scores, composite score, decision, rationale,
    scoring_method, and optional teaching_dimension.
    """
    opp_id = opp.get("id", "")

    # --- Determine scoring method and weights ---
    bayesian_weights = _get_bayesian_weights()
    if bayesian_weights is not None:
        weights = bayesian_weights
        scoring_method = "bayesian"
    else:
        weights = _get_calibrated_weights()
        # Distinguish gap-calibrated from static
        scoring_method = "calibrated" if weights != SCORE_WEIGHTS else "static"

    # Teaching dimension (top-3 discriminative dims, when Bayesian available)
    teaching_dim: Optional[List[str]] = None
    if scoring_method == "bayesian":
        teaching_dim = _compute_teaching_dimension(weights)

    # --- Dimension evaluators (lazy, keyed by dimension name) ---
    # Pre-fetch shared values used by multiple dimensions
    _teaming: Optional[float] = None
    _engagement: Optional[float] = None

    def _eval_capability_fit() -> float:
        return _get_capability_coverage(opp_id)

    def _eval_past_performance() -> float:
        return _get_quality_score_avg(opp_id)

    def _eval_competitive_position() -> float:
        nonlocal _teaming, _engagement
        if _teaming is None:
            _teaming = _get_teaming_fit(opp_id)
        if _engagement is None:
            _engagement = _get_engagement_score(opp_id)
        return min(1.0, (_teaming * _COMP_TEAMING_WEIGHT + _engagement * _COMP_ENGAGEMENT_WEIGHT))

    def _eval_compliance_readiness() -> float:
        score = _COMPLIANCE_BASELINE
        set_aside = (opp.get("set_aside") or "").lower()
        if set_aside in ("total small business", "8(a)", "hubzone", "sdvosb", "wosb", "edwosb"):
            score += _SETASIDE_BONUS
        naics = opp.get("naics_code") or ""
        if naics.startswith("5415") or naics.startswith("5112"):
            score += _NAICS_BONUS
        return min(1.0, score)

    def _eval_resource_availability() -> float:
        plan = _get_capture_plan_status(opp_id)
        score = _RESOURCE_BASELINE
        if plan:
            score = _RESOURCE_WITH_PLAN
            if plan.get("win_strategy"):
                score += _RESOURCE_WIN_BONUS
            if plan.get("teaming_strategy"):
                score += _RESOURCE_TEAMING_BONUS
        return min(1.0, score)

    def _eval_strategic_alignment() -> float:
        nonlocal _engagement
        if _engagement is None:
            _engagement = _get_engagement_score(opp_id)
        score = _STRATEGIC_BASELINE
        est_value = opp.get("estimated_value") or 0
        if isinstance(est_value, str):
            try:
                est_value = float(est_value.replace(",", "").replace("$", ""))
            except (ValueError, TypeError):
                est_value = 0
        if est_value > _STRATEGIC_VALUE_HIGH:
            score += _STRATEGIC_VALUE_HIGH_BONUS
        elif est_value > _STRATEGIC_VALUE_MED:
            score += _STRATEGIC_VALUE_MED_BONUS
        if _engagement > _STRATEGIC_ENGAGEMENT_THRESH:
            score += _STRATEGIC_ENGAGEMENT_BONUS
        return min(1.0, score)

    evaluators = {
        "capability_fit": _eval_capability_fit,
        "past_performance": _eval_past_performance,
        "competitive_position": _eval_competitive_position,
        "compliance_readiness": _eval_compliance_readiness,
        "resource_availability": _eval_resource_availability,
        "strategic_alignment": _eval_strategic_alignment,
    }

    # --- Evaluate dimensions in optimal order (early termination) ---
    order = _optimal_assessment_order(weights)
    dimensions: Dict[str, float] = {}
    early_terminated = False

    for idx, dim in enumerate(order):
        dimensions[dim] = evaluators[dim]()

        # Early termination: after top N dims, if partial composite
        # predicts NO_BID with high confidence, skip the rest.
        if idx == _EARLY_TERM_DIMS - 1 and len(order) > _EARLY_TERM_DIMS:
            evaluated_weight = sum(weights[d] for d in dimensions)
            if evaluated_weight > 0:
                partial_composite = sum(dimensions[d] * weights[d] for d in dimensions) / evaluated_weight
            else:
                partial_composite = 0.0

            if partial_composite < NO_BID_THRESHOLD * _EARLY_TERM_MULTIPLIER:
                # Fill remaining dimensions with 0.0 (worst-case assumption)
                for remaining_dim in order[_EARLY_TERM_DIMS:]:
                    dimensions[remaining_dim] = 0.0
                early_terminated = True
                break

    # Composite weighted average
    composite = sum(dimensions[k] * weights[k] for k in weights)

    # Decision
    if early_terminated:
        decision = "no_bid"
    elif composite >= BID_THRESHOLD:
        decision = "bid"
    elif composite < NO_BID_THRESHOLD:
        decision = "no_bid"
    else:
        decision = "deferred"

    # Rationale (deterministic template)
    top_dims = sorted(dimensions.items(), key=lambda x: x[1], reverse=True)
    strengths = [f"{k}={v:.0%}" for k, v in top_dims[:2] if v >= _STRENGTH_THRESHOLD]
    weaknesses = [f"{k}={v:.0%}" for k, v in top_dims if v < _WEAKNESS_THRESHOLD]

    parts = [f"Composite score: {composite:.0%}."]
    if strengths:
        parts.append(f"Strengths: {', '.join(strengths)}.")
    if weaknesses:
        parts.append(f"Gaps: {', '.join(weaknesses)}.")
    if early_terminated:
        evaluated_names = [d for d in order[:_EARLY_TERM_DIMS]]
        parts.append(f"Early termination after {_EARLY_TERM_DIMS} dimensions ({', '.join(evaluated_names)}).")
        parts.append("Recommend: NO BID — early termination, insufficient coverage.")
    elif decision == "bid":
        parts.append("Recommend: BID.")
    elif decision == "no_bid":
        parts.append("Recommend: NO BID — insufficient coverage.")
    else:
        parts.append("Recommend: DEFER — needs further evaluation.")

    result: Dict[str, Any] = {
        "dimensions": dimensions,
        "composite": round(composite, _COMPOSITE_ROUND),
        "win_probability": round(composite, _COMPOSITE_ROUND),  # proxy
        "decision": decision,
        "rationale": " ".join(parts),
        "scoring_method": scoring_method,
        "early_terminated": early_terminated,
    }
    if teaching_dim is not None:
        result["teaching_dimension"] = teaching_dim
    return result


# ---------------------------------------------------------------------------
# Store decision
# ---------------------------------------------------------------------------


def _store_decision(opportunity_id: str, result: Dict) -> Optional[str]:
    """Write bid decision to pg_bid_decisions.

    Includes scoring_method in score_breakdown for audit trail.
    """
    conn = get_connection()
    dec_id = _generate_id("pgdec")
    now = _utcnow_iso()
    try:
        # Enrich score_breakdown with scoring metadata for audit trail
        breakdown = dict(result.get("dimensions", {}))
        breakdown["_scoring_method"] = result.get("scoring_method", "static")
        if result.get("early_terminated"):
            breakdown["_early_terminated"] = True
        if result.get("teaching_dimension"):
            breakdown["_teaching_dimension"] = result["teaching_dimension"]

        conn.execute(
            "INSERT INTO pg_bid_decisions "
            "(id, opportunity_id, decision, win_probability, "
            "score_breakdown, rationale, decided_by, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (
                dec_id,
                opportunity_id,
                result["decision"],
                result["win_probability"],
                json.dumps(breakdown),
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


def _audit_decide(event_type: str, opportunity_id: Optional[str], details: Dict, success: bool) -> None:
    """Log decide event to audit trail."""
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO pg_proposal_genesis_audit "
            "(id, event_type, reflex_name, risk_tier, opportunity_id, "
            "details, success, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
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
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning(
            "_audit_decide: best-effort INSERT into pg_proposal_genesis_audit failed (non-blocking): %s",
            exc,
        )
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
    max_decisions = config.get("max_decisions_per_run", _MAX_DECISIONS_PER_RUN)

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
            entry: Dict[str, Any] = {
                "opportunity_id": opp_id,
                "decision": result["decision"],
                "win_probability": result["win_probability"],
                "decision_id": dec_id,
                "scoring_method": result.get("scoring_method", "static"),
            }
            if result.get("early_terminated"):
                entry["early_terminated"] = True
            decisions_made.append(entry)

        _audit_decide(
            f"bid_decision_{result['decision']}",
            opp_id,
            {
                "decision_id": dec_id,
                "composite": result["composite"],
                "decision": result["decision"],
                "dimensions": result["dimensions"],
                "scoring_method": result.get("scoring_method", "static"),
                "early_terminated": result.get("early_terminated", False),
                "teaching_dimension": result.get("teaching_dimension"),
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
            "no_bid": sum(1 for d in decisions_made if d["decision"] == "no_bid"),
            "deferred": sum(1 for d in decisions_made if d["decision"] == "deferred"),
            "calibration": calibration,
            "decisions": decisions_made,
        },
    }
