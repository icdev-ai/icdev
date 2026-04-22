"""Bond ETF weekly close data layer for FathomDesk trading engine.

Fetches 13-week weekly closes for TLT, HYG, LQD, AGG + SPY benchmark via yfinance.
Computes 4-week and 13-week momentum. Classifies bond market regime:
  CREDIT_STRESS      — HYG momentum lags LQD by >5pp (credit spread widening)
  DURATION_SELLOFF   — LQD and TLT both negative while SPY positive (rate sell-off)
  FLIGHT_TO_SAFETY   — TLT positive while SPY negative (risk-off bid)
  NEUTRAL            — no pattern active

Falls back to deterministic sample data when yfinance is unavailable.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

BOND_ETFS = ["TLT", "HYG", "LQD", "AGG"]
_SPY = "SPY"

# Sample anchor prices for deterministic fallback
_SAMPLE_ANCHORS = {
    "TLT": 92.0,
    "HYG": 77.0,
    "LQD": 106.0,
    "AGG": 96.0,
    "SPY": 510.0,
}


def _fetch_live_bond_etfs() -> dict | None:
    """Fetch 13 weekly closes for each bond ETF + SPY benchmark via yfinance.

    Returns None if yfinance is unavailable or all tickers fail.
    """
    try:
        import yfinance as yf
    except ImportError:
        return None

    results: dict[str, dict] = {}

    for ticker in BOND_ETFS + [_SPY]:
        try:
            # Fetch ~4 months of weekly data to guarantee ≥13 rows
            raw = yf.Ticker(ticker).history(period="4mo", interval="1wk")
            closes = list(raw["Close"].dropna()) if len(raw) > 0 else []
            # Take last 13 weeks
            closes = [round(float(v), 4) for v in closes[-13:]]
        except Exception:
            closes = []

        if len(closes) < 2:
            # Not enough data — skip; fallback handles missing tickers
            continue

        price = closes[-1]
        price_4w = closes[-4] if len(closes) >= 4 else closes[0]
        price_13w = closes[0]

        results[ticker] = {
            "price": round(price, 4),
            "prices_13w": closes,
            "momentum_4w": round((price - price_4w) / price_4w, 6) if price_4w else 0.0,
            "momentum_13w": round((price - price_13w) / price_13w, 6) if price_13w else 0.0,
        }

    return results if results else None


def _generate_sample_bond_etfs() -> dict[str, dict]:
    """Deterministic sample data seeded by current UTC date (includes SPY benchmark)."""
    seed_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    seed = int(hashlib.sha256(seed_str.encode()).hexdigest()[:8], 16)

    results: dict[str, dict] = {}
    for ticker in BOND_ETFS + [_SPY]:
        anchor = _SAMPLE_ANCHORS[ticker]
        # Reproducible but varied weekly walk (±0.5% per week)
        rng_seed = seed ^ hash(ticker)
        prices = []
        p = anchor
        for i in range(13):
            step = ((rng_seed >> (i * 3)) & 0xFF) / 0xFF * 0.01 - 0.005
            p = round(p * (1 + step), 4)
            prices.append(p)

        price = prices[-1]
        price_4w = prices[-4]
        price_13w = prices[0]

        results[ticker] = {
            "price": round(price, 4),
            "prices_13w": prices,
            "momentum_4w": round((price - price_4w) / price_4w, 6) if price_4w else 0.0,
            "momentum_13w": round((price - price_13w) / price_13w, 6) if price_13w else 0.0,
        }

    return results


def get_bond_etf_snapshot() -> dict:
    """Return 13-week weekly close snapshot for TLT, HYG, LQD, AGG + SPY benchmark.

    Each ticker entry contains:
      price        — latest weekly close
      prices_13w   — list of up to 13 weekly closes (oldest → newest)
      momentum_4w  — (price - price_4w_ago) / price_4w_ago
      momentum_13w — (price - price_13w_ago) / price_13w_ago

    Falls back to deterministic sample data when yfinance is unavailable.
    """
    live = _fetch_live_bond_etfs()
    all_tickers = BOND_ETFS + [_SPY]

    if live is not None:
        # Fill any missing tickers from sample data
        sample = _generate_sample_bond_etfs() if len(live) < len(all_tickers) else {}
        for ticker in all_tickers:
            if ticker not in live:
                live[ticker] = sample[ticker]
        data_source = "live"
        payload = live
    else:
        payload = _generate_sample_bond_etfs()
        data_source = "sample"

    return {
        **payload,
        "data_source": data_source,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# Regime classifier
# ---------------------------------------------------------------------------

# Threshold for CREDIT_STRESS: HYG underperforms LQD by more than this (as decimal)
_CREDIT_STRESS_THRESHOLD = 0.05


def classify_bond_etf_regime(snapshot: dict) -> dict:
    """Classify the bond market regime from a bond ETF snapshot.

    Rules (checked in priority order — first match wins):
      CREDIT_STRESS    — HYG momentum lags LQD by >5pp on 4w OR 13w horizon
      DURATION_SELLOFF — LQD and TLT both negative while SPY positive (4w OR 13w)
      FLIGHT_TO_SAFETY — TLT positive while SPY negative (4w OR 13w)
      NEUTRAL          — none of the above

    Args:
        snapshot: Output of get_bond_etf_snapshot(). Must contain TLT, HYG,
                  LQD, and SPY keys with momentum_4w / momentum_13w fields.

    Returns:
        Dict with:
          regime        — primary regime label
          active_signals — list of all triggered signal names (may span timeframes)
          momentum      — per-ticker {4w, 13w} momentum summary
    """
    def _mom(ticker: str, tf: str) -> float:
        entry = snapshot.get(ticker, {})
        return float(entry.get(f"momentum_{tf}", 0.0))

    hyg_4w, hyg_13w = _mom("HYG", "4w"), _mom("HYG", "13w")
    lqd_4w, lqd_13w = _mom("LQD", "4w"), _mom("LQD", "13w")
    tlt_4w, tlt_13w = _mom("TLT", "4w"), _mom("TLT", "13w")
    spy_4w, spy_13w = _mom("SPY", "4w"), _mom("SPY", "13w")

    active_signals: list[str] = []

    # --- CREDIT_STRESS -------------------------------------------------------
    credit_stress_4w = (hyg_4w - lqd_4w) < -_CREDIT_STRESS_THRESHOLD
    credit_stress_13w = (hyg_13w - lqd_13w) < -_CREDIT_STRESS_THRESHOLD
    if credit_stress_4w:
        active_signals.append("CREDIT_STRESS_4W")
    if credit_stress_13w:
        active_signals.append("CREDIT_STRESS_13W")

    # --- DURATION_SELLOFF ----------------------------------------------------
    duration_selloff_4w = lqd_4w < 0 and tlt_4w < 0 and spy_4w > 0
    duration_selloff_13w = lqd_13w < 0 and tlt_13w < 0 and spy_13w > 0
    if duration_selloff_4w:
        active_signals.append("DURATION_SELLOFF_4W")
    if duration_selloff_13w:
        active_signals.append("DURATION_SELLOFF_13W")

    # --- FLIGHT_TO_SAFETY ----------------------------------------------------
    flight_4w = tlt_4w > 0 and spy_4w < 0
    flight_13w = tlt_13w > 0 and spy_13w < 0
    if flight_4w:
        active_signals.append("FLIGHT_TO_SAFETY_4W")
    if flight_13w:
        active_signals.append("FLIGHT_TO_SAFETY_13W")

    # Primary regime — priority: CREDIT_STRESS > DURATION_SELLOFF > FLIGHT_TO_SAFETY > NEUTRAL
    if credit_stress_4w or credit_stress_13w:
        regime = "CREDIT_STRESS"
    elif duration_selloff_4w or duration_selloff_13w:
        regime = "DURATION_SELLOFF"
    elif flight_4w or flight_13w:
        regime = "FLIGHT_TO_SAFETY"
    else:
        regime = "NEUTRAL"

    return {
        "regime": regime,
        "active_signals": active_signals,
        "momentum": {
            t: {
                "4w": round(_mom(t, "4w"), 6),
                "13w": round(_mom(t, "13w"), 6),
            }
            for t in ["TLT", "HYG", "LQD", "AGG", "SPY"]
        },
    }
