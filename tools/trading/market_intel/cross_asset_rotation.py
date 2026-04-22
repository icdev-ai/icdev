"""Cross-asset rotation engine for FathomDesk (Family: XAR).

Computes 7 inter-market ratios with 3-week and 8-week OLS trend slopes.
Each ratio is labeled RISING / FALLING / NEUTRAL with magnitude.

Ratios:
  1. gold_sp500    — GLD / SPY    (risk-off vs equities)
  2. copper_gold   — HG=F / GLD   (Doctor Copper: cyclical vs safe-haven)
  3. bonds_equity  — TLT / SPY    (duration vs risk)
  4. oil_equity    — CL=F / SPY   (commodity cycle vs equities)
  5. dxy_commodity — UUP / DJP    (dollar vs commodity basket)
  6. gold_oil      — GLD / USO    (quality spread within real assets)
  7. real_yield    — ^TNX - T5YIFR (real yield proxy in %)

Multi-signal rotation confirmation (5-condition, score 0-5):
  1. TLT/SPY rising ≥3 weeks   → bonds_equity trend_3w == RISING
  2. Gold/S&P rising ≥3 weeks  → gold_sp500 trend_3w == RISING
  3. HY OAS >50bps above 8-week trough
  4. VIX 20-day MA > 22
  5. Copper/Gold falling ≥4 weeks

  Signals: ≥3 → ROTATION_ALERT, ≥4 → HARD_EXIT, <3 → NEUTRAL

Usage:
    python tools/trading/market_intel/cross_asset_rotation.py --compute --json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

# Trading-day window sizes
_DAYS_3W = 15   # 3 calendar weeks ≈ 15 trading days
_DAYS_8W = 40   # 8 calendar weeks ≈ 40 trading days

# Threshold for RISING/FALLING on price ratios (% per week)
_SLOPE_THRESHOLD_RATIO = 0.20   # 0.20 %/week ≈ 10 % annualised
# Threshold for RISING/FALLING on real yield (pct-points per week)
_SLOPE_THRESHOLD_YIELD = 0.05   # 5 bp/week

# Magnitude classification thresholds (multiples of the base threshold)
_MAGNITUDE_STRONG_MULT = 2.5   # |slope| > 2.5× threshold → "strong"
_MAGNITUDE_MODERATE_MULT = 1.0 # |slope| ≥ 1.0× threshold → "moderate" (else "weak")

# Fallback when FRED T5YIFR is unavailable (long-run median)
_BREAKEVEN_FALLBACK_PCT = 2.3

# 4-week window for copper/gold falling check (condition 5)
_DAYS_4W = 20  # 4 calendar weeks ≈ 20 trading days

# Rotation scoring parameters
_VIX_MA_DAYS = 20
_VIX_MA_THRESHOLD = 22.0
_HY_OAS_WIDENING_BPS = 50.0          # bps above 8-week trough triggers condition 3
_HY_OAS_TROUGH_LOOKBACK = 40         # trading days (~8 weeks) for trough window
_ROTATION_ALERT_SCORE = 3
_ROTATION_HARD_EXIT_SCORE = 4

# Approximate anchor for deterministic HY OAS fallback (bps)
_HY_OAS_ANCHOR_BPS = 420.0

# Definitions for price-pair ratios (numerator ETF / denominator ETF)
_RATIO_DEFS: list[dict] = [
    {
        "name": "gold_sp500",
        "label": "Gold / S&P 500 (GLD/SPY)",
        "numerator": "GLD",
        "denominator": "SPY",
        "description": "Risk-off indicator: rising signals defensive rotation into gold",
    },
    {
        "name": "copper_gold",
        "label": "Copper / Gold (HG=F/GLD)",
        "numerator": "HG=F",
        "denominator": "GLD",
        "description": "Doctor Copper: rising signals cyclical/growth preference over safe-haven",
    },
    {
        "name": "bonds_equity",
        "label": "Bonds / Equity (TLT/SPY)",
        "numerator": "TLT",
        "denominator": "SPY",
        "description": "Duration vs risk: rising signals flight to safety or falling rate expectations",
    },
    {
        "name": "oil_equity",
        "label": "Oil / Equity (CL=F/SPY)",
        "numerator": "CL=F",
        "denominator": "SPY",
        "description": "Commodity cycle: rising signals inflationary pressure or supply shock",
    },
    {
        "name": "dxy_commodity",
        "label": "DXY / Commodity Basket (UUP/DJP)",
        "numerator": "UUP",
        "denominator": "DJP",
        "description": "Dollar vs commodities: rising signals USD strength / commodity headwind",
    },
    {
        "name": "gold_oil",
        "label": "Gold / Oil (GLD/USO)",
        "numerator": "GLD",
        "denominator": "USO",
        "description": "Quality spread: rising signals flight to monetary safe-haven over energy hedge",
    },
]

_RATIO_TICKERS: list[str] = list({
    sym
    for defn in _RATIO_DEFS
    for sym in [defn["numerator"], defn["denominator"]]
})
_TNX_TICKER = "^TNX"
_FRED_BREAKEVEN = "T5YIFR"

# Approximate anchor prices for deterministic fallback
_SAMPLE_ANCHORS: dict[str, float] = {
    "GLD": 230.0,
    "SPY": 520.0,
    "HG=F": 4.50,
    "TLT": 93.0,
    "CL=F": 82.0,
    "UUP": 28.5,
    "DJP": 28.0,
    "USO": 75.0,
    "^TNX": 4.40,
}


# ---------------------------------------------------------------------------
# Magnitude helpers
# ---------------------------------------------------------------------------

def _magnitude_label(slope_abs: float, is_yield: bool = False) -> str:
    """Classify slope magnitude as 'strong', 'moderate', or 'weak'.

    Uses ratio or yield-specific base threshold scaled by multipliers.
    """
    threshold = _SLOPE_THRESHOLD_YIELD if is_yield else _SLOPE_THRESHOLD_RATIO
    if slope_abs >= threshold * _MAGNITUDE_STRONG_MULT:
        return "strong"
    if slope_abs >= threshold * _MAGNITUDE_MODERATE_MULT:
        return "moderate"
    return "weak"


# ---------------------------------------------------------------------------
# Slope helpers
# ---------------------------------------------------------------------------

def _compute_slope(
    series: list[float],
    n_days: int,
    is_yield: bool = False,
) -> tuple[float, str, float]:
    """OLS slope on the last n_days of series.

    For price ratios: returns slope as % per week (normalised by window mean).
    For yield difference: returns slope as pct-points per week (absolute).

    Returns (slope_per_week, label, magnitude).
    """
    try:
        import numpy as np
    except ImportError:
        return 0.0, "NEUTRAL", 0.0

    window = np.array(series[-n_days:], dtype=float)
    if len(window) < 4:
        return 0.0, "NEUTRAL", 0.0

    x = np.arange(len(window), dtype=float)
    coeffs = np.polyfit(x, window, 1)
    slope_per_day = float(coeffs[0])

    if is_yield:
        slope_per_week = slope_per_day * 5
        threshold = _SLOPE_THRESHOLD_YIELD
    else:
        mean_val = float(np.mean(np.abs(window)))
        if mean_val == 0.0:
            return 0.0, "NEUTRAL", 0.0
        slope_per_week = (slope_per_day / mean_val) * 5 * 100  # % per week
        threshold = _SLOPE_THRESHOLD_RATIO

    magnitude = abs(slope_per_week)

    if slope_per_week > threshold:
        label = "RISING"
    elif slope_per_week < -threshold:
        label = "FALLING"
    else:
        label = "NEUTRAL"

    return round(slope_per_week, 4), label, round(magnitude, 4)


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------

def _fetch_live_prices(tickers: list[str], period: str = "3mo") -> dict[str, list[float]]:
    """Fetch daily close history for each ticker via yfinance.

    Returns {ticker: [closes]} for tickers with ≥10 observations.
    """
    try:
        import yfinance as yf
    except ImportError:
        return {}

    result: dict[str, list[float]] = {}
    for ticker in tickers:
        try:
            raw = yf.Ticker(ticker).history(period=period)
            closes = [round(float(v), 6) for v in raw["Close"].dropna()]
            if len(closes) >= 10:
                result[ticker] = closes
        except Exception:
            pass

    return result


def _fetch_fred_breakeven(n_days: int = 65) -> list[float] | None:
    """Fetch T5YIFR (5y5y forward breakeven) from FRED. Returns None if unavailable."""
    import os
    api_key = os.environ.get("FRED_API_KEY", "")
    if not api_key:
        return None
    try:
        from fredapi import Fred
        fred = Fred(api_key=api_key)
        series = fred.get_series(_FRED_BREAKEVEN, observation_start="2024-01-01")
        values = [float(v) for v in series.dropna()]
        return values[-n_days:] if values else None
    except Exception:
        return None


def _generate_sample_prices(tickers: list[str], n: int = 65) -> dict[str, list[float]]:
    """Deterministic daily closes seeded by current UTC date."""
    seed_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    base_seed = int(hashlib.sha256(seed_str.encode()).hexdigest()[:8], 16)

    result: dict[str, list[float]] = {}
    for ticker in tickers:
        anchor = _SAMPLE_ANCHORS.get(ticker, 100.0)
        rng = base_seed ^ hash(ticker)
        prices: list[float] = []
        p = anchor
        for i in range(n):
            step = ((rng >> (i % 30)) & 0xFF) / 255 * 0.016 - 0.008
            p = round(max(p * (1 + step), 0.001), 6)
            prices.append(p)
        result[ticker] = prices

    return result


def _fetch_vix_history(n_days: int = 30) -> list[float]:
    """Fetch ^VIX daily closes via yfinance. Returns [] if unavailable."""
    try:
        import yfinance as yf
        raw = yf.Ticker("^VIX").history(period="2mo")
        closes = [round(float(v), 4) for v in raw["Close"].dropna()]
        return closes[-n_days:] if closes else []
    except Exception:
        return []


def _fetch_hy_oas_history(n_days: int = 45) -> list[float]:
    """Fetch HY OAS (BAMLH0A0HYM2) from FRED in basis points. Returns [] if unavailable."""
    import os
    api_key = os.environ.get("FRED_API_KEY", "")
    if not api_key:
        return []
    try:
        from fredapi import Fred
        fred = Fred(api_key=api_key)
        series = fred.get_series("BAMLH0A0HYM2", observation_start="2025-01-01")
        values = [float(v) for v in series.dropna()]
        return values[-n_days:] if values else []
    except Exception:
        return []


def _generate_sample_vix(n: int = 30) -> list[float]:
    """Deterministic VIX daily sample seeded by UTC date."""
    seed_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    base_seed = int(hashlib.sha256(seed_str.encode()).hexdigest()[:8], 16)
    rng = base_seed ^ hash("VIX")
    values: list[float] = []
    v = 18.0
    for i in range(n):
        step = ((rng >> (i % 30)) & 0xFF) / 255 * 0.12 - 0.06
        v = round(max(v * (1 + step), 8.0), 2)
        values.append(v)
    return values


def _generate_sample_hy_oas(n: int = 45) -> list[float]:
    """Deterministic HY OAS daily sample seeded by UTC date (in basis points)."""
    seed_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    base_seed = int(hashlib.sha256(seed_str.encode()).hexdigest()[:8], 16)
    rng = base_seed ^ hash("HYOAS")
    values: list[float] = []
    v = _HY_OAS_ANCHOR_BPS
    for i in range(n):
        step = ((rng >> (i % 30)) & 0xFF) / 255 * 0.04 - 0.02
        v = round(max(v * (1 + step), 100.0), 2)
        values.append(v)
    return values


# ---------------------------------------------------------------------------
# Rotation engine
# ---------------------------------------------------------------------------

def compute_rotation(
    prices: dict[str, list[float]],
    breakeven_series: list[float] | None,
) -> dict:
    """Compute 7 cross-asset rotation ratios with 3w and 8w trend slopes.

    Args:
        prices: {ticker: [daily_closes]}.
        breakeven_series: Daily T5YIFR readings or None (uses constant fallback).

    Returns:
        Dict with 'ratios' list.
    """
    ratios: list[dict] = []

    # --- Ratios 1–6: price-pair ratios ---
    for defn in _RATIO_DEFS:
        num_sym = defn["numerator"]
        den_sym = defn["denominator"]
        num_p = prices.get(num_sym, [])
        den_p = prices.get(den_sym, [])

        min_len = min(len(num_p), len(den_p))
        if min_len < 4:
            ratios.append({
                **defn,
                "value": None,
                "trend_3w": "NEUTRAL",
                "trend_8w": "NEUTRAL",
                "slope_3w": 0.0,
                "slope_8w": 0.0,
                "magnitude_3w": 0.0,
                "magnitude_8w": 0.0,
                "slope_unit": "pct_per_week",
                "note": "insufficient_data",
            })
            continue

        ratio_series = [
            round(n / d, 8) if d != 0.0 else 0.0
            for n, d in zip(num_p[-min_len:], den_p[-min_len:])
        ]

        slope_3w, label_3w, mag_3w = _compute_slope(ratio_series, _DAYS_3W)
        slope_8w, label_8w, mag_8w = _compute_slope(ratio_series, _DAYS_8W)

        ratios.append({
            "name": defn["name"],
            "label": defn["label"],
            "numerator": num_sym,
            "denominator": den_sym,
            "description": defn["description"],
            "value": round(ratio_series[-1], 6),
            "trend_3w": label_3w,
            "trend_8w": label_8w,
            "slope_3w": slope_3w,
            "slope_8w": slope_8w,
            "magnitude_3w": mag_3w,
            "magnitude_label_3w": _magnitude_label(mag_3w),
            "magnitude_8w": mag_8w,
            "magnitude_label_8w": _magnitude_label(mag_8w),
            "slope_unit": "pct_per_week",
        })

    # --- Ratio 7: Real Yield proxy (^TNX - T5YIFR) ---
    tnx_series = prices.get(_TNX_TICKER, [])

    if len(tnx_series) >= 4:
        if breakeven_series and len(breakeven_series) >= 4:
            n = min(len(tnx_series), len(breakeven_series))
            ry_series = [
                round(t - b, 4)
                for t, b in zip(tnx_series[-n:], breakeven_series[-n:])
            ]
        else:
            ry_series = [round(v - _BREAKEVEN_FALLBACK_PCT, 4) for v in tnx_series]

        slope_3w, label_3w, mag_3w = _compute_slope(ry_series, _DAYS_3W, is_yield=True)
        slope_8w, label_8w, mag_8w = _compute_slope(ry_series, _DAYS_8W, is_yield=True)

        ratios.append({
            "name": "real_yield",
            "label": "Real Yield Proxy (^TNX - T5YIFR)",
            "numerator": "^TNX",
            "denominator": "T5YIFR",
            "description": (
                "Rising = tighter real financing conditions "
                "(typically negative for risk assets and gold)"
            ),
            "value": round(ry_series[-1], 4),
            "trend_3w": label_3w,
            "trend_8w": label_8w,
            "slope_3w": slope_3w,
            "slope_8w": slope_8w,
            "magnitude_3w": mag_3w,
            "magnitude_label_3w": _magnitude_label(mag_3w, is_yield=True),
            "magnitude_8w": mag_8w,
            "magnitude_label_8w": _magnitude_label(mag_8w, is_yield=True),
            "slope_unit": "pct_pts_per_week",
            "breakeven_source": "fred" if breakeven_series else "constant_fallback",
        })
    else:
        ratios.append({
            "name": "real_yield",
            "label": "Real Yield Proxy (^TNX - T5YIFR)",
            "numerator": "^TNX",
            "denominator": "T5YIFR",
            "description": (
                "Rising = tighter real financing conditions "
                "(typically negative for risk assets and gold)"
            ),
            "value": None,
            "trend_3w": "NEUTRAL",
            "trend_8w": "NEUTRAL",
            "slope_3w": 0.0,
            "slope_8w": 0.0,
            "magnitude_3w": 0.0,
            "magnitude_8w": 0.0,
            "slope_unit": "pct_pts_per_week",
            "note": "insufficient_data",
        })

    return {"ratios": ratios, "ratio_count": len(ratios)}


def compute_rotation_scoring(
    rotation_result: dict,
    prices: dict[str, list[float]],
    vix_series: list[float],
    hy_oas_series: list[float],
) -> dict:
    """Score the 5-condition rotation confirmation model.

    Conditions:
      1. TLT/SPY rising ≥3 weeks  (bonds_equity trend_3w == RISING)
      2. Gold/S&P rising ≥3 weeks  (gold_sp500 trend_3w == RISING)
      3. HY OAS >50bps above 8-week trough
      4. VIX 20-day MA > 22
      5. Copper/Gold falling ≥4 weeks  (OLS slope over 20 trading days)

    Thresholds:
      score ≥ 4 → HARD_EXIT
      score ≥ 3 → ROTATION_ALERT
      score <  3 → NEUTRAL

    Returns:
        rotation_score (int 0-5), rotation_signal (str),
        triggered_conditions (list[str]).
    """
    ratios_by_name = {r["name"]: r for r in rotation_result.get("ratios", [])}
    triggered: list[str] = []

    # Condition 1: TLT/SPY rising 3+ weeks
    if ratios_by_name.get("bonds_equity", {}).get("trend_3w") == "RISING":
        triggered.append("tlt_spy_rising_3w")

    # Condition 2: Gold/S&P rising 3+ weeks
    if ratios_by_name.get("gold_sp500", {}).get("trend_3w") == "RISING":
        triggered.append("gold_sp500_rising_3w")

    # Condition 3: HY OAS widened >50bps from 8-week trough
    if len(hy_oas_series) >= 8:
        lookback = min(_HY_OAS_TROUGH_LOOKBACK, len(hy_oas_series))
        trough = min(hy_oas_series[-lookback:])
        if hy_oas_series[-1] - trough > _HY_OAS_WIDENING_BPS:
            triggered.append("hy_oas_widened_50bps")

    # Condition 4: VIX 20-day MA > 22
    if len(vix_series) >= _VIX_MA_DAYS:
        vix_ma = sum(vix_series[-_VIX_MA_DAYS:]) / _VIX_MA_DAYS
        if vix_ma > _VIX_MA_THRESHOLD:
            triggered.append("vix_20d_ma_above_22")

    # Condition 5: Copper/Gold falling 4+ weeks
    cg_num = prices.get("HG=F", [])
    cg_den = prices.get("GLD", [])
    if len(cg_num) >= 4 and len(cg_den) >= 4:
        min_len = min(len(cg_num), len(cg_den))
        cg_series = [
            round(n / d, 8) if d != 0.0 else 0.0
            for n, d in zip(cg_num[-min_len:], cg_den[-min_len:])
        ]
        _, label_4w, _ = _compute_slope(cg_series, _DAYS_4W)
        if label_4w == "FALLING":
            triggered.append("copper_gold_falling_4w")

    score = len(triggered)

    if score >= _ROTATION_HARD_EXIT_SCORE:
        signal = "HARD_EXIT"
    elif score >= _ROTATION_ALERT_SCORE:
        signal = "ROTATION_ALERT"
    else:
        signal = "NEUTRAL"

    return {
        "rotation_score": score,
        "rotation_signal": signal,
        "triggered_conditions": triggered,
    }


def compute_cross_asset_rotation() -> dict:
    """Fetch data, compute 7 cross-asset rotation ratios, and score 5-condition rotation signal.

    Returns:
        Dict with ratios, rotation_signal, rotation_score, triggered_conditions,
        data_source, and timestamp.
    """
    all_tickers = _RATIO_TICKERS + [_TNX_TICKER]

    live = _fetch_live_prices(all_tickers, period="3mo")

    # Require ≥5 live tickers before trusting live data
    if len(live) >= 5:
        missing = [t for t in all_tickers if t not in live]
        if missing:
            live.update(_generate_sample_prices(missing))
        prices = live
        data_source = "live"
    else:
        prices = _generate_sample_prices(all_tickers)
        data_source = "sample"

    breakeven = _fetch_fred_breakeven(n_days=65)

    result = compute_rotation(prices, breakeven)

    # VIX and HY OAS for 5-condition scoring (fall back to deterministic sample)
    vix_series = _fetch_vix_history(n_days=30) or _generate_sample_vix(n=30)
    hy_oas_series = _fetch_hy_oas_history(n_days=45) or _generate_sample_hy_oas(n=45)

    scoring = compute_rotation_scoring(result, prices, vix_series, hy_oas_series)
    result.update(scoring)

    result["data_source"] = data_source
    result["timestamp"] = datetime.now(timezone.utc).isoformat()
    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Cross-asset rotation engine — 7 ratios + 5-condition rotation scoring"
    )
    parser.add_argument("--compute", action="store_true", help="Compute all 7 ratios and rotation signal")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    if not args.compute:
        parser.print_help()
        return

    result = compute_cross_asset_rotation()

    if args.json:
        print(json.dumps(result, indent=2))
        return

    print(f"Cross-Asset Rotation  [{result['data_source']}]  {result['timestamp']}")
    print(f"{'Ratio':<22} {'Value':>10}  {'3w':<8} {'mag':<10} {'slope':>8}  {'8w':<8} {'mag':<10} {'slope':>8}")
    print("-" * 88)
    for r in result["ratios"]:
        val_str = f"{r['value']:.5f}" if r["value"] is not None else "    N/A"
        unit = "pp" if r.get("slope_unit") == "pct_pts_per_week" else "% "
        mag3 = r.get("magnitude_label_3w", "")
        mag8 = r.get("magnitude_label_8w", "")
        print(
            f"{r['name']:<22} {val_str:>10}  "
            f"{r['trend_3w']:<8} {mag3:<10} {r['slope_3w']:>+7.3f}{unit}  "
            f"{r['trend_8w']:<8} {mag8:<10} {r['slope_8w']:>+7.3f}{unit}"
        )

    score = result.get("rotation_score", 0)
    signal = result.get("rotation_signal", "NEUTRAL")
    triggered = result.get("triggered_conditions", [])
    print(f"\nRotation Score: {score}/5  →  {signal}")
    if triggered:
        print("Triggered:", ", ".join(triggered))


if __name__ == "__main__":
    main()
