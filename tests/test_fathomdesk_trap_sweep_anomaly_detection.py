# CUI // SP-CTI
"""Tests for fathomdesk_trap_sweep anomaly detection (aiify-rm-ff651-phase-5280)."""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# _load_trap_config
# ---------------------------------------------------------------------------

class TestLoadTrapConfig:
    def test_returns_defaults_when_yaml_missing(self, tmp_path, monkeypatch):
        import tools.genesis.reflexes.fathomdesk_trap_sweep as m
        orig = m.BASE_DIR
        try:
            monkeypatch.setattr(m, "BASE_DIR", tmp_path)
            result = m._load_trap_config()
            assert result["min_confidence"] == m._DEFAULT_MIN_CONFIDENCE
            assert result["cooldown_hours"] == m._DEFAULT_COOLDOWN_HOURS
        finally:
            m.BASE_DIR = orig

    def test_returns_dict_with_required_keys(self):
        from tools.genesis.reflexes.fathomdesk_trap_sweep import _load_trap_config
        result = _load_trap_config()
        for key in ("cooldown_hours", "dedup_window_hours", "min_confidence",
                    "sentiment_elevation", "sentiment_bearish_threshold"):
            assert key in result, f"missing key: {key}"

    def test_default_confidence_in_valid_range(self):
        from tools.genesis.reflexes.fathomdesk_trap_sweep import _DEFAULT_MIN_CONFIDENCE
        assert 0.0 < _DEFAULT_MIN_CONFIDENCE < 1.0

    def test_cooldown_positive(self):
        from tools.genesis.reflexes.fathomdesk_trap_sweep import _DEFAULT_COOLDOWN_HOURS
        assert _DEFAULT_COOLDOWN_HOURS > 0


# ---------------------------------------------------------------------------
# _calibrate_thresholds_with_ai — always-on, silent fallback
# ---------------------------------------------------------------------------

class TestCalibrateThresholdsWithAi:
    _BASE = {
        "cooldown_hours": 4,
        "dedup_window_hours": 24,
        "min_confidence": 0.50,
        "sentiment_elevation": 0.15,
        "sentiment_bearish_threshold": 0.40,
        "use_llm_anomaly_scoring": False,
    }

    def _make_conn(self, trap_count=5, signal_count=30):
        conn = MagicMock()
        conn.execute.return_value.fetchone.side_effect = [
            (trap_count,),
            (signal_count,),
        ]
        return conn

    def test_returns_base_on_llm_import_error(self):
        from tools.genesis.reflexes.fathomdesk_trap_sweep import _calibrate_thresholds_with_ai
        conn = self._make_conn()
        saved = sys.modules.get("tools.llm.router")
        try:
            sys.modules["tools.llm.router"] = None  # type: ignore[assignment]
            result = _calibrate_thresholds_with_ai(conn, self._BASE)
            assert result["min_confidence"] == self._BASE["min_confidence"]
        finally:
            if saved is None:
                sys.modules.pop("tools.llm.router", None)
            else:
                sys.modules["tools.llm.router"] = saved

    def test_parses_valid_llm_json_response(self):
        from tools.genesis.reflexes.fathomdesk_trap_sweep import _calibrate_thresholds_with_ai
        conn = self._make_conn()
        mock_response = MagicMock()
        mock_response.content = '{"min_confidence": 0.60, "sentiment_elevation": 0.12, "sentiment_bearish_threshold": 0.35}'
        mock_router_instance = MagicMock()
        mock_router_instance.invoke.return_value = mock_response
        mock_router_mod = MagicMock(LLMRouter=MagicMock(return_value=mock_router_instance))
        mock_provider_mod = MagicMock(LLMRequest=MagicMock())

        with patch.dict(sys.modules, {
            "tools.llm.router": mock_router_mod,
            "tools.llm.provider": mock_provider_mod,
        }):
            result = _calibrate_thresholds_with_ai(conn, self._BASE)

        assert result["min_confidence"] == 0.60
        assert result["sentiment_elevation"] == 0.12
        assert result["sentiment_bearish_threshold"] == 0.35
        assert result.get("_ai_calibrated") is True

    def test_clamps_min_confidence_to_lower_bound(self):
        """LLM returning below 0.35 must be clamped to 0.35."""
        from tools.genesis.reflexes.fathomdesk_trap_sweep import _calibrate_thresholds_with_ai
        conn = self._make_conn()
        mock_response = MagicMock()
        mock_response.content = '{"min_confidence": 0.10, "sentiment_elevation": 0.15, "sentiment_bearish_threshold": 0.40}'
        mock_router_instance = MagicMock()
        mock_router_instance.invoke.return_value = mock_response
        mock_router_mod = MagicMock(LLMRouter=MagicMock(return_value=mock_router_instance))
        mock_provider_mod = MagicMock(LLMRequest=MagicMock())

        with patch.dict(sys.modules, {
            "tools.llm.router": mock_router_mod,
            "tools.llm.provider": mock_provider_mod,
        }):
            result = _calibrate_thresholds_with_ai(conn, self._BASE)

        assert result["min_confidence"] >= 0.35

    def test_clamps_min_confidence_to_upper_bound(self):
        """LLM returning above 0.80 must be clamped to 0.80."""
        from tools.genesis.reflexes.fathomdesk_trap_sweep import _calibrate_thresholds_with_ai
        conn = self._make_conn()
        mock_response = MagicMock()
        mock_response.content = '{"min_confidence": 0.99, "sentiment_elevation": 0.15, "sentiment_bearish_threshold": 0.40}'
        mock_router_instance = MagicMock()
        mock_router_instance.invoke.return_value = mock_response
        mock_router_mod = MagicMock(LLMRouter=MagicMock(return_value=mock_router_instance))
        mock_provider_mod = MagicMock(LLMRequest=MagicMock())

        with patch.dict(sys.modules, {
            "tools.llm.router": mock_router_mod,
            "tools.llm.provider": mock_provider_mod,
        }):
            result = _calibrate_thresholds_with_ai(conn, self._BASE)

        assert result["min_confidence"] <= 0.80

    def test_clamps_sentiment_elevation_bounds(self):
        """sentiment_elevation must stay within 0.05-0.30."""
        from tools.genesis.reflexes.fathomdesk_trap_sweep import _calibrate_thresholds_with_ai
        conn = self._make_conn()
        mock_response = MagicMock()
        mock_response.content = '{"min_confidence": 0.50, "sentiment_elevation": 0.99, "sentiment_bearish_threshold": 0.40}'
        mock_router_instance = MagicMock()
        mock_router_instance.invoke.return_value = mock_response
        mock_router_mod = MagicMock(LLMRouter=MagicMock(return_value=mock_router_instance))
        mock_provider_mod = MagicMock(LLMRequest=MagicMock())

        with patch.dict(sys.modules, {
            "tools.llm.router": mock_router_mod,
            "tools.llm.provider": mock_provider_mod,
        }):
            result = _calibrate_thresholds_with_ai(conn, self._BASE)

        assert 0.05 <= result["sentiment_elevation"] <= 0.30

    def test_clamps_sentiment_bearish_threshold_bounds(self):
        """sentiment_bearish_threshold must stay within 0.25-0.60."""
        from tools.genesis.reflexes.fathomdesk_trap_sweep import _calibrate_thresholds_with_ai
        conn = self._make_conn()
        mock_response = MagicMock()
        mock_response.content = '{"min_confidence": 0.50, "sentiment_elevation": 0.15, "sentiment_bearish_threshold": 0.05}'
        mock_router_instance = MagicMock()
        mock_router_instance.invoke.return_value = mock_response
        mock_router_mod = MagicMock(LLMRouter=MagicMock(return_value=mock_router_instance))
        mock_provider_mod = MagicMock(LLMRequest=MagicMock())

        with patch.dict(sys.modules, {
            "tools.llm.router": mock_router_mod,
            "tools.llm.provider": mock_provider_mod,
        }):
            result = _calibrate_thresholds_with_ai(conn, self._BASE)

        assert result["sentiment_bearish_threshold"] >= 0.25

    def test_strips_markdown_fences(self):
        """LLM may return ```json ... ``` — must parse correctly."""
        from tools.genesis.reflexes.fathomdesk_trap_sweep import _calibrate_thresholds_with_ai
        conn = self._make_conn()
        mock_response = MagicMock()
        mock_response.content = (
            "```json\n"
            '{"min_confidence": 0.65, "sentiment_elevation": 0.18, "sentiment_bearish_threshold": 0.38}\n'
            "```"
        )
        mock_router_instance = MagicMock()
        mock_router_instance.invoke.return_value = mock_response
        mock_router_mod = MagicMock(LLMRouter=MagicMock(return_value=mock_router_instance))
        mock_provider_mod = MagicMock(LLMRequest=MagicMock())

        with patch.dict(sys.modules, {
            "tools.llm.router": mock_router_mod,
            "tools.llm.provider": mock_provider_mod,
        }):
            result = _calibrate_thresholds_with_ai(conn, self._BASE)

        assert result["min_confidence"] == 0.65
        assert result["sentiment_elevation"] == 0.18

    def test_returns_base_on_invalid_json(self):
        from tools.genesis.reflexes.fathomdesk_trap_sweep import _calibrate_thresholds_with_ai
        conn = self._make_conn()
        mock_response = MagicMock()
        mock_response.content = "not valid json"
        mock_router_instance = MagicMock()
        mock_router_instance.invoke.return_value = mock_response
        mock_router_mod = MagicMock(LLMRouter=MagicMock(return_value=mock_router_instance))
        mock_provider_mod = MagicMock(LLMRequest=MagicMock())

        with patch.dict(sys.modules, {
            "tools.llm.router": mock_router_mod,
            "tools.llm.provider": mock_provider_mod,
        }):
            result = _calibrate_thresholds_with_ai(conn, self._BASE)

        assert result["min_confidence"] == self._BASE["min_confidence"]
        assert result.get("_ai_calibrated") is None

    def test_db_query_failure_does_not_prevent_calibration(self):
        """DB error on trap/signal counts → calibration still runs with count=0."""
        from tools.genesis.reflexes.fathomdesk_trap_sweep import _calibrate_thresholds_with_ai
        conn = MagicMock()
        conn.execute.side_effect = Exception("table missing")
        mock_response = MagicMock()
        mock_response.content = '{"min_confidence": 0.55, "sentiment_elevation": 0.15, "sentiment_bearish_threshold": 0.40}'
        mock_router_instance = MagicMock()
        mock_router_instance.invoke.return_value = mock_response
        mock_router_mod = MagicMock(LLMRouter=MagicMock(return_value=mock_router_instance))
        mock_provider_mod = MagicMock(LLMRequest=MagicMock())

        with patch.dict(sys.modules, {
            "tools.llm.router": mock_router_mod,
            "tools.llm.provider": mock_provider_mod,
        }):
            result = _calibrate_thresholds_with_ai(conn, self._BASE)

        assert result["min_confidence"] == 0.55


# ---------------------------------------------------------------------------
# Module-level constants and structure
# ---------------------------------------------------------------------------

class TestModuleStructure:
    def test_exposes_default_constants(self):
        import tools.genesis.reflexes.fathomdesk_trap_sweep as m
        assert hasattr(m, "_DEFAULT_MIN_CONFIDENCE")
        assert hasattr(m, "_DEFAULT_COOLDOWN_HOURS")
        assert hasattr(m, "_DEFAULT_DEDUP_WINDOW_HOURS")
        assert hasattr(m, "_DEFAULT_SENTIMENT_ELEVATION")
        assert hasattr(m, "_DEFAULT_SENTIMENT_BEARISH_THRESHOLD")

    def test_default_min_confidence_in_valid_range(self):
        from tools.genesis.reflexes.fathomdesk_trap_sweep import _DEFAULT_MIN_CONFIDENCE
        assert 0.35 <= _DEFAULT_MIN_CONFIDENCE <= 0.80

    def test_default_sentiment_elevation_in_valid_range(self):
        from tools.genesis.reflexes.fathomdesk_trap_sweep import _DEFAULT_SENTIMENT_ELEVATION
        assert 0.05 <= _DEFAULT_SENTIMENT_ELEVATION <= 0.30

    def test_default_sentiment_bearish_threshold_in_valid_range(self):
        from tools.genesis.reflexes.fathomdesk_trap_sweep import _DEFAULT_SENTIMENT_BEARISH_THRESHOLD
        assert 0.25 <= _DEFAULT_SENTIMENT_BEARISH_THRESHOLD <= 0.60

    def test_syntax_valid(self):
        import importlib
        import tools.genesis.reflexes.fathomdesk_trap_sweep as m
        importlib.reload(m)
