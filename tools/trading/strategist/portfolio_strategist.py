#!/usr/bin/env python3
# CUI // SP-CTI
"""FathomDesk Portfolio Strategist — autonomous long-term investment agent.

Synthesizes ALL FathomDesk data (signal heatmap, multi-timeframe performance,
macro regime, KG centrality, scenario resilience, expert consensus, quality
score) to produce actionable portfolio strategies with 4-tier allocation:

  Core (60-70%)       — Long-term compounders: 10yr/20yr winners + moat + centrality
  Tactical (15-25%)   — Sector rotation aligned with macro regime + 1yr/5yr momentum
  Opportunistic (5-10%) — Event-driven from cascade/scenario analysis
  Hedges (5-10%)      — Protective positions from scenario downside + correlation breaks

Quality Integration
-------------------
A 9th factor — quality_score (0-100) from tools/trading/analysts/quality.py —
is incorporated into the composite ranking with regime-aware weighting:

  Default regimes : quality_weight = 0.10
  Stagflation     : quality_weight = 0.30 (elevated; favors FCF-backed, low-
                    leverage dividend growers; penalises high-PE/low-FCF names)

Stagflation-specific rules
--------------------------
* quality_weight elevated to 0.30 (other weights scaled down proportionally)
* Core-tier gate: quality_score >= 70 required (low-quality names demoted to
  tactical or opportunistic)
* High-PE / low-FCF penalty: if quality_score < 50, composite rank is
  multiplied by 0.85 (15% haircut) to push poor-quality names to lower tiers
* Rationale strings explicitly flag quality in stagflation context

Usage:
    python tools/trading/strategist/portfolio_strategist.py --run
    python tools/trading/strategist/portfolio_strategist.py --run --tier core
    python tools/trading/strategist/portfolio_strategist.py --latest --json
    python tools/trading/strategist/portfolio_strategist.py --backtest --json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[3]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.trading.db import get_conn
from tools.trading.market_intel.universe import (
    ALL_SECTORS,
    get_full_universe,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
TIER_WEIGHTS = {
    "core": (0.60, 0.70),  # 60-70%
    "tactical": (0.15, 0.25),  # 15-25%
    "opportunistic": (0.05, 0.10),  # 5-10%
    "hedge": (0.05, 0.10),  # 5-10%
}

PERIOD_DAYS = {"p1y": 252, "p5y": 1260, "p10y": 2520, "p20y": 5040}

# Multi-timeframe weights for composite momentum scoring
TIMEFRAME_WEIGHTS = {
    "p1y": 0.15,  # Recent momentum (short-term signal)
    "p5y": 0.25,  # Medium cycle (business cycle exposure)
    "p10y": 0.35,  # Full cycle compounder identification
    "p20y": 0.25,  # Secular trend / survivor bias filter
}

# ---------------------------------------------------------------------------
# Quality weight constants
# ---------------------------------------------------------------------------
# Base quality weight applied in all non-stagflation regimes.
QUALITY_WEIGHT_BASE: float = 0.10

# Elevated quality weight for stagflation — rewards FCF-backed, low-leverage
# dividend growers and penalises high-PE / low-FCF names.
QUALITY_WEIGHT_STAGFLATION: float = 0.30

# Core-tier quality gate in stagflation: tickers below this score are demoted.
QUALITY_GATE_STAGFLATION_CORE: float = 70.0

# Composite rank haircut applied to low-quality names (<50) in stagflation.
QUALITY_LOW_PENALTY_STAGFLATION: float = 0.85

# ---------------------------------------------------------------------------
# Composite factor weights per regime
# ---------------------------------------------------------------------------
# Weights must sum to 1.0.  "quality" is the 9th factor added in this task.
#
# Default (non-stagflation):
#   momentum 0.22 | consistency 0.15 | macro 0.13 | expert 0.10 |
#   scenario 0.10 | kg 0.05 | heatmap 0.12 | heatmap_conf 0.03 | quality 0.10
#
# Stagflation (+0.20 to quality, spread reduction across other factors):
#   momentum 0.12 | consistency 0.13 | macro 0.11 | expert 0.08 |
#   scenario 0.10 | kg 0.03 | heatmap 0.10 | heatmap_conf 0.03 | quality 0.30
_FACTOR_WEIGHTS: dict[str, dict[str, float]] = {
    "default": {
        "momentum":           0.22,
        "consistency":        0.15,
        "macro_alignment":    0.13,
        "expert_alignment":   0.10,
        "scenario_resilience":0.10,
        "kg_centrality":      0.05,
        "heatmap_score":      0.12,
        "heatmap_confidence": 0.03,
        "quality":            0.10,
    },
    "stagflation": {
        "momentum":           0.12,
        "consistency":        0.13,
        "macro_alignment":    0.11,
        "expert_alignment":   0.08,
        "scenario_resilience":0.10,
        "kg_centrality":      0.03,
        "heatmap_score":      0.10,
        "heatmap_confidence": 0.03,
        "quality":            0.30,
    },
}

# Macro regime → sector affinity matrix (positive = overweight, negative = underweight)
REGIME_SECTOR_AFFINITY = {
    "expansion": {
        "Technology": 0.8,
        "Semiconductors": 0.9,
        "Consumer Discretionary": 0.7,
        "Communication Services": 0.6,
        "Cybersecurity": 0.5,
        "Financials": 0.4,
        "Fintech": 0.7,
        "Banks": 0.3,
        "Industrials": 0.5,
        "Materials": 0.3,
        "Utilities": -0.4,
        "Consumer Staples": -0.3,
        "Real Estate": 0.2,
        "Healthcare": 0.2,
        "Big Pharma": -0.1,
        "Biotech": 0.6,
        "Defense": 0.1,
        "Oil & Gas": 0.2,
        "Crypto": 0.7,
        "Fixed Income": -0.6,
        "Commodities": 0.1,
        "International": 0.3,
        "Index": 0.5,
        "Dividend": -0.2,
        "Thematic": 0.8,
        "Balanced": 0.1,
        "Energy": 0.2,
        "Forex": 0.0,
    },
    "contraction": {
        "Technology": -0.3,
        "Semiconductors": -0.5,
        "Consumer Discretionary": -0.6,
        "Communication Services": -0.2,
        "Cybersecurity": 0.3,
        "Financials": -0.4,
        "Fintech": -0.5,
        "Banks": -0.6,
        "Industrials": -0.3,
        "Materials": -0.4,
        "Utilities": 0.8,
        "Consumer Staples": 0.7,
        "Real Estate": -0.3,
        "Healthcare": 0.6,
        "Big Pharma": 0.7,
        "Biotech": -0.2,
        "Defense": 0.5,
        "Oil & Gas": -0.2,
        "Crypto": -0.8,
        "Fixed Income": 0.9,
        "Commodities": 0.3,
        "International": -0.3,
        "Index": -0.4,
        "Dividend": 0.6,
        "Thematic": -0.7,
        "Balanced": 0.3,
        "Energy": -0.3,
        "Forex": 0.3,
    },
    "recovery": {
        "Technology": 0.5,
        "Semiconductors": 0.7,
        "Consumer Discretionary": 0.8,
        "Communication Services": 0.4,
        "Cybersecurity": 0.3,
        "Financials": 0.6,
        "Fintech": 0.6,
        "Banks": 0.7,
        "Industrials": 0.6,
        "Materials": 0.5,
        "Utilities": -0.2,
        "Consumer Staples": -0.1,
        "Real Estate": 0.4,
        "Healthcare": 0.2,
        "Big Pharma": 0.0,
        "Biotech": 0.4,
        "Defense": 0.2,
        "Oil & Gas": 0.3,
        "Crypto": 0.5,
        "Fixed Income": -0.4,
        "Commodities": 0.2,
        "International": 0.4,
        "Index": 0.6,
        "Dividend": 0.1,
        "Thematic": 0.5,
        "Balanced": 0.2,
        "Energy": 0.3,
        "Forex": 0.0,
    },
    "stagflation": {
        "Technology": -0.5,
        "Semiconductors": -0.6,
        "Consumer Discretionary": -0.8,
        "Communication Services": -0.3,
        "Cybersecurity": 0.2,
        "Financials": -0.3,
        "Fintech": -0.6,
        "Banks": -0.2,
        "Industrials": -0.2,
        "Materials": 0.4,
        "Utilities": 0.6,
        "Consumer Staples": 0.8,
        "Real Estate": -0.5,
        "Healthcare": 0.5,
        "Big Pharma": 0.6,
        "Biotech": -0.3,
        "Defense": 0.7,
        "Oil & Gas": 0.8,
        "Crypto": -0.7,
        "Fixed Income": 0.4,
        "Commodities": 0.9,
        "International": -0.4,
        "Index": -0.5,
        "Dividend": 0.5,
        "Thematic": -0.6,
        "Balanced": 0.1,
        "Energy": 0.7,
        "Forex": 0.2,
    },
    "neutral": {sector: 0.0 for sector in ALL_SECTORS},
}


# ---------------------------------------------------------------------------
# Performance computation (mirrors dashboard _compute_ticker_performance)
# ---------------------------------------------------------------------------
def compute_ticker_performance(ticker: str) -> dict:
    """Deterministic 1yr/5yr/10yr/20yr total return % for a ticker."""
    max_days = 5040
    seed = int(hashlib.sha256(ticker.encode("utf-8")).hexdigest()[:8], 16)
    base_price = 100.0 + (seed % 400)
    price = base_price

    lookback_days = {max_days - d: key for key, d in PERIOD_DAYS.items() if d < max_days}
    prices_at = {}

    for day in range(1, max_days + 1):
        seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
        change_pct = ((seed % 1000) - 500) / 10000.0
        price *= 1 + change_pct
        if day in lookback_days:
            prices_at[lookback_days[day]] = price

    end_price = price
    result = {}
    for key, period_days in PERIOD_DAYS.items():
        start = base_price if period_days == max_days else prices_at.get(key)
        if start and start > 0:
            result[key] = round((end_price - start) / start * 100, 1)
        else:
            result[key] = 0.0
    return result


# ---------------------------------------------------------------------------
# Data collectors — pull everything from FathomDesk DB
# ---------------------------------------------------------------------------
def _collect_signals(conn) -> dict[str, dict]:
    """Latest signal per ticker from ad_signals."""
    rows = conn.execute(
        "SELECT ticker, direction, composite_score, confidence, component_scores "
        "FROM ad_signals WHERE id IN ("
        "  SELECT id FROM ad_signals s2 WHERE s2.ticker = ad_signals.ticker "
        "  ORDER BY created_at DESC LIMIT 1"
        ") ORDER BY composite_score DESC"
    ).fetchall()
    signals = {}
    for r in rows:
        d = dict(r)
        try:
            d["components"] = json.loads(d.get("component_scores") or "{}")
        except (json.JSONDecodeError, TypeError):
            d["components"] = {}
        signals[d["ticker"]] = d
    return signals


def _collect_macro(conn) -> dict:
    """Latest macro context."""
    row = conn.execute("SELECT * FROM ad_macro_context ORDER BY created_at DESC LIMIT 1").fetchone()
    if not row:
        return {"regime": "neutral", "regime_score": 50.0}
    return dict(row)


def _collect_macro_sector_impacts(conn) -> dict[str, dict]:
    """Latest sector-level macro impact scores."""
    rows = conn.execute(
        "SELECT sector, rate_effect, oil_effect, dxy_effect, impact_score "
        "FROM ad_macro_sector_impact "
        "WHERE id IN ("
        "  SELECT MAX(id) FROM ad_macro_sector_impact GROUP BY sector"
        ")"
    ).fetchall()
    result = {}
    for r in rows:
        d = dict(r)
        d["composite_impact"] = d.get("impact_score", 0) or 0
        result[d["sector"]] = d
    return result


def _collect_expert_consensus(conn) -> dict[str, dict]:
    """Latest CIS recommendation per ticker."""
    rows = conn.execute(
        "SELECT ticker, final_direction, final_conviction, narrative, auto_trade "
        "FROM ad_cis_recommendations "
        "WHERE id IN ("
        "  SELECT MAX(id) FROM ad_cis_recommendations GROUP BY ticker"
        ")"
    ).fetchall()
    result = {}
    for r in rows:
        d = dict(r)
        d["direction"] = d.get("final_direction", "HOLD")
        d["conviction"] = d.get("final_conviction", 50) or 50
        d["confidence"] = 50  # derived from expert_votes if needed
        result[d["ticker"]] = d
    return result


def _collect_scenario_resilience(conn) -> dict[str, float]:
    """Average scenario impact (1yr) per ticker — lower magnitude = more resilient."""
    rows = conn.execute(
        "SELECT entity_name, AVG(ABS(impact_1y_pct)) as avg_abs_impact "
        "FROM ad_scenario_impacts WHERE entity_type = 'ticker' "
        "GROUP BY entity_name"
    ).fetchall()
    resilience = {}
    for r in rows:
        avg = r["avg_abs_impact"] or 0.0
        # Convert to 0-100 resilience score (lower impact = higher resilience)
        resilience[r["entity_name"]] = round(max(0, 100 - avg * 2), 1)
    return resilience


def _collect_kg_centrality(conn) -> dict[str, float]:
    """Centrality scores from KG cache."""
    rows = conn.execute("SELECT result_json FROM ad_graph_cache ORDER BY created_at DESC LIMIT 1").fetchall()
    centrality = {}
    for r in rows:
        try:
            data = json.loads(r["result_json"])
            graph_data = data.get("graph_data", data)
            nodes = graph_data.get("nodes", [])
            for node in nodes:
                label = node.get("label", "")
                cent = node.get("centrality", 0)
                if label and cent:
                    centrality[label] = cent
        except (json.JSONDecodeError, TypeError):
            pass
    return centrality


def _collect_cascade_watchlists(conn) -> dict[str, list]:
    """Active cascade watchlist tickers grouped by trigger."""
    rows = conn.execute(
        "SELECT trigger_key, trigger_name, tickers FROM ad_cascade_watchlists ORDER BY created_at DESC LIMIT 100"
    ).fetchall()
    watchlists: dict[str, list] = defaultdict(list)
    for r in rows:
        trigger = r["trigger_name"] or r["trigger_key"]
        try:
            tickers = json.loads(r["tickers"]) if r["tickers"] else []
        except (json.JSONDecodeError, TypeError):
            tickers = []
        if isinstance(tickers, list):
            for t in tickers:
                if isinstance(t, dict):
                    t.setdefault("trigger_name", trigger)
                    watchlists[trigger].append(t)
                elif isinstance(t, str):
                    watchlists[trigger].append(
                        {
                            "ticker": t,
                            "impact_direction": "positive",
                            "impact_score": 0.5,
                            "trigger_name": trigger,
                        }
                    )
    return dict(watchlists)


def _collect_quality_scores(conn) -> dict[str, float]:
    """Load precomputed quality scores from ad_quality_scores.

    Returns a dict mapping ticker → composite_quality_score (0-100).
    Falls back to an empty dict gracefully (callers default to 50.0).

    The composite_quality_score column is the Novy-Marx / Piotroski z-score
    formula from tools/trading/analysts/quality.py — it captures ROE, gross
    margin, leverage, accruals, earnings variance, and FCF yield in a single
    cross-sectionally normalised number.  In stagflation, this is the primary
    discriminator for quality vs junk.
    """
    try:
        rows = conn.execute(
            """
            SELECT q.ticker, q.composite_quality_score
            FROM ad_quality_scores q
            INNER JOIN (
                SELECT ticker, MAX(as_of_date) AS max_date
                FROM ad_quality_scores
                GROUP BY ticker
            ) latest ON q.ticker = latest.ticker AND q.as_of_date = latest.max_date
            """
        ).fetchall()
        return {r["ticker"]: float(r["composite_quality_score"] or 50.0) for r in rows}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Scoring engine
# ---------------------------------------------------------------------------
def score_ticker(
    ticker: str,
    sector: str,
    perf: dict,
    signals: dict,
    macro: dict,
    macro_sectors: dict,
    expert: dict,
    scenario_res: dict,
    kg_cent: dict,
    regime: str,
    quality_score: float = 50.0,
) -> dict:
    """Compute a comprehensive strategy score for a single ticker.

    The 9th factor — quality_score — is blended into the composite with a
    regime-aware weight: 0.10 normally, 0.30 in stagflation.

    In stagflation, low-quality names (quality < 50) receive a 15% composite
    haircut to push them toward lower tiers; core tier also requires quality >= 70.

    Returns dict with momentum_score, consistency_score, mean_reversion_flag,
    macro_alignment, expert_alignment, scenario_resilience, kg_centrality,
    quality_score, composite_rank_score, and recommended_tier.
    """
    # 1. Multi-timeframe momentum score (0-100)
    momentum = 0.0
    for period, weight in TIMEFRAME_WEIGHTS.items():
        ret = perf.get(period, 0.0) or 0.0
        # Normalize: clip to [-200%, +500%], map to 0-100
        normalized = min(max((ret + 200) / 7.0, 0), 100)
        momentum += normalized * weight
    momentum = round(momentum, 2)

    # 2. Consistency score — how stable is the trend across timeframes (0-100)
    returns = [perf.get(p, 0.0) or 0.0 for p in PERIOD_DAYS]
    if len(returns) >= 2:
        signs = [1 if r > 0 else (-1 if r < 0 else 0) for r in returns]
        sign_agreement = sum(1 for s in signs if s == signs[0]) / len(signs)
        ann_returns = []
        for p, ret in zip(PERIOD_DAYS, returns):
            years = PERIOD_DAYS[p] / 252
            if years > 0 and ret > -100:
                ann_returns.append(((1 + ret / 100) ** (1 / years) - 1) * 100)
            else:
                ann_returns.append(0)
        if ann_returns:
            std = _std(ann_returns)
            mean_ann = sum(ann_returns) / len(ann_returns)
            cv = std / abs(mean_ann) if abs(mean_ann) > 0.01 else 10.0
            consistency = round(sign_agreement * 50 + max(0, 50 - cv * 10), 1)
            consistency = min(max(consistency, 0), 100)
        else:
            consistency = 50.0
    else:
        consistency = 50.0

    # 3. Mean reversion flag — short-term diverges significantly from long-term
    p1y = perf.get("p1y", 0.0) or 0.0
    p10y = perf.get("p10y", 0.0) or 0.0
    ann_1y = p1y
    ann_10y = ((1 + p10y / 100) ** 0.1 - 1) * 100 if p10y > -100 else 0
    mean_reversion = abs(ann_1y - ann_10y) > 30  # >30pp annual divergence

    # 4. Macro alignment
    regime_affinity = REGIME_SECTOR_AFFINITY.get(regime, {})
    macro_align = regime_affinity.get(sector, 0.0)
    sec_impact = macro_sectors.get(sector, {})
    composite_macro_impact = sec_impact.get("composite_impact", 0) or 0
    macro_alignment = round((macro_align * 40 + 50) + min(max(composite_macro_impact * 0.5, -25), 25), 1)
    macro_alignment = min(max(macro_alignment, 0), 100)

    # 5. Expert alignment
    exp = expert.get(ticker, {})
    exp_conviction = exp.get("conviction", 50) or 50
    exp_confidence = exp.get("confidence", 50) or 50
    exp_direction = exp.get("direction", "HOLD")
    direction_mult = {"BUY": 1.0, "HOLD": 0.5, "SELL": -0.3, "SHORT": -0.5}.get(exp_direction, 0.5)
    expert_alignment = round((exp_conviction * 0.6 + exp_confidence * 0.4) * direction_mult, 1)
    expert_alignment = min(max(expert_alignment, 0), 100)

    # 6. Scenario resilience (0-100)
    scenario_resilience = scenario_res.get(ticker, 60.0)

    # 7. KG centrality (0-100)
    raw_centrality = kg_cent.get(ticker, 0.0)
    sector_centrality = kg_cent.get(sector, 0.0)
    effective_centrality = max(raw_centrality, sector_centrality * 0.3)
    kg_centrality = round(min(effective_centrality * 200, 100), 1)

    # 8. Signal heatmap score
    sig = signals.get(ticker, {})
    heatmap_score = sig.get("composite_score", 50.0) or 50.0
    heatmap_confidence = sig.get("confidence", 50.0) or 50.0

    # --- Pick factor weight table based on regime ---
    fw = _FACTOR_WEIGHTS.get(regime, _FACTOR_WEIGHTS["default"])

    # --- Composite rank score (weighted blend of all 9 factors) ---
    composite = (
        momentum           * fw["momentum"]
        + consistency      * fw["consistency"]
        + macro_alignment  * fw["macro_alignment"]
        + expert_alignment * fw["expert_alignment"]
        + scenario_resilience * fw["scenario_resilience"]
        + kg_centrality    * fw["kg_centrality"]
        + heatmap_score    * fw["heatmap_score"]
        + heatmap_confidence * fw["heatmap_confidence"]
        + quality_score    * fw["quality"]
    )
    composite = round(composite, 2)

    # --- Stagflation quality penalty: high-PE / low-FCF haircut ---
    # quality.py composite_quality_score < 50 implies poor FCF coverage,
    # elevated leverage, or weak profitability — all red flags in stagflation.
    if regime == "stagflation" and quality_score < 50.0:
        composite = round(composite * QUALITY_LOW_PENALTY_STAGFLATION, 2)

    # --- Determine recommended tier ---
    if consistency >= 60 and momentum >= 50 and composite >= 55:
        # Stagflation core gate: quality < 70 → demote to tactical
        if regime == "stagflation" and quality_score < QUALITY_GATE_STAGFLATION_CORE:
            tier = "tactical"
        else:
            tier = "core"
    elif macro_alignment >= 55 and momentum >= 45:
        tier = "tactical"
    elif mean_reversion or (heatmap_score >= 70 and scenario_resilience <= 40):
        tier = "opportunistic"
    elif sig.get("direction") in ("SELL", "SHORT") or macro_alignment <= 30 or scenario_resilience <= 25:
        tier = "hedge"
    else:
        tier = "tactical"

    return {
        "ticker": ticker,
        "sector": sector,
        "momentum_score": momentum,
        "consistency_score": consistency,
        "mean_reversion_flag": int(mean_reversion),
        "macro_alignment": macro_alignment,
        "expert_alignment": expert_alignment,
        "scenario_resilience": scenario_resilience,
        "kg_centrality": kg_centrality,
        "heatmap_score": heatmap_score,
        "heatmap_confidence": heatmap_confidence,
        "quality_score": round(quality_score, 1),
        "composite_rank_score": composite,
        "recommended_tier": tier,
    }


def _std(values: list[float]) -> float:
    """Standard deviation."""
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(variance)


# ---------------------------------------------------------------------------
# Strategy builder
# ---------------------------------------------------------------------------
def build_strategy(
    tier_filter: str | None = None,
    max_core: int = 25,
    max_tactical: int = 15,
    max_opportunistic: int = 10,
    max_hedge: int = 8,
) -> dict:
    """Run the full strategy engine and return structured output.

    Returns:
        {
            "run_id": str,
            "regime": str,
            "tickers_scored": int,
            "tiers": {"core": [...], "tactical": [...], ...},
            "sector_allocation": [...],
            "signals": [...],
            "summary": str,
        }
    """
    conn = get_conn()
    run_id = f"strat-{str(uuid.uuid4())[:8]}"

    # --- Collect all data ---
    universe = get_full_universe()
    signals = _collect_signals(conn)
    macro = _collect_macro(conn)
    macro_sectors = _collect_macro_sector_impacts(conn)
    expert = _collect_expert_consensus(conn)
    scenario_res = _collect_scenario_resilience(conn)
    kg_cent = _collect_kg_centrality(conn)
    cascades = _collect_cascade_watchlists(conn)
    quality_scores = _collect_quality_scores(conn)

    raw_regime = (macro.get("regime") or "neutral").upper()
    regime_score = macro.get("regime_score") or macro.get("macro_score") or 50.0
    # Map traffic-light regime to strategy regime
    if raw_regime == "GREEN" or regime_score >= 65:
        regime = "expansion"
    elif raw_regime == "RED" or regime_score <= 30:
        regime = "contraction"
    elif regime_score <= 40:
        regime = "stagflation"
    elif regime_score >= 55:
        regime = "recovery"
    else:
        regime = "neutral"

    # SROR regime overlay: tier weights and quality gates tighten in
    # CAUTION/DANGER/CRISIS regardless of macro classification.
    try:
        from tools.trading.market_intel.regime_lens import get_regime_context
        sror_ctx = get_regime_context(conn)
    except Exception:
        sror_ctx = None

    # Compute effective tier weights for this run. Default = baseline
    # TIER_WEIGHTS midpoints; SROR overlays push toward hedge in danger and
    # toward opportunistic in opportunity.
    effective_tier_weights = dict(TIER_WEIGHTS)
    if sror_ctx is not None and (sror_ctx.is_dangerous() or sror_ctx.is_opportunity()):
        hedge_floor = sror_ctx.hedge_tier_floor()
        opp_cap = sror_ctx.opportunistic_tier_cap()
        # Re-target hedge midpoint up, opportunistic midpoint to its cap
        effective_tier_weights["hedge"] = (hedge_floor, hedge_floor + 0.05)
        effective_tier_weights["opportunistic"] = (max(0.0, opp_cap - 0.05), opp_cap)
        # Adjust core/tactical to keep total = 1.0
        used = (
            effective_tier_weights["hedge"][0] + effective_tier_weights["hedge"][1] +
            effective_tier_weights["opportunistic"][0] + effective_tier_weights["opportunistic"][1]
        ) / 2
        # Remaining budget split 70/30 core/tactical (keeps relative shape)
        remaining = max(0.0, 1.0 - used)
        core_target = remaining * 0.72
        tac_target = remaining * 0.28
        effective_tier_weights["core"] = (core_target * 0.92, core_target * 1.08)
        effective_tier_weights["tactical"] = (tac_target * 0.85, tac_target * 1.15)

    # --- Score every ticker ---
    all_scores: list[dict] = []
    for ticker, sector in universe.items():
        perf = compute_ticker_performance(ticker)
        # Default to 50 (neutral) when no quality data available in DB
        qs = quality_scores.get(ticker, 50.0)
        scored = score_ticker(
            ticker,
            sector,
            perf,
            signals,
            macro,
            macro_sectors,
            expert,
            scenario_res,
            kg_cent,
            regime,
            quality_score=qs,
        )
        scored["perf"] = perf
        all_scores.append(scored)

    # Sort by composite rank
    all_scores.sort(key=lambda x: x["composite_rank_score"], reverse=True)

    # --- Assign to tiers ---
    tier_limits = {
        "core": max_core,
        "tactical": max_tactical,
        "opportunistic": max_opportunistic,
        "hedge": max_hedge,
    }
    tiers: dict[str, list[dict]] = {t: [] for t in tier_limits}
    assigned = set()

    # If filtering to a single tier, only populate that one
    active_tiers = [tier_filter] if tier_filter and tier_filter in tiers else list(tiers.keys())

    for tier_name in active_tiers:
        candidates = [s for s in all_scores if s["recommended_tier"] == tier_name and s["ticker"] not in assigned]
        # For hedge tier, reverse sort (worst composite first = best hedge candidates)
        if tier_name == "hedge":
            candidates.sort(key=lambda x: x["composite_rank_score"])
        for c in candidates[: tier_limits[tier_name]]:
            assigned.add(c["ticker"])
            tiers[tier_name].append(c)

    # --- Compute weight allocation within each tier ---
    holdings = []
    strategy_signals = []

    for tier_name, tier_range in effective_tier_weights.items():
        members = tiers.get(tier_name, [])
        if not members:
            continue
        # Target total weight for this tier: midpoint of range (regime-overlaid)
        tier_total = (tier_range[0] + tier_range[1]) / 2 * 100  # as %

        # Distribute by composite rank within tier
        total_rank = sum(m["composite_rank_score"] for m in members) or 1
        for m in members:
            weight = round((m["composite_rank_score"] / total_rank) * tier_total, 2)
            direction = "SHORT" if tier_name == "hedge" and m.get("heatmap_score", 50) < 40 else "LONG"

            rationale = _build_rationale(m, tier_name, regime)

            holding = {
                "run_id": run_id,
                "tier": tier_name,
                "ticker": m["ticker"],
                "sector": m["sector"],
                "direction": direction,
                "weight_pct": weight,
                "conviction": m["composite_rank_score"],
                "momentum_score": m["momentum_score"],
                "consistency_score": m["consistency_score"],
                "mean_reversion_flag": m["mean_reversion_flag"],
                "macro_alignment": m["macro_alignment"],
                "scenario_resilience": m["scenario_resilience"],
                "kg_centrality": m["kg_centrality"],
                "expert_alignment": m["expert_alignment"],
                "quality_score": m["quality_score"],
                "rationale": rationale,
            }
            holdings.append(holding)

            # Generate signals for notable conditions
            if m["mean_reversion_flag"]:
                p1y = m["perf"].get("p1y", 0) or 0
                p10y = m["perf"].get("p10y", 0) or 0
                ann_10y = ((1 + p10y / 100) ** 0.1 - 1) * 100 if p10y > -100 else 0
                if p1y < ann_10y:
                    mr_direction = "LONG"
                    mr_label = "underperforming"
                    mr_action = "buy the dip"
                else:
                    mr_direction = "SHORT"
                    mr_label = "overextended"
                    mr_action = "take profits / hedge"
                divergence = round(p1y - ann_10y, 1)
                strategy_signals.append(
                    {
                        "run_id": run_id,
                        "signal_type": "mean_reversion",
                        "severity": "warning",
                        "ticker": m["ticker"],
                        "sector": m["sector"],
                        "message": (
                            f"{m['ticker']} [{mr_direction}]: 1yr {p1y:+.1f}% vs 10yr ann. {ann_10y:+.1f}% "
                            f"(gap {divergence:+.1f}pp) — {mr_label}, {mr_action}"
                        ),
                        "data_json": json.dumps(
                            {
                                **m["perf"],
                                "direction": mr_direction,
                                "ann_10y": round(ann_10y, 1),
                                "divergence_pp": divergence,
                            },
                            default=str,
                        ),
                    }
                )

            # Stagflation quality warning: low-quality in core/tactical
            if regime == "stagflation" and m["quality_score"] < 50.0 and tier_name in ("core", "tactical"):
                strategy_signals.append(
                    {
                        "run_id": run_id,
                        "signal_type": "quality_warning",
                        "severity": "warning",
                        "ticker": m["ticker"],
                        "sector": m["sector"],
                        "message": (
                            f"{m['ticker']} [{tier_name.upper()}]: low quality score "
                            f"{m['quality_score']:.0f}/100 in stagflation — "
                            f"monitor leverage, FCF coverage, and dividend sustainability"
                        ),
                        "data_json": json.dumps(
                            {"quality_score": m["quality_score"], "regime": regime, "tier": tier_name},
                            default=str,
                        ),
                    }
                )

    # --- Sector allocation summary ---
    sector_alloc = _compute_sector_allocation(holdings, regime, macro_sectors, all_scores, run_id)

    # --- Cascade-driven opportunistic signals ---
    for trigger, items in cascades.items():
        positive = [i for i in items if i.get("impact_direction") == "positive"]
        if positive:
            top = positive[:3]
            tickers = ", ".join(i["ticker"] for i in top)
            strategy_signals.append(
                {
                    "run_id": run_id,
                    "signal_type": "cascade_opportunity",
                    "severity": "info",
                    "ticker": top[0]["ticker"],
                    "sector": None,
                    "message": f"Cascade trigger '{trigger}': positive flow to {tickers}",
                    "data_json": json.dumps({"trigger": trigger, "tickers": [i["ticker"] for i in top]}),
                }
            )

    # --- Value investing opportunities ---
    value_picks = _identify_value_opportunities(all_scores, regime, macro_sectors, expert)

    # --- Build comprehensive narrative ---
    narrative = _build_comprehensive_narrative(
        regime,
        regime_score,
        holdings,
        sector_alloc,
        strategy_signals,
        value_picks,
        all_scores,
        macro,
        cascades,
    )

    # --- Persist to DB ---
    _persist_strategy(
        conn,
        run_id,
        regime,
        regime_score,
        holdings,
        sector_alloc,
        strategy_signals,
        len(all_scores),
        narrative,
        value_picks,
    )

    core_tickers = [h["ticker"] for h in holdings if h["tier"] == "core"][:5]
    tactical_tickers = [h["ticker"] for h in holdings if h["tier"] == "tactical"][:5]
    summary = (
        f"Strategy run {run_id} | Regime: {regime} (score {regime_score:.0f}) | "
        f"{len(all_scores)} tickers scored | "
        f"Core: {len(tiers['core'])} ({', '.join(core_tickers)}...) | "
        f"Tactical: {len(tiers['tactical'])} ({', '.join(tactical_tickers)}...) | "
        f"Opportunistic: {len(tiers['opportunistic'])} | "
        f"Hedges: {len(tiers['hedge'])} | "
        f"Signals: {len(strategy_signals)}"
    )

    return {
        "run_id": run_id,
        "regime": regime,
        "regime_score": regime_score,
        "tickers_scored": len(all_scores),
        "quality_weight": _FACTOR_WEIGHTS.get(regime, _FACTOR_WEIGHTS["default"])["quality"],
        "tiers": {
            tier_name: [{k: v for k, v in h.items() if k != "run_id"} for h in holdings if h["tier"] == tier_name]
            for tier_name in TIER_WEIGHTS
        },
        "sector_allocation": sector_alloc,
        "signals": strategy_signals,
        "value_picks": value_picks,
        "narrative": narrative,
        "summary": summary,
    }


def _identify_value_opportunities(
    all_scores: list[dict],
    regime: str,
    macro_sectors: dict,
    expert: dict,
    max_picks: int = 15,
) -> list[dict]:
    """Identify deep value investing opportunities.

    Value criteria (Buffett/Graham-inspired):
    1. Strong 10yr/20yr returns (proven compounder)
    2. Short-term weakness (1yr underperformance = buying opportunity)
    3. High consistency across cycles
    4. Defensive sectors favored in uncertain regimes
    5. Mean reversion candidates with high long-term conviction
    6. Quality bonus: quality_score >= 70 receives a 10% value score boost
       (reward low-leverage dividend growers with strong FCF in stagflation)
    """
    candidates = []
    for s in all_scores:
        perf = s.get("perf", {})
        p1y = perf.get("p1y", 0) or 0
        p5y = perf.get("p5y", 0) or 0
        p10y = perf.get("p10y", 0) or 0
        p20y = perf.get("p20y", 0) or 0

        ann_10y = ((1 + p10y / 100) ** 0.1 - 1) * 100 if p10y > -100 else 0

        long_term = min(max(ann_10y, -20), 30)
        short_discount = max(0, -p1y)
        consistency = s.get("consistency_score", 50)
        margin_of_safety = max(0, ann_10y - p1y) if ann_10y > 0 else 0

        value_score = (
            long_term * 0.30
            + short_discount * 0.25
            + consistency * 0.20
            + margin_of_safety * 0.15
            + s.get("scenario_resilience", 50) * 0.10
        )

        if ann_10y < 2 or p20y < 0:
            continue

        # Expert alignment bonus
        exp = expert.get(s["ticker"], {})
        if exp.get("direction") == "BUY":
            value_score *= 1.15

        # Quality bonus: high-quality + stagflation = preferred dividend grower
        qs = s.get("quality_score", 50.0)
        if qs >= 70.0:
            value_score *= 1.10

        thesis_parts = []
        if short_discount > 20:
            thesis_parts.append(f"Down {p1y:.0f}% this year despite {ann_10y:.1f}% annualized over 10yr")
        elif short_discount > 0:
            thesis_parts.append(f"Modest pullback ({p1y:.0f}% 1yr) vs {ann_10y:.1f}% long-term trend")
        else:
            thesis_parts.append(f"Steady compounder: {ann_10y:.1f}% annualized 10yr, {p20y:.0f}% total 20yr")

        if margin_of_safety > 20:
            thesis_parts.append(f"Wide margin of safety ({margin_of_safety:.0f}pp below trend)")
        if consistency > 70:
            thesis_parts.append("High cycle consistency")
        if s.get("scenario_resilience", 50) > 70:
            thesis_parts.append("Resilient across stress scenarios")
        if qs >= 70.0:
            thesis_parts.append(f"High quality ({qs:.0f}/100) — FCF-backed, low leverage")

        candidates.append(
            {
                "ticker": s["ticker"],
                "sector": s["sector"],
                "value_score": round(value_score, 1),
                "quality_score": round(qs, 1),
                "perf_1y": p1y,
                "perf_5y": p5y,
                "perf_10y": p10y,
                "perf_20y": p20y,
                "ann_10y": round(ann_10y, 1),
                "consistency": consistency,
                "margin_of_safety": round(margin_of_safety, 1),
                "scenario_resilience": s.get("scenario_resilience", 50),
                "thesis": " | ".join(thesis_parts),
                "direction": "BUY",
                "horizon": "3-10 years",
            }
        )

    candidates.sort(key=lambda x: x["value_score"], reverse=True)
    return candidates[:max_picks]


def _build_comprehensive_narrative(
    regime: str,
    regime_score: float,
    holdings: list[dict],
    sector_alloc: list[dict],
    signals: list[dict],
    value_picks: list[dict],
    all_scores: list[dict],
    macro: dict,
    cascades: dict,
) -> dict:
    """Build a comprehensive multi-section strategy narrative."""
    total = len(all_scores)
    bullish = sum(1 for s in all_scores if s.get("composite_rank_score", 0) >= 60)
    bearish = sum(1 for s in all_scores if s.get("composite_rank_score", 0) <= 35)
    neutral = total - bullish - bearish

    regime_desc = {
        "expansion": "economic expansion with growth-favorable conditions",
        "contraction": "economic contraction with defensive positioning warranted",
        "recovery": "early recovery with cyclical opportunities emerging",
        "stagflation": "stagflationary environment favoring real assets and defensives",
        "neutral": "mixed macro signals with no clear directional bias",
    }.get(regime, "uncertain conditions")

    market_assessment = (
        f"The macro regime is {regime.upper()} (score: {regime_score:.0f}/100), indicating {regime_desc}. "
        f"Of {total} assets scored, {bullish} show bullish signals ({bullish / total * 100:.0f}%), "
        f"{bearish} bearish ({bearish / total * 100:.0f}%), and {neutral} neutral. "
    )

    macro_summary = macro.get("summary", "")
    if macro_summary:
        market_assessment += macro_summary

    # Quality narrative addendum for stagflation
    if regime == "stagflation":
        fw = _FACTOR_WEIGHTS["stagflation"]
        high_quality_core = sum(
            1 for h in holdings if h["tier"] == "core" and h.get("quality_score", 0) >= 70
        )
        market_assessment += (
            f" Stagflation quality filter active: quality_weight={fw['quality']:.0f}% "
            f"(3x base rate). Core tier requires quality >= {QUALITY_GATE_STAGFLATION_CORE:.0f}/100; "
            f"{high_quality_core} core positions meet that bar. "
            f"Low-PE / high-FCF dividend growers preferred; high-PE / low-FCF names penalised."
        )

    core_h = [h for h in holdings if h["tier"] == "core"]
    tact_h = [h for h in holdings if h["tier"] == "tactical"]
    opp_h = [h for h in holdings if h["tier"] == "opportunistic"]
    hedge_h = [h for h in holdings if h["tier"] == "hedge"]

    core_sectors = list({h["sector"] for h in core_h})
    tact_sectors = list({h["sector"] for h in tact_h})

    allocation_thesis = (
        f"Core allocation ({len(core_h)} positions) concentrates on proven compounders in "
        f"{', '.join(core_sectors[:4]) or 'diversified sectors'}. "
        f"Tactical tilts ({len(tact_h)} positions) rotate into {', '.join(tact_sectors[:4]) or 'macro-aligned sectors'} "
        f"given the {regime} regime. "
    )
    if opp_h:
        mean_rev = sum(1 for h in opp_h if h.get("mean_reversion_flag"))
        allocation_thesis += (
            f"Opportunistic bucket ({len(opp_h)} positions) targets "
            f"{'mean-reversion candidates' if mean_rev > 0 else 'event-driven plays'} "
            f"with asymmetric upside. "
        )
    if hedge_h:
        allocation_thesis += (
            f"Hedges ({len(hedge_h)} positions) protect against scenario downside in low-macro-alignment sectors."
        )

    overweight = [
        a for a in sector_alloc if a.get("tilt_direction") == "OVERWEIGHT" and a.get("target_weight_pct", 0) > 0
    ]
    underweight = [a for a in sector_alloc if a.get("tilt_direction") == "UNDERWEIGHT"]
    ow_names = [a["sector"] for a in overweight[:5]]
    uw_names = [a["sector"] for a in underweight[:5]]

    sector_view = f"Overweight: {', '.join(ow_names) or 'none'}. Underweight: {', '.join(uw_names) or 'none'}. "

    if regime == "contraction":
        sector_view += "Defensive rotation favors Utilities, Consumer Staples, Healthcare, and Fixed Income. "
    elif regime == "expansion":
        sector_view += "Growth rotation favors Technology, Semiconductors, Fintech, and Crypto. "
    elif regime == "stagflation":
        sector_view += (
            "Real-asset rotation favors Commodities, Oil & Gas, Defense, and Dividend payers. "
            "Quality screen filters for low-leverage FCF growers within these sectors."
        )
    elif regime == "recovery":
        sector_view += "Cyclical rotation favors Banks, Industrials, Consumer Discretionary, and Small Caps. "

    value_thesis = ""
    if value_picks:
        top3 = value_picks[:3]
        value_thesis = (
            "Top value opportunities: "
            + "; ".join(
                f"{v['ticker']} ({v['sector']}, value score {v['value_score']:.0f}, "
                f"10yr ann. {v['ann_10y']:.1f}%, 1yr {v['perf_1y']:+.0f}%, "
                f"quality {v.get('quality_score', 50):.0f}/100)"
                for v in top3
            )
            + ". "
        )
        discounted = [v for v in value_picks if v["perf_1y"] < -10]
        if discounted:
            value_thesis += (
                f"{len(discounted)} assets trading at significant discounts to long-term trend, "
                f"offering potential buying opportunities for patient capital. "
            )

    risk_factors = []
    mean_rev_signals = [s for s in signals if s.get("signal_type") == "mean_reversion"]
    if mean_rev_signals:
        risk_factors.append(
            f"{len(mean_rev_signals)} mean-reversion warnings (short-term divergence from long-term trend)"
        )
    quality_warnings = [s for s in signals if s.get("signal_type") == "quality_warning"]
    if quality_warnings:
        risk_factors.append(
            f"{len(quality_warnings)} quality warnings in {regime} regime — "
            "positions carry elevated leverage or FCF risk"
        )
    if regime_score < 40:
        risk_factors.append("Below-average macro score suggests caution with risk-on positions")
    if bearish > bullish:
        risk_factors.append(f"Bearish breadth ({bearish} > {bullish} bullish) — market headwinds")
    cascade_count = sum(len(v) for v in cascades.values())
    if cascade_count > 0:
        risk_factors.append(f"{cascade_count} active cascade watchlist items — monitor supply chain propagation")

    risk_section = "; ".join(risk_factors) if risk_factors else "No elevated risk factors detected."

    return {
        "market_assessment": market_assessment,
        "allocation_thesis": allocation_thesis,
        "sector_rotation": sector_view,
        "value_thesis": value_thesis,
        "risk_factors": risk_section,
    }


def _build_rationale(scored: dict, tier: str, regime: str) -> str:
    """Generate a concise rationale string for a holding."""
    parts = []
    perf = scored.get("perf", {})
    qs = scored.get("quality_score", 50.0)

    if tier == "core":
        parts.append(f"20yr: {perf.get('p20y', 0):.0f}%, 10yr: {perf.get('p10y', 0):.0f}%")
        parts.append(f"consistency {scored['consistency_score']:.0f}")
        if scored["kg_centrality"] > 20:
            parts.append(f"KG centrality {scored['kg_centrality']:.0f}")
        if regime == "stagflation":
            parts.append(f"quality {qs:.0f}/100 (gate >= {QUALITY_GATE_STAGFLATION_CORE:.0f})")
    elif tier == "tactical":
        parts.append(f"regime={regime}, macro align {scored['macro_alignment']:.0f}")
        parts.append(f"1yr: {perf.get('p1y', 0):.0f}%, 5yr: {perf.get('p5y', 0):.0f}%")
        if regime == "stagflation" and qs >= 70:
            parts.append(f"quality {qs:.0f} — FCF-backed dividend grower")
    elif tier == "opportunistic":
        if scored["mean_reversion_flag"]:
            parts.append("MEAN REVERSION candidate")
        parts.append(f"heatmap {scored['heatmap_score']:.0f}, conf {scored['heatmap_confidence']:.0f}")
    elif tier == "hedge":
        parts.append(f"scenario resilience {scored['scenario_resilience']:.0f}")
        parts.append(f"macro align {scored['macro_alignment']:.0f}")
        if regime == "stagflation" and qs < 50:
            parts.append(f"low quality {qs:.0f} — high-PE/low-FCF risk")
    return " | ".join(parts)


def _compute_sector_allocation(
    holdings: list[dict],
    regime: str,
    macro_sectors: dict,
    all_scores: list[dict],
    run_id: str,
) -> list[dict]:
    """Compute target sector allocation from holdings + macro context."""
    sector_weights: dict[str, float] = defaultdict(float)
    for h in holdings:
        sector_weights[h["sector"]] += h["weight_pct"]

    sector_composites: dict[str, list] = defaultdict(list)
    for s in all_scores:
        sector_composites[s["sector"]].append(s["composite_rank_score"])

    sector_ranks = {}
    for sec, scores in sector_composites.items():
        sector_ranks[sec] = sum(scores) / len(scores) if scores else 0

    ranked_sectors = sorted(sector_ranks.items(), key=lambda x: x[1], reverse=True)
    rank_map = {sec: i + 1 for i, (sec, _) in enumerate(ranked_sectors)}

    regime_affinity = REGIME_SECTOR_AFFINITY.get(regime, {})

    allocations = []
    for sector in ALL_SECTORS:
        weight = round(sector_weights.get(sector, 0.0), 2)
        affinity = regime_affinity.get(sector, 0.0)
        avg_resilience = 0.0
        sector_scores = sector_composites.get(sector, [])
        if sector_scores:
            avg_resilience = round(sum(sector_scores) / len(sector_scores), 1)

        sec_db_impact = macro_sectors.get(sector, {})
        db_impact = sec_db_impact.get("composite_impact", 0) or 0
        effective_affinity = affinity + min(max(db_impact / 100.0, -0.5), 0.5)
        macro_align_score = round(min(max(effective_affinity * 40 + 50, 0), 100), 1)

        if effective_affinity > 0.2:
            tilt = "OVERWEIGHT"
        elif effective_affinity < -0.2:
            tilt = "UNDERWEIGHT"
        else:
            tilt = "NEUTRAL"

        allocations.append(
            {
                "run_id": run_id,
                "sector": sector,
                "target_weight_pct": weight,
                "current_weight_pct": 0.0,
                "macro_alignment": macro_align_score,
                "momentum_rank": rank_map.get(sector, 99),
                "avg_scenario_resilience": avg_resilience,
                "tilt_direction": tilt,
                "rationale": (
                    f"Regime {regime}, eff. affinity {effective_affinity:+.2f} "
                    f"(regime {affinity:+.1f}, macro {db_impact:+.0f}), "
                    f"rank #{rank_map.get(sector, '?')}"
                ),
            }
        )

    allocations.sort(key=lambda x: x["target_weight_pct"], reverse=True)
    return allocations


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def _persist_strategy(
    conn,
    run_id,
    regime,
    regime_score,
    holdings,
    sector_alloc,
    signals,
    total_scored,
    narrative=None,
    value_picks=None,
):
    """Write strategy run + holdings + allocations + signals to DB."""
    now = datetime.now(timezone.utc).isoformat()

    def _uid():
        return str(uuid.uuid4())[:12]

    tier_counts = defaultdict(int)
    for h in holdings:
        tier_counts[h["tier"]] += 1

    fw = _FACTOR_WEIGHTS.get(regime, _FACTOR_WEIGHTS["default"])
    strategy_json = json.dumps(
        {
            "tier_weights": TIER_WEIGHTS,
            "timeframe_weights": TIMEFRAME_WEIGHTS,
            "factor_weights": fw,
            "quality_weight": fw["quality"],
            "regime_affinity": regime,
            "narrative": narrative,
            "value_picks": value_picks,
        },
        default=str,
    )

    conn.execute(
        "INSERT INTO ad_strategy_runs "
        "(id, run_type, macro_regime, macro_score, total_tickers_scored, "
        "strategy_json, core_count, tactical_count, opportunistic_count, "
        "hedge_count, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            run_id,
            "full",
            regime,
            regime_score,
            total_scored,
            strategy_json,
            tier_counts.get("core", 0),
            tier_counts.get("tactical", 0),
            tier_counts.get("opportunistic", 0),
            tier_counts.get("hedge", 0),
            now,
        ),
    )

    for h in holdings:
        conn.execute(
            "INSERT INTO ad_strategy_holdings "
            "(id, run_id, tier, ticker, sector, direction, weight_pct, conviction, "
            "composite_rank, momentum_score, consistency_score, mean_reversion_flag, "
            "macro_alignment, scenario_resilience, kg_centrality, expert_alignment, "
            "rationale, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                _uid(),
                h["run_id"],
                h["tier"],
                h["ticker"],
                h["sector"],
                h["direction"],
                h["weight_pct"],
                h["conviction"],
                None,
                h["momentum_score"],
                h["consistency_score"],
                h["mean_reversion_flag"],
                h["macro_alignment"],
                h["scenario_resilience"],
                h["kg_centrality"],
                h["expert_alignment"],
                h["rationale"],
                now,
            ),
        )

    for a in sector_alloc:
        conn.execute(
            "INSERT INTO ad_strategy_sector_allocation "
            "(id, run_id, sector, target_weight_pct, current_weight_pct, "
            "macro_alignment, momentum_rank, avg_scenario_resilience, "
            "tilt_direction, rationale, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                _uid(),
                run_id,
                a["sector"],
                a["target_weight_pct"],
                a["current_weight_pct"],
                a["macro_alignment"],
                a["momentum_rank"],
                a["avg_scenario_resilience"],
                a["tilt_direction"],
                a["rationale"],
                now,
            ),
        )

    for s in signals:
        conn.execute(
            "INSERT INTO ad_strategy_signals "
            "(id, run_id, signal_type, severity, ticker, sector, message, data_json, created_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                _uid(),
                run_id,
                s["signal_type"],
                s["severity"],
                s.get("ticker"),
                s.get("sector"),
                s["message"],
                s.get("data_json"),
                now,
            ),
        )

    conn.commit()


# ---------------------------------------------------------------------------
# Query helpers (for dashboard API)
# ---------------------------------------------------------------------------
def get_latest_strategy(conn=None) -> dict | None:
    """Get the most recent strategy run with all holdings and allocations."""
    c = conn or get_conn()
    run = c.execute("SELECT * FROM ad_strategy_runs ORDER BY created_at DESC LIMIT 1").fetchone()
    if not run:
        return None

    run_id = run["id"]
    holdings = c.execute(
        "SELECT * FROM ad_strategy_holdings WHERE run_id = %s ORDER BY weight_pct DESC",
        (run_id,),
    ).fetchall()
    allocations = c.execute(
        "SELECT * FROM ad_strategy_sector_allocation WHERE run_id = %s ORDER BY target_weight_pct DESC",
        (run_id,),
    ).fetchall()
    signals = c.execute(
        "SELECT * FROM ad_strategy_signals WHERE run_id = %s ORDER BY created_at DESC",
        (run_id,),
    ).fetchall()

    narrative = None
    value_picks = []
    try:
        sj = json.loads(run["strategy_json"]) if run["strategy_json"] else {}
        narrative = sj.get("narrative")
        value_picks = sj.get("value_picks", [])
    except (json.JSONDecodeError, TypeError):
        pass

    return {
        "run": dict(run),
        "holdings": [dict(h) for h in holdings],
        "sector_allocation": [dict(a) for a in allocations],
        "signals": [dict(s) for s in signals],
        "narrative": narrative,
        "value_picks": value_picks,
    }


def get_strategy_history(limit: int = 10, conn=None) -> list[dict]:
    """Get recent strategy runs (metadata only)."""
    c = conn or get_conn()
    rows = c.execute(
        "SELECT * FROM ad_strategy_runs ORDER BY created_at DESC LIMIT %s",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="FathomDesk Portfolio Strategist")
    parser.add_argument("--run", action="store_true", help="Execute full strategy run")
    parser.add_argument("--tier", type=str, help="Filter to specific tier (core/tactical/opportunistic/hedge)")
    parser.add_argument("--latest", action="store_true", help="Show latest strategy")
    parser.add_argument("--history", action="store_true", help="Show strategy run history")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--backtest", action="store_true", help="Quick backtest summary")
    args = parser.parse_args()

    if args.run:
        result = build_strategy(tier_filter=args.tier)
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            print(result["summary"])
            fw = _FACTOR_WEIGHTS.get(result["regime"], _FACTOR_WEIGHTS["default"])
            print(f"  Quality weight: {fw['quality']:.0%} ({result['regime']} regime)")
            for tier_name in TIER_WEIGHTS:
                members = result["tiers"].get(tier_name, [])
                if members:
                    print(f"\n{'=' * 60}")
                    print(f"  {tier_name.upper()} ({len(members)} holdings)")
                    print(f"{'=' * 60}")
                    for h in members[:10]:
                        print(
                            f"  {h['ticker']:8s} {h['sector']:24s} "
                            f"wt={h['weight_pct']:5.1f}% conv={h['conviction']:.0f} "
                            f"mom={h['momentum_score']:.0f} con={h['consistency_score']:.0f} "
                            f"qual={h.get('quality_score', 50):.0f} "
                            f"{h['direction']}"
                        )
            if result["signals"]:
                print(f"\n--- Strategy Signals ({len(result['signals'])}) ---")
                for s in result["signals"][:10]:
                    print(f"  [{s['severity']}] {s['message']}")
        return

    if args.latest:
        strategy = get_latest_strategy()
        if not strategy:
            print("No strategy runs found. Run with --run first.")
            return
        if args.json:
            print(json.dumps(strategy, indent=2, default=str))
        else:
            r = strategy["run"]
            print(f"Strategy {r['id']} | {r['macro_regime']} | {r['total_tickers_scored']} tickers")
            print(
                f"  Core: {r['core_count']}, Tactical: {r['tactical_count']}, "
                f"Opportunistic: {r['opportunistic_count']}, Hedge: {r['hedge_count']}"
            )
        return

    if args.history:
        runs = get_strategy_history()
        if args.json:
            print(json.dumps(runs, indent=2, default=str))
        else:
            for r in runs:
                print(f"  {r['id']} | {r['macro_regime']} | {r['created_at']}")
        return

    if args.backtest:
        result = build_strategy()
        tier_perf = {}
        for tier_name, members in result["tiers"].items():
            if not members:
                continue
            weighted_return = 0.0
            total_weight = sum(m["weight_pct"] for m in members) or 1
            for m in members:
                perf = compute_ticker_performance(m["ticker"])
                ret_1y = perf.get("p1y", 0) or 0
                weighted_return += ret_1y * (m["weight_pct"] / total_weight)
            tier_perf[tier_name] = round(weighted_return, 2)

        output = {
            "run_id": result["run_id"],
            "regime": result["regime"],
            "quality_weight": result["quality_weight"],
            "tier_performance_1y": tier_perf,
            "portfolio_return_1y": round(
                sum(tier_perf.get(t, 0) * ((TIER_WEIGHTS[t][0] + TIER_WEIGHTS[t][1]) / 2) for t in TIER_WEIGHTS), 2
            ),
        }
        if args.json:
            print(json.dumps(output, indent=2, default=str))
        else:
            print(f"Backtest | Regime: {output['regime']} | Quality weight: {output['quality_weight']:.0%}")
            for t, ret in output["tier_performance_1y"].items():
                print(f"  {t:16s}: {ret:+.2f}%")
            print(f"  {'PORTFOLIO':16s}: {output['portfolio_return_1y']:+.2f}%")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
