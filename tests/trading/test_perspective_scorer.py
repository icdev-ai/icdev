# CUI // SP-CTI
"""Tests for INTaaS perspective scorer CoD integration."""
from __future__ import annotations

from unittest.mock import MagicMock, patch


from tools.trading.analysis.perspective_scorer import score_perspectives


class TestScorePerspectivesDeterministic:
    def test_basic_bullish(self):
        findings = {
            "fundamental": {"score": 75, "metrics": {"price_vs_sma20": 1.0, "price_vs_sma50": 1.0}},
            "sentiment": {"score": 0.5, "positive_count": 5, "negative_count": 2},
            "news": {"catalyst_count": 3, "net_direction": "bullish"},
            "technical": {"signals": ["RSI_OVERSOLD", "MACD_BULLISH_CROSS"]},
        }
        result = score_perspectives("AAPL", findings)
        assert result["signal"] in ("buy", "strong_buy")
        assert result["reasoning_source"] == "deterministic"
        assert result["chain_mode"] == "single"
        assert "confidence" in result

    def test_basic_bearish(self):
        findings = {
            "fundamental": {"score": 30, "metrics": {"volatility": 0.05}},
            "sentiment": {"score": -0.5, "positive_count": 1, "negative_count": 5},
            "news": {"net_direction": "bearish", "net_impact": "high"},
            "technical": {"signals": ["RSI_OVERBOUGHT", "MACD_BEARISH_CROSS"]},
        }
        result = score_perspectives("TSLA", findings)
        assert result["signal"] in ("sell", "strong_sell")
        assert result["reasoning_source"] == "deterministic"


class TestScorePerspectivesCoD:
    def test_cod_path_returns_parsed_result(self):
        findings = {
            "fundamental": {"score": 75, "metrics": {}},
            "sentiment": {"score": 0.3, "positive_count": 3, "negative_count": 2},
            "news": {"catalyst_count": 2, "net_direction": "bullish"},
            "technical": {"signals": []},
        }

        mock_resp = MagicMock()
        mock_resp.content = (
            '{"signal": "buy", "net_score": 35, "confidence": 0.75, '
            '"bull_summary": "Strong fundamentals", "bear_summary": "Some risk"}'
        )

        with patch("tools.llm.router.LLMRouter") as mock_router_cls:
            mock_router = MagicMock()
            mock_router.invoke.return_value = mock_resp
            mock_router_cls.return_value = mock_router
            result = score_perspectives("AAPL", findings, use_cod=True)

        assert result["signal"] == "buy"
        assert result["net_score"] == 35
        assert result["confidence"] == 0.75
        assert result["reasoning_source"] == "cod"
        assert result["chain_mode"] == "cod"
        mock_router.invoke.assert_called_once()
        args, _ = mock_router.invoke.call_args
        assert args[0] == "intaas_multiperspectivity"

    def test_cod_failure_falls_back_to_deterministic(self):
        findings = {
            "fundamental": {"score": 60, "metrics": {}},
            "sentiment": {"score": 0.2, "positive_count": 2, "negative_count": 2},
            "news": {"catalyst_count": 1, "net_direction": "neutral"},
            "technical": {"signals": []},
        }

        with patch("tools.llm.router.LLMRouter") as mock_router_cls:
            mock_router = MagicMock()
            mock_router.invoke.side_effect = RuntimeError("LLM unavailable")
            mock_router_cls.return_value = mock_router
            result = score_perspectives("AAPL", findings, use_cod=True)

        assert result["reasoning_source"] == "deterministic"
        assert result["chain_mode"] == "single"
        assert "signal" in result

    def test_cod_invalid_json_falls_back(self):
        findings = {
            "fundamental": {"score": 60, "metrics": {}},
            "sentiment": {"score": 0.2, "positive_count": 2, "negative_count": 2},
            "news": {"catalyst_count": 1, "net_direction": "neutral"},
            "technical": {"signals": []},
        }

        mock_resp = MagicMock()
        mock_resp.content = "not valid json"

        with patch("tools.llm.router.LLMRouter") as mock_router_cls:
            mock_router = MagicMock()
            mock_router.invoke.return_value = mock_resp
            mock_router_cls.return_value = mock_router
            result = score_perspectives("AAPL", findings, use_cod=True)

        assert result["reasoning_source"] == "deterministic"
        assert result["chain_mode"] == "single"
