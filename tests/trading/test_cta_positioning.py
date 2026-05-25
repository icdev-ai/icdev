# CUI // SP-CTI
"""Tests for CTA Positioning module (FDMM Core-01/04)."""
from __future__ import annotations

import pytest

from tools.trading.market_intel.cta_positioning import (
    _direction,
    _momentum_score,
    _vol_regime,
    clear_universe_cache,
    compute_atr_series,
    compute_cta_positioning,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _uptrend_prices(n: int = 80, base: float = 100.0, step: float = 0.5):
    """Steady uptrend — CTA score > 0.6 at n=80, step=0.5 (39% / 30% = 1.3 → 1.0)."""
    closes = [base + i * step for i in range(n)]
    highs = [c + 1.0 for c in closes]
    lows = [c - 1.0 for c in closes]
    return closes, highs, lows


def _flat_prices(n: int = 80, base: float = 100.0):
    closes = [base] * n
    highs = [base + 1.0] * n
    lows = [base - 1.0] * n
    return closes, highs, lows


def _uptrend_with_atr_spike(n: int = 80):
    """Uptrend where the final bar has a large high-low range (ATR spike)."""
    closes, highs, lows = _uptrend_prices(n)
    # Spike final bar to force ATR spike
    highs[-1] = closes[-1] + 50.0
    lows[-1] = closes[-1] - 50.0
    return closes, highs, lows


def _downtrend_prices(n: int = 80, base: float = 130.0, step: float = 0.5):
    """Steady downtrend — CTA score < -0.1."""
    closes = [base - i * step for i in range(n)]
    highs = [c + 1.0 for c in closes]
    lows = [c - 1.0 for c in closes]
    return closes, highs, lows


# ---------------------------------------------------------------------------
# ATR helpers
# ---------------------------------------------------------------------------

class TestComputeAtrSeries:
    def test_returns_empty_for_insufficient_bars(self):
        closes = [100.0] * 10
        highs = [c + 1 for c in closes]
        lows = [c - 1 for c in closes]
        result = compute_atr_series(highs, lows, closes, period=14)
        assert result == []

    def test_length_correct(self):
        closes = [100.0] * 30
        highs = [c + 1 for c in closes]
        lows = [c - 1 for c in closes]
        result = compute_atr_series(highs, lows, closes, period=14)
        # len(trs) = 29; ATR series = 29 - 14 + 1 = 16
        assert len(result) == 16

    def test_flat_prices_atr_equals_range(self):
        closes = [100.0] * 30
        highs = [102.0] * 30
        lows = [98.0] * 30
        result = compute_atr_series(highs, lows, closes, period=14)
        # All TRs = 4.0 (high-low), ATR converges to 4.0
        assert abs(result[-1] - 4.0) < 1e-6

    def test_spike_increases_atr(self):
        closes = [100.0] * 40
        highs = [c + 2.0 for c in closes]
        lows = [c - 2.0 for c in closes]
        # Spike the last bar
        highs[-1] = 200.0
        lows[-1] = 50.0
        result = compute_atr_series(highs, lows, closes, period=14)
        # ATR must jump significantly after the spike
        assert result[-1] > 10.0


# ---------------------------------------------------------------------------
# Direction helper
# ---------------------------------------------------------------------------

class TestDirection:
    def test_long_above_band(self):
        assert _direction(0.5) == "long"

    def test_short_below_band(self):
        assert _direction(-0.5) == "short"

    def test_neutral_within_band(self):
        assert _direction(0.05) == "neutral"

    def test_neutral_at_zero(self):
        assert _direction(0.0) == "neutral"


# ---------------------------------------------------------------------------
# Momentum score
# ---------------------------------------------------------------------------

class TestMomentumScore:
    def test_uptrend_positive(self):
        closes = [100.0 + i * 0.5 for i in range(55)]
        score = _momentum_score(closes, lookback=50)
        assert score > 0.0

    def test_downtrend_negative(self):
        closes = [130.0 - i * 0.5 for i in range(55)]
        score = _momentum_score(closes, lookback=50)
        assert score < 0.0

    def test_flat_zero(self):
        closes = [100.0] * 55
        score = _momentum_score(closes, lookback=50)
        assert score == 0.0

    def test_clamped_upper(self):
        closes = [1.0] * 51
        closes[-1] = 1000.0
        assert _momentum_score(closes) == 1.0

    def test_clamped_lower(self):
        closes = [1000.0] * 51
        closes[-1] = 1.0
        assert _momentum_score(closes) == -1.0

    def test_insufficient_data_returns_zero(self):
        assert _momentum_score([100.0] * 10, lookback=50) == 0.0


# ---------------------------------------------------------------------------
# vol_regime helper
# ---------------------------------------------------------------------------

class TestVolRegime:
    def test_spike(self):
        assert _vol_regime(3.5, 2.0) == "spike"

    def test_high(self):
        assert _vol_regime(2.5, 2.0) == "high"

    def test_normal(self):
        assert _vol_regime(2.0, 2.0) == "normal"

    def test_low(self):
        assert _vol_regime(1.0, 2.0) == "low"

    def test_zero_avg_returns_normal(self):
        assert _vol_regime(5.0, 0.0) == "normal"


# ---------------------------------------------------------------------------
# Core tests: vol_deleveraging_alert
# ---------------------------------------------------------------------------

class TestVolDeleveragingAlert:
    def setup_method(self):
        clear_universe_cache()

    def test_alert_true_when_atr_spike_and_high_cta(self):
        """ATR spike (final bar ±50pt range) + strong uptrend → alert=True."""
        closes, highs, lows = _uptrend_with_atr_spike(n=80)
        result = compute_cta_positioning("TST", closes, highs, lows)
        assert result.cta_score > 0.6, f"expected cta_score>0.6, got {result.cta_score}"
        assert result.vol_regime == "spike", f"expected spike, got {result.vol_regime}"
        assert result.vol_deleveraging_alert is True

    def test_alert_false_when_no_atr_spike(self):
        """Normal ATR (no spike) — alert must be False even with high CTA score."""
        closes, highs, lows = _uptrend_prices(n=80)
        result = compute_cta_positioning("TST", closes, highs, lows)
        assert result.vol_deleveraging_alert is False

    def test_alert_false_when_cta_score_low(self):
        """ATR spike but CTA score ≤ threshold — alert must be False."""
        closes, highs, lows = _flat_prices(n=80)
        # Spike ATR on last bar
        highs[-1] = closes[-1] + 50.0
        lows[-1] = closes[-1] - 50.0
        result = compute_cta_positioning("TST", closes, highs, lows)
        # Flat prices → cta_score ≈ 0.0 (below threshold)
        assert result.cta_score <= 0.6
        assert result.vol_deleveraging_alert is False

    def test_alert_fields_present_in_dataclass(self):
        """CTAScore must expose both new fields."""
        closes, highs, lows = _uptrend_prices(n=60)
        result = compute_cta_positioning("TST", closes, highs, lows)
        assert hasattr(result, "vol_deleveraging_alert")
        assert hasattr(result, "crowding_ratio")
        assert isinstance(result.vol_deleveraging_alert, bool)


# ---------------------------------------------------------------------------
# Core tests: crowding_ratio
# ---------------------------------------------------------------------------

class TestCrowdingRatio:
    def setup_method(self):
        clear_universe_cache()

    def test_crowding_ratio_none_when_no_universe(self):
        """Without universe_closes, crowding_ratio must be None."""
        closes, highs, lows = _uptrend_prices()
        result = compute_cta_positioning("TST", closes, highs, lows)
        assert result.crowding_ratio is None

    def test_crowding_ratio_majority_same_direction(self):
        """3-of-4 universe tickers are long → crowding_ratio = 0.75."""
        closes, highs, lows = _uptrend_prices(n=80)

        up_cl = [100.0 + i * 0.5 for i in range(80)]
        dn_cl = [130.0 - i * 0.5 for i in range(80)]
        universe = {
            "A": up_cl,
            "B": up_cl,
            "C": up_cl,
            "D": dn_cl,
        }
        result = compute_cta_positioning("TST", closes, highs, lows, universe_closes=universe)
        assert result.crowding_ratio is not None
        assert abs(result.crowding_ratio - 0.75) < 1e-3

    def test_crowding_ratio_opposite_direction(self):
        """Main ticker is long; universe tickers are all short → crowding = 0.0."""
        closes, highs, lows = _uptrend_prices(n=80)

        dn_cl = [130.0 - i * 0.5 for i in range(80)]
        universe = {"X": dn_cl, "Y": dn_cl}
        result = compute_cta_positioning("TST", closes, highs, lows, universe_closes=universe)
        assert result.crowding_ratio == 0.0

    def test_crowding_ratio_bounds(self):
        """crowding_ratio must be in [0.0, 1.0]."""
        closes, highs, lows = _uptrend_prices(n=80)

        up_cl = [100.0 + i * 0.5 for i in range(80)]
        universe = {"A": up_cl, "B": up_cl, "C": up_cl}
        result = compute_cta_positioning("TST", closes, highs, lows, universe_closes=universe)
        assert result.crowding_ratio is not None
        assert 0.0 <= result.crowding_ratio <= 1.0

    def test_universe_cache_reused_across_calls(self):
        """Second call with universe=None uses previously cached scores."""
        closes, highs, lows = _uptrend_prices(n=80)
        up_cl = [100.0 + i * 0.5 for i in range(80)]
        universe = {"A": up_cl, "B": up_cl}

        # First call populates cache
        r1 = compute_cta_positioning("TST", closes, highs, lows, universe_closes=universe)
        # Second call — no universe supplied
        r2 = compute_cta_positioning("TST", closes, highs, lows)
        # Cache is populated so r1 has a ratio; r2 has None (passed None)
        assert r1.crowding_ratio is not None
        assert r2.crowding_ratio is None


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------

class TestInputValidation:
    def test_mismatched_lengths_raises(self):
        with pytest.raises(ValueError, match="equal length"):
            compute_cta_positioning("TST", [1.0, 2.0], [1.0], [1.0])

    def test_too_few_bars_raises(self):
        with pytest.raises(ValueError, match="at least 2"):
            compute_cta_positioning("TST", [100.0], [101.0], [99.0])
