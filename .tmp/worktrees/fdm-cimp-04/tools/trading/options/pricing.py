# CUI // SP-CTI
"""FathomDesk Phase 7.8 — Black-Scholes pricing + Greeks.

Pure-Python closed-form. Used by:
  - probability.compute_payoff_at_time (time-T interim pricing)
  - portfolio_greeks (fallback when chain-supplied greeks are stale)
  - future rolling calculator (compare "close now" vs "roll 30d out")

Conventions
-----------
  S = spot
  K = strike
  T = time to expiry in YEARS (caller converts DTE/365)
  r = annual risk-free rate (fraction, e.g. 0.04 for 4%)
  sigma = annual IV (fraction, e.g. 0.30 for 30%)
  q = continuous dividend yield (fraction, default 0)

Return units
------------
  bs_price         : per-share dollars
  bs_greeks        :
    delta    — per $1 move in S
    gamma    — rate of delta change per $1
    theta    — per-day (annual/365)
    vega     — per 1% IV change (/100)
    rho      — per 1% rate change (/100)
    vanna    — ∂Δ/∂σ: delta sensitivity to IV (per 1% IV change, /100)
    charm    — ∂Δ/∂t: delta decay per calendar day
    volga    — ∂vega/∂σ: vega sensitivity to IV (per 1% IV change, /100)

Edge cases never raise — we clamp T and sigma to small positives and
fall through to intrinsic for expired / zero-vol contracts.
"""
from __future__ import annotations

import math
from typing import Literal

_SQRT_TWO = math.sqrt(2.0)
_SQRT_2PI = math.sqrt(2.0 * math.pi)


def _n_cdf(x: float) -> float:
    """Standard normal CDF via erf."""
    return 0.5 * (1.0 + math.erf(x / _SQRT_TWO))


def _n_pdf(x: float) -> float:
    """Standard normal PDF."""
    return math.exp(-0.5 * x * x) / _SQRT_2PI


def _intrinsic(option_type: str, S: float, K: float) -> float:
    if option_type == "call":
        return max(0.0, S - K)
    return max(0.0, K - S)


def bs_price(
    option_type: str,
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    q: float = 0.0,
) -> float:
    """Black-Scholes-Merton price for a European option (with continuous
    dividend yield q). Returns per-share dollars. Never raises."""
    ot = (option_type or "").lower()
    if ot not in ("call", "put"):
        raise ValueError(f"option_type must be 'call' or 'put', got {option_type!r}")
    # Degenerate: at/after expiry, price is intrinsic.
    if T is None or T <= 0 or S is None or S <= 0 or K is None or K <= 0:
        return round(_intrinsic(ot, float(S or 0), float(K or 0)), 6)
    # Degenerate: zero vol → price is the discounted intrinsic at the
    # forward price, clamped at 0.
    if sigma is None or sigma <= 0:
        forward = S * math.exp((r - q) * T)
        return round(
            max(0.0, (forward - K) if ot == "call" else (K - forward))
            * math.exp(-r * T),
            6,
        )

    sqrtT = math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * sqrtT)
    d2 = d1 - sigma * sqrtT
    disc_r = math.exp(-r * T)
    disc_q = math.exp(-q * T)

    if ot == "call":
        price = S * disc_q * _n_cdf(d1) - K * disc_r * _n_cdf(d2)
    else:
        price = K * disc_r * _n_cdf(-d2) - S * disc_q * _n_cdf(-d1)
    return round(price, 6)


def bs_greeks(
    option_type: str,
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    q: float = 0.0,
) -> dict:
    """Delta, gamma, theta (per-day), vega (per-1%-IV), rho (per-1%-rate).
    Also returns vanna (∂Δ/∂σ), charm (∂Δ/∂t per day), volga (∂vega/∂σ).
    Returns zeros at degenerate inputs; never raises."""
    ot = (option_type or "").lower()
    if ot not in ("call", "put"):
        raise ValueError(f"option_type must be 'call' or 'put', got {option_type!r}")

    if T is None or T <= 0 or S is None or S <= 0 or K is None or K <= 0 \
            or sigma is None or sigma <= 0:
        # At/after expiry: delta is a step function; other greeks vanish.
        if T is not None and T <= 0 and S is not None and K is not None:
            if ot == "call":
                d = 1.0 if S > K else (0.5 if S == K else 0.0)
            else:
                d = -1.0 if S < K else (-0.5 if S == K else 0.0)
        else:
            d = 0.0
        return {"delta": round(d, 6), "gamma": 0.0, "theta": 0.0,
                "vega": 0.0, "rho": 0.0, "vanna": 0.0, "charm": 0.0, "volga": 0.0}

    sqrtT = math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * sqrtT)
    d2 = d1 - sigma * sqrtT
    pdf = _n_pdf(d1)
    disc_r = math.exp(-r * T)
    disc_q = math.exp(-q * T)

    if ot == "call":
        delta = disc_q * _n_cdf(d1)
        theta_annual = (
            -(S * disc_q * pdf * sigma) / (2.0 * sqrtT)
            - r * K * disc_r * _n_cdf(d2)
            + q * S * disc_q * _n_cdf(d1)
        )
        rho = K * T * disc_r * _n_cdf(d2) / 100.0
    else:
        delta = -disc_q * _n_cdf(-d1)
        theta_annual = (
            -(S * disc_q * pdf * sigma) / (2.0 * sqrtT)
            + r * K * disc_r * _n_cdf(-d2)
            - q * S * disc_q * _n_cdf(-d1)
        )
        rho = -K * T * disc_r * _n_cdf(-d2) / 100.0

    gamma = disc_q * pdf / (S * sigma * sqrtT)
    vega = S * disc_q * pdf * sqrtT / 100.0  # per 1% IV change
    theta = theta_annual / 365.0              # per calendar day

    # Second-order Greeks
    # Vanna: ∂Δ/∂σ — how delta changes as IV moves (scaled per 1% IV)
    vanna = -d2 * disc_q * pdf / sigma / 100.0
    # Charm: ∂Δ/∂t — delta decay per calendar day (annual rate / 365)
    charm = (-disc_q * pdf * (2.0 * (r - q) * T - d2 * sigma * sqrtT)
             / (2.0 * T * sigma * sqrtT)) / 365.0
    # Volga (vomma): ∂vega/∂σ — how vega changes as IV moves (scaled per 1% IV)
    volga = S * disc_q * pdf * sqrtT * d1 * d2 / sigma / 100.0

    return {
        "delta": round(delta, 6),
        "gamma": round(gamma, 8),
        "theta": round(theta, 6),
        "vega": round(vega, 6),
        "rho": round(rho, 6),
        "vanna": round(vanna, 8),
        "charm": round(charm, 8),
        "volga": round(volga, 8),
    }
