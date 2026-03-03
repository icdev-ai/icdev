#!/usr/bin/env python3
"""Tests for SignalForge Risk Engine."""
# CUI // SP-CTI

import pytest

from tools.trading.risk_engine import (
    get_risk_limits,
    calculate_position_size,
    calculate_trade_risk,
    check_circuit_breaker,
    update_trailing_stop,
    check_eod_flatten,
    validate_trade,
)


class TestGetRiskLimits:
    """Test risk limit retrieval."""

    def test_returns_limits(self, sample_config):
        limits = get_risk_limits(sample_config)
        assert limits.take_profit_pct == 0.015
        assert limits.stop_loss_pct == 0.010
        assert limits.max_daily_loss_pct == 0.03
        assert limits.max_positions == 1

    def test_trailing_stop_fields(self, sample_config):
        limits = get_risk_limits(sample_config)
        assert limits.trailing_stop_enabled is True
        assert limits.trailing_stop_atr_mult == 2.0
        assert limits.trailing_activate_pct == 0.005

    def test_default_config(self):
        """Should work with empty config (defaults)."""
        limits = get_risk_limits({})
        assert limits.take_profit_pct == 0.015
        assert limits.max_positions == 1


class TestCalculatePositionSize:
    """Test ATR-based position sizing."""

    def test_basic_sizing(self, sample_config):
        size = calculate_position_size(100000, 5200.0, 12.5, sample_config)
        assert size.contracts >= 1
        assert size.method == "atr"
        assert size.risk_per_contract > 0
        assert size.total_risk > 0

    def test_larger_account_more_contracts(self, sample_config):
        size_small = calculate_position_size(50000, 5200.0, 12.5, sample_config)
        size_large = calculate_position_size(500000, 5200.0, 12.5, sample_config)
        assert size_large.contracts >= size_small.contracts

    def test_higher_atr_fewer_contracts(self, sample_config):
        size_low = calculate_position_size(100000, 5200.0, 5.0, sample_config)
        size_high = calculate_position_size(100000, 5200.0, 50.0, sample_config)
        assert size_low.contracts >= size_high.contracts

    def test_zero_atr_no_contracts(self, sample_config):
        size = calculate_position_size(100000, 5200.0, 0, sample_config)
        assert size.contracts == 0

    def test_fixed_method(self):
        config = {"risk": {"position_size_method": "fixed", "risk_per_trade_pct": 0.01}}
        size = calculate_position_size(100000, 5200.0, 12.5, config)
        assert size.contracts == 1

    def test_account_risk_pct(self, sample_config):
        size = calculate_position_size(100000, 5200.0, 12.5, sample_config)
        assert 0 <= size.account_risk_pct <= 1


class TestCalculateTradeRisk:
    """Test trade risk computation."""

    def test_long_trade(self, sample_config):
        risk = calculate_trade_risk("LONG", 5200.0, 1, sample_config)
        assert risk.direction == "LONG"
        assert risk.take_profit > risk.entry_price
        assert risk.stop_loss < risk.entry_price
        assert risk.max_gain > 0
        assert risk.max_loss > 0

    def test_short_trade(self, sample_config):
        risk = calculate_trade_risk("SHORT", 5200.0, 1, sample_config)
        assert risk.direction == "SHORT"
        assert risk.take_profit < risk.entry_price
        assert risk.stop_loss > risk.entry_price
        assert risk.max_gain > 0
        assert risk.max_loss > 0

    def test_risk_reward_ratio(self, sample_config):
        risk = calculate_trade_risk("LONG", 5200.0, 1, sample_config)
        # TP=1.5%, SL=1.0% -> R:R should be 1.5
        assert risk.risk_reward_ratio == pytest.approx(1.5, abs=0.01)

    def test_multi_contract(self, sample_config):
        risk1 = calculate_trade_risk("LONG", 5200.0, 1, sample_config)
        risk3 = calculate_trade_risk("LONG", 5200.0, 3, sample_config)
        assert risk3.max_gain == pytest.approx(risk1.max_gain * 3, abs=1)
        assert risk3.max_loss == pytest.approx(risk1.max_loss * 3, abs=1)

    def test_invalid_direction(self, sample_config):
        with pytest.raises(ValueError, match="Invalid direction"):
            calculate_trade_risk("FLAT", 5200.0, 1, sample_config)


class TestCircuitBreaker:
    """Test daily loss circuit breaker."""

    def test_not_triggered_profit(self, sample_config):
        result = check_circuit_breaker(500.0, 100000, sample_config)
        assert result["triggered"] is False

    def test_not_triggered_small_loss(self, sample_config):
        result = check_circuit_breaker(-1000.0, 100000, sample_config)
        assert result["triggered"] is False

    def test_triggered_large_loss(self, sample_config):
        # 3% of 100000 = 3000
        result = check_circuit_breaker(-3500.0, 100000, sample_config)
        assert result["triggered"] is True

    def test_threshold_boundary(self, sample_config):
        # Exactly at threshold
        result = check_circuit_breaker(-3000.0, 100000, sample_config)
        assert result["triggered"] is True

    def test_remaining_budget(self, sample_config):
        result = check_circuit_breaker(-1000.0, 100000, sample_config)
        assert result["remaining_budget"] == pytest.approx(2000.0, abs=1)


class TestTrailingStop:
    """Test trailing stop updates."""

    def test_no_activate_below_threshold(self, sample_config):
        # Trailing activates at 0.5% profit
        new_stop = update_trailing_stop(
            "LONG", 5200.0, 5201.0, 5180.0, 10.0, sample_config,
        )
        assert new_stop == 5180.0  # Unchanged

    def test_activate_above_threshold(self, sample_config):
        # 0.5% of 5200 = 26 -> price needs to be >= 5226
        new_stop = update_trailing_stop(
            "LONG", 5200.0, 5230.0, 5180.0, 10.0, sample_config,
        )
        # New stop = 5230 - (10 * 2.0) = 5210, which is > 5180
        assert new_stop > 5180.0

    def test_trailing_only_tightens(self, sample_config):
        # Stop should never loosen
        new_stop = update_trailing_stop(
            "LONG", 5200.0, 5240.0, 5220.0, 10.0, sample_config,
        )
        assert new_stop >= 5220.0

    def test_short_trailing(self, sample_config):
        # SHORT: profit when price goes down
        new_stop = update_trailing_stop(
            "SHORT", 5200.0, 5170.0, 5220.0, 10.0, sample_config,
        )
        # New stop = 5170 + (10 * 2.0) = 5190, which is < 5220
        assert new_stop <= 5220.0

    def test_disabled_trailing(self):
        config = {"risk": {"trailing_stop": {"enabled": False}}}
        new_stop = update_trailing_stop(
            "LONG", 5200.0, 5300.0, 5180.0, 10.0, config,
        )
        assert new_stop == 5180.0  # Unchanged when disabled


class TestEodFlatten:
    """Test end-of-day flatten logic."""

    def test_before_eod(self, sample_config):
        assert check_eod_flatten(14, 30, sample_config) is False

    def test_at_eod(self, sample_config):
        assert check_eod_flatten(15, 55, sample_config) is True

    def test_after_eod(self, sample_config):
        assert check_eod_flatten(15, 59, sample_config) is True

    def test_disabled(self):
        config = {"risk": {"end_of_day_flatten": False}}
        assert check_eod_flatten(15, 55, config) is False


class TestValidateTrade:
    """Test trade validation logic."""

    def test_valid_trade(self, sample_config):
        result = validate_trade("LONG", 0, 0, 100000, 12, 0, sample_config)
        assert result["allowed"] is True

    def test_max_positions(self, sample_config):
        result = validate_trade("LONG", 1, 0, 100000, 12, 0, sample_config)
        assert result["allowed"] is False
        assert "Max positions" in result["reason"]

    def test_circuit_breaker_blocks(self, sample_config):
        result = validate_trade("LONG", 0, -5000, 100000, 12, 0, sample_config)
        assert result["allowed"] is False
        assert "Circuit breaker" in result["reason"]

    def test_eod_blocks(self, sample_config):
        result = validate_trade("LONG", 0, 0, 100000, 15, 55, sample_config)
        assert result["allowed"] is False
        assert "EOD" in result["reason"]

    def test_invalid_direction(self, sample_config):
        result = validate_trade("UP", 0, 0, 100000, 12, 0, sample_config)
        assert result["allowed"] is False
        assert "Invalid direction" in result["reason"]
