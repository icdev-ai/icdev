"""Historical crisis fingerprint matching for SROR (Family 8).

Counts recession precursors, computes crisis similarity scores, and
extracts HMM transition probabilities. 100% deterministic — no LLM.

Usage:
    python tools/trading/market_intel/crisis_fingerprints.py --analyze --json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))


# ---------------------------------------------------------------------------
# Recession precursor definitions (7 classic signals)
# ---------------------------------------------------------------------------
RECESSION_PRECURSORS = {
    "yield_curve_inverted": {
        "description": "Yield curve (10Y-2Y) inverted",
        "check": lambda m, e: m.get("yield_spread", 0.5) < 0,
    },
    "rising_claims": {
        "description": "Initial claims rising above 250k (4-wk MA)",
        "check": lambda m, e: e.get("initial_claims_4wma_k", 220) > 250,
    },
    "lei_declining": {
        "description": "Leading Economic Index declining",
        "check": lambda m, e: e.get("lei_monthly_change", 0) < -0.2,
    },
    "credit_spreads_wide": {
        "description": "HY credit spreads above 500bps",
        "check": lambda m, e: e.get("credit_spread_hy_bps", 400) > 500,
    },
    "ism_contraction": {
        "description": "ISM Manufacturing PMI below 50",
        "check": lambda m, e: e.get("ism_pmi", 50) < 50,
    },
    "housing_declining": {
        "description": "Building permits YoY decline >10%",
        "check": lambda m, e: e.get("building_permits_yoy_pct", 0) < -10,
    },
    "sahm_rule_triggered": {
        "description": "Sahm Rule indicator above 0.50",
        "check": lambda m, e: e.get("sahm_rule", 0.1) > 0.50,
    },
}


# ---------------------------------------------------------------------------
# Crisis fingerprints (historical indicator profiles)
# ---------------------------------------------------------------------------
CRISIS_FINGERPRINTS = {
    "gfc_2008": {
        "label": "2008 Global Financial Crisis",
        "vector": {
            "yield_inverted": 1.0,
            "credit_wide": 1.0,
            "housing_decline": 1.0,
            "unemployment_rising": 1.0,
            "lei_declining": 1.0,
            "loan_contraction": 1.0,
            "vix_spike": 0.9,
            "bank_stress": 1.0,
            "bbb_cliff": 1.0,   # massive IG→HY fallen-angel cascade in 2008
        },
    },
    "covid_2020": {
        "label": "2020 COVID Crash",
        "vector": {
            "vix_spike": 1.0,
            "oil_crash": 1.0,
            "claims_spike": 1.0,
            "consumer_conf_drop": 1.0,
            "breadth_collapse": 0.9,
            "yield_inverted": 0.3,
            "credit_wide": 0.8,
            "bank_stress": 0.4,
            "bbb_cliff": 0.8,   # rapid BBB widening + IG/HY compression in March 2020
        },
    },
    "rate_shock_2022": {
        "label": "2022 Rate Shock",
        "vector": {
            "yield_inverted": 1.0,
            "fed_tightening": 1.0,
            "housing_decline": 0.8,
            "consumer_conf_drop": 0.7,
            "lei_declining": 0.6,
            "credit_wide": 0.5,
            "vix_spike": 0.4,
            "loan_contraction": 0.2,
            "bbb_cliff": 0.7,   # BBB OAS widening with IG/HY compression as rates spiked
        },
    },
}


def _build_current_vector(macro_raw: dict, extended: dict) -> dict:
    """Build normalized current indicator vector for crisis comparison."""
    vix = macro_raw.get("vix", 20)
    yield_spread = macro_raw.get("yield_spread", 0.5)
    oil_change = macro_raw.get("oil_change_30d", 0)
    loan_growth = macro_raw.get("loan_growth_yoy", 2.0)

    # BBB cliff: slope of BBB OAS over 8 weeks positive AND IG/HY ratio compressed below 3.5x
    bbb_oas_slope = extended.get("bbb_oas_slope_8w_bps", 0.0)
    ig_oas = extended.get("ig_oas_bps", 110.0)
    hy_oas = extended.get("credit_spread_hy_bps", 450.0)
    ig_hy_ratio = hy_oas / ig_oas if ig_oas > 0 else 4.0
    bbb_cliff_active = 1.0 if (bbb_oas_slope > 0 and ig_hy_ratio < 3.5) else 0.0

    return {
        "yield_inverted": 1.0 if yield_spread < 0 else max(0, (0.5 - yield_spread) / 0.5),
        "credit_wide": min(1.0, max(0, (extended.get("credit_spread_hy_bps", 400) - 350) / 450)),
        "housing_decline": min(1.0, max(0, (-extended.get("building_permits_yoy_pct", 0)) / 20)),
        "unemployment_rising": min(1.0, max(0, macro_raw.get("unemp_trend_months", 0) / 4)),
        "lei_declining": min(1.0, max(0, (-extended.get("lei_monthly_change", 0)) / 0.8)),
        "loan_contraction": min(1.0, max(0, (-loan_growth) / 5)),
        "vix_spike": min(1.0, max(0, (vix - 20) / 40)),
        "bank_stress": min(1.0, max(0, (macro_raw.get("bank_cash_assets_b", 3500) - 3500) / 1000)),
        "oil_crash": min(1.0, max(0, (-oil_change - 0.05) / 0.15)),
        "claims_spike": min(1.0, max(0, (extended.get("initial_claims_4wma_k", 220) - 220) / 200)),
        "consumer_conf_drop": min(1.0, max(0, (70 - extended.get("consumer_confidence", 70)) / 30)),
        "fed_tightening": min(1.0, max(0, (macro_raw.get("fed_funds", 5.0) - 3.0) / 3.0)),
        "breadth_collapse": 1.0 if (
            macro_raw.get("sp500", 5200) > macro_raw.get("sp500_sma50", 5100) and
            macro_raw.get("nasdaq", 16000) <= macro_raw.get("nasdaq_sma50", 15800)
        ) else 0.0,
        "bbb_cliff": bbb_cliff_active,
    }


def _cosine_similarity(v1: dict, v2: dict) -> float:
    """Compute cosine similarity between two sparse vectors."""
    all_keys = set(v1.keys()) | set(v2.keys())
    dot = sum(v1.get(k, 0) * v2.get(k, 0) for k in all_keys)
    mag1 = math.sqrt(sum(v1.get(k, 0) ** 2 for k in all_keys))
    mag2 = math.sqrt(sum(v2.get(k, 0) ** 2 for k in all_keys))
    if mag1 == 0 or mag2 == 0:
        return 0.0
    return dot / (mag1 * mag2)


def count_recession_precursors(macro_raw: dict, extended: dict) -> dict:
    """Count how many of the 7 classic recession precursors are active.

    Returns:
        Dict with count, active list, total, and danger_score.
    """
    active = []
    for name, precursor in RECESSION_PRECURSORS.items():
        try:
            if precursor["check"](macro_raw, extended):
                active.append({
                    "name": name,
                    "description": precursor["description"],
                })
        except Exception:
            pass  # graceful degradation on missing data

    count = len(active)
    # Score: 0 precursors = 0 danger, 7 = 100
    danger_score = min(100, int(count / 7 * 100 * 1.3))  # slight amplification

    return {
        "count": count,
        "total": len(RECESSION_PRECURSORS),
        "active": active,
        "danger_score": danger_score,
    }


def compute_crisis_similarity(macro_raw: dict, extended: dict) -> dict:
    """Compare current conditions against historical crisis fingerprints.

    Returns:
        Dict with similarities per crisis, max_similarity, closest match.
    """
    current = _build_current_vector(macro_raw, extended)
    similarities = {}

    for crisis_id, crisis in CRISIS_FINGERPRINTS.items():
        sim = _cosine_similarity(current, crisis["vector"])
        similarities[crisis_id] = {
            "label": crisis["label"],
            "similarity": round(sim, 3),
        }

    max_sim = max((s["similarity"] for s in similarities.values()), default=0)
    closest = max(similarities.items(), key=lambda x: x[1]["similarity"])[0] if similarities else None

    return {
        "similarities": similarities,
        "max_similarity": round(max_sim, 3),
        "closest_match": closest,
        "danger_score": min(100, int(max_sim * 100)),
    }


def get_hmm_transition_risk() -> dict:
    """Extract HMM transition probability to risk-off states.

    Returns dict with transition probabilities or defaults if no model.
    """
    try:
        from tools.trading.ml.regime_hmm import get_transition_matrix
        matrix = get_transition_matrix()
        if matrix and "transition_probs" in matrix:
            # Look for probability of transitioning to worst states
            probs = matrix["transition_probs"]
            risk_off_prob = probs.get("to_risk_off", 0.15)
            deep_risk_off_prob = probs.get("to_deep_risk_off", 0.05)
            combined = min(1.0, risk_off_prob + deep_risk_off_prob)
            return {
                "risk_off_prob": round(risk_off_prob, 3),
                "deep_risk_off_prob": round(deep_risk_off_prob, 3),
                "combined_risk_prob": round(combined, 3),
                "danger_score": min(100, int(combined * 150)),
                "source": "hmm_model",
            }
    except Exception:
        pass

    # Fallback: no trained HMM available
    return {
        "risk_off_prob": 0.15,
        "deep_risk_off_prob": 0.05,
        "combined_risk_prob": 0.20,
        "danger_score": 20,
        "source": "default",
    }


def analyze_historical_patterns(macro_raw: dict, extended: dict) -> dict:
    """Full Family 8 analysis: precursors + similarity + HMM.

    Returns aggregated danger_score and opportunity_score.
    """
    precursors = count_recession_precursors(macro_raw, extended)
    similarity = compute_crisis_similarity(macro_raw, extended)
    hmm = get_hmm_transition_risk()

    # Weighted aggregate: precursors 40%, similarity 35%, HMM 25%
    danger = (
        precursors["danger_score"] * 0.40 +
        similarity["danger_score"] * 0.35 +
        hmm["danger_score"] * 0.25
    )
    danger = min(100, round(danger, 1))
    opportunity = max(0, round(100 - danger, 1))

    return {
        "recession_precursors": precursors,
        "crisis_similarity": similarity,
        "hmm_transitions": hmm,
        "danger_score": danger,
        "opportunity_score": opportunity,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Crisis fingerprint analyzer")
    parser.add_argument("--analyze", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if args.analyze:
        from tools.trading.data.macro_data import fetch_macro_context, fetch_extended_macro
        ctx = fetch_macro_context()
        ext = fetch_extended_macro()
        result = analyze_historical_patterns(ctx.get("raw_values", {}), ext)
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            p = result["recession_precursors"]
            print(f"Recession precursors: {p['count']}/{p['total']}")
            for a in p["active"]:
                print(f"  - {a['description']}")
            s = result["crisis_similarity"]
            print(f"\nClosest crisis match: {s['closest_match']} ({s['max_similarity']:.1%})")
            print(f"Composite danger: {result['danger_score']}")


if __name__ == "__main__":
    main()
