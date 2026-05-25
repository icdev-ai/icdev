# CUI // SP-CTI
"""Phase 7.7 — tests for tools/trading/options/probability.py."""
from __future__ import annotations


from tools.trading.options.probability import compute_pop


def _long_call(strike: float, premium: float = 3.0) -> list[dict]:
    return [{
        "option_type": "call", "strike": strike,
        "action": "buy_to_open", "premium": premium, "qty": 1,
    }]


def _long_put(strike: float, premium: float = 3.0) -> list[dict]:
    return [{
        "option_type": "put", "strike": strike,
        "action": "buy_to_open", "premium": premium, "qty": 1,
    }]


# ---------------------------------------------------------------------------
# Basic shape + ranges
# ---------------------------------------------------------------------------
def test_pop_is_in_0_100():
    r = compute_pop(_long_call(150), spot=150.0, iv_annual_pct=30,
                    dte_days=30, underlying="AAPL", expiry="2026-05-19")
    assert r is not None
    assert 0 <= r["pop_pct"] <= 100
    assert r["n_samples"] >= 100
    assert r["model"] == "lognormal-mc"


def test_percentile_cone_ordered():
    r = compute_pop(_long_call(150), spot=150.0, iv_annual_pct=30,
                    dte_days=30, underlying="AAPL", expiry="2026-05-19")
    pp = r["percentile_prices"]
    assert pp["p5"] < pp["p25"] < pp["p50"] < pp["p75"] < pp["p95"]


def test_pnl_distribution_sensible():
    r = compute_pop(_long_call(150), spot=150.0, iv_annual_pct=30,
                    dte_days=30, underlying="AAPL", expiry="2026-05-19")
    d = r["pnl_distribution"]
    assert d["std"] > 0
    assert d["p5"] < d["p95"]


# ---------------------------------------------------------------------------
# Monotone invariants
# ---------------------------------------------------------------------------
def test_otm_call_has_lower_pop_than_atm():
    atm = compute_pop(_long_call(150), spot=150.0, iv_annual_pct=30,
                      dte_days=30, underlying="AAPL", expiry="2026-05-19")
    otm = compute_pop(_long_call(170), spot=150.0, iv_annual_pct=30,
                      dte_days=30, underlying="AAPL", expiry="2026-05-19")
    assert otm["pop_pct"] < atm["pop_pct"]


def test_higher_iv_widens_cone():
    lo = compute_pop(_long_call(150), spot=150.0, iv_annual_pct=20,
                     dte_days=30, underlying="AAPL", expiry="2026-05-19")
    hi = compute_pop(_long_call(150), spot=150.0, iv_annual_pct=60,
                     dte_days=30, underlying="AAPL", expiry="2026-05-19")
    lo_width = lo["percentile_prices"]["p95"] - lo["percentile_prices"]["p5"]
    hi_width = hi["percentile_prices"]["p95"] - hi["percentile_prices"]["p5"]
    assert hi_width > lo_width


def test_longer_dte_widens_cone():
    near = compute_pop(_long_call(150), spot=150.0, iv_annual_pct=30,
                       dte_days=7, underlying="AAPL", expiry="2026-04-26")
    far = compute_pop(_long_call(150), spot=150.0, iv_annual_pct=30,
                      dte_days=90, underlying="AAPL", expiry="2026-07-19")
    near_w = near["percentile_prices"]["p95"] - near["percentile_prices"]["p5"]
    far_w = far["percentile_prices"]["p95"] - far["percentile_prices"]["p5"]
    assert far_w > near_w


# ---------------------------------------------------------------------------
# Deterministic seeding
# ---------------------------------------------------------------------------
def test_deterministic_same_inputs_same_output():
    a = compute_pop(_long_call(150), spot=150.0, iv_annual_pct=30,
                    dte_days=30, underlying="AAPL", expiry="2026-05-19")
    b = compute_pop(_long_call(150), spot=150.0, iv_annual_pct=30,
                    dte_days=30, underlying="AAPL", expiry="2026-05-19")
    assert a["pop_pct"] == b["pop_pct"]
    assert a["expected_pnl"] == b["expected_pnl"]


def test_different_expiry_changes_seed():
    a = compute_pop(_long_call(150), spot=150.0, iv_annual_pct=30,
                    dte_days=30, underlying="AAPL", expiry="2026-05-19")
    b = compute_pop(_long_call(150), spot=150.0, iv_annual_pct=30,
                    dte_days=30, underlying="AAPL", expiry="2026-06-19")
    # Different seed → different specific POP (extremely unlikely to collide).
    assert a["pop_pct"] != b["pop_pct"]


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------
def test_intraday_returns_none():
    r = compute_pop(_long_call(150), spot=150.0, iv_annual_pct=30,
                    dte_days=0)
    assert r is None


def test_missing_spot_returns_none():
    r = compute_pop(_long_call(150), spot=0, iv_annual_pct=30, dte_days=30)
    assert r is None


def test_empty_legs_returns_none():
    r = compute_pop([], spot=150, iv_annual_pct=30, dte_days=30)
    assert r is None


def test_none_iv_uses_fallback():
    r = compute_pop(_long_call(150), spot=150.0, iv_annual_pct=None,
                    dte_days=30, underlying="AAPL", expiry="2026-05-19")
    assert r is not None
    assert r["iv_used_pct"] > 0  # fallback kicked in


# ---------------------------------------------------------------------------
# Short-premium POP > long-premium POP on the same underlying + expiry
# (a short OTM put typically has POP ≥ 70%).
# ---------------------------------------------------------------------------
def test_short_otm_put_has_higher_pop_than_long_otm_put():
    short_leg = [{"option_type": "put", "strike": 140, "action": "sell_to_open",
                  "premium": 1.5, "qty": 1}]
    long_leg = _long_put(140, premium=1.5)
    s = compute_pop(short_leg, spot=150.0, iv_annual_pct=30, dte_days=30,
                    underlying="AAPL", expiry="2026-05-19")
    l = compute_pop(long_leg, spot=150.0, iv_annual_pct=30, dte_days=30,
                    underlying="AAPL", expiry="2026-05-19")
    assert s["pop_pct"] > l["pop_pct"]
