# CUI // SP-CTI
"""Tests for put_call_sentiment confluence pillar (fdmm-pcr-03/04)."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from tools.trading.analysis.confluence_pillars.put_call_sentiment import (
    _sma,
    _zscore,
    evaluate,
)
from tools.trading.analysis.confluence_scorer import PILLAR_WEIGHTS

_MODULE = "tools.trading.analysis.confluence_pillars.put_call_sentiment"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fear_series(n: int = 30) -> list[float]:
    """Raw P/C series where the final SMA value is a strong high outlier (z > 1.5)."""
    # 29 values at 0.85, last spikes to 3.0
    # After SMA(10): last smoothed ≈ (0.85*9+3.0)/10 = 1.065; others ≈ 0.85 → z ≈ 4.5
    return [0.85] * (n - 1) + [3.0]


def _complacency_series(n: int = 30) -> list[float]:
    """Raw P/C series where the final SMA value is a strong low outlier (z < -1.5)."""
    # 29 values at 1.5, last drops to 0.3
    # After SMA(10): last smoothed ≈ (1.5*9+0.3)/10 = 1.38; others ≈ 1.5 → z ≈ -4.5
    return [1.5] * (n - 1) + [0.3]


def _neutral_series(n: int = 30) -> list[float]:
    """Flat P/C series → z-score = 0 (neutral zone)."""
    return [0.85] * n


# ---------------------------------------------------------------------------
# Case 1: FEAR_EXTREME → bull regardless of ticker P/C
# ---------------------------------------------------------------------------

class TestFearExtreme:
    def test_returns_bull(self):
        with patch(f"{_MODULE}._fetch_equity_pc_series", return_value=_fear_series()), \
             patch(f"{_MODULE}._fetch_ticker_pc", return_value=None):
            vote = evaluate("AAPL", "BULL")
        assert vote.direction == "bull"
        assert vote.label == "FEAR_EXTREME"
        assert vote.score >= 0.6

    def test_bull_regardless_of_low_ticker_pc(self):
        """FEAR_EXTREME must not be overridden by a low per-ticker P/C ratio."""
        with patch(f"{_MODULE}._fetch_equity_pc_series", return_value=_fear_series()), \
             patch(f"{_MODULE}._fetch_ticker_pc", return_value=0.2):
            vote = evaluate("TSLA", "BULL")
        assert vote.direction == "bull"
        assert vote.label == "FEAR_EXTREME"

    def test_evidence_contains_zscore(self):
        with patch(f"{_MODULE}._fetch_equity_pc_series", return_value=_fear_series()), \
             patch(f"{_MODULE}._fetch_ticker_pc", return_value=None):
            vote = evaluate("AAPL", "BULL")
        assert "equity_pc_zscore" in vote.evidence
        assert vote.evidence["equity_pc_zscore"] > 1.5


# ---------------------------------------------------------------------------
# Case 2: COMPLACENCY_EXTREME → bear (headwind for bulls)
# ---------------------------------------------------------------------------

class TestComplacencyExtreme:
    def test_returns_bear(self):
        with patch(f"{_MODULE}._fetch_equity_pc_series", return_value=_complacency_series()), \
             patch(f"{_MODULE}._fetch_ticker_pc", return_value=None):
            vote = evaluate("AAPL", "BEAR")
        assert vote.direction == "bear"
        assert vote.label == "COMPLACENCY_EXTREME"
        assert vote.score >= 0.6

    def test_bear_is_headwind_for_bull_signal(self):
        """Even with a BULL signal, COMPLACENCY_EXTREME returns bear direction."""
        with patch(f"{_MODULE}._fetch_equity_pc_series", return_value=_complacency_series()), \
             patch(f"{_MODULE}._fetch_ticker_pc", return_value=None):
            vote = evaluate("SPY", "BULL")
        assert vote.direction == "bear"


# ---------------------------------------------------------------------------
# Case 3: Neutral zone — flat series
# ---------------------------------------------------------------------------

class TestNeutralZone:
    def test_neutral_when_zscore_in_range(self):
        with patch(f"{_MODULE}._fetch_equity_pc_series", return_value=_neutral_series()), \
             patch(f"{_MODULE}._fetch_ticker_pc", return_value=None):
            vote = evaluate("AAPL", "BULL")
        assert vote.direction == "neutral"
        assert vote.label == "NEUTRAL"
        assert vote.score == 0.0


# ---------------------------------------------------------------------------
# Case 4: Per-ticker P/C modifiers (neutral zone only)
# ---------------------------------------------------------------------------

class TestTickerPCModifier:
    def test_high_ticker_pc_confirms_bear(self):
        """ticker pc_ratio > 1.5 + BEAR signal → confirms bear in neutral zone."""
        with patch(f"{_MODULE}._fetch_equity_pc_series", return_value=_neutral_series()), \
             patch(f"{_MODULE}._fetch_ticker_pc", return_value=1.8):
            vote = evaluate("SPY", "BEAR")
        assert vote.direction == "bear"
        assert "bear" in vote.label.lower()
        assert vote.score >= 0.5

    def test_low_ticker_pc_warns_complacency(self):
        """ticker pc_ratio < 0.5 + BULL signal → warns complacency → bear direction."""
        with patch(f"{_MODULE}._fetch_equity_pc_series", return_value=_neutral_series()), \
             patch(f"{_MODULE}._fetch_ticker_pc", return_value=0.3):
            vote = evaluate("TSLA", "BULL")
        assert vote.direction == "bear"
        assert "complacency" in vote.label.lower()


# ---------------------------------------------------------------------------
# Case 5: Graceful neutral when P/C data unavailable
# ---------------------------------------------------------------------------

class TestUnavailableData:
    def test_neutral_when_no_equity_pc_data(self):
        with patch(f"{_MODULE}._fetch_equity_pc_series", return_value=None):
            vote = evaluate("AAPL", "BULL")
        assert vote.direction == "neutral"
        assert vote.score == 0.0
        assert vote.label == "UNAVAILABLE"
        assert "reason" in vote.evidence

    def test_neutral_when_insufficient_history(self):
        # Only 5 raw values → SMA(10) produces empty list → UNAVAILABLE
        with patch(f"{_MODULE}._fetch_equity_pc_series", return_value=[0.85] * 5):
            vote = evaluate("AAPL", "BULL")
        assert vote.direction == "neutral"
        assert vote.label == "UNAVAILABLE"


# ---------------------------------------------------------------------------
# Internal helper unit tests
# ---------------------------------------------------------------------------

class TestSMAHelper:
    def test_length(self):
        assert len(_sma([1.0] * 20, 10)) == 11

    def test_flat_sma_equals_value(self):
        result = _sma([2.5] * 20, 10)
        assert all(abs(v - 2.5) < 1e-9 for v in result)


class TestZscoreHelper:
    def test_flat_returns_zero(self):
        assert _zscore([1.0] * 20) == 0.0

    def test_spike_positive(self):
        series = [0.85] * 29 + [2.5]
        assert _zscore(series) > 0

    def test_returns_none_for_single_value(self):
        assert _zscore([1.0]) is None


# ---------------------------------------------------------------------------
# Case 6: Weight normalization — PILLAR_WEIGHTS integrity (fdmm-pcr-04)
# ---------------------------------------------------------------------------

_SCORER_MODULE = "tools.trading.analysis.confluence_pillars.put_call_sentiment"


class TestPillarWeightNormalization:
    def test_pillar_weights_sum_to_one(self):
        """PILLAR_WEIGHTS must sum to exactly 1.0 (within 0.001)."""
        assert abs(sum(PILLAR_WEIGHTS.values()) - 1.0) < 0.001

    def test_put_call_weight_is_0_08(self):
        assert PILLAR_WEIGHTS["put_call_sentiment"] == pytest.approx(0.08)

    def test_sentiment_bullish_weight_is_0_08(self):
        assert PILLAR_WEIGHTS["sentiment_bullish"] == pytest.approx(0.08)

    def test_news_bullish_weight_is_0_08(self):
        assert PILLAR_WEIGHTS["news_bullish"] == pytest.approx(0.08)

    def test_unavailable_pc_excluded_from_denominator(self):
        """When put_call pillar is UNAVAILABLE it must be direction='unknown',
        so _score_pillars excludes its weight from active_weight_sum (renormalize)."""
        from tools.trading.analysis.confluence_scorer import evaluate as cs_evaluate

        with patch(f"{_SCORER_MODULE}._fetch_equity_pc_series", return_value=None), \
             patch(f"{_SCORER_MODULE}._fetch_ticker_pc", return_value=None):
            result = cs_evaluate(
                ticker="AAPL",
                composite_direction="BUY",
                component_scores={"fundamental": 80, "technical": 80,
                                  "sentiment": 80, "news": 80},
                macro_regime="GREEN",
                perspective={"net_score": 5, "consensus": True},
                advisor={"direction": "BUY"},
                expert_consensus={"bull_votes": 5, "total_votes": 6},
            )
        # All 8 known pillars agree → score should be Tier A (≥ 80)
        # even though put_call is unknown (excluded from denominator).
        assert result.confluence_score >= 80.0

    def test_available_pc_included_in_scoring(self):
        """When put_call data IS available, the pillar direction is counted."""
        from tools.trading.analysis.confluence_scorer import evaluate as cs_evaluate

        with patch(f"{_SCORER_MODULE}._fetch_equity_pc_series", return_value=_fear_series()), \
             patch(f"{_SCORER_MODULE}._fetch_ticker_pc", return_value=None):
            result = cs_evaluate(
                ticker="AAPL",
                composite_direction="BUY",
                component_scores={"fundamental": 80, "technical": 80,
                                  "sentiment": 80, "news": 80},
                macro_regime="GREEN",
            )
        # put_call pillar should appear in the pillar list
        pc_pillars = [p for p in result.pillars if p.name == "put_call_sentiment"]
        assert len(pc_pillars) == 1
        assert pc_pillars[0].direction in ("bull", "bear", "neutral")

    def test_no_duplicate_pc_pillar_when_caller_supplies_it(self):
        """Auto-injection must be skipped if caller passes put_call_sentiment in extra_pillars."""
        from tools.trading.analysis.confluence_scorer import (
            evaluate as cs_evaluate,
            PillarVote as ScorerPillarVote,
        )

        manual = ScorerPillarVote(
            name="put_call_sentiment",
            direction="bear",
            weight=0.08,
            evidence="caller-supplied",
        )
        result = cs_evaluate(
            ticker="TSLA",
            composite_direction="BUY",
            extra_pillars=[manual],
        )
        pc_count = sum(1 for p in result.pillars if p.name == "put_call_sentiment")
        assert pc_count == 1
