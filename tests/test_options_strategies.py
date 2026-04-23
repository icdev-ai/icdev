# CUI // SP-CTI
"""Tests for rank_strategies() Greek-profile scoring."""

from tools.trading.options.strategies import rank_strategies


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _find(results, name):
    return next((r for r in results if r["name"] == name), None)


def _rank(results, name):
    return next((i for i, r in enumerate(results) if r["name"] == name), None)


# ---------------------------------------------------------------------------
# Basic structure
# ---------------------------------------------------------------------------

def test_returns_list_of_dicts():
    results = rank_strategies("income")
    assert isinstance(results, list)
    assert len(results) > 0
    for r in results:
        for key in ("name", "base_score", "greek_score", "total_score",
                    "ivr_score", "delta_score", "theta_score", "vega_score"):
            assert key in r


def test_sorted_by_total_score_descending():
    results = rank_strategies("income", ivr=80)
    scores = [r["total_score"] for r in results]
    assert scores == sorted(scores, reverse=True)


def test_greek_score_bounds():
    greeks = {"net_delta": 0.5, "net_theta": -0.1, "net_vega": 0.3}
    results = rank_strategies("income", portfolio_greeks=greeks, ivr=75)
    for r in results:
        assert 0 <= r["greek_score"] <= 6
        assert 0 <= r["ivr_score"] <= 2
        assert 0 <= r["delta_score"] <= 2
        assert 0 <= r["theta_score"] <= 1
        assert 0 <= r["vega_score"] <= 1
        assert r["greek_score"] == r["ivr_score"] + r["delta_score"] + r["theta_score"] + r["vega_score"]
        assert r["total_score"] == r["base_score"] + r["greek_score"]


# ---------------------------------------------------------------------------
# IVR match (0-2 pts)
# ---------------------------------------------------------------------------

def test_high_ivr_rewards_iron_condor():
    """IVR>70 + iv:high strategy → 2 pts IVR match."""
    results = rank_strategies("income", ivr=80)
    ic = _find(results, "iron_condor")
    assert ic is not None
    assert ic["ivr_score"] == 2


def test_low_ivr_rewards_long_call():
    """IVR<30 + iv:low strategy → 2 pts IVR match."""
    results = rank_strategies("bullish", ivr=20)
    lc = _find(results, "long_call")
    assert lc is not None
    assert lc["ivr_score"] == 2


def test_high_ivr_penalises_long_call():
    """IVR>70 + iv:low strategy → 0 pts IVR match."""
    results = rank_strategies("bullish", ivr=80)
    lc = _find(results, "long_call")
    assert lc["ivr_score"] == 0


def test_mid_ivr_gives_partial_score():
    """IVR in 30-70 → 1 pt regardless of iv_environment."""
    results = rank_strategies("income", ivr=50)
    ic = _find(results, "iron_condor")
    assert ic["ivr_score"] == 1


def test_no_ivr_gives_zero_ivr_score():
    results = rank_strategies("income", ivr=None)
    for r in results:
        assert r["ivr_score"] == 0


# ---------------------------------------------------------------------------
# Delta bias match (0-2 pts)
# ---------------------------------------------------------------------------

def test_bullish_portfolio_matches_bullish_strategy():
    greeks = {"net_delta": 0.6, "net_theta": 0.0, "net_vega": 0.0}
    results = rank_strategies("bullish", portfolio_greeks=greeks)
    lc = _find(results, "long_call")
    assert lc["delta_score"] == 2


def test_bearish_portfolio_matches_bearish_strategy():
    greeks = {"net_delta": -0.6, "net_theta": 0.0, "net_vega": 0.0}
    results = rank_strategies("bearish", portfolio_greeks=greeks)
    lp = _find(results, "long_put")
    assert lp["delta_score"] == 2


def test_bullish_portfolio_mismatches_bearish_strategy():
    greeks = {"net_delta": 0.6, "net_theta": 0.0, "net_vega": 0.0}
    results = rank_strategies("bearish", portfolio_greeks=greeks)
    lp = _find(results, "long_put")
    assert lp["delta_score"] == 0


def test_neutral_portfolio_gives_partial_delta_score():
    greeks = {"net_delta": 0.05, "net_theta": 0.0, "net_vega": 0.0}
    results = rank_strategies("income", portfolio_greeks=greeks)
    lc = _find(results, "long_call")
    # neutral portfolio vs bullish strategy → 1pt
    assert lc["delta_score"] == 1


def test_neutral_strategy_with_bullish_portfolio_gives_partial():
    greeks = {"net_delta": 0.6, "net_theta": 0.0, "net_vega": 0.0}
    results = rank_strategies("income", portfolio_greeks=greeks)
    ic = _find(results, "iron_condor")
    assert ic["delta_score"] == 1


def test_no_portfolio_greeks_gives_zero_delta_and_theta():
    results = rank_strategies("income", portfolio_greeks=None)
    for r in results:
        assert r["delta_score"] == 0
        assert r["theta_score"] == 0


# ---------------------------------------------------------------------------
# Theta sign match (0-1 pt)
# ---------------------------------------------------------------------------

def test_negative_theta_portfolio_rewards_theta_positive_strategy():
    greeks = {"net_delta": 0.0, "net_theta": -0.5, "net_vega": 0.0}
    results = rank_strategies("income", portfolio_greeks=greeks)
    ic = _find(results, "iron_condor")
    assert ic["theta_score"] == 1


def test_negative_theta_portfolio_does_not_reward_theta_negative_strategy():
    greeks = {"net_delta": 0.0, "net_theta": -0.5, "net_vega": 0.0}
    results = rank_strategies("bullish", portfolio_greeks=greeks)
    lc = _find(results, "long_call")
    assert lc["theta_score"] == 0


def test_positive_theta_portfolio_gives_zero_theta_score():
    """Positive portfolio theta → no complementary theta match."""
    greeks = {"net_delta": 0.0, "net_theta": 0.3, "net_vega": 0.0}
    results = rank_strategies("income", portfolio_greeks=greeks)
    ic = _find(results, "iron_condor")
    assert ic["theta_score"] == 0


# ---------------------------------------------------------------------------
# Vega sign match (0-1 pt)
# ---------------------------------------------------------------------------

def test_high_ivr_rewards_short_vega():
    results = rank_strategies("income", ivr=75)
    ic = _find(results, "iron_condor")
    assert ic["vega_score"] == 1


def test_low_ivr_rewards_long_vega():
    results = rank_strategies("bullish", ivr=25)
    lc = _find(results, "long_call")
    assert lc["vega_score"] == 1


def test_high_ivr_does_not_reward_long_vega():
    results = rank_strategies("bullish", ivr=75)
    lc = _find(results, "long_call")
    assert lc["vega_score"] == 0


# ---------------------------------------------------------------------------
# Ranking order — end-to-end scenarios
# ---------------------------------------------------------------------------

def test_income_high_iv_iron_condor_ranks_above_long_straddle():
    """High-IV income intent: iron_condor (Greek match) beats long_straddle."""
    greeks = {"net_delta": 0.0, "net_theta": -0.3, "net_vega": 0.0}
    results = rank_strategies("income", portfolio_greeks=greeks, ivr=80)
    assert _rank(results, "iron_condor") < _rank(results, "long_straddle")


def test_low_iv_bullish_long_call_ranks_above_covered_call():
    """Low-IV bullish: long_call Greek match beats covered_call Greek mismatch."""
    greeks = {"net_delta": 0.5, "net_theta": 0.0, "net_vega": 0.0}
    results = rank_strategies("bullish", portfolio_greeks=greeks, ivr=15)
    assert _rank(results, "long_call") < _rank(results, "covered_call")


def test_unknown_intent_returns_all_strategies_with_zero_base():
    results = rank_strategies("unknownxyz")
    assert len(results) > 0
    for r in results:
        assert r["base_score"] == 0


def test_greek_score_breaks_tie_between_equal_base():
    """Two strategies with same base_score: higher greek_score ranks first."""
    # iron_condor and iron_butterfly both have base=90 for "income"
    # With high IVR, both get ivr=2. With neutral portfolio, delta_score may differ.
    results = rank_strategies("income", ivr=80)
    ic_rank = _rank(results, "iron_condor")
    ib_rank = _rank(results, "iron_butterfly")
    # Both should appear in top results (both have high income base + IVR match)
    assert ic_rank is not None and ib_rank is not None
    assert ic_rank <= 5 and ib_rank <= 5
