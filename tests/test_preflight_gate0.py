# CUI // SP-CTI
"""Tests for preflight.py Gate 0 — per-strategy risk floor.

fd-floor-03: verifies that Gate 0 correctly blocks / warns based on
backtest Sharpe and drawdown against fathomdesk_strategy_floors.yaml.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from tools.trading.options.preflight import (
    GateResultCode,
    _get_floor,
    _latest_backtest,
    run_preflight,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MINIMAL_PROPOSAL = {
    "status": "ok",
    "strategy_id": "iron_condor",
    "expiry": None,          # None → DTE gate skipped; avoids confounding Gate 3
    "payoff": {"max_loss": -100.0, "max_profit": 50.0},
    "warnings": [],
}


def _proposal(strategy_id: str = "iron_condor", **overrides) -> dict:
    p = dict(_MINIMAL_PROPOSAL)
    p["strategy_id"] = strategy_id
    p.update(overrides)
    return p


def _backtest_md(sharpe: float | None, drawdown: float | None) -> str:
    """Build a minimal backtest markdown artifact with given metrics."""
    s = str(sharpe) if sharpe is not None else "None"
    d = str(drawdown) if drawdown is not None else "None"
    return (
        "# BacktestResult — Institutional Memory\n\n"
        "| Field | Value | Note |\n"
        "|-------|-------|------|\n"
        "| ticker | AAPL | |\n"
        "| strategy_id | iron_condor | |\n"
        f"| sharpe_ratio | {s} | |\n"
        f"| max_drawdown_pct | {d} | |\n"
    )


# ---------------------------------------------------------------------------
# _get_floor — YAML loader
# ---------------------------------------------------------------------------

class TestGetFloor:
    def test_defaults_returned_for_unknown_strategy(self):
        floor = _get_floor("does_not_exist")
        assert floor["sharpe_min"] == pytest.approx(1.5)
        assert floor["max_drawdown_pct"] == pytest.approx(15.0)

    def test_override_merged_on_defaults(self):
        # naked_put has sharpe_min=2.0, max_drawdown_pct=10.0 in the YAML
        floor = _get_floor("naked_put")
        assert floor["sharpe_min"] == pytest.approx(2.0)
        assert floor["max_drawdown_pct"] == pytest.approx(10.0)

    def test_empty_override_inherits_defaults(self):
        floor = _get_floor("long_call")
        assert floor["sharpe_min"] == pytest.approx(1.5)
        assert floor["max_drawdown_pct"] == pytest.approx(15.0)


# ---------------------------------------------------------------------------
# _latest_backtest — file scanner
# ---------------------------------------------------------------------------

class TestLatestBacktest:
    def test_returns_none_when_no_institutional_dir(self, tmp_path):
        with patch("tools.trading.options.preflight._BASE_DIR", tmp_path):
            result = _latest_backtest("iron_condor")
        assert result is None

    def test_returns_none_when_no_matching_file(self, tmp_path):
        inst = tmp_path / "memory" / "institutional"
        inst.mkdir(parents=True)
        with patch("tools.trading.options.preflight._BASE_DIR", tmp_path):
            result = _latest_backtest("iron_condor")
        assert result is None

    def test_parses_real_metrics(self, tmp_path):
        inst = tmp_path / "memory" / "institutional"
        inst.mkdir(parents=True)
        (inst / "backtest-iron_condor-2025-01-01.md").write_text(
            _backtest_md(1.8, 9.5), encoding="utf-8"
        )
        with patch("tools.trading.options.preflight._BASE_DIR", tmp_path):
            result = _latest_backtest("iron_condor")
        assert result is not None
        assert result["sharpe_ratio"] == pytest.approx(1.8)
        assert result["max_drawdown_pct"] == pytest.approx(9.5)

    def test_parses_none_metrics(self, tmp_path):
        inst = tmp_path / "memory" / "institutional"
        inst.mkdir(parents=True)
        (inst / "backtest-iron_condor-2025-06-01.md").write_text(
            _backtest_md(None, None), encoding="utf-8"
        )
        with patch("tools.trading.options.preflight._BASE_DIR", tmp_path):
            result = _latest_backtest("iron_condor")
        assert result is not None
        assert result["sharpe_ratio"] is None
        assert result["max_drawdown_pct"] is None

    def test_picks_most_recent_file(self, tmp_path):
        inst = tmp_path / "memory" / "institutional"
        inst.mkdir(parents=True)
        # Older file: sharpe 1.0 (below floor)
        (inst / "backtest-iron_condor-2024-01-01.md").write_text(
            _backtest_md(1.0, 8.0), encoding="utf-8"
        )
        # Newer file: sharpe 2.0 (above floor)
        (inst / "backtest-iron_condor-2025-01-01.md").write_text(
            _backtest_md(2.0, 5.0), encoding="utf-8"
        )
        with patch("tools.trading.options.preflight._BASE_DIR", tmp_path):
            result = _latest_backtest("iron_condor")
        assert result["sharpe_ratio"] == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# run_preflight Gate 0 integration
# ---------------------------------------------------------------------------

class TestGate0NoBactestRecord:
    """Gate 0: no artifact file exists."""

    def test_warns_on_paper_when_no_backtest(self, tmp_path):
        with patch("tools.trading.options.preflight._BASE_DIR", tmp_path):
            result = run_preflight(_proposal(), is_live=False)
        codes = {w["code"] for w in result["warnings"]}
        assert GateResultCode.NO_BACKTEST_ON_RECORD in codes
        assert result["allowed"] is True  # paper: warn only

    def test_blocks_on_live_when_no_backtest(self, tmp_path):
        with patch("tools.trading.options.preflight._BASE_DIR", tmp_path):
            result = run_preflight(_proposal(), is_live=True, equity=100_000)
        codes = {b["code"] for b in result["blocks"]}
        assert GateResultCode.NO_BACKTEST_ON_RECORD in codes
        assert result["allowed"] is False


class TestGate0NoneMetrics:
    """Gate 0: artifact exists but sharpe/drawdown not populated (scenario sim)."""

    def test_warns_paper_on_none_metrics(self, tmp_path):
        inst = tmp_path / "memory" / "institutional"
        inst.mkdir(parents=True)
        (inst / "backtest-iron_condor-2025-01-01.md").write_text(
            _backtest_md(None, None), encoding="utf-8"
        )
        with patch("tools.trading.options.preflight._BASE_DIR", tmp_path):
            result = run_preflight(_proposal(), is_live=False)
        codes = {w["code"] for w in result["warnings"]}
        assert GateResultCode.NO_BACKTEST_ON_RECORD in codes

    def test_blocks_live_on_none_metrics(self, tmp_path):
        inst = tmp_path / "memory" / "institutional"
        inst.mkdir(parents=True)
        (inst / "backtest-iron_condor-2025-01-01.md").write_text(
            _backtest_md(None, None), encoding="utf-8"
        )
        with patch("tools.trading.options.preflight._BASE_DIR", tmp_path):
            result = run_preflight(_proposal(), is_live=True, equity=100_000)
        codes = {b["code"] for b in result["blocks"]}
        assert GateResultCode.NO_BACKTEST_ON_RECORD in codes


class TestGate0SharpeFloor:
    """Gate 0: Sharpe ratio below floor."""

    def test_below_sharpe_warns_on_paper(self, tmp_path):
        inst = tmp_path / "memory" / "institutional"
        inst.mkdir(parents=True)
        (inst / "backtest-iron_condor-2025-01-01.md").write_text(
            _backtest_md(sharpe=0.9, drawdown=8.0), encoding="utf-8"  # 0.9 < 1.5 floor
        )
        with patch("tools.trading.options.preflight._BASE_DIR", tmp_path):
            result = run_preflight(_proposal(), is_live=False)
        codes = {w["code"] for w in result["warnings"]}
        assert GateResultCode.BELOW_SHARPE_FLOOR in codes
        assert result["allowed"] is True

    def test_below_sharpe_blocks_on_live(self, tmp_path):
        inst = tmp_path / "memory" / "institutional"
        inst.mkdir(parents=True)
        (inst / "backtest-iron_condor-2025-01-01.md").write_text(
            _backtest_md(sharpe=0.9, drawdown=8.0), encoding="utf-8"
        )
        with patch("tools.trading.options.preflight._BASE_DIR", tmp_path):
            result = run_preflight(_proposal(), is_live=True, equity=100_000)
        codes = {b["code"] for b in result["blocks"]}
        assert GateResultCode.BELOW_SHARPE_FLOOR in codes
        assert result["allowed"] is False

    def test_above_sharpe_floor_passes(self, tmp_path):
        inst = tmp_path / "memory" / "institutional"
        inst.mkdir(parents=True)
        (inst / "backtest-iron_condor-2025-01-01.md").write_text(
            _backtest_md(sharpe=2.1, drawdown=8.0), encoding="utf-8"  # 2.1 > 1.5 floor
        )
        with patch("tools.trading.options.preflight._BASE_DIR", tmp_path):
            result = run_preflight(_proposal(), is_live=True, equity=100_000)
        g0_blocks = [b for b in result["blocks"] if "sharpe" in b["code"]]
        assert g0_blocks == []


class TestGate0DrawdownFloor:
    """Gate 0: max drawdown exceeds floor."""

    def test_excess_drawdown_warns_on_paper(self, tmp_path):
        inst = tmp_path / "memory" / "institutional"
        inst.mkdir(parents=True)
        (inst / "backtest-iron_condor-2025-01-01.md").write_text(
            _backtest_md(sharpe=2.0, drawdown=20.0), encoding="utf-8"  # 20 > 15 floor
        )
        with patch("tools.trading.options.preflight._BASE_DIR", tmp_path):
            result = run_preflight(_proposal(), is_live=False)
        codes = {w["code"] for w in result["warnings"]}
        assert GateResultCode.ABOVE_DRAWDOWN_FLOOR in codes

    def test_excess_drawdown_blocks_on_live(self, tmp_path):
        inst = tmp_path / "memory" / "institutional"
        inst.mkdir(parents=True)
        (inst / "backtest-iron_condor-2025-01-01.md").write_text(
            _backtest_md(sharpe=2.0, drawdown=20.0), encoding="utf-8"
        )
        with patch("tools.trading.options.preflight._BASE_DIR", tmp_path):
            result = run_preflight(_proposal(), is_live=True, equity=100_000)
        codes = {b["code"] for b in result["blocks"]}
        assert GateResultCode.ABOVE_DRAWDOWN_FLOOR in codes

    def test_within_drawdown_floor_passes(self, tmp_path):
        inst = tmp_path / "memory" / "institutional"
        inst.mkdir(parents=True)
        (inst / "backtest-iron_condor-2025-01-01.md").write_text(
            _backtest_md(sharpe=2.0, drawdown=10.0), encoding="utf-8"  # 10 < 15 floor
        )
        with patch("tools.trading.options.preflight._BASE_DIR", tmp_path):
            result = run_preflight(_proposal(), is_live=True, equity=100_000)
        g0_blocks = [b for b in result["blocks"] if "drawdown" in b["code"]]
        assert g0_blocks == []


class TestGate0PerStrategyOverride:
    """Gate 0: naked_put has a stricter floor (sharpe_min=2.0, drawdown=10%)."""

    def test_naked_put_stricter_sharpe_floor(self, tmp_path):
        inst = tmp_path / "memory" / "institutional"
        inst.mkdir(parents=True)
        # sharpe=1.8 passes iron_condor floor (1.5) but fails naked_put floor (2.0)
        (inst / "backtest-naked_put-2025-01-01.md").write_text(
            _backtest_md(sharpe=1.8, drawdown=8.0), encoding="utf-8"
        )
        with patch("tools.trading.options.preflight._BASE_DIR", tmp_path):
            result = run_preflight(_proposal("naked_put"), is_live=True, equity=100_000)
        codes = {b["code"] for b in result["blocks"]}
        assert GateResultCode.BELOW_SHARPE_FLOOR in codes

    def test_naked_put_stricter_drawdown_floor(self, tmp_path):
        inst = tmp_path / "memory" / "institutional"
        inst.mkdir(parents=True)
        # drawdown=12% passes iron_condor floor (15%) but fails naked_put floor (10%)
        (inst / "backtest-naked_put-2025-01-01.md").write_text(
            _backtest_md(sharpe=2.5, drawdown=12.0), encoding="utf-8"
        )
        with patch("tools.trading.options.preflight._BASE_DIR", tmp_path):
            result = run_preflight(_proposal("naked_put"), is_live=True, equity=100_000)
        codes = {b["code"] for b in result["blocks"]}
        assert GateResultCode.ABOVE_DRAWDOWN_FLOOR in codes


class TestGate0MetaExposed:
    """Gate 0 results surface in run_preflight meta."""

    def test_meta_includes_gate0_fields(self, tmp_path):
        with patch("tools.trading.options.preflight._BASE_DIR", tmp_path):
            result = run_preflight(_proposal(), is_live=False)
        assert "gate0_backtest" in result["meta"]
        assert "gate0_floor" in result["meta"]
        assert result["meta"]["gate0_floor"]["sharpe_min"] == pytest.approx(1.5)
