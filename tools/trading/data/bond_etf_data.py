"""Bond ETF weekly close data layer for FathomDesk trading engine.

Fetches 13-week weekly closes for TLT, HYG, LQD, AGG via yfinance.
Computes 4-week and 13-week momentum. Falls back to deterministic sample
data when yfinance is unavailable.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

BOND_ETFS = ["TLT", "HYG", "LQD", "AGG"]

# Sample anchor prices for deterministic fallback
_SAMPLE_ANCHORS = {
    "TLT": 92.0,
    "HYG": 77.0,
    "LQD": 106.0,
    "AGG": 96.0,
}


def _fetch_live_bond_etfs() -> dict | None:
    """Fetch 13 weekly closes for each bond ETF via yfinance.

    Returns None if yfinance is unavailable or all tickers fail.
    """
    try:
        import yfinance as yf
    except ImportError:
        return None

    results: dict[str, dict] = {}

    for ticker in BOND_ETFS:
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
    """Deterministic sample data seeded by current UTC date."""
    seed_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    seed = int(hashlib.sha256(seed_str.encode()).hexdigest()[:8], 16)

    results: dict[str, dict] = {}
    for ticker in BOND_ETFS:
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
    """Return 13-week weekly close snapshot for TLT, HYG, LQD, AGG.

    Each ticker entry contains:
      price        — latest weekly close
      prices_13w   — list of up to 13 weekly closes (oldest → newest)
      momentum_4w  — (price - price_4w_ago) / price_4w_ago
      momentum_13w — (price - price_13w_ago) / price_13w_ago

    Falls back to deterministic sample data when yfinance is unavailable.
    """
    live = _fetch_live_bond_etfs()

    if live is not None:
        # Fill any missing tickers from sample data
        sample = _generate_sample_bond_etfs() if len(live) < len(BOND_ETFS) else {}
        for ticker in BOND_ETFS:
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
