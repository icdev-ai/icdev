#!/usr/bin/env python3
"""Tests for SignalForge Trade Journal."""
# CUI // SP-CTI

import pytest

from tools.trading.trade_journal import (
    record_signal,
    record_exit,
    get_summary,
    get_trade_history,
    get_equity_curve,
    verify_hash_chain,
    _compute_hash,
)


class TestHashChain:
    """Test SHA-256 hash chain mechanics."""

    def test_compute_hash_deterministic(self):
        data = {"trade_id": "test-1", "entry_price": 5200.0}
        h1 = _compute_hash(data, "")
        h2 = _compute_hash(data, "")
        assert h1 == h2

    def test_different_data_different_hash(self):
        data1 = {"trade_id": "test-1", "entry_price": 5200.0}
        data2 = {"trade_id": "test-2", "entry_price": 5200.0}
        assert _compute_hash(data1, "") != _compute_hash(data2, "")

    def test_prev_hash_affects_result(self):
        data = {"trade_id": "test-1", "entry_price": 5200.0}
        h1 = _compute_hash(data, "")
        h2 = _compute_hash(data, "abc123")
        assert h1 != h2


class TestRecordSignal:
    """Test trade signal recording."""

    def test_record_basic(self, tmp_db):
        result = record_signal(
            direction="LONG",
            entry_price=5200.0,
            take_profit=5278.0,
            stop_loss=5148.0,
            db_path=tmp_db,
        )
        assert result["status"] == "recorded"
        assert result["trade_id"].startswith("sf-")
        assert len(result["hash"]) == 64  # SHA-256 hex

    def test_record_with_metadata(self, tmp_db):
        result = record_signal(
            direction="SHORT",
            entry_price=5200.0,
            take_profit=5122.0,
            stop_loss=5252.0,
            position_size=2,
            confidence=0.85,
            model_version="v1.0",
            signal_features={"rsi_14": 0.72, "macd_hist": -0.003},
            db_path=tmp_db,
        )
        assert result["status"] == "recorded"

    def test_hash_chain_grows(self, tmp_db):
        r1 = record_signal("LONG", 5200.0, 5278.0, 5148.0, db_path=tmp_db)
        r2 = record_signal("SHORT", 5300.0, 5222.0, 5352.0, db_path=tmp_db)
        assert r1["hash"] != r2["hash"]


class TestRecordExit:
    """Test trade exit recording."""

    def test_close_trade(self, tmp_db):
        entry = record_signal("LONG", 5200.0, 5278.0, 5148.0, db_path=tmp_db)
        exit_result = record_exit(
            entry["trade_id"], 5250.0, "take_profit", bars_held=10, db_path=tmp_db,
        )
        assert exit_result["status"] == "closed"
        assert exit_result["pnl_points"] == 50.0  # 5250 - 5200
        assert exit_result["pnl_dollars"] == 2500.0  # 50 * 50

    def test_short_exit(self, tmp_db):
        entry = record_signal("SHORT", 5200.0, 5122.0, 5252.0, db_path=tmp_db)
        exit_result = record_exit(
            entry["trade_id"], 5150.0, "take_profit", bars_held=8, db_path=tmp_db,
        )
        assert exit_result["pnl_points"] == 50.0  # 5200 - 5150

    def test_losing_trade(self, tmp_db):
        entry = record_signal("LONG", 5200.0, 5278.0, 5148.0, db_path=tmp_db)
        exit_result = record_exit(
            entry["trade_id"], 5150.0, "stop_loss", bars_held=5, db_path=tmp_db,
        )
        assert exit_result["pnl_points"] == -50.0
        assert exit_result["pnl_dollars"] < 0

    def test_exit_nonexistent_trade(self, tmp_db):
        result = record_exit("sf-doesnotexist", 5200.0, "test", db_path=tmp_db)
        assert result["status"] == "error"

    def test_exit_already_closed(self, tmp_db):
        entry = record_signal("LONG", 5200.0, 5278.0, 5148.0, db_path=tmp_db)
        record_exit(entry["trade_id"], 5250.0, "take_profit", db_path=tmp_db)
        # Try to close again
        result = record_exit(entry["trade_id"], 5260.0, "test", db_path=tmp_db)
        assert result["status"] == "error"


class TestGetSummary:
    """Test performance summary generation."""

    def test_empty_journal(self, tmp_db):
        summary = get_summary(tmp_db)
        assert summary["total_trades"] == 0
        assert summary["win_rate"] == 0

    def test_summary_with_trades(self, tmp_db):
        # Create and close 3 trades
        for price_offset in [50, -30, 20]:
            entry = record_signal("LONG", 5200.0, 5278.0, 5148.0, db_path=tmp_db)
            record_exit(entry["trade_id"], 5200.0 + price_offset, "test", db_path=tmp_db)

        summary = get_summary(tmp_db)
        assert summary["total_trades"] == 3
        assert 0 <= summary["win_rate"] <= 1
        assert summary["total_pnl"] != 0

    def test_win_rate_calculation(self, tmp_db):
        # 2 wins, 1 loss
        for exit_price in [5250.0, 5260.0, 5150.0]:
            entry = record_signal("LONG", 5200.0, 5278.0, 5148.0, db_path=tmp_db)
            record_exit(entry["trade_id"], exit_price, "test", db_path=tmp_db)

        summary = get_summary(tmp_db)
        assert summary["win_rate"] == pytest.approx(2/3, abs=0.01)


class TestTradeHistory:
    """Test trade history retrieval."""

    def test_empty_history(self, tmp_db):
        history = get_trade_history(db_path=tmp_db)
        assert history == []

    def test_history_with_trades(self, tmp_db):
        record_signal("LONG", 5200.0, 5278.0, 5148.0, db_path=tmp_db)
        record_signal("SHORT", 5300.0, 5222.0, 5352.0, db_path=tmp_db)

        history = get_trade_history(db_path=tmp_db)
        assert len(history) == 2

    def test_history_limit(self, tmp_db):
        for _ in range(5):
            record_signal("LONG", 5200.0, 5278.0, 5148.0, db_path=tmp_db)

        history = get_trade_history(limit=3, db_path=tmp_db)
        assert len(history) == 3


class TestEquityCurve:
    """Test equity curve generation."""

    def test_empty_curve(self, tmp_db):
        curve = get_equity_curve(tmp_db)
        assert curve == []

    def test_curve_cumulative(self, tmp_db):
        for exit_price in [5250.0, 5260.0, 5230.0]:
            entry = record_signal("LONG", 5200.0, 5278.0, 5148.0, db_path=tmp_db)
            record_exit(entry["trade_id"], exit_price, "test", db_path=tmp_db)

        curve = get_equity_curve(tmp_db)
        assert len(curve) == 3
        # Cumulative should be sum of individual PnLs
        total = sum(c["pnl"] for c in curve)
        assert curve[-1]["cumulative"] == pytest.approx(total, abs=1)


class TestVerifyHashChain:
    """Test hash chain verification."""

    def test_empty_chain(self, tmp_db):
        result = verify_hash_chain(tmp_db)
        assert result["valid"] is True
        assert result["records"] == 0

    def test_valid_chain(self, tmp_db):
        record_signal("LONG", 5200.0, 5278.0, 5148.0, db_path=tmp_db)
        record_signal("SHORT", 5300.0, 5222.0, 5352.0, db_path=tmp_db)
        record_signal("LONG", 5250.0, 5328.0, 5198.0, db_path=tmp_db)

        result = verify_hash_chain(tmp_db)
        assert result["valid"] is True
        assert result["records"] == 3
        assert result["status"] == "valid"
