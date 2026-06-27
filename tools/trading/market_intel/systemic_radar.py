"""Systemic Risk & Opportunity Radar (SROR) — unified early-warning system.

Aggregates 8 indicator families × 3 time horizons into a composite
danger/opportunity score. Identifies the current regime, raises alerts,
and persists snapshots for dashboard visualization.

8 Indicator Families:
  1. Market Stress         — VIX, credit spreads, put/call, skew, breadth
  2. Macro Leading         — LEI, ISM PMI, permits, claims, Sahm Rule
  3. Monetary Liquidity    — Van Metre + Fed balance sheet, M2, reserves
  4. News Sentiment        — cluster sentiment, divergences, omissions
  5. Supply Chain / Geo    — geopolitical risk, KG stress propagation
  6. Cross-Asset Diverge   — stocks/bonds, gold/dollar, oil/equity
  7. Institutional Flow    — insider buy/sell, analyst up/downgrades
  8. Historical Pattern    — recession precursors, crisis fingerprints

Time Horizons:
  - immediate   (0-2 weeks)
  - near_term   (2-12 weeks)
  - medium_term (3-12 months)

100% deterministic — no LLM calls in core scoring.

Usage:
    python tools/trading/market_intel/systemic_radar.py --compute --json
    python tools/trading/market_intel/systemic_radar.py --latest --json
    python tools/trading/market_intel/systemic_radar.py --history --days 30
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.trading.db import get_conn


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------
_CONFIG_PATH = Path(__file__).resolve().parents[3] / "args" / "systemic_radar_config.yaml"

_DEFAULT_CONFIG = {
    "family_weights": {
        "market_stress": 0.20,
        "macro_leading": 0.18,
        "monetary_liquidity": 0.15,
        "news_sentiment": 0.12,
        "supply_chain_geo": 0.10,
        "cross_asset_diverge": 0.10,
        "institutional_flow": 0.07,
        "historical_pattern": 0.08,
    },
    "families": {
        f: {"enabled": True}
        for f in [
            "market_stress", "macro_leading", "monetary_liquidity",
            "news_sentiment", "supply_chain_geo", "cross_asset_diverge",
            "institutional_flow", "historical_pattern",
        ]
    },
    "scoring": {
        "divergence_danger_bonus": 10,
        "convergence_opportunity_bonus": 5,
    },
    "alerts": {
        "danger_escalation_threshold": 75,
        "opportunity_emerging_threshold": 65,
        "min_families_red_for_critical": 3,
    },
}


def _load_config() -> dict:
    """Load SROR config from YAML, falling back to defaults."""
    try:
        import yaml
        if _CONFIG_PATH.exists():
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                loaded = yaml.safe_load(f) or {}
                # Merge with defaults
                config = dict(_DEFAULT_CONFIG)
                for k, v in loaded.items():
                    if isinstance(v, dict) and k in config:
                        config[k] = {**config[k], **v}
                    else:
                        config[k] = v
                return config
    except Exception:
        pass
    return dict(_DEFAULT_CONFIG)


# ---------------------------------------------------------------------------
# Time-horizon mapping per indicator
# ---------------------------------------------------------------------------
HORIZON_MAP = {
    # Immediate (0-2 weeks)
    "vix": "immediate",
    "skew_index": "immediate",
    "put_call_ratio": "immediate",
    "credit_spread_hy_bps": "immediate",
    "ig_oas_bps": "immediate",
    "bbb_oas_bps": "immediate",
    "sofr_ff_spread_bps": "immediate",
    "news_sentiment_score": "immediate",
    "narrative_divergence_count": "immediate",
    "omission_density": "immediate",
    "geopolitical_risk": "immediate",
    "supply_chain_risk": "immediate",
    "gold_dollar_stress": "immediate",
    "vix_equity_divergence": "immediate",

    # Near-term (2-12 weeks)
    "ism_pmi": "near_term",
    "initial_claims_4wma_k": "near_term",
    "consumer_confidence": "near_term",
    "consumer_confidence_declining_3m": "near_term",
    "fed_bs_change_pct": "near_term",
    "tga_balance_b": "near_term",
    "net_liquidity_b": "near_term",
    "bank_reserves_b": "near_term",
    "cluster_velocity": "near_term",
    "kg_stress_depth": "near_term",
    "insider_ratio": "near_term",
    "analyst_ratio": "near_term",
    "hmm_transition": "near_term",
    "stocks_vs_bonds": "near_term",
    "oil_equity_divergence": "near_term",
    "breadth_divergence": "near_term",
    "credit_equity_divergence": "near_term",

    # Medium-term (3-12 months)
    "yield_spread": "medium_term",
    "lei_monthly_change": "medium_term",
    "building_permits_yoy_pct": "medium_term",
    "sahm_rule": "medium_term",
    "m2_yoy_pct": "medium_term",
    "loan_growth_yoy": "medium_term",
    "recession_precursors": "medium_term",
    "crisis_similarity": "medium_term",
    "supply_chain_concentration": "medium_term",
}


# ---------------------------------------------------------------------------
# Family scorers
# ---------------------------------------------------------------------------

def _score_market_stress(macro_raw: dict, extended: dict) -> dict:
    """Family 1: Market Stress indicators."""
    from tools.trading.data.macro_data import _score_indicator, score_extended_indicator

    indicators = []
    # VIX (immediate)
    vix = macro_raw.get("vix", 20)
    scored = _score_indicator("vix", vix)
    indicators.append({
        "name": "vix", "raw_value": vix, "score": scored["score"],
        "signal": scored["signal"], "label": scored["label"],
        "horizon": "immediate", "weight": 0.20,
    })

    # Credit spreads (immediate): IG OAS, BBB OAS, HY OAS, SOFR-FF spread
    cfg = _load_config()
    cw = cfg.get("families", {}).get("market_stress", {}).get(
        "credit_spreads_weights", {"ig_oas": 0.30, "bbb_oas": 0.25, "hy_oas": 0.25, "sofr_ff": 0.20}
    )

    ig_oas = extended.get("ig_oas_bps", 110)
    scored = score_extended_indicator("ig_oas_bps", ig_oas)
    indicators.append({
        "name": "ig_oas_bps", "raw_value": ig_oas, "score": scored["score"],
        "signal": scored["signal"], "label": scored["label"],
        "horizon": "immediate", "weight": cw.get("ig_oas", 0.30),
    })

    bbb_oas = extended.get("bbb_oas_bps", 175)
    scored = score_extended_indicator("bbb_oas_bps", bbb_oas)
    indicators.append({
        "name": "bbb_oas_bps", "raw_value": bbb_oas, "score": scored["score"],
        "signal": scored["signal"], "label": scored["label"],
        "horizon": "immediate", "weight": cw.get("bbb_oas", 0.25),
    })

    credit_spread = extended.get("credit_spread_hy_bps", 400)
    scored = score_extended_indicator("credit_spread_hy_bps", credit_spread)
    indicators.append({
        "name": "credit_spread_hy_bps", "raw_value": credit_spread, "score": scored["score"],
        "signal": scored["signal"], "label": scored["label"],
        "horizon": "immediate", "weight": cw.get("hy_oas", 0.25),
    })

    sofr_ff = extended.get("sofr_ff_spread_bps", 5)
    scored = score_extended_indicator("sofr_ff_spread_bps", sofr_ff)
    indicators.append({
        "name": "sofr_ff_spread_bps", "raw_value": sofr_ff, "score": scored["score"],
        "signal": scored["signal"], "label": scored["label"],
        "horizon": "immediate", "weight": cw.get("sofr_ff", 0.20),
    })

    # Skew (immediate)
    skew = extended.get("skew_index", 130)
    scored = score_extended_indicator("skew_index", skew)
    indicators.append({
        "name": "skew_index", "raw_value": skew, "score": scored["score"],
        "signal": scored["signal"], "label": scored["label"],
        "horizon": "immediate", "weight": 0.15,
    })

    # Put/Call (immediate)
    pc = extended.get("put_call_ratio", 0.85)
    scored = score_extended_indicator("put_call_ratio", pc)
    indicators.append({
        "name": "put_call_ratio", "raw_value": pc, "score": scored["score"],
        "signal": scored["signal"], "label": scored["label"],
        "horizon": "immediate", "weight": 0.13,
    })

    # S&P trend (immediate)
    sp_trend = macro_raw.get("sp500", 5200) - macro_raw.get("sp500_sma50", 5100)
    scored = _score_indicator("sp500_trend", sp_trend)
    indicators.append({
        "name": "sp500_trend", "raw_value": sp_trend, "score": scored["score"],
        "signal": scored["signal"], "label": scored["label"],
        "horizon": "immediate", "weight": 0.15,
    })

    # Gold/copper ratio (near-term)
    # Use gold_change_30d as proxy
    gold_change = macro_raw.get("gold_change_30d", 0)
    scored = _score_indicator("gold_change_30d", gold_change)
    indicators.append({
        "name": "gold_change_30d", "raw_value": gold_change, "score": scored["score"],
        "signal": scored["signal"], "label": scored["label"],
        "horizon": "near_term", "weight": 0.15,
    })

    return _aggregate_family(indicators, "market_stress")


def _score_macro_leading(macro_raw: dict, extended: dict) -> dict:
    """Family 2: Macro-Economic Leading indicators."""
    from tools.trading.data.macro_data import _score_indicator, score_extended_indicator

    indicators = []
    # LEI (medium-term)
    lei_change = extended.get("lei_monthly_change", 0)
    scored = score_extended_indicator("lei_monthly_change", lei_change)
    indicators.append({
        "name": "lei_monthly_change", "raw_value": lei_change, "score": scored["score"],
        "signal": scored["signal"], "label": scored["label"],
        "horizon": "medium_term", "weight": 0.18,
    })

    # ISM PMI (near-term)
    ism = extended.get("ism_pmi", 50)
    scored = score_extended_indicator("ism_pmi", ism)
    indicators.append({
        "name": "ism_pmi", "raw_value": ism, "score": scored["score"],
        "signal": scored["signal"], "label": scored["label"],
        "horizon": "near_term", "weight": 0.16,
    })

    # Building permits (medium-term)
    permits_yoy = extended.get("building_permits_yoy_pct", 0)
    scored = score_extended_indicator("building_permits_yoy_pct", permits_yoy)
    indicators.append({
        "name": "building_permits_yoy_pct", "raw_value": permits_yoy, "score": scored["score"],
        "signal": scored["signal"], "label": scored["label"],
        "horizon": "medium_term", "weight": 0.13,
    })

    # Initial claims (near-term)
    claims = extended.get("initial_claims_4wma_k", 220)
    scored = score_extended_indicator("initial_claims_4wma_k", claims)
    indicators.append({
        "name": "initial_claims_4wma_k", "raw_value": claims, "score": scored["score"],
        "signal": scored["signal"], "label": scored["label"],
        "horizon": "near_term", "weight": 0.15,
    })

    # Sahm Rule (medium-term)
    sahm = extended.get("sahm_rule", 0.1)
    scored = score_extended_indicator("sahm_rule", sahm)
    indicators.append({
        "name": "sahm_rule", "raw_value": sahm, "score": scored["score"],
        "signal": scored["signal"], "label": scored["label"],
        "horizon": "medium_term", "weight": 0.15,
    })

    # Consumer confidence (near-term)
    cc = extended.get("consumer_confidence", 70)
    scored = score_extended_indicator("consumer_confidence", cc)
    indicators.append({
        "name": "consumer_confidence", "raw_value": cc, "score": scored["score"],
        "signal": scored["signal"], "label": scored["label"],
        "horizon": "near_term", "weight": 0.10,
    })

    # Yield curve (medium-term)
    yield_spread = macro_raw.get("yield_spread", 0.5)
    scored = _score_indicator("yield_spread", yield_spread)
    indicators.append({
        "name": "yield_spread", "raw_value": yield_spread, "score": scored["score"],
        "signal": scored["signal"], "label": scored["label"],
        "horizon": "medium_term", "weight": 0.13,
    })

    return _aggregate_family(indicators, "macro_leading")


def _score_monetary_liquidity(macro_raw: dict, extended: dict) -> dict:
    """Family 3: Monetary/Liquidity indicators (Van Metre + Fed balance sheet)."""
    from tools.trading.data.macro_data import _score_indicator, score_extended_indicator

    indicators = []
    # Van Metre money multiplier
    mm = macro_raw.get("money_multiplier", 3.5)
    scored = _score_indicator("money_multiplier", mm)
    indicators.append({
        "name": "money_multiplier", "raw_value": mm, "score": scored["score"],
        "signal": scored["signal"], "label": scored["label"],
        "horizon": "near_term", "weight": 0.12,
    })

    # Loan growth
    loans = macro_raw.get("loan_growth_yoy", 2)
    scored = _score_indicator("loan_growth_yoy", loans)
    indicators.append({
        "name": "loan_growth_yoy", "raw_value": loans, "score": scored["score"],
        "signal": scored["signal"], "label": scored["label"],
        "horizon": "medium_term", "weight": 0.18,
    })

    # Velocity
    vel = macro_raw.get("velocity_of_money", 1.2)
    scored = _score_indicator("velocity_of_money", vel)
    indicators.append({
        "name": "velocity_of_money", "raw_value": vel, "score": scored["score"],
        "signal": scored["signal"], "label": scored["label"],
        "horizon": "near_term", "weight": 0.10,
    })

    # Fed balance sheet change
    fed_bs_chg = extended.get("fed_bs_change_pct", 0)
    scored = score_extended_indicator("fed_bs_change_pct", fed_bs_chg)
    indicators.append({
        "name": "fed_bs_change_pct", "raw_value": fed_bs_chg, "score": scored["score"],
        "signal": scored["signal"], "label": scored["label"],
        "horizon": "near_term", "weight": 0.12,
    })

    # TGA
    tga = extended.get("tga_balance_b", 500)
    scored = score_extended_indicator("tga_balance_b", tga)
    indicators.append({
        "name": "tga_balance_b", "raw_value": tga, "score": scored["score"],
        "signal": scored["signal"], "label": scored["label"],
        "horizon": "near_term", "weight": 0.08,
    })

    # Net liquidity
    net_liq = extended.get("net_liquidity_b", 6500)
    scored = score_extended_indicator("net_liquidity_b", net_liq)
    indicators.append({
        "name": "net_liquidity_b", "raw_value": net_liq, "score": scored["score"],
        "signal": scored["signal"], "label": scored["label"],
        "horizon": "near_term", "weight": 0.12,
    })

    # M2 YoY
    m2 = extended.get("m2_yoy_pct", 2)
    scored = score_extended_indicator("m2_yoy_pct", m2)
    indicators.append({
        "name": "m2_yoy_pct", "raw_value": m2, "score": scored["score"],
        "signal": scored["signal"], "label": scored["label"],
        "horizon": "medium_term", "weight": 0.10,
    })

    # Bank reserves
    res = extended.get("bank_reserves_b", 3200)
    scored = score_extended_indicator("bank_reserves_b", res)
    indicators.append({
        "name": "bank_reserves_b", "raw_value": res, "score": scored["score"],
        "signal": scored["signal"], "label": scored["label"],
        "horizon": "near_term", "weight": 0.10,
    })

    # RRP
    rrp = macro_raw.get("rrp_balance_b", 200)
    scored = _score_indicator("rrp_balance", rrp)
    indicators.append({
        "name": "rrp_balance", "raw_value": rrp, "score": scored["score"],
        "signal": scored["signal"], "label": scored["label"],
        "horizon": "near_term", "weight": 0.08,
    })

    return _aggregate_family(indicators, "monetary_liquidity")


def _score_news_sentiment() -> dict:
    """Family 4: News Sentiment Intelligence."""
    try:
        from tools.trading.news.aggregator import compute_sentiment_aggregate
        sent = compute_sentiment_aggregate(window_hours=48)
    except Exception:
        sent = {
            "sentiment_score": 0, "cluster_count": 0, "regime_cluster_count": 0,
            "cluster_velocity": 1.0, "meta_scenarios_active": 0,
            "danger_score": 40, "opportunity_score": 50,
        }

    try:
        from tools.trading.news.news_reasoner import compute_omission_density, compute_narrative_convergence
        omissions = compute_omission_density()
        convergence = compute_narrative_convergence()
    except Exception:
        omissions = {"omission_count": 0, "omission_score": 0}
        convergence = {"divergence_count": 0, "convergence_score": 70}

    # Build indicators
    indicators = [
        {
            "name": "news_sentiment_score",
            "raw_value": sent["sentiment_score"],
            "score": int(50 + sent["sentiment_score"] / 2),  # -100..100 → 0..100
            "signal": "RED" if sent["sentiment_score"] < -20 else "GREEN" if sent["sentiment_score"] > 20 else "YELLOW",
            "label": f"Net news sentiment: {sent['sentiment_score']:.1f}",
            "horizon": "immediate", "weight": 0.30,
        },
        {
            "name": "narrative_divergence_count",
            "raw_value": convergence["divergence_count"],
            "score": convergence["convergence_score"],
            "signal": "RED" if convergence["divergence_count"] > 3 else "YELLOW" if convergence["divergence_count"] > 0 else "GREEN",
            "label": f"Narrative: {convergence['narrative_status']}",
            "horizon": "immediate", "weight": 0.25,
        },
        {
            "name": "omission_density",
            "raw_value": omissions["omission_count"],
            "score": 100 - omissions["omission_score"],
            "signal": "RED" if omissions["omission_count"] > 2 else "YELLOW" if omissions["omission_count"] > 0 else "GREEN",
            "label": f"{omissions['omission_count']} key topics omitted by news",
            "horizon": "immediate", "weight": 0.20,
        },
        {
            "name": "cluster_velocity",
            "raw_value": sent["cluster_velocity"],
            "score": max(0, 100 - int(sent["cluster_velocity"] * 30)),
            "signal": "RED" if sent["cluster_velocity"] > 2 else "YELLOW" if sent["cluster_velocity"] > 1.3 else "GREEN",
            "label": f"Cluster velocity: {sent['cluster_velocity']:.2f}x",
            "horizon": "near_term", "weight": 0.15,
        },
        {
            "name": "meta_scenarios_active",
            "raw_value": sent["meta_scenarios_active"],
            "score": max(0, 100 - sent["meta_scenarios_active"] * 40),
            "signal": "RED" if sent["meta_scenarios_active"] > 0 else "GREEN",
            "label": f"{sent['meta_scenarios_active']} meta scenarios active",
            "horizon": "near_term", "weight": 0.10,
        },
    ]

    result = _aggregate_family(indicators, "news_sentiment")
    result["sentiment_details"] = sent
    result["omissions"] = omissions
    result["convergence"] = convergence
    return result


def _score_supply_chain_geo(macro_raw: dict, macro_ctx: dict) -> dict:
    """Family 5: Supply Chain / Geopolitical."""
    geo = macro_ctx.get("geopolitical_risk", {}) or {}
    supply = macro_ctx.get("supply_chain_risk", {}) or {}
    geo_score = int(geo.get("score", 0))
    supply_score = int(supply.get("score", 0))

    # Invert: higher risk = lower indicator score
    geo_indicator_score = max(0, 100 - geo_score)
    supply_indicator_score = max(0, 100 - supply_score)

    # KG stress score
    try:
        from tools.trading.market_intel.cascade_engine import compute_kg_stress_score
        kg_stress = compute_kg_stress_score(max_depth=2)  # cheap depth
    except Exception:
        kg_stress = {"danger_score": 30, "opportunity_score": 60, "stress_depth": 0}

    # Correlation cluster states + monetary catalyst overlay
    monetary_catalyst_score_val = 75
    cluster_divergence_score = 80
    try:
        from tools.trading.market_intel.correlation_engine import (
            score_monetary_catalysts, score_cluster_states
        )
        cat_result = score_monetary_catalysts(macro_ctx.get("raw", {}))
        active_cats = cat_result.get("active_count", 0)
        # More active monetary catalysts = higher opportunity (liquidity-driven rally)
        # but also higher systemic risk if they reverse simultaneously
        monetary_catalyst_score_val = min(90, 50 + active_cats * 12)

        cl_result = score_cluster_states(macro_ctx.get("raw", {}))
        diverging = sum(
            1 for c in cl_result.get("clusters", {}).values()
            if c.get("state") == "diverging"
        )
        bearish_clusters = sum(
            1 for c in cl_result.get("clusters", {}).values()
            if c.get("state") == "bearish"
        )
        # Diverging clusters = warning (internal breakdown, potential cascade)
        cluster_divergence_score = max(20, 85 - diverging * 15 - bearish_clusters * 8)
    except Exception:
        pass

    indicators = [
        {
            "name": "geopolitical_risk",
            "raw_value": geo_score,
            "score": geo_indicator_score,
            "signal": "RED" if geo_score > 50 else "YELLOW" if geo_score > 25 else "GREEN",
            "label": f"Geopolitical: {geo.get('risk_level', 'LOW')}",
            "horizon": "immediate", "weight": 0.20,
        },
        {
            "name": "supply_chain_risk",
            "raw_value": supply_score,
            "score": supply_indicator_score,
            "signal": "RED" if supply_score > 55 else "YELLOW" if supply_score > 25 else "GREEN",
            "label": f"Supply chain: {supply.get('risk_level', 'LOW')}",
            "horizon": "immediate", "weight": 0.20,
        },
        {
            "name": "kg_stress_depth",
            "raw_value": kg_stress.get("stress_depth", 0),
            "score": int(kg_stress["opportunity_score"]),
            "signal": "RED" if kg_stress["danger_score"] > 65 else "YELLOW" if kg_stress["danger_score"] > 40 else "GREEN",
            "label": f"KG stress depth: {kg_stress.get('stress_depth', 0):.1f}",
            "horizon": "near_term", "weight": 0.20,
        },
        {
            "name": "monetary_catalyst_environment",
            "raw_value": monetary_catalyst_score_val,
            "score": monetary_catalyst_score_val,
            "signal": "GREEN" if monetary_catalyst_score_val >= 70 else "YELLOW" if monetary_catalyst_score_val >= 50 else "RED",
            "label": f"Monetary catalysts: {monetary_catalyst_score_val:.0f}/100",
            "horizon": "near_term", "weight": 0.20,
        },
        {
            "name": "cluster_correlation_divergence",
            "raw_value": cluster_divergence_score,
            "score": cluster_divergence_score,
            "signal": "GREEN" if cluster_divergence_score >= 70 else "YELLOW" if cluster_divergence_score >= 45 else "RED",
            "label": f"Cluster coherence: {cluster_divergence_score:.0f}/100",
            "horizon": "near_term", "weight": 0.20,
        },
    ]

    return _aggregate_family(indicators, "supply_chain_geo")


def _score_cross_asset_divergence(macro_raw: dict, extended: dict) -> dict:
    """Family 6: Cross-Asset Divergences."""
    # Fetch rotation signal to pass into divergence detector (XAR-04)
    rotation = None
    try:
        from tools.trading.market_intel.cross_asset_rotation import compute_cross_asset_rotation
        xar = compute_cross_asset_rotation()
        rotation = {
            "rotation_signal": xar.get("rotation_signal", "NEUTRAL"),
            "rotation_score": xar.get("rotation_score", 0),
            "triggered_conditions": xar.get("triggered_conditions", []),
        }
    except Exception:
        pass

    try:
        from tools.trading.market_intel.cross_asset_divergence import detect_divergences
        result = detect_divergences(macro_raw, extended, rotation=rotation)
    except Exception:
        result = {"divergences": [], "divergence_count": 0, "danger_score": 20, "opportunity_score": 75}

    # Build a single composite indicator per divergence
    indicators = []
    for d in result.get("divergences", []):
        indicators.append({
            "name": d["name"],
            "raw_value": d["danger_score"],
            "score": max(0, 100 - d["danger_score"]),
            "signal": "RED" if d["severity"] in ("high", "critical") else "YELLOW",
            "label": d["message"],
            "horizon": HORIZON_MAP.get(d["name"], "near_term"),
            "weight": 1.0,
        })

    if not indicators:
        # No divergences = low-risk indicator
        indicators.append({
            "name": "no_divergences",
            "raw_value": 0,
            "score": 85,
            "signal": "GREEN",
            "label": "No cross-asset divergences detected",
            "horizon": "immediate",
            "weight": 1.0,
        })

    return _aggregate_family(indicators, "cross_asset_diverge")


def _score_institutional_flow() -> dict:
    """Family 7: Institutional Flow (insiders + analysts)."""
    conn = get_conn()
    insider_ratio = 0.5  # default: neutral
    analyst_ratio = 0.5

    try:
        # Insider transactions last 30 days
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        rows = conn.execute(
            "SELECT transaction_type, COUNT(*) FROM ad_insider_transactions "
            "WHERE created_at >= %s GROUP BY transaction_type",
            (cutoff,)
        ).fetchall()
        buys = 0
        sells = 0
        for row in rows:
            ttype = (row[0] or "").lower()
            count = int(row[1])
            if "buy" in ttype or "purchase" in ttype:
                buys += count
            elif "sell" in ttype or "sale" in ttype:
                sells += count
        total = buys + sells
        if total > 0:
            insider_ratio = buys / total

        # Analyst ratings last 30 days
        rows = conn.execute(
            "SELECT change, COUNT(*) FROM ad_analyst_ratings "
            "WHERE created_at >= %s GROUP BY change",
            (cutoff,)
        ).fetchall()
        upgrades = 0
        downgrades = 0
        for row in rows:
            change = (row[0] or "").lower()
            count = int(row[1])
            if "upgrade" in change:
                upgrades += count
            elif "downgrade" in change:
                downgrades += count
        total = upgrades + downgrades
        if total > 0:
            analyst_ratio = upgrades / total
    except Exception:
        pass

    indicators = [
        {
            "name": "insider_ratio",
            "raw_value": insider_ratio,
            "score": int(insider_ratio * 100),  # 1.0 = all buys = score 100
            "signal": "GREEN" if insider_ratio > 0.55 else "RED" if insider_ratio < 0.35 else "YELLOW",
            "label": f"Insider buy ratio: {insider_ratio:.1%}",
            "horizon": "near_term", "weight": 0.50,
        },
        {
            "name": "analyst_ratio",
            "raw_value": analyst_ratio,
            "score": int(analyst_ratio * 100),
            "signal": "GREEN" if analyst_ratio > 0.55 else "RED" if analyst_ratio < 0.35 else "YELLOW",
            "label": f"Analyst upgrade ratio: {analyst_ratio:.1%}",
            "horizon": "near_term", "weight": 0.50,
        },
    ]

    return _aggregate_family(indicators, "institutional_flow")


def _score_historical_pattern(macro_raw: dict, extended: dict) -> dict:
    """Family 8: Historical Pattern Matching."""
    try:
        from tools.trading.market_intel.crisis_fingerprints import analyze_historical_patterns
        result = analyze_historical_patterns(macro_raw, extended)
    except Exception:
        result = {
            "recession_precursors": {"count": 0, "total": 7, "danger_score": 10, "active": []},
            "crisis_similarity": {"max_similarity": 0, "danger_score": 0, "closest_match": None},
            "hmm_transitions": {"combined_risk_prob": 0.2, "danger_score": 20},
            "danger_score": 15,
            "opportunity_score": 75,
        }

    precursors = result["recession_precursors"]
    similarity = result["crisis_similarity"]
    hmm = result["hmm_transitions"]

    indicators = [
        {
            "name": "recession_precursors",
            "raw_value": precursors["count"],
            "score": max(0, 100 - precursors["danger_score"]),
            "signal": "RED" if precursors["count"] >= 4 else "YELLOW" if precursors["count"] >= 2 else "GREEN",
            "label": f"{precursors['count']}/{precursors['total']} recession precursors active",
            "horizon": "medium_term", "weight": 0.40,
        },
        {
            "name": "crisis_similarity",
            "raw_value": similarity.get("max_similarity", 0),
            "score": max(0, 100 - similarity["danger_score"]),
            "signal": "RED" if similarity["max_similarity"] > 0.7 else "YELLOW" if similarity["max_similarity"] > 0.5 else "GREEN",
            "label": f"Closest crisis: {similarity.get('closest_match', 'none')} ({similarity.get('max_similarity', 0):.0%})",
            "horizon": "medium_term", "weight": 0.35,
        },
        {
            "name": "hmm_transition",
            "raw_value": hmm.get("combined_risk_prob", 0),
            "score": max(0, 100 - hmm["danger_score"]),
            "signal": "RED" if hmm["combined_risk_prob"] > 0.4 else "YELLOW" if hmm["combined_risk_prob"] > 0.25 else "GREEN",
            "label": f"HMM P(risk-off): {hmm.get('combined_risk_prob', 0):.0%}",
            "horizon": "near_term", "weight": 0.25,
        },
    ]

    out = _aggregate_family(indicators, "historical_pattern")
    out["details"] = result
    return out


# ---------------------------------------------------------------------------
# Aggregation helpers
# ---------------------------------------------------------------------------

def _aggregate_family(indicators: list[dict], family: str) -> dict:
    """Aggregate indicator scores into family-level danger/opportunity."""
    total_weight = sum(i["weight"] for i in indicators)
    if total_weight == 0:
        return {
            "family": family, "danger_score": 50, "opportunity_score": 50,
            "indicators": indicators, "red_count": 0, "green_count": 0,
        }

    # Normalized score: weighted avg (0-100 where higher = better/greener)
    avg_score = sum(i["score"] * i["weight"] for i in indicators) / total_weight
    # Opportunity = score; Danger = 100 - score
    opportunity = round(avg_score, 1)
    danger = round(100 - avg_score, 1)

    red_count = sum(1 for i in indicators if i["signal"] == "RED")
    green_count = sum(1 for i in indicators if i["signal"] == "GREEN")

    return {
        "family": family,
        "danger_score": danger,
        "opportunity_score": opportunity,
        "indicators": indicators,
        "red_count": red_count,
        "green_count": green_count,
    }


def _compute_horizon_scores(family_results: dict) -> dict:
    """Compute danger/opportunity scores per time horizon."""
    config = _load_config()
    family_weights = config["family_weights"]

    horizons = ["immediate", "near_term", "medium_term"]
    result = {}

    for horizon in horizons:
        total_danger = 0.0
        total_opp = 0.0
        total_weight = 0.0

        for family, data in family_results.items():
            horizon_indicators = [i for i in data["indicators"] if i.get("horizon") == horizon]
            if not horizon_indicators:
                continue
            # Family-local weights for these horizon indicators
            hw = sum(i["weight"] for i in horizon_indicators)
            if hw == 0:
                continue
            horizon_score = sum(i["score"] * i["weight"] for i in horizon_indicators) / hw
            family_weight = family_weights.get(family, 0.1)
            total_danger += (100 - horizon_score) * family_weight
            total_opp += horizon_score * family_weight
            total_weight += family_weight

        if total_weight > 0:
            result[horizon] = {
                "danger": round(total_danger / total_weight, 1),
                "opportunity": round(total_opp / total_weight, 1),
            }
        else:
            result[horizon] = {"danger": 50.0, "opportunity": 50.0}

    return result


def _compute_composite(family_results: dict) -> dict:
    """Compute composite danger/opportunity from family results."""
    config = _load_config()
    family_weights = config["family_weights"]

    danger = 0.0
    opportunity = 0.0
    total_weight = 0.0

    for family, data in family_results.items():
        w = family_weights.get(family, 0.1)
        danger += data["danger_score"] * w
        opportunity += data["opportunity_score"] * w
        total_weight += w

    if total_weight > 0:
        danger /= total_weight
        opportunity /= total_weight

    return {
        "danger": round(danger, 1),
        "opportunity": round(opportunity, 1),
    }


def _classify_regime(composite: dict) -> str:
    """Classify regime based on composite scores."""
    d = composite["danger"]
    o = composite["opportunity"]

    if d >= 85 and o <= 20:
        return "CRISIS"
    elif d >= 75 and o <= 30:
        return "DANGER"
    elif d >= 60 and o <= 40:
        return "CAUTION"
    elif d <= 25 and o >= 60:
        return "STRONG_OPPORTUNITY"
    elif d <= 40 and o >= 50:
        return "OPPORTUNITY"
    else:
        return "NEUTRAL"


# ---------------------------------------------------------------------------
# Alert generation
# ---------------------------------------------------------------------------

def _generate_alerts(composite: dict, family_results: dict, regime: str) -> list[dict]:
    """Generate SROR alerts based on composite state."""
    config = _load_config()
    alerts = []
    danger_threshold = config["alerts"].get("danger_escalation_threshold", 75)
    opp_threshold = config["alerts"].get("opportunity_emerging_threshold", 65)
    min_red = config["alerts"].get("min_families_red_for_critical", 3)

    red_families = sum(
        1 for f in family_results.values()
        if f["red_count"] > (f["red_count"] + f["green_count"]) / 2
    )

    # Danger escalation
    if composite["danger"] >= danger_threshold:
        severity = "critical" if red_families >= min_red else "high"
        alerts.append({
            "alert_type": "systemic_danger_escalation",
            "severity": severity,
            "family": None,
            "indicator_name": None,
            "message": f"Composite danger at {composite['danger']:.0f} — regime {regime} ({red_families} families RED)",
            "threshold_breached": float(danger_threshold),
            "current_value": composite["danger"],
        })

    # Opportunity emerging
    if composite["opportunity"] >= opp_threshold:
        alerts.append({
            "alert_type": "systemic_opportunity_emerging",
            "severity": "medium",
            "family": None,
            "indicator_name": None,
            "message": f"Opportunity score at {composite['opportunity']:.0f} — regime {regime}",
            "threshold_breached": float(opp_threshold),
            "current_value": composite["opportunity"],
        })

    # Per-family critical alerts
    for family, data in family_results.items():
        if data["danger_score"] >= 80:
            alerts.append({
                "alert_type": "family_danger_spike",
                "severity": "high",
                "family": family,
                "indicator_name": None,
                "message": f"{family} family at danger score {data['danger_score']:.0f} ({data['red_count']} RED indicators)",
                "threshold_breached": 80.0,
                "current_value": data["danger_score"],
            })

    return alerts


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _uid() -> str:
    return str(uuid.uuid4())[:12]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _save_snapshot(
    composite: dict,
    horizons: dict,
    family_results: dict,
    regime: str,
    data_source: str,
) -> str:
    """Persist snapshot to DB and return snapshot ID."""
    conn = get_conn()
    snapshot_id = _uid()

    # Collect active divergences
    cad = family_results.get("cross_asset_diverge", {})
    active_divs = [i.get("label") for i in cad.get("indicators", []) if i.get("signal") in ("RED", "YELLOW")]

    # Historical pattern details
    hp = family_results.get("historical_pattern", {})
    hp_details = hp.get("details", {})
    precursor_count = hp_details.get("recession_precursors", {}).get("count", 0)

    family_scores = {
        f: {"danger": d["danger_score"], "opportunity": d["opportunity_score"],
            "red": d["red_count"], "green": d["green_count"]}
        for f, d in family_results.items()
    }

    indicator_details = {}
    for f, d in family_results.items():
        indicator_details[f] = [
            {k: v for k, v in i.items() if k not in ("raw_value",) or isinstance(v, (int, float, str, bool))}
            for i in d["indicators"]
        ]

    conn.execute(
        "INSERT INTO ad_systemic_radar_snapshots ("
        "id, danger_score_immediate, danger_score_near_term, danger_score_medium_term, "
        "opportunity_score_immediate, opportunity_score_near_term, opportunity_score_medium_term, "
        "composite_danger, composite_opportunity, regime_label, "
        "family_scores_json, indicator_details_json, active_divergences_json, "
        "recession_precursor_count, crisis_similarity_json, hmm_transition_json, "
        "data_source, created_at) VALUES ("
        "%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            snapshot_id,
            horizons["immediate"]["danger"], horizons["near_term"]["danger"], horizons["medium_term"]["danger"],
            horizons["immediate"]["opportunity"], horizons["near_term"]["opportunity"], horizons["medium_term"]["opportunity"],
            composite["danger"], composite["opportunity"], regime,
            json.dumps(family_scores),
            json.dumps(indicator_details, default=str),
            json.dumps(active_divs),
            precursor_count,
            json.dumps(hp_details.get("crisis_similarity", {}), default=str),
            json.dumps(hp_details.get("hmm_transitions", {}), default=str),
            data_source,
            _now(),
        ),
    )

    # Save individual indicators
    for family, data in family_results.items():
        for ind in data["indicators"]:
            conn.execute(
                "INSERT INTO ad_systemic_radar_indicators ("
                "id, snapshot_id, family, indicator_name, raw_value, normalized_score, "
                "signal, time_horizon, weight, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    _uid(), snapshot_id, family,
                    str(ind.get("name", "")),
                    float(ind.get("raw_value", 0)) if isinstance(ind.get("raw_value"), (int, float)) else 0.0,
                    float(ind.get("score", 50)),
                    str(ind.get("signal", "NEUTRAL")),
                    str(ind.get("horizon", "immediate")),
                    float(ind.get("weight", 0.1)),
                    _now(),
                ),
            )

    conn.commit()
    return snapshot_id


def _save_alerts(snapshot_id: str, alerts: list[dict]) -> None:
    """Persist alerts to DB."""
    if not alerts:
        return
    conn = get_conn()
    for alert in alerts:
        conn.execute(
            "INSERT INTO ad_systemic_radar_alerts ("
            "id, snapshot_id, alert_type, severity, family, indicator_name, "
            "message, threshold_breached, current_value, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                _uid(), snapshot_id,
                alert["alert_type"], alert["severity"],
                alert.get("family"), alert.get("indicator_name"),
                alert["message"], alert.get("threshold_breached"),
                alert.get("current_value"), _now(),
            ),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Main compute function
# ---------------------------------------------------------------------------

def compute_and_store() -> dict:
    """Run full SROR computation and persist snapshot.

    Returns dict with snapshot_id, composite scores, regime, alerts.
    """
    from tools.trading.data.macro_data import fetch_macro_context, fetch_extended_macro

    config = _load_config()
    enabled_families = {k for k, v in config.get("families", {}).items() if v.get("enabled", True)}

    # Fetch data
    macro_ctx = fetch_macro_context()
    macro_raw = macro_ctx.get("raw_values", {})
    extended = fetch_extended_macro()
    data_source = macro_ctx.get("data_source", "sample")

    # Score each family
    family_results = {}

    if "market_stress" in enabled_families:
        family_results["market_stress"] = _score_market_stress(macro_raw, extended)
    if "macro_leading" in enabled_families:
        family_results["macro_leading"] = _score_macro_leading(macro_raw, extended)
    if "monetary_liquidity" in enabled_families:
        family_results["monetary_liquidity"] = _score_monetary_liquidity(macro_raw, extended)
    if "news_sentiment" in enabled_families:
        family_results["news_sentiment"] = _score_news_sentiment()
    if "supply_chain_geo" in enabled_families:
        family_results["supply_chain_geo"] = _score_supply_chain_geo(macro_raw, macro_ctx)
    if "cross_asset_diverge" in enabled_families:
        family_results["cross_asset_diverge"] = _score_cross_asset_divergence(macro_raw, extended)
    if "institutional_flow" in enabled_families:
        family_results["institutional_flow"] = _score_institutional_flow()
    if "historical_pattern" in enabled_families:
        family_results["historical_pattern"] = _score_historical_pattern(macro_raw, extended)

    # Compute composite + horizon scores
    composite = _compute_composite(family_results)
    horizons = _compute_horizon_scores(family_results)

    # Divergence bonus
    cad = family_results.get("cross_asset_diverge", {})
    cad_count = len([i for i in cad.get("indicators", []) if i.get("signal") == "RED"])
    if cad_count > 0:
        bonus = config["scoring"].get("divergence_danger_bonus", 10)
        composite["danger"] = min(100, composite["danger"] + bonus)
        composite["opportunity"] = max(0, composite["opportunity"] - bonus / 2)

    # Geopolitical-news contribution to composite_danger. Feeds the Radar
    # from the news_impact_tracer pipeline: country-subject events with
    # negative net ticker impact raise danger on the immediate horizon.
    try:
        from tools.trading.news.impact_store import geopolitical_danger_score
        geo = geopolitical_danger_score(hours=24)
        geo_danger = int(geo.get("danger_score", 0) or 0)
        if geo_danger > 0:
            # Cap the lift to 20 points so a single event can't dominate
            geo_lift = min(20, geo_danger * 0.2)
            composite["danger"] = min(100, composite["danger"] + geo_lift)
            composite["_geopolitical_news"] = geo
            composite["_geopolitical_lift"] = round(geo_lift, 2)
    except Exception:
        pass

    # Classify regime
    regime = _classify_regime(composite)

    # Generate alerts
    alerts = _generate_alerts(composite, family_results, regime)

    # Save snapshot
    snapshot_id = _save_snapshot(composite, horizons, family_results, regime, data_source)
    _save_alerts(snapshot_id, alerts)

    return {
        "snapshot_id": snapshot_id,
        "composite": composite,
        "horizons": horizons,
        "regime": regime,
        "family_scores": {
            f: {"danger": d["danger_score"], "opportunity": d["opportunity_score"],
                "red": d["red_count"], "green": d["green_count"]}
            for f, d in family_results.items()
        },
        "alerts": alerts,
        "data_source": data_source,
        "timestamp": _now(),
    }


# ---------------------------------------------------------------------------
# Query functions (for dashboard API)
# ---------------------------------------------------------------------------

def get_latest_snapshot() -> dict | None:
    """Fetch the most recent SROR snapshot."""
    conn = get_conn()
    row = conn.execute(
        "SELECT id, danger_score_immediate, danger_score_near_term, danger_score_medium_term, "
        "opportunity_score_immediate, opportunity_score_near_term, opportunity_score_medium_term, "
        "composite_danger, composite_opportunity, regime_label, "
        "family_scores_json, active_divergences_json, recession_precursor_count, "
        "data_source, created_at "
        "FROM ad_systemic_radar_snapshots ORDER BY created_at DESC LIMIT 1"
    ).fetchone()

    if not row:
        return None

    return {
        "snapshot_id": row[0],
        "horizons": {
            "immediate": {"danger": row[1], "opportunity": row[4]},
            "near_term": {"danger": row[2], "opportunity": row[5]},
            "medium_term": {"danger": row[3], "opportunity": row[6]},
        },
        "composite": {"danger": row[7], "opportunity": row[8]},
        "regime": row[9],
        "family_scores": json.loads(row[10] or "{}"),
        "active_divergences": json.loads(row[11] or "[]"),
        "recession_precursor_count": row[12],
        "data_source": row[13],
        "timestamp": row[14],
    }


def get_history(days: int = 30) -> list[dict]:
    """Fetch historical snapshots for charting."""
    conn = get_conn()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = conn.execute(
        "SELECT id, composite_danger, composite_opportunity, regime_label, "
        "danger_score_immediate, danger_score_near_term, danger_score_medium_term, "
        "created_at FROM ad_systemic_radar_snapshots "
        "WHERE created_at >= %s ORDER BY created_at ASC",
        (cutoff,)
    ).fetchall()

    return [
        {
            "snapshot_id": row[0],
            "composite_danger": row[1],
            "composite_opportunity": row[2],
            "regime": row[3],
            "danger_immediate": row[4],
            "danger_near_term": row[5],
            "danger_medium_term": row[6],
            "timestamp": row[7],
        }
        for row in rows
    ]


def get_active_alerts(limit: int = 50) -> list[dict]:
    """Fetch recent unacknowledged alerts."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT id, snapshot_id, alert_type, severity, family, indicator_name, "
        "message, threshold_breached, current_value, created_at "
        "FROM ad_systemic_radar_alerts "
        "WHERE acknowledged = 0 ORDER BY created_at DESC LIMIT %s",
        (limit,)
    ).fetchall()

    return [
        {
            "id": row[0], "snapshot_id": row[1], "alert_type": row[2],
            "severity": row[3], "family": row[4], "indicator_name": row[5],
            "message": row[6], "threshold_breached": row[7],
            "current_value": row[8], "created_at": row[9],
        }
        for row in rows
    ]


def get_family_indicators(snapshot_id: str, family: str) -> list[dict]:
    """Fetch detailed indicators for a family within a snapshot."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT indicator_name, raw_value, normalized_score, signal, "
        "time_horizon, weight FROM ad_systemic_radar_indicators "
        "WHERE snapshot_id = %s AND family = %s ORDER BY weight DESC",
        (snapshot_id, family)
    ).fetchall()

    return [
        {
            "name": row[0], "raw_value": row[1], "score": row[2],
            "signal": row[3], "horizon": row[4], "weight": row[5],
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Systemic Risk & Opportunity Radar (SROR)")
    parser.add_argument("--compute", action="store_true", help="Compute and store new snapshot")
    parser.add_argument("--latest", action="store_true", help="Fetch latest snapshot")
    parser.add_argument("--history", action="store_true", help="Fetch historical snapshots")
    parser.add_argument("--alerts", action="store_true", help="Fetch active alerts")
    parser.add_argument("--days", type=int, default=30, help="History window days")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    result = None
    if args.compute:
        result = compute_and_store()
    elif args.latest:
        result = get_latest_snapshot()
    elif args.history:
        result = get_history(args.days)
    elif args.alerts:
        result = get_active_alerts()
    else:
        parser.print_help()
        return

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        if args.compute and result:
            print(f"Snapshot: {result['snapshot_id']}")
            print(f"Regime: {result['regime']}")
            print(f"Composite danger: {result['composite']['danger']}")
            print(f"Composite opportunity: {result['composite']['opportunity']}")
            print(f"Alerts: {len(result['alerts'])}")
        elif result:
            print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
