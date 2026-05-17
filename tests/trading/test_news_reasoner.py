# CUI // SP-CTI
"""Tests for INTaaS news reasoner CoT integration."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tools.trading.news.news_reasoner import reason_cluster


class TestReasonClusterCoT:
    def test_large_cluster_uses_cot(self):
        """Clusters with >=5 items should invoke CoT."""
        items = [{"title": f"News {i}", "net_direction": "bullish"} for i in range(5)]
        cluster_meta = {"category": "macro", "status": "emerging"}

        with patch("tools.trading.news.news_reasoner._invoke_llm") as mock_llm:
            mock_llm.return_value = {
                "cluster_signal": "bullish",
                "cluster_confidence": 0.8,
                "narrative_convergence": "aligned",
                "coordinated_omissions": [],
                "contrarian_items": [],
                "divergence_assessment": "Test",
            }
            result = reason_cluster(items, cluster_meta, macro_context=None)

        assert result["chain_mode"] == "cot"
        assert result["reasoning_source"] == "llm"
        mock_llm.assert_called_once()
        call_args, call_kwargs = mock_llm.call_args
        assert call_kwargs.get("chain_mode") == "cot"

    def test_small_cluster_uses_single_llm(self):
        """Clusters with <5 items should use single-LLM mode."""
        items = [{"title": "News 1", "net_direction": "bullish"}]
        cluster_meta = {"category": "macro", "status": "emerging"}

        with patch("tools.trading.news.news_reasoner._invoke_llm") as mock_llm:
            mock_llm.return_value = {
                "cluster_signal": "bullish",
                "cluster_confidence": 0.5,
                "narrative_convergence": "aligned",
                "coordinated_omissions": [],
                "contrarian_items": [],
                "divergence_assessment": "Test",
            }
            result = reason_cluster(items, cluster_meta, macro_context=None)

        assert result["chain_mode"] == "single"
        assert result["reasoning_source"] == "llm"
        mock_llm.assert_called_once()
        call_args, call_kwargs = mock_llm.call_args
        assert call_kwargs.get("chain_mode") == ""

    def test_regime_status_uses_cot(self):
        """Clusters with status 'regime' should invoke CoT regardless of item count."""
        items = [{"title": "News 1", "net_direction": "bearish"}]
        cluster_meta = {"category": "macro", "status": "regime"}

        with patch("tools.trading.news.news_reasoner._invoke_llm") as mock_llm:
            mock_llm.return_value = {
                "cluster_signal": "bearish",
                "cluster_confidence": 0.9,
                "narrative_convergence": "aligned",
                "coordinated_omissions": [],
                "contrarian_items": [],
                "divergence_assessment": "Test",
            }
            result = reason_cluster(items, cluster_meta, macro_context=None)

        assert result["chain_mode"] == "cot"
        mock_llm.assert_called_once()
        call_args, call_kwargs = mock_llm.call_args
        assert call_kwargs.get("chain_mode") == "cot"

    def test_llm_failure_falls_back_to_heuristic(self):
        """When _invoke_llm returns None, heuristic fallback must succeed."""
        items = [
            {"title": "Bullish news", "net_direction": "bullish"},
            {"title": "Bearish news", "net_direction": "bearish"},
        ]
        cluster_meta = {"category": "earnings", "status": "emerging"}

        with patch("tools.trading.news.news_reasoner._invoke_llm") as mock_llm:
            mock_llm.return_value = None
            result = reason_cluster(items, cluster_meta, macro_context=None)

        assert result["cluster_signal"] in ("bullish", "bearish", "neutral")
        assert "cluster_confidence" in result
        assert result.get("reasoning_source") != "llm"
