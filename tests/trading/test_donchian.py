# CUI // SP-CTI
"""Tests for Donchian channel breakout detector."""
from __future__ import annotations

import pytest

from tools.trading.ta.donchian import DonchianSignal, detect_donchian_breakout


def _flat_prices(n: int = 55, base: float = 100.0) -> list[float]:
    """n+1 prices all equal to base — no breakout."""
    return [base] * (n + 1)


def _uptrend_prices(n: int = 55, base: float = 100.0, spike: float = 10.0) -> list[float]:
    """Window of flat prices then a current price above the upper band."""
    return [base] * n + [base + spike]


def _downtrend_prices(n: int = 55, base: float = 100.0, drop: float = 10.0) -> list[float]:
    """Window of flat prices then a current price below the lower band."""
    return [base] * n + [base - drop]


class TestDonchianSignalFields:
    def test_flat_returns_none_breakout(self):
        sig = detect_donchian_breakout(_flat_prices())
        assert isinstance(sig, DonchianSignal)
        assert sig.breakout == "NONE"
        assert sig.breakout_strength == 0.0
        assert sig.upper_band == sig.lower_band == sig.mid_band == 100.0

    def test_flat_bands_are_equal(self):
        sig = detect_donchian_breakout(_flat_prices(base=200.0))
        assert sig.upper_band == sig.lower_band
        assert sig.mid_band == sig.upper_band


class TestDonchianBullBreakout:
    def test_uptrend_returns_bull(self):
        sig = detect_donchian_breakout(_uptrend_prices(spike=5.0))
        assert sig.breakout == "BULL"

    def test_bull_strength_positive(self):
        sig = detect_donchian_breakout(_uptrend_prices(base=100.0, spike=5.0))
        assert sig.breakout_strength > 0.0

    def test_bull_strength_clamped_to_one(self):
        # Price far above band — strength must not exceed 1.0
        prices = [100.0] * 55 + [1_000_000.0]
        sig = detect_donchian_breakout(prices)
        assert sig.breakout_strength <= 1.0

    def test_bull_upper_band_is_window_max(self):
        prices = list(range(1, 56)) + [200.0]  # window max = 55
        sig = detect_donchian_breakout(prices)
        assert sig.upper_band == 55.0
        assert sig.breakout == "BULL"


class TestDonchianBearBreakout:
    def test_downtrend_returns_bear(self):
        sig = detect_donchian_breakout(_downtrend_prices(drop=5.0))
        assert sig.breakout == "BEAR"

    def test_bear_strength_positive(self):
        sig = detect_donchian_breakout(_downtrend_prices(base=100.0, drop=5.0))
        assert sig.breakout_strength > 0.0

    def test_bear_strength_formula(self):
        # lower = 100, current = 90 → strength = (100-90)/100 = 0.1
        prices = [100.0] * 55 + [90.0]
        sig = detect_donchian_breakout(prices)
        assert sig.breakout == "BEAR"
        assert abs(sig.breakout_strength - 0.1) < 1e-6

    def test_bear_lower_band_is_window_min(self):
        prices = list(range(10, 65)) + [5.0]  # window min = 10
        sig = detect_donchian_breakout(prices)
        assert sig.lower_band == 10.0
        assert sig.breakout == "BEAR"


class TestDonchianMidBand:
    def test_mid_band_is_average_of_upper_lower(self):
        prices = list(range(1, 56)) + [30.0]  # window: 1–55, upper=55, lower=1
        sig = detect_donchian_breakout(prices)
        assert abs(sig.mid_band - (sig.upper_band + sig.lower_band) / 2) < 1e-9

    def test_mid_band_no_breakout(self):
        # Current price at mid_band → NONE
        prices = [90.0] * 27 + [110.0] * 28 + [100.0]  # upper=110, lower=90, mid=100
        sig = detect_donchian_breakout(prices)
        assert sig.breakout == "NONE"
        assert abs(sig.mid_band - 100.0) < 1e-9


class TestDonchianPeriods:
    def test_n_20_period(self):
        prices = [100.0] * 20 + [115.0]
        sig = detect_donchian_breakout(prices, n=20)
        assert sig.breakout == "BULL"

    def test_n_100_period(self):
        prices = [100.0] * 100 + [85.0]
        sig = detect_donchian_breakout(prices, n=100)
        assert sig.breakout == "BEAR"

    def test_insufficient_prices_raises(self):
        with pytest.raises(ValueError, match="at least"):
            detect_donchian_breakout([100.0] * 10, n=55)
