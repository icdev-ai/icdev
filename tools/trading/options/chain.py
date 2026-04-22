# CUI // SP-CTI
"""Options chain utilities — IV rank and percentile computation."""

import time
import math
from datetime import datetime, timezone
from typing import Optional

import yfinance as yf

# In-memory cache: key -> (timestamp, result)
_IVR_CACHE: dict = {}
_CACHE_TTL = 3600  # 1 hour in seconds


def _get_atm_iv(options, spot: float) -> Optional[float]:
    """Return ATM implied volatility from a yfinance Options object."""
    calls = options.calls
    puts = options.puts
    if calls is None or calls.empty:
        return None
    # Find strike closest to spot
    strikes = calls["strike"].tolist()
    if not strikes:
        return None
    atm_strike = min(strikes, key=lambda s: abs(s - spot))
    row = calls[calls["strike"] == atm_strike]
    if row.empty:
        return None
    iv = row["impliedVolatility"].iloc[0]
    if iv is None or (isinstance(iv, float) and math.isnan(iv)):
        # fall back to puts
        if puts is not None and not puts.empty:
            row_p = puts[puts["strike"] == atm_strike]
            if not row_p.empty:
                iv_p = row_p["impliedVolatility"].iloc[0]
                if iv_p and not math.isnan(iv_p):
                    return float(iv_p)
        return None
    return float(iv)


def compute_ivr(ticker: str, current_atm_iv: float) -> dict:
    """Compute IV Rank and IV Percentile for *ticker* using 52-week history.

    Fetches the options chain across monthly expirations for the past 52 weeks,
    collects ATM implied volatility at each expiration, and derives:
      - iv_rank       — where current IV sits relative to the 52w range (0-100)
      - iv_percentile — fraction of historical IVs below current IV (0-100)
      - iv_52w_high   — highest ATM IV observed across sampled expirations
      - iv_52w_low    — lowest ATM IV observed
      - current_iv    — the value passed in (echoed back for convenience)

    Results are cached in memory for 1 hour per ticker.
    """
    cache_key = ticker.upper()
    now = time.monotonic()

    # Return cached result if fresh
    if cache_key in _IVR_CACHE:
        ts, cached = _IVR_CACHE[cache_key]
        if now - ts < _CACHE_TTL:
            # Update current_iv in cached result to the caller's value
            result = dict(cached)
            result["current_iv"] = current_atm_iv
            iv_high = result["iv_52w_high"]
            iv_low = result["iv_52w_low"]
            spread = iv_high - iv_low
            if spread > 0:
                result["iv_rank"] = round(
                    ((current_atm_iv - iv_low) / spread) * 100, 2
                )
                hist = cached.get("_hist_ivs", [])
                if hist:
                    below = sum(1 for v in hist if v <= current_atm_iv)
                    result["iv_percentile"] = round((below / len(hist)) * 100, 2)
            return {k: v for k, v in result.items() if not k.startswith("_")}

    ticker_obj = yf.Ticker(cache_key)

    # Get current spot price
    try:
        info = ticker_obj.fast_info
        spot = float(info.last_price)
    except Exception:
        spot = current_atm_iv * 100  # rough fallback

    # Collect all expirations and filter to ~monthly over past 52 weeks
    try:
        expirations = ticker_obj.options  # tuple of "YYYY-MM-DD" strings
    except Exception:
        expirations = ()

    today = datetime.now(timezone.utc)
    cutoff_days = 365  # 52 weeks
    monthly_ivs: list[float] = []

    # Sample expirations roughly monthly (keep every ~4th week boundary)
    sampled = []
    last_month: Optional[str] = None
    for exp in expirations:
        try:
            exp_dt = datetime.strptime(exp, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        delta_days = (exp_dt - today).days
        # Only past/near-future within 52 weeks behind or up to ~52 ahead
        if delta_days > cutoff_days or delta_days < -cutoff_days:
            continue
        month_key = exp[:7]  # "YYYY-MM"
        if month_key != last_month:
            sampled.append(exp)
            last_month = month_key

    for exp in sampled:
        try:
            chain = ticker_obj.option_chain(exp)
            iv = _get_atm_iv(chain, spot)
            if iv is not None and iv > 0:
                monthly_ivs.append(iv)
        except Exception:
            continue

    # Always include the current IV the caller provided
    if current_atm_iv > 0:
        monthly_ivs.append(current_atm_iv)

    if not monthly_ivs:
        # Cannot compute meaningful stats — return safe defaults
        return {
            "iv_rank": 50.0,
            "iv_percentile": 50.0,
            "iv_52w_high": current_atm_iv,
            "iv_52w_low": current_atm_iv,
            "current_iv": current_atm_iv,
        }

    iv_high = max(monthly_ivs)
    iv_low = min(monthly_ivs)
    spread = iv_high - iv_low

    if spread > 0:
        iv_rank = round(((current_atm_iv - iv_low) / spread) * 100, 2)
        below = sum(1 for v in monthly_ivs if v <= current_atm_iv)
        iv_percentile = round((below / len(monthly_ivs)) * 100, 2)
    else:
        iv_rank = 50.0
        iv_percentile = 50.0

    iv_rank = max(0.0, min(100.0, iv_rank))
    iv_percentile = max(0.0, min(100.0, iv_percentile))

    result = {
        "iv_rank": iv_rank,
        "iv_percentile": iv_percentile,
        "iv_52w_high": round(iv_high, 4),
        "iv_52w_low": round(iv_low, 4),
        "current_iv": current_atm_iv,
        "_hist_ivs": monthly_ivs,
    }

    _IVR_CACHE[cache_key] = (now, result)

    return {k: v for k, v in result.items() if not k.startswith("_")}
