# CUI // SP-CTI
"""Phase 7.8 — tests for tools/trading/options/pricing.py (Black-Scholes)."""
from __future__ import annotations

import math

import pytest

from tools.trading.options.pricing import bs_price, bs_greeks


# ---------------------------------------------------------------------------
# Put-call parity: C - P = S·exp(-qT) - K·exp(-rT)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("S,K,T,r,sigma,q", [
    (100, 100, 0.5, 0.05, 0.25, 0.0),
    (100, 110, 0.25, 0.04, 0.30, 0.0),
    (50, 45, 1.0, 0.03, 0.40, 0.02),
    (200, 180, 0.1, 0.05, 0.60, 0.0),
])
def test_put_call_parity(S, K, T, r, sigma, q):
    c = bs_price("call", S, K, T, r, sigma, q)
    p = bs_price("put", S, K, T, r, sigma, q)
    expected = S * math.exp(-q * T) - K * math.exp(-r * T)
    assert abs((c - p) - expected) < 1e-4


# ---------------------------------------------------------------------------
# ATM symmetry when r=q=0: C == P
# ---------------------------------------------------------------------------
def test_atm_symmetry_zero_rates():
    c = bs_price("call", 100, 100, 0.5, 0.0, 0.25, 0.0)
    p = bs_price("put", 100, 100, 0.5, 0.0, 0.25, 0.0)
    assert abs(c - p) < 1e-6


# ---------------------------------------------------------------------------
# T→0 edge: price == intrinsic
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("ot,S,K,expected", [
    ("call", 110, 100, 10.0),
    ("call", 90, 100, 0.0),
    ("put", 90, 100, 10.0),
    ("put", 110, 100, 0.0),
])
def test_t_zero_returns_intrinsic(ot, S, K, expected):
    assert bs_price(ot, S, K, 0, 0.05, 0.25) == expected


# ---------------------------------------------------------------------------
# Monotone: call increases with S, put decreases with S
# ---------------------------------------------------------------------------
def test_call_grows_with_spot():
    low = bs_price("call", 95, 100, 0.5, 0.04, 0.30)
    high = bs_price("call", 105, 100, 0.5, 0.04, 0.30)
    assert high > low


def test_put_declines_with_spot():
    low_spot = bs_price("put", 95, 100, 0.5, 0.04, 0.30)
    high_spot = bs_price("put", 105, 100, 0.5, 0.04, 0.30)
    assert low_spot > high_spot


# ---------------------------------------------------------------------------
# Monotone in volatility: both call and put increase with sigma
# ---------------------------------------------------------------------------
def test_call_grows_with_vol():
    low = bs_price("call", 100, 100, 0.5, 0.04, 0.20)
    high = bs_price("call", 100, 100, 0.5, 0.04, 0.60)
    assert high > low


def test_put_grows_with_vol():
    low = bs_price("put", 100, 100, 0.5, 0.04, 0.20)
    high = bs_price("put", 100, 100, 0.5, 0.04, 0.60)
    assert high > low


# ---------------------------------------------------------------------------
# Known reference: S=100 K=100 T=0.5 r=0.05 sigma=0.25 q=0
# Expected ~8.27 (d1=0.2298, N(d1)=0.5909).
# ---------------------------------------------------------------------------
def test_known_atm_halfyear_25vol():
    c = bs_price("call", 100, 100, 0.5, 0.05, 0.25)
    assert abs(c - 8.27) < 0.02


# ---------------------------------------------------------------------------
# Greeks sanity
# ---------------------------------------------------------------------------
def test_call_delta_in_0_1():
    for S in (80, 100, 120):
        g = bs_greeks("call", S, 100, 0.5, 0.04, 0.25)
        assert 0 <= g["delta"] <= 1


def test_put_delta_in_neg1_0():
    for S in (80, 100, 120):
        g = bs_greeks("put", S, 100, 0.5, 0.04, 0.25)
        assert -1 <= g["delta"] <= 0


def test_gamma_nonneg_for_both_sides():
    g_call = bs_greeks("call", 100, 100, 0.5, 0.04, 0.25)
    g_put = bs_greeks("put", 100, 100, 0.5, 0.04, 0.25)
    assert g_call["gamma"] >= 0
    assert g_put["gamma"] >= 0


def test_vega_nonneg_for_both_sides():
    g_call = bs_greeks("call", 100, 100, 0.5, 0.04, 0.25)
    g_put = bs_greeks("put", 100, 100, 0.5, 0.04, 0.25)
    assert g_call["vega"] >= 0
    assert g_put["vega"] >= 0


def test_long_option_theta_negative():
    """Long options decay over time — theta < 0 for ATM long positions."""
    assert bs_greeks("call", 100, 100, 0.5, 0.04, 0.25)["theta"] < 0
    assert bs_greeks("put", 100, 100, 0.5, 0.04, 0.25)["theta"] < 0


# ---------------------------------------------------------------------------
# ATM call delta ≈ 0.5 + small rate+vol drift ⇒ bounded around that
# ---------------------------------------------------------------------------
def test_atm_call_delta_around_half():
    d = bs_greeks("call", 100, 100, 0.5, 0.04, 0.25)["delta"]
    assert 0.5 < d < 0.65


# ---------------------------------------------------------------------------
# Edge cases — zero / invalid inputs never raise
# ---------------------------------------------------------------------------
def test_zero_t_no_raise():
    bs_price("call", 100, 100, 0, 0.04, 0.25)
    bs_greeks("call", 100, 100, 0, 0.04, 0.25)


def test_zero_sigma_returns_discounted_intrinsic():
    # Forward-based: max(0, F-K) discounted. With S=100 K=90 r=0.05 T=1,
    # forward = 100*exp(0.05) ≈ 105.13, intrinsic at forward = 15.13,
    # discounted = 15.13 * exp(-0.05) = 14.39
    p = bs_price("call", 100, 90, 1.0, 0.05, 0)
    assert 14.3 < p < 14.5


def test_negative_spot_does_not_raise():
    # API should be graceful; return intrinsic (which is 0 for bogus inputs).
    result = bs_price("call", -1, 100, 0.5, 0.04, 0.25)
    assert result == 0.0


def test_invalid_option_type_raises():
    with pytest.raises(ValueError):
        bs_price("banana", 100, 100, 0.5, 0.04, 0.25)
    with pytest.raises(ValueError):
        bs_greeks("banana", 100, 100, 0.5, 0.04, 0.25)
