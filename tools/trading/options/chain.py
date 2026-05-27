# CUI // SP-CTI
"""Options chain utilities — IV rank and percentile computation."""

from tools.logging.icdev_logger import get_logger
import time
import math
from datetime import datetime, timezone
from typing import Optional

log = get_logger(__name__)

import yfinance as yf

# In-memory cache: key -> (timestamp, result)  — protected by a lock for thread safety
import threading as _threading
_IVR_CACHE: dict = {}
_IVR_LOCK = _threading.Lock()
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

    # Return cached result if fresh (lock for thread safety)
    with _IVR_LOCK:
        _cached_entry = _IVR_CACHE.get(cache_key)
    if _cached_entry is not None:
        ts, cached = _cached_entry
        if now - ts < _CACHE_TTL:
            # Update live IV fields without mutating the cached dict
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

    with _IVR_LOCK:
        _IVR_CACHE[cache_key] = (now, result)

    return {k: v for k, v in result.items() if not k.startswith("_")}


def _safe_int(v) -> int:
    """Convert v to int, returning 0 for None/NaN/non-numeric."""
    try:
        f = float(v)
        return 0 if math.isnan(f) else int(f)
    except (TypeError, ValueError):
        return 0


def format_occ_symbol(
    underlying: str,
    expiry: str,
    option_type: str,
    strike: float,
) -> str:
    """Return an OCC option symbol string.

    Format: {UNDERLYING}{YY}{MM}{DD}{C/P}{strike*1000 zero-padded to 8 digits}
    Example: AAPL231020C00150000 (AAPL, 2023-10-20, Call, $150.00 strike)
    """
    try:
        dt = datetime.strptime(expiry, "%Y-%m-%d")
    except (ValueError, TypeError):
        dt = datetime.now(timezone.utc)
    yy = dt.strftime("%y")
    mm = dt.strftime("%m")
    dd = dt.strftime("%d")
    cp = "C" if str(option_type).lower().startswith("c") else "P"
    strike_int = int(round(float(strike) * 1000))
    return f"{underlying.upper()}{yy}{mm}{dd}{cp}{strike_int:08d}"


def parse_occ_symbol(symbol: str) -> Optional[dict]:
    """Parse an OCC option symbol into its components.

    Format: {UNDERLYING}{YY}{MM}{DD}{C/P}{strike*1000 zero-padded to 8 digits}
    Returns dict with keys: underlying, expiry, option_type, strike — or None on parse failure.
    """
    import re
    if not symbol:
        return None
    m = re.match(r"^([A-Z]{1,6})(\d{2})(\d{2})(\d{2})([CP])(\d{8})$", symbol.upper().strip())
    if not m:
        return None
    underlying, yy, mm, dd, cp, strike_raw = m.groups()
    try:
        expiry = f"20{yy}-{mm}-{dd}"
        strike = int(strike_raw) / 1000.0
        return {
            "underlying": underlying,
            "root": underlying,        # alias used by sandbox_engine
            "expiry": expiry,
            "option_type": "call" if cp == "C" else "put",
            "strike": strike,
        }
    except Exception:
        return None


def fetch_contract_quote(contract_symbol: str) -> Optional[dict]:
    """Return a live bid/ask/mid quote for a single OCC contract symbol.

    Parses the symbol to get underlying/expiry/strike/type, fetches the chain
    for that underlying, and finds the matching contract row.
    Returns None if the symbol is invalid or no quote is available.
    """
    meta = parse_occ_symbol(contract_symbol)
    if not meta:
        return None
    try:
        ticker_obj = yf.Ticker(meta["underlying"])
        expiry = meta["expiry"]
        strike = meta["strike"]
        opt_type = meta["option_type"]
        try:
            spot = float(ticker_obj.fast_info.last_price)
        except Exception:
            spot = 0.0
        chain = ticker_obj.option_chain(expiry)
        df = chain.calls if opt_type == "call" else chain.puts
        row_df = df[abs(df["strike"] - strike) < 0.01]
        if row_df.empty:
            return None
        row = row_df.iloc[0]
        bid = _sf_static(row.get("bid"))
        ask = _sf_static(row.get("ask"))
        iv = _sf_static(row.get("impliedVolatility"))
        mid = round((bid + ask) / 2, 4) if (bid + ask) > 0 else None
        return {
            "symbol": contract_symbol,
            "underlying": meta["underlying"],
            "expiry": expiry,
            "option_type": opt_type,
            "strike": strike,
            "bid": bid,
            "ask": ask,
            "mid": mid,
            "last": _sf_static(row.get("lastPrice")),
            "iv": iv,
            "volume": _safe_int(row.get("volume")),
            "open_interest": _safe_int(row.get("openInterest")),
            "spot": spot,
        }
    except Exception:
        return None


def _sf_static(v, default: float = 0.0) -> float:
    """NaN-safe float for use outside fetch_chain's local scope."""
    try:
        f = float(v)
        return default if (math.isnan(f) or math.isinf(f)) else f
    except Exception:
        return default


def _compute_dte(expiry: str) -> float:
    """Time to expiry in years (fractional) for Black-Scholes input."""
    try:
        exp_dt = datetime.strptime(expiry, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        today = datetime.now(timezone.utc)
        seconds = (exp_dt - today).total_seconds()
        return max(seconds, 0.0) / (365.25 * 24 * 3600)
    except (ValueError, TypeError):
        return 0.0


def _safe(v: float, decimals: int) -> Optional[float]:
    """Round v, returning None for NaN/Inf so JSON stays valid."""
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return round(f, decimals)
    except Exception:
        return None


def _attach_greeks(contract: dict, spot: float, r: float = 0.05) -> dict:
    """Compute and attach BS Greeks to a contract dict in-place. Returns contract."""
    try:
        from tools.trading.options.pricing import bs_greeks
        iv = float(contract.get("iv") or 0)
        strike = float(contract.get("strike") or 0)
        expiry = contract.get("expiry", "")
        T = _compute_dte(expiry)
        opt_type = contract.get("option_type", "call")
        if iv > 0 and strike > 0 and T > 0 and spot > 0:
            g = bs_greeks(S=spot, K=strike, T=T, r=r, sigma=iv, option_type=opt_type)
            contract.update({
                "delta": _safe(g.get("delta", 0), 4),
                "gamma": _safe(g.get("gamma", 0), 4),
                "theta": _safe(g.get("theta", 0), 4),
                "vega":  _safe(g.get("vega",  0), 4),
                "rho":   _safe(g.get("rho",   0), 4),
                "vanna": _safe(g.get("vanna", 0), 6),
                "charm": _safe(g.get("charm", 0), 6),
                "volga": _safe(g.get("volga", 0), 6),
            })
    except Exception:
        pass
    return contract


def _is_rate_limit_error(exc: Exception) -> bool:
    msg = str(exc)
    return any(k in msg for k in ("Rate", "Too Many", "429", "HTTPError: 429", "rate limit"))


def fetch_chain(ticker: str, force_refresh: bool = False, _retry: int = 0) -> dict:
    """Fetch the live options chain for *ticker* via yfinance with BS Greeks.

    Returns a dict with keys: underlying, spot, expirations, contracts, calls, puts.
    Returns empty dict on failure. Retries up to 2× on rate-limit with exponential backoff.
    """
    import time as _time
    _MAX_RETRIES = 2
    _BACKOFF_BASE = 15  # seconds — 15s, 30s on successive rate limits

    try:
        ticker_obj = yf.Ticker(ticker.upper())
        spot: float = 0.0
        try:
            spot = float(ticker_obj.fast_info.last_price)
        except Exception:
            pass
        if not spot:
            try:
                hist = ticker_obj.history(period="1d")
                if not hist.empty:
                    spot = float(hist["Close"].iloc[-1])
            except Exception:
                pass
        try:
            expirations = list(ticker_obj.options or [])
        except Exception as _oe:
            if _is_rate_limit_error(_oe):
                if _retry < _MAX_RETRIES:
                    wait = _BACKOFF_BASE * (2 ** _retry)
                    log.warning("chain.fetch_chain: rate limited on %s — backoff %ds (attempt %d/%d)",
                                ticker, wait, _retry + 1, _MAX_RETRIES)
                    _time.sleep(wait)
                    return fetch_chain(ticker, force_refresh=force_refresh, _retry=_retry + 1)
                return {"error": "rate_limited", "message": "Yahoo Finance rate limit — wait 60s and retry"}
            return {"error": "no_options", "message": f"No options data for {ticker.upper()}: {_oe}"}
        all_calls: list[dict] = []
        all_puts: list[dict] = []
        def _sf(v, default: float = 0.0) -> float:
            """NaN-safe float — returns default if value is NaN/Inf/None."""
            try:
                f = float(v)
                return default if (math.isnan(f) or math.isinf(f)) else f
            except Exception:
                return default

        for exp in expirations[:4]:  # limit to nearest 4 expirations for speed
            try:
                yf_chain = ticker_obj.option_chain(exp)
                for _, row in yf_chain.calls.iterrows():
                    bid = _sf(row.get("bid"))
                    ask = _sf(row.get("ask"))
                    strike_c = _sf(row.get("strike"))
                    c = {
                        "symbol": format_occ_symbol(ticker.upper(), exp, "call", strike_c),
                        "strike": strike_c,
                        "expiry": exp,
                        "option_type": "call",
                        "bid": bid,
                        "ask": ask,
                        "mid": round((bid + ask) / 2, 2),
                        "iv": _sf(row.get("impliedVolatility")),
                        "volume": _safe_int(row.get("volume")),
                        "open_interest": _safe_int(row.get("openInterest")),
                    }
                    all_calls.append(_attach_greeks(c, spot))
                for _, row in yf_chain.puts.iterrows():
                    bid = _sf(row.get("bid"))
                    ask = _sf(row.get("ask"))
                    strike_p = _sf(row.get("strike"))
                    c = {
                        "symbol": format_occ_symbol(ticker.upper(), exp, "put", strike_p),
                        "strike": strike_p,
                        "expiry": exp,
                        "option_type": "put",
                        "bid": bid,
                        "ask": ask,
                        "mid": round((bid + ask) / 2, 2),
                        "iv": _sf(row.get("impliedVolatility")),
                        "volume": _safe_int(row.get("volume")),
                        "open_interest": _safe_int(row.get("openInterest")),
                    }
                    all_puts.append(_attach_greeks(c, spot))
            except Exception:
                continue
        contracts = all_calls + all_puts

        # Compute ATM IV for IVR badge
        atm_iv: float = 0.0
        if all_calls and spot > 0:
            atm_call = min(all_calls, key=lambda c: abs(c["strike"] - spot))
            atm_iv = atm_call.get("iv") or 0.0

        ivr_data: dict = {}
        if atm_iv > 0:
            try:
                ivr_data = compute_ivr(ticker.upper(), atm_iv)
            except Exception:
                pass

        return {
            "underlying": ticker.upper(),
            "underlying_last": spot,
            "spot": spot,
            "expirations": expirations,
            "calls": all_calls,
            "puts": all_puts,
            "contracts": contracts,  # flat list expected by strike_picker / pick_expiry
            "atm_iv": round(atm_iv * 100, 2),  # as percentage
            "iv_rank": ivr_data.get("iv_rank"),
            "iv_percentile": ivr_data.get("iv_percentile"),
            "iv_52w_high": ivr_data.get("iv_52w_high"),
            "iv_52w_low": ivr_data.get("iv_52w_low"),
        }
    except Exception as _fe:
        _msg = str(_fe)
        if "Rate" in _msg or "Too Many" in _msg or "429" in _msg:
            return {"error": "rate_limited", "message": "Yahoo Finance rate limit — wait 60s and retry"}
        return {"error": "fetch_failed", "message": _msg}


def snapshot_chain(ticker: str, conn=None) -> dict:
    """Take an option chain snapshot for *ticker*, compute IVR, and persist it.

    Fetches the nearest monthly expiration via yfinance, derives ATM IV,
    calls compute_ivr(), stores the result in ad_option_chain_snapshots.ivr_pct,
    and returns the persisted dict (including snap_id).
    """
    from tools.trading.db import save_chain_snapshot

    cache_key = ticker.upper()
    ticker_obj = yf.Ticker(cache_key)

    # Spot price
    spot: float = 0.0
    try:
        spot = float(ticker_obj.fast_info.last_price)
    except Exception:
        pass

    # Nearest expiration for ATM IV
    try:
        expirations = ticker_obj.options
    except Exception:
        expirations = ()

    atm_iv: float = 0.0
    expiration_count = len(expirations)
    for exp in expirations:
        try:
            chain = ticker_obj.option_chain(exp)
            iv = _get_atm_iv(chain, spot) if spot > 0 else None
            if iv and iv > 0:
                atm_iv = iv
                break
        except Exception:
            continue

    if atm_iv <= 0 and spot > 0:
        atm_iv = spot * 0.25  # 25% IV fallback when chain unavailable

    ivr = compute_ivr(cache_key, atm_iv)
    snap_id = save_chain_snapshot(
        ticker=cache_key,
        ivr=ivr,
        spot_price=spot if spot > 0 else None,
        expiration_count=expiration_count,
        conn=conn,
    )
    return {"snap_id": snap_id, "ticker": cache_key, **ivr}
