# CUI // SP-CTI
"""FathomDesk Quality Analyst — 7-dimension fundamental quality scorer.

Scores stocks on seven quality dimensions using fundamental metrics from
``ad_fundamental_metrics``, produces a Piotroski F-Score (0-9), and
combines everything into a composite quality score (0-100).

Seven Dimensions
----------------
1. Valuation          — PE ratio, PB ratio, FCF yield
2. Profitability      — ROE, ROIC, gross / operating / net margins
3. Growth             — EPS and revenue growth consistency (3-year)
4. Balance Sheet      — debt-to-equity, accruals (earnings quality)
5. Capital Allocation — dividend vs FCF, payout ratio, insider buy ratio
6. Moat Durability    — composite of margin stability, capital efficiency, insider activity
7. PE/NAV Mispricing  — implied ROE gap (actual ROE − P/B÷PE), sector-tier-weighted

PE/NAV Framework
----------------
From the identity  P/B = PE × ROE  →  implied_ROE = P/B / PE
  roe_gap = actual_ROE − implied_ROE
  positive gap → earning more than market's NAV premium implies (undervalued)
  negative gap → market pricing in future ROE expansion not yet realized (overvalued)

Signal is sector-tier-weighted:
  Tier 1 (REITs, banks, insurers) — full weight; NAV is audited/explicit
  Tier 2 (utilities, energy, industrials) — 70% weight; estimable from balance sheet
  Tier 3 (software, biotech, services) — 40% weight; goodwill dominates, PB is noisy

Composite Formula (Novy-Marx / Piotroski inspired, v2)
------------------------------------------------------
Q = 0.23·z(ROE)
  + 0.18·z(GrossMargin)          # proxy for GrossProfit / Assets
  + 0.13·z(-D/E)                 # lower leverage → higher z
  + 0.13·z(-Accruals)            # lower accruals → higher z (cleaner earnings)
  + 0.08·z(-EarningsVar)         # lower variance (proxied by |eps_growth| deviation)
  + 0.15·z(FCFYield)
  + 0.10·z(ROE_Gap)              # PE/NAV mispricing signal

Z-scores are computed cross-sectionally when a population is provided;
otherwise reference market-wide statistics are used (mu / sigma constants below).

Piotroski F-Score (0-9, >= 7 = HIGH quality)
---------------------------------------------
Profitability (4):
  F1  ROA > 0
  F2  FCF yield > 0  (proxy: positive operating cash flow)
  F3  EPS 3-year growth > 0  (improving profitability)
  F4  Accruals ratio < 0  (cash earnings exceed accounting earnings)
Leverage / Liquidity (3):
  F5  Debt-to-equity < 1.0  (conservative leverage)
  F6  Dividend yield < FCF yield  (dividend is FCF-covered)
  F7  Payout ratio < 0.80  (capital retained for reinvestment)
Operating Efficiency (2):
  F8  Gross margin > 0.25  (strong product economics)
  F9  Operating margin > 0.10  (efficient operations)

Usage
-----
    # Standalone CLI
    python tools/trading/analysts/quality.py --ticker NVDA --json
    python tools/trading/analysts/quality.py --ticker AAPL --save --json

    # Programmatic
    from tools.trading.analysts.quality import score_quality
    result = score_quality("NVDA", fundamentals_dict)

    # Load from DB and save results
    from tools.trading.analysts.quality import load_fundamentals, save_quality_scores
    fm = load_fundamentals("AAPL")
    result = score_quality("AAPL", fm)
    save_quality_scores("AAPL", result)
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ICDEV™ parent path setup
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from tools.db.storage import get_connection  # noqa: E402

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_ROOT = Path(__file__).resolve().parents[3]
_ICDEV_DB = _ROOT / "data" / "icdev.db"

# ---------------------------------------------------------------------------
# PE/NAV sector tier classification
# ---------------------------------------------------------------------------
# Tier 1: audited/explicit NAV — highest PE/NAV signal fidelity
# Tier 2: NAV estimable from balance sheet — moderate fidelity
# Tier 3: goodwill/intangibles dominate book value — lowest fidelity
NAV_SECTOR_TIERS: dict[str, int] = {
    "real_estate": 1, "reit": 1,
    "financials": 1, "banks": 1, "banking": 1, "insurance": 1,
    "closed_end_fund": 1, "bdc": 1,
    "utilities": 2, "energy": 2, "materials": 2,
    "industrials": 2, "consumer_staples": 2,
    "consumer_discretionary": 2, "healthcare": 2,
    "information_technology": 3, "technology": 3,
    "communication_services": 3, "software": 3,
    "biotech": 3, "services": 3,
}

# PE/NAV quadrant thresholds (market-wide reference)
_PE_HIGH_THRESHOLD = 20.0   # above → high PE
_PB_HIGH_THRESHOLD = 2.5    # above → premium to NAV

# ---------------------------------------------------------------------------
# Model version
# ---------------------------------------------------------------------------
MODEL_VERSION = "v2"

# ---------------------------------------------------------------------------
# Reference statistics for z-score normalization
# (broad US equity market estimates; override by passing a population)
# ---------------------------------------------------------------------------
_REF_STATS: dict[str, dict[str, float]] = {
    "roe":            {"mu": 0.15,  "sigma": 0.15},
    "gross_margin":   {"mu": 0.35,  "sigma": 0.22},
    "neg_debt_equity":{"mu": -1.50, "sigma": 1.50},   # stored as -D/E
    "neg_accruals":   {"mu": -0.05, "sigma": 0.10},   # stored as -accruals_ratio
    "neg_earn_var":   {"mu": -0.15, "sigma": 0.20},   # stored as -|eps_growth - 0.10|
    "fcf_yield":      {"mu": 0.03,  "sigma": 0.04},
    "roe_gap":        {"mu": 0.00,  "sigma": 0.08},   # actual_ROE − implied_ROE
}

# Composite weights (must sum to 1.0)
_COMPOSITE_WEIGHTS: dict[str, float] = {
    "roe":             0.23,
    "gross_margin":    0.18,
    "neg_debt_equity": 0.13,
    "neg_accruals":    0.13,
    "neg_earn_var":    0.08,
    "fcf_yield":       0.15,
    "roe_gap":         0.10,   # PE/NAV mispricing signal
}

# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe(value: Any, default: float = 0.0) -> float:
    """Return float, substituting default for None / NaN."""
    if value is None:
        return default
    try:
        f = float(value)
        return default if math.isnan(f) or math.isinf(f) else f
    except (TypeError, ValueError):
        return default


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def _z_score(value: float, mu: float, sigma: float) -> float:
    """Compute z-score, clamped to [-3, +3] to prevent outlier distortion."""
    if sigma <= 0:
        return 0.0
    return max(-3.0, min(3.0, (value - mu) / sigma))


def _z_to_score(z: float) -> float:
    """Convert z-score to 0-100 scale.  z=0 → 50, ±3σ → 0 / 100."""
    return _clamp(50.0 + z * (50.0 / 3.0))


def _population_stats(values: list[float]) -> tuple[float, float]:
    """Compute mean and population std from a list of finite floats."""
    n = len(values)
    if n == 0:
        return 0.0, 1.0
    mu = sum(values) / n
    variance = sum((v - mu) ** 2 for v in values) / n
    sigma = math.sqrt(variance) if variance > 0 else 1.0
    return mu, sigma


# ---------------------------------------------------------------------------
# PE/NAV helpers
# ---------------------------------------------------------------------------

def nav_tier_for_sector(sector: str | None) -> int:
    """Return sector tier (1/2/3) for PE/NAV signal reliability."""
    if not sector:
        return 2
    key = sector.lower().replace(" ", "_").replace("-", "_")
    return NAV_SECTOR_TIERS.get(key, 2)


def compute_pe_nav_metrics(f: dict[str, Any]) -> dict[str, Any]:
    """Compute PE/NAV framework metrics from a fundamentals dict.

    Uses the identity  P/B = PE × ROE  →  implied_ROE = P/B / PE.
    The roe_gap (actual − implied) is the primary mispricing signal.

    Returns:
        implied_roe  — float or None when PE/PB unavailable
        roe_gap      — float or None (positive = undervalued)
        nav_quadrant — one of four string labels
        pe_nav_score — 0-100 (50 = neutral; higher = better value)
        sector_tier  — 1, 2, or 3
    """
    pe  = _safe(f.get("pe_ratio"),  default=0.0)
    pb  = _safe(f.get("pb_ratio"),  default=0.0)
    roe = _safe(f.get("roe"),       default=0.0)

    # implied_ROE from P/B = PE × ROE identity
    if pe > 0 and pb > 0:
        implied_roe: float | None = pb / pe
        roe_gap: float | None     = roe - implied_roe
    else:
        implied_roe = None
        roe_gap     = None

    # Four-quadrant classification
    if pe > 0 and pb > 0:
        high_pe = pe > _PE_HIGH_THRESHOLD
        high_pb = pb > _PB_HIGH_THRESHOLD
        if not high_pe and not high_pb:
            nav_quadrant = "deep_value"
        elif not high_pe and high_pb:
            nav_quadrant = "efficient_compounder"
        elif high_pe and not high_pb:
            nav_quadrant = "asset_rich_earnings_poor"
        else:
            nav_quadrant = "fully_priced"
    else:
        nav_quadrant = "unknown"

    # PE/NAV score: centered on 50, ±5% roe_gap → ±30 pts
    if roe_gap is not None:
        pe_nav_score = _clamp(50.0 + roe_gap * 600.0)
    else:
        pe_nav_score = 50.0  # neutral when inputs missing

    tier = nav_tier_for_sector(f.get("sector"))

    return {
        "implied_roe":  round(implied_roe, 4) if implied_roe is not None else None,
        "roe_gap":      round(roe_gap, 4)     if roe_gap is not None     else None,
        "nav_quadrant": nav_quadrant,
        "pe_nav_score": round(pe_nav_score, 2),
        "sector_tier":  tier,
    }


def _score_pe_nav(f: dict[str, Any]) -> float:
    """Score PE/NAV mispricing dimension (0-100), tier-weighted.

    Tier 1 (REITs, banks)     — full signal weight
    Tier 2 (industrials, etc) — 70% signal + 30% neutral blend
    Tier 3 (software, biotech) — 40% signal + 60% neutral blend
    """
    pnm   = compute_pe_nav_metrics(f)
    score = pnm["pe_nav_score"]
    tier  = pnm["sector_tier"]
    if tier == 1:
        return round(score, 2)
    elif tier == 2:
        return round(score * 0.70 + 50.0 * 0.30, 2)
    else:
        return round(score * 0.40 + 50.0 * 0.60, 2)


# ---------------------------------------------------------------------------
# 1. Valuation dimension
# ---------------------------------------------------------------------------

def _score_valuation(f: dict[str, Any]) -> float:
    """Score valuation quality (0-100). Higher = cheaper / better value."""
    pe   = _safe(f.get("pe_ratio"),   default=25.0)
    pb   = _safe(f.get("pb_ratio"),   default=3.0)
    fcfy = _safe(f.get("fcf_yield"),  default=0.03)

    # PE: 10 → score 90, 25 → 50, 40 → 10
    pe_score = _clamp(100.0 - (pe - 10.0) * 2.67)

    # PB: 1 → 90, 3 → 50, 5 → 10
    pb_score = _clamp(100.0 - (pb - 1.0) * 20.0)

    # FCF yield: 0% → 0, 3% → 50, 6%+ → 100
    fcfy_score = _clamp(fcfy * 1_666.67)

    return round(0.35 * pe_score + 0.25 * pb_score + 0.40 * fcfy_score, 2)


# ---------------------------------------------------------------------------
# 2. Profitability dimension
# ---------------------------------------------------------------------------

def _score_profitability(f: dict[str, Any]) -> float:
    """Score profitability quality (0-100). Higher = more profitable."""
    roe  = _safe(f.get("roe"),              default=0.10)
    roic = _safe(f.get("roic"),             default=0.10)
    gm   = _safe(f.get("gross_margin"),     default=0.30)
    om   = _safe(f.get("operating_margin"), default=0.10)
    nm   = _safe(f.get("net_margin"),       default=0.05)

    # ROE: 0% → 0, 15% → 50, 30%+ → 100
    roe_score  = _clamp(roe  * 333.33)
    roic_score = _clamp(roic * 333.33)

    # Margins: gross >50% → 100, operating >20% → 100, net >15% → 100
    gm_score = _clamp(gm  * 200.0)
    om_score = _clamp(om  * 500.0)
    nm_score = _clamp(nm  * 666.67)

    return round(
        0.30 * roe_score
        + 0.25 * roic_score
        + 0.20 * gm_score
        + 0.15 * om_score
        + 0.10 * nm_score,
        2,
    )


# ---------------------------------------------------------------------------
# 3. Growth sustainability dimension
# ---------------------------------------------------------------------------

def _score_growth(f: dict[str, Any]) -> float:
    """Score growth sustainability quality (0-100)."""
    eps_g = _safe(f.get("eps_growth_3y"), default=0.0)
    div_g = _safe(f.get("dividend_growth_5y"), default=0.0)

    # EPS growth: -20% → 0, 0% → 40, 15% → 75, 25%+ → 100
    eps_score = _clamp(40.0 + eps_g * 240.0)

    # Growth consistency bonus: positive EPS + positive dividend growth
    consistency = 0.0
    if eps_g > 0:
        consistency += 25.0
    if eps_g > 0.10:
        consistency += 15.0
    if div_g > 0:
        consistency += 10.0

    return round(0.60 * eps_score + 0.40 * consistency, 2)


# ---------------------------------------------------------------------------
# 4. Balance sheet strength dimension
# ---------------------------------------------------------------------------

def _score_balance_sheet(f: dict[str, Any]) -> float:
    """Score balance sheet quality (0-100)."""
    de  = _safe(f.get("debt_to_equity"),  default=1.5)
    acc = _safe(f.get("accruals_ratio"),  default=0.05)

    # D/E: 0 → 100, 1 → 67, 3 → 0
    de_score = _clamp(100.0 - de * 33.33)

    # Accruals: -0.10 → 100, 0 → 67, 0.15 → 0
    acc_score = _clamp(66.67 - acc * 444.44)

    return round(0.55 * de_score + 0.45 * acc_score, 2)


# ---------------------------------------------------------------------------
# 5. Capital allocation dimension
# ---------------------------------------------------------------------------

def _score_capital_allocation(f: dict[str, Any]) -> float:
    """Score capital allocation quality (0-100)."""
    fcfy   = _safe(f.get("fcf_yield"),       default=0.03)
    divy   = _safe(f.get("dividend_yield"),  default=0.02)
    payout = _safe(f.get("payout_ratio"),    default=0.50)
    insiders = _safe(f.get("insider_buy_ratio"), default=0.0)

    # Dividend covered by FCF (0 ≤ ratio ≤ 1 is ideal)
    if fcfy > 0:
        coverage_ratio = min(divy / fcfy, 2.0) if fcfy > 0 else 1.0
        coverage_score = _clamp(100.0 - coverage_ratio * 50.0)
    else:
        coverage_score = 30.0  # penalise when no FCF

    # Payout ratio: 0-40% ideal, 80%+ poor
    payout_score = _clamp(100.0 - payout * 125.0)

    # Insider buy ratio: 0 → 50, 1.0 → 100
    insider_score = _clamp(50.0 + insiders * 50.0)

    return round(0.45 * coverage_score + 0.35 * payout_score + 0.20 * insider_score, 2)


# ---------------------------------------------------------------------------
# 6. Moat durability dimension
# ---------------------------------------------------------------------------

def _score_moat(f: dict[str, Any]) -> float:
    """Score moat durability (0-100).

    A moat is inferred from high/stable returns on capital, strong gross margins
    (pricing power), low accruals (earnings reliability), and insider confidence.
    """
    roe    = _safe(f.get("roe"),              default=0.10)
    roic   = _safe(f.get("roic"),             default=0.10)
    gm     = _safe(f.get("gross_margin"),     default=0.30)
    acc    = _safe(f.get("accruals_ratio"),   default=0.05)
    ins    = _safe(f.get("insider_buy_ratio"),default=0.0)

    # Pricing power: gross margin > 40% is a strong moat signal
    pricing_score = _clamp(gm * 250.0)

    # Returns > WACC proxy (ROIC > 15% indicates moat)
    returns_score = _clamp((roe + roic) / 2.0 * 333.33)

    # Earnings quality: low accruals
    earnings_quality = _clamp(66.67 - acc * 444.44)

    # Management alignment: insider buying
    mgmt_score = _clamp(50.0 + ins * 50.0)

    return round(
        0.35 * pricing_score
        + 0.30 * returns_score
        + 0.25 * earnings_quality
        + 0.10 * mgmt_score,
        2,
    )


# ---------------------------------------------------------------------------
# Piotroski F-Score
# ---------------------------------------------------------------------------

def piotroski_f_score(f: dict[str, Any]) -> dict[str, Any]:
    """Compute Piotroski F-Score (0-9).

    Returns dict with individual criteria flags, total score, and quality label.
    >= 7 = HIGH quality; 4-6 = NEUTRAL; <= 3 = LOW quality.
    """
    roa    = _safe(f.get("roa"),              default=0.0)
    fcfy   = _safe(f.get("fcf_yield"),        default=0.0)
    eps_g  = _safe(f.get("eps_growth_3y"),    default=0.0)
    acc    = _safe(f.get("accruals_ratio"),   default=0.0)
    de     = _safe(f.get("debt_to_equity"),   default=99.0)
    divy   = _safe(f.get("dividend_yield"),   default=0.0)
    payout = _safe(f.get("payout_ratio"),     default=1.0)
    gm     = _safe(f.get("gross_margin"),     default=0.0)
    om     = _safe(f.get("operating_margin"), default=0.0)

    criteria = {
        # Profitability
        "F1_roa_positive":       int(roa > 0.0),
        "F2_fcf_positive":       int(fcfy > 0.0),
        "F3_eps_growth_positive":int(eps_g > 0.0),
        "F4_low_accruals":       int(acc < 0.0),
        # Leverage / Liquidity
        "F5_low_leverage":       int(de < 1.0),
        "F6_dividend_covered":   int(divy <= fcfy or fcfy <= 0.0),
        "F7_sustainable_payout": int(payout < 0.80),
        # Operating Efficiency
        "F8_strong_gross_margin":    int(gm > 0.25),
        "F9_positive_operating_margin": int(om > 0.10),
    }

    total = sum(criteria.values())
    if total >= 7:
        label = "HIGH"
    elif total >= 4:
        label = "NEUTRAL"
    else:
        label = "LOW"

    return {"criteria": criteria, "score": total, "label": label}


# ---------------------------------------------------------------------------
# Composite quality score (z-score formula)
# ---------------------------------------------------------------------------

def _composite_quality(
    f: dict[str, Any],
    population: list[dict[str, Any]] | None = None,
) -> float:
    """Compute composite quality score (0-100) via z-score formula.

    Q = 0.23·z(ROE) + 0.18·z(GrossMargin) + 0.13·z(-D/E)
      + 0.13·z(-Accruals) + 0.08·z(-EarningsVar) + 0.15·z(FCFYield)
      + 0.10·z(ROE_Gap)   ← PE/NAV mispricing signal

    If ``population`` is provided (list of fundamentals dicts), cross-sectional
    mu / sigma are used.  Otherwise, market-wide reference stats apply.
    """
    roe   = _safe(f.get("roe"),              0.0)
    gm    = _safe(f.get("gross_margin"),     0.0)
    de    = _safe(f.get("debt_to_equity"),   0.0)
    acc   = _safe(f.get("accruals_ratio"),   0.0)
    eps_g = _safe(f.get("eps_growth_3y"),    0.0)
    fcfy  = _safe(f.get("fcf_yield"),        0.0)

    # Negated values for "lower is better" dimensions
    neg_de  = -de
    neg_acc = -acc
    # EarningsVar proxy: deviation from expected 10% growth, negated
    neg_ev  = -(abs(eps_g - 0.10))

    # PE/NAV roe_gap — tier-downweighted for intangibles-heavy sectors
    pnm     = compute_pe_nav_metrics(f)
    roe_gap = pnm["roe_gap"] if pnm["roe_gap"] is not None else 0.0
    # For Tier 3 the signal is unreliable — blend toward zero (neutral)
    tier    = pnm["sector_tier"]
    if tier == 3:
        roe_gap = roe_gap * 0.4
    elif tier == 2:
        roe_gap = roe_gap * 0.7

    if population:
        def _pop_z(key: str, raw_values: list[float], val: float) -> float:
            finite = [v for v in raw_values if not (math.isnan(v) or math.isinf(v))]
            mu, sigma = _population_stats(finite) if finite else (0.0, 1.0)
            return _z_score(val, mu, sigma)

        roe_vals    = [_safe(p.get("roe"),              0.0) for p in population]
        gm_vals     = [_safe(p.get("gross_margin"),     0.0) for p in population]
        de_vals     = [-_safe(p.get("debt_to_equity"),  0.0) for p in population]
        acc_vals    = [-_safe(p.get("accruals_ratio"),  0.0) for p in population]
        ev_vals     = [-(abs(_safe(p.get("eps_growth_3y"), 0.0) - 0.10)) for p in population]
        fcfy_vals   = [_safe(p.get("fcf_yield"),        0.0) for p in population]
        # roe_gap cross-section: compute on-the-fly per population member
        gap_vals    = []
        for p in population:
            pm = compute_pe_nav_metrics(p)
            g  = pm["roe_gap"] if pm["roe_gap"] is not None else 0.0
            t  = pm["sector_tier"]
            gap_vals.append(g * (0.4 if t == 3 else 0.7 if t == 2 else 1.0))

        z_roe    = _pop_z("roe",     roe_vals,  roe)
        z_gm     = _pop_z("gm",      gm_vals,   gm)
        z_de     = _pop_z("de",      de_vals,   neg_de)
        z_acc    = _pop_z("acc",     acc_vals,  neg_acc)
        z_ev     = _pop_z("ev",      ev_vals,   neg_ev)
        z_fcfy   = _pop_z("fcfy",    fcfy_vals, fcfy)
        z_roegap = _pop_z("roe_gap", gap_vals,  roe_gap)
    else:
        z_roe    = _z_score(roe,     **_REF_STATS["roe"])
        z_gm     = _z_score(gm,      **_REF_STATS["gross_margin"])
        z_de     = _z_score(neg_de,  **_REF_STATS["neg_debt_equity"])
        z_acc    = _z_score(neg_acc, **_REF_STATS["neg_accruals"])
        z_ev     = _z_score(neg_ev,  **_REF_STATS["neg_earn_var"])
        z_fcfy   = _z_score(fcfy,    **_REF_STATS["fcf_yield"])
        z_roegap = _z_score(roe_gap, **_REF_STATS["roe_gap"])

    q_z = (
        _COMPOSITE_WEIGHTS["roe"]              * z_roe
        + _COMPOSITE_WEIGHTS["gross_margin"]   * z_gm
        + _COMPOSITE_WEIGHTS["neg_debt_equity"] * z_de
        + _COMPOSITE_WEIGHTS["neg_accruals"]   * z_acc
        + _COMPOSITE_WEIGHTS["neg_earn_var"]   * z_ev
        + _COMPOSITE_WEIGHTS["fcf_yield"]      * z_fcfy
        + _COMPOSITE_WEIGHTS["roe_gap"]        * z_roegap
    )

    # q_z is in roughly [-3, +3]; map to [0, 100]
    return round(_z_to_score(q_z), 2)


# ---------------------------------------------------------------------------
# Main entry point: score_quality
# ---------------------------------------------------------------------------

def score_quality(
    ticker: str,
    fundamentals: dict[str, Any],
    population: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Score stock quality across 6 dimensions + composite + Piotroski.

    Args:
        ticker: Ticker symbol (e.g. "NVDA").
        fundamentals: Dict matching ``ad_fundamental_metrics`` columns.
        population: Optional list of fundamentals dicts for cross-sectional
                    z-score normalization.

    Returns:
        {
            "ticker": str,
            "analyst": "quality",
            "as_of_date": ISO date string,
            "dimensions": {
                "valuation": float,          # 0-100
                "profitability": float,
                "growth": float,
                "balance_sheet": float,
                "capital_allocation": float,
                "moat": float,
            },
            "piotroski": {
                "criteria": {F1..F9: 0|1},
                "score": int (0-9),
                "label": "HIGH"|"NEUTRAL"|"LOW",
            },
            "composite_score": float,        # 0-100, z-score formula
            "score": float,                  # 0-100, equal-weight of 6 dims
            "quality_label": str,            # "HIGH"|"ABOVE_AVERAGE"|"AVERAGE"|"BELOW_AVERAGE"|"LOW"
            "summary": str,
        }
    """
    f = fundamentals or {}

    # PE/NAV analysis (computed once, reused by dim scorer and result)
    pe_nav_analysis = compute_pe_nav_metrics(f)

    dims = {
        "valuation":          _score_valuation(f),
        "profitability":      _score_profitability(f),
        "growth":             _score_growth(f),
        "balance_sheet":      _score_balance_sheet(f),
        "capital_allocation": _score_capital_allocation(f),
        "moat":               _score_moat(f),
        "pe_nav":             _score_pe_nav(f),
    }

    # Equal-weight composite of the 7 dimensions
    eq_score = round(sum(dims.values()) / len(dims), 2)

    # Z-score composite (includes roe_gap factor)
    z_composite = _composite_quality(f, population)

    # Piotroski
    piotroski = piotroski_f_score(f)

    # Quality label based on equal-weight score
    if eq_score >= 75:
        quality_label = "HIGH"
    elif eq_score >= 60:
        quality_label = "ABOVE_AVERAGE"
    elif eq_score >= 45:
        quality_label = "AVERAGE"
    elif eq_score >= 30:
        quality_label = "BELOW_AVERAGE"
    else:
        quality_label = "LOW"

    # Human-readable summary
    top_dims  = sorted(dims.items(), key=lambda x: x[1], reverse=True)[:2]
    weak_dims = sorted(dims.items(), key=lambda x: x[1])[:2]
    roe_gap   = pe_nav_analysis.get("roe_gap")
    gap_str   = f", ROE gap {roe_gap:+.1%}" if roe_gap is not None else ""
    summary = (
        f"{ticker} quality: {quality_label} (score {eq_score:.0f}/100, "
        f"composite z-score {z_composite:.0f}/100{gap_str}). "
        f"Piotroski F={piotroski['score']}/9 ({piotroski['label']}). "
        f"NAV quadrant: {pe_nav_analysis['nav_quadrant']} "
        f"[tier {pe_nav_analysis['sector_tier']}]. "
        f"Strengths: {top_dims[0][0]} ({top_dims[0][1]:.0f}), "
        f"{top_dims[1][0]} ({top_dims[1][1]:.0f}). "
        f"Weaknesses: {weak_dims[0][0]} ({weak_dims[0][1]:.0f}), "
        f"{weak_dims[1][0]} ({weak_dims[1][1]:.0f})."
    )

    as_of_date = str(f.get("as_of_date", datetime.now(timezone.utc).date().isoformat()))

    return {
        "ticker":          ticker,
        "analyst":         "quality",
        "as_of_date":      as_of_date,
        "dimensions":      dims,
        "pe_nav_analysis": pe_nav_analysis,
        "piotroski":       piotroski,
        "composite_score": z_composite,
        "score":           eq_score,
        "quality_label":   quality_label,
        "summary":         summary,
    }


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def _get_db(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_path) if db_path else _ICDEV_DB
    conn = get_connection(str(path))
    return conn


def load_fundamentals(
    ticker: str,
    db_path: str | Path | None = None,
    as_of_date: str | None = None,
) -> dict[str, Any]:
    """Load fundamental metrics for a ticker from ``ad_fundamental_metrics``.

    Returns the most recent row if ``as_of_date`` is not specified.
    Returns empty dict if no data is found.
    """
    conn = _get_db(db_path)
    try:
        if as_of_date:
            row = conn.execute(
                "SELECT * FROM ad_fundamental_metrics "
                "WHERE ticker = %s AND as_of_date = %s LIMIT 1",
                (ticker.upper(), as_of_date),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM ad_fundamental_metrics "
                "WHERE ticker = %s ORDER BY as_of_date DESC LIMIT 1",
                (ticker.upper(),),
            ).fetchone()
        return dict(row) if row else {}
    except sqlite3.OperationalError:
        return {}
    finally:
        conn.close()


def load_population(
    db_path: str | Path | None = None,
    as_of_date: str | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Load the most recent fundamental metrics for all tickers (cross-section).

    Used for cross-sectional z-score normalization.
    """
    conn = _get_db(db_path)
    try:
        if as_of_date:
            rows = conn.execute(
                "SELECT * FROM ad_fundamental_metrics WHERE as_of_date = %s LIMIT %s",
                (as_of_date, limit),
            ).fetchall()
        else:
            # Most recent snapshot per ticker
            rows = conn.execute(
                """
                SELECT f.*
                FROM ad_fundamental_metrics f
                INNER JOIN (
                    SELECT ticker, MAX(as_of_date) AS max_date
                    FROM ad_fundamental_metrics
                    GROUP BY ticker
                ) latest ON f.ticker = latest.ticker AND f.as_of_date = latest.max_date
                LIMIT %s
                """,
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []
    finally:
        conn.close()


def save_quality_scores(
    ticker: str,
    result: dict[str, Any],
    db_path: str | Path | None = None,
) -> bool:
    """Upsert quality scores into ``ad_quality_scores``.

    Returns True on success, False on failure.
    """
    conn = _get_db(db_path)
    try:
        dims    = result.get("dimensions", {})
        pnm     = result.get("pe_nav_analysis", {})
        now     = _utcnow()
        conn.execute(
            """
            INSERT INTO ad_quality_scores (
                id, ticker, as_of_date,
                value_quality, growth_quality, profitability_quality,
                balance_sheet_quality, capital_allocation_quality, moat_score,
                composite_quality_score, model_version,
                classification, created_at, updated_at,
                pe_nav_score, implied_roe, roe_gap, nav_quadrant
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'CUI // SP-CTI', %s, %s, %s, %s, %s, %s)
            ON CONFLICT(ticker, as_of_date) DO UPDATE SET
                value_quality              = excluded.value_quality,
                growth_quality             = excluded.growth_quality,
                profitability_quality      = excluded.profitability_quality,
                balance_sheet_quality      = excluded.balance_sheet_quality,
                capital_allocation_quality = excluded.capital_allocation_quality,
                moat_score                 = excluded.moat_score,
                composite_quality_score    = excluded.composite_quality_score,
                model_version              = excluded.model_version,
                updated_at                 = excluded.updated_at,
                pe_nav_score               = excluded.pe_nav_score,
                implied_roe                = excluded.implied_roe,
                roe_gap                    = excluded.roe_gap,
                nav_quadrant               = excluded.nav_quadrant
            """,
            (
                str(uuid.uuid4()),
                ticker.upper(),
                result.get("as_of_date", datetime.now(timezone.utc).date().isoformat()),
                dims.get("valuation"),
                dims.get("growth"),
                dims.get("profitability"),
                dims.get("balance_sheet"),
                dims.get("capital_allocation"),
                dims.get("moat"),
                result.get("composite_score"),
                MODEL_VERSION,
                now,
                now,
                dims.get("pe_nav"),
                pnm.get("implied_roe"),
                pnm.get("roe_gap"),
                pnm.get("nav_quadrant"),
            ),
        )
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_sample_fundamentals(ticker: str) -> dict[str, Any]:
    """Build a sample/demo fundamentals dict for testing purposes."""
    return {
        "ticker":            ticker,
        "as_of_date":        datetime.now(timezone.utc).date().isoformat(),
        "sector":            "information_technology",
        "pe_ratio":          28.5,
        "pb_ratio":          8.2,
        "ps_ratio":          7.1,
        "ev_ebitda":         22.0,
        "fcf_yield":         0.032,
        "roe":               0.42,
        "roa":               0.18,
        "roic":              0.35,
        "gross_margin":      0.56,
        "operating_margin":  0.26,
        "net_margin":        0.22,
        "eps_ttm":           12.40,
        "eps_growth_3y":     0.18,
        "dividend_yield":    0.01,
        "payout_ratio":      0.08,
        "dividend_growth_5y":0.05,
        "debt_to_equity":    0.52,
        "accruals_ratio":   -0.03,
        "insider_buy_ratio": 0.15,
        "nav_per_share":     None,  # N/A for tech sector (Tier 3)
    }


def main() -> None:
    # Ensure UTF-8 output on all platforms (Windows cp1252 workaround)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="FathomDesk Quality Analyst — 6-dimension quality scorer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--ticker", required=True, help="Stock ticker symbol (e.g. NVDA)")
    parser.add_argument(
        "--save", action="store_true",
        help="Persist results to ad_quality_scores in icdev.db",
    )
    parser.add_argument(
        "--population", action="store_true",
        help="Load cross-sectional population from DB for z-score normalization",
    )
    parser.add_argument(
        "--db", default=None,
        help="Path to SQLite DB (default: data/icdev.db)",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument(
        "--sample", action="store_true",
        help="Use built-in sample data (no DB required)",
    )
    args = parser.parse_args()

    ticker = args.ticker.upper()
    db_path = args.db or None

    # Load fundamentals
    if args.sample:
        fundamentals = _build_sample_fundamentals(ticker)
    else:
        fundamentals = load_fundamentals(ticker, db_path)
        if not fundamentals:
            print(
                json.dumps(
                    {"ok": False, "error": f"No fundamental data for {ticker} in DB. "
                     "Use --sample for demo or load data via ad_fundamental_metrics."},
                    indent=2,
                )
            )
            sys.exit(1)

    # Load population for cross-sectional z-scores
    population = None
    if args.population:
        population = load_population(db_path)
        if len(population) < 5:
            population = None  # not enough data, fall back to reference stats

    # Score
    result = score_quality(ticker, fundamentals, population)

    if args.save:
        saved = save_quality_scores(ticker, result, db_path)
        result["saved"] = saved

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        dims = result["dimensions"]
        piot = result["piotroski"]
        pnm  = result.get("pe_nav_analysis", {})
        print(f"\n{'='*64}")
        print(f"  FathomDesk Quality Score — {ticker}")
        print(f"{'='*64}")
        print(f"  Overall: {result['score']:.1f}/100  [{result['quality_label']}]")
        print(f"  Composite (z-score formula): {result['composite_score']:.1f}/100")
        print(f"  Piotroski F-Score: {piot['score']}/9  [{piot['label']}]")
        print("\n  Dimension Scores (7):")
        for dim, val in sorted(dims.items(), key=lambda x: x[1], reverse=True):
            bar = "#" * int(val / 5)
            print(f"    {dim:<24} {val:5.1f}  {bar}")
        print("\n  PE/NAV Analysis:")
        print(f"    Quadrant:     {pnm.get('nav_quadrant', 'N/A')}")
        print(f"    Sector Tier:  {pnm.get('sector_tier', 'N/A')}")
        if pnm.get("implied_roe") is not None:
            print(f"    Implied ROE:  {pnm['implied_roe']:.1%}")
        if pnm.get("roe_gap") is not None:
            gap = pnm["roe_gap"]
            direction = "undervalued ▲" if gap > 0 else "overvalued ▼" if gap < 0 else "fair"
            print(f"    ROE Gap:      {gap:+.1%}  ({direction})")
        print("\n  Piotroski Criteria:")
        for crit, flag in piot["criteria"].items():
            mark = "✓" if flag else "✗"
            print(f"    {mark} {crit}")
        print(f"\n  {result['summary']}")
        print(f"{'='*64}\n")


if __name__ == "__main__":
    main()
