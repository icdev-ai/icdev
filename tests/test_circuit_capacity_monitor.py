# CUI // SP-CTI
"""Tests for circuit_capacity_monitor anomaly detection (aiify-rm-ff651-phase-5261)."""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch



# ---------------------------------------------------------------------------
# _compute_fleet_stats
# ---------------------------------------------------------------------------

class TestComputeFleetStats:
    def _row(self, util: float):
        """Return a dict-like row as the function expects."""
        r = MagicMock()
        r.__getitem__ = lambda self, k: {"utilization_pct": util}[k]
        r.keys = lambda: ["utilization_pct"]
        return r

    def test_empty_rows_returns_empty(self):
        from tools.genesis.reflexes.circuit_capacity_monitor import _compute_fleet_stats
        assert _compute_fleet_stats([]) == {}

    def test_single_row(self):
        from tools.genesis.reflexes.circuit_capacity_monitor import _compute_fleet_stats
        row = self._row(60.0)
        stats = _compute_fleet_stats([row])
        assert stats["count"] == 1
        assert stats["mean"] == 60.0

    def test_multiple_rows_percentiles(self):
        from tools.genesis.reflexes.circuit_capacity_monitor import _compute_fleet_stats
        rows = [self._row(v) for v in [20.0, 40.0, 60.0, 80.0, 90.0]]
        stats = _compute_fleet_stats(rows)
        assert stats["count"] == 5
        assert stats["p75"] >= stats["mean"]
        assert stats["p90"] >= stats["p75"]
        assert stats["p95"] >= stats["p90"]

    def test_index_based_row_access(self):
        """Rows may be plain tuples; index 3 = utilization_pct."""
        from tools.genesis.reflexes.circuit_capacity_monitor import _compute_fleet_stats
        # tuple rows: (circuit_id, carrier, bandwidth_gbps, utilization_pct, ...)
        rows = [(f"C{i}", "AT&T", 10.0, float(i * 10), "active") for i in range(1, 6)]
        stats = _compute_fleet_stats(rows)
        assert stats["count"] == 5
        assert stats["mean"] == 30.0  # (10+20+30+40+50)/5

    def test_skips_non_numeric_utilization(self):
        from tools.genesis.reflexes.circuit_capacity_monitor import _compute_fleet_stats
        rows = [("C1", "AT&T", 10.0, "N/A", "active"),
                ("C2", "AT&T", 10.0, 60.0, "active")]
        stats = _compute_fleet_stats(rows)
        assert stats["count"] == 1
        assert stats["mean"] == 60.0


# ---------------------------------------------------------------------------
# _load_thresholds
# ---------------------------------------------------------------------------

class TestLoadThresholds:
    def test_returns_defaults_when_yaml_missing(self, tmp_path):
        from tools.genesis.reflexes.circuit_capacity_monitor import (
            _DEFAULT_THRESHOLDS,
            _load_thresholds,
        )
        import tools.genesis.reflexes.circuit_capacity_monitor as m
        # Temporarily point _ARGS_PATH to a non-existent file
        original = m._ARGS_PATH
        try:
            m._ARGS_PATH = tmp_path / "nonexistent.yaml"
            result = _load_thresholds()
            assert result["warn_pct"] == _DEFAULT_THRESHOLDS["warn_pct"]
            assert result["critical_pct"] == _DEFAULT_THRESHOLDS["critical_pct"]
        finally:
            m._ARGS_PATH = original

    def test_returns_dict_with_required_keys(self):
        from tools.genesis.reflexes.circuit_capacity_monitor import _load_thresholds
        result = _load_thresholds()
        for key in ("warn_pct", "critical_pct", "min_warn_pct", "max_warn_pct",
                    "min_critical_pct", "max_critical_pct"):
            assert key in result, f"missing key: {key}"

    def test_warn_less_than_critical_in_defaults(self):
        from tools.genesis.reflexes.circuit_capacity_monitor import _DEFAULT_THRESHOLDS
        assert _DEFAULT_THRESHOLDS["warn_pct"] < _DEFAULT_THRESHOLDS["critical_pct"]


# ---------------------------------------------------------------------------
# _calibrate_thresholds_with_ai
# ---------------------------------------------------------------------------

class TestCalibrateThresholdsWithAi:
    _BASE = {
        "warn_pct": 70.0,
        "critical_pct": 85.0,
        "min_warn_pct": 55.0,
        "max_warn_pct": 82.0,
        "min_critical_pct": 70.0,
        "max_critical_pct": 95.0,
    }
    _STATS = {"count": 10, "mean": 50.0, "p75": 65.0, "p90": 78.0, "p95": 82.0}

    def test_returns_base_when_fleet_stats_empty(self):
        from tools.genesis.reflexes.circuit_capacity_monitor import _calibrate_thresholds_with_ai
        result = _calibrate_thresholds_with_ai({}, self._BASE)
        assert result["warn_pct"] == self._BASE["warn_pct"]
        assert result["critical_pct"] == self._BASE["critical_pct"]

    def test_falls_back_to_base_on_llm_import_error(self):
        from tools.genesis.reflexes.circuit_capacity_monitor import _calibrate_thresholds_with_ai
        saved = sys.modules.get("tools.llm.router")
        try:
            sys.modules["tools.llm.router"] = None  # type: ignore[assignment]
            result = _calibrate_thresholds_with_ai(self._STATS, self._BASE)
            assert result["warn_pct"] == self._BASE["warn_pct"]
        finally:
            if saved is None:
                sys.modules.pop("tools.llm.router", None)
            else:
                sys.modules["tools.llm.router"] = saved

    def test_parses_valid_llm_json_response(self):
        from tools.genesis.reflexes.circuit_capacity_monitor import _calibrate_thresholds_with_ai

        mock_response = MagicMock()
        mock_response.content = '{"warn_pct": 72.0, "critical_pct": 87.0, "rationale": "test"}'
        mock_router_instance = MagicMock()
        mock_router_instance.invoke.return_value = mock_response
        mock_router_mod = MagicMock(LLMRouter=MagicMock(return_value=mock_router_instance))
        mock_provider_mod = MagicMock(LLMRequest=MagicMock())

        with patch.dict(sys.modules, {
            "tools.llm.router": mock_router_mod,
            "tools.llm.provider": mock_provider_mod,
        }):
            result = _calibrate_thresholds_with_ai(self._STATS, self._BASE)

        assert result["warn_pct"] == 72.0
        assert result["critical_pct"] == 87.0

    def test_enforces_10pct_gap_invariant(self):
        """critical must be at least warn + 10."""
        from tools.genesis.reflexes.circuit_capacity_monitor import _calibrate_thresholds_with_ai

        mock_response = MagicMock()
        # LLM returns gap < 10
        mock_response.content = '{"warn_pct": 75.0, "critical_pct": 80.0, "rationale": "narrow"}'
        mock_router_instance = MagicMock()
        mock_router_instance.invoke.return_value = mock_response
        mock_router_mod = MagicMock(LLMRouter=MagicMock(return_value=mock_router_instance))
        mock_provider_mod = MagicMock(LLMRequest=MagicMock())

        with patch.dict(sys.modules, {
            "tools.llm.router": mock_router_mod,
            "tools.llm.provider": mock_provider_mod,
        }):
            result = _calibrate_thresholds_with_ai(self._STATS, self._BASE)

        assert result["critical_pct"] >= result["warn_pct"] + 10.0

    def test_clamps_to_allowed_ranges(self):
        """LLM output outside allowed ranges must be clamped."""
        from tools.genesis.reflexes.circuit_capacity_monitor import _calibrate_thresholds_with_ai

        mock_response = MagicMock()
        mock_response.content = '{"warn_pct": 10.0, "critical_pct": 99.0, "rationale": "extreme"}'
        mock_router_instance = MagicMock()
        mock_router_instance.invoke.return_value = mock_response
        mock_router_mod = MagicMock(LLMRouter=MagicMock(return_value=mock_router_instance))
        mock_provider_mod = MagicMock(LLMRequest=MagicMock())

        with patch.dict(sys.modules, {
            "tools.llm.router": mock_router_mod,
            "tools.llm.provider": mock_provider_mod,
        }):
            result = _calibrate_thresholds_with_ai(self._STATS, self._BASE)

        assert result["warn_pct"] >= self._BASE["min_warn_pct"]
        assert result["warn_pct"] <= self._BASE["max_warn_pct"]
        assert result["critical_pct"] <= self._BASE["max_critical_pct"]

    def test_strips_markdown_fences(self):
        """LLM may return ```json ... ``` — must parse correctly."""
        from tools.genesis.reflexes.circuit_capacity_monitor import _calibrate_thresholds_with_ai

        mock_response = MagicMock()
        mock_response.content = (
            '```json\n{"warn_pct": 73.0, "critical_pct": 88.0, "rationale": "ok"}\n```'
        )
        mock_router_instance = MagicMock()
        mock_router_instance.invoke.return_value = mock_response
        mock_router_mod = MagicMock(LLMRouter=MagicMock(return_value=mock_router_instance))
        mock_provider_mod = MagicMock(LLMRequest=MagicMock())

        with patch.dict(sys.modules, {
            "tools.llm.router": mock_router_mod,
            "tools.llm.provider": mock_provider_mod,
        }):
            result = _calibrate_thresholds_with_ai(self._STATS, self._BASE)

        assert result["warn_pct"] == 73.0
        assert result["critical_pct"] == 88.0

    def test_returns_base_on_invalid_json(self):
        from tools.genesis.reflexes.circuit_capacity_monitor import _calibrate_thresholds_with_ai

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
            result = _calibrate_thresholds_with_ai(self._STATS, self._BASE)

        assert result["warn_pct"] == self._BASE["warn_pct"]
        assert result["critical_pct"] == self._BASE["critical_pct"]


# ---------------------------------------------------------------------------
# run() result schema
# ---------------------------------------------------------------------------

class TestRunResultSchema:
    def test_result_docstring_documents_thresholds_used(self):
        from tools.genesis.reflexes.circuit_capacity_monitor import run
        assert "thresholds_used" in (run.__doc__ or "")

    def test_module_exposes_default_constants(self):
        import tools.genesis.reflexes.circuit_capacity_monitor as m
        assert hasattr(m, "_DEFAULT_WARN_PCT")
        assert hasattr(m, "_DEFAULT_CRITICAL_PCT")
        assert m._DEFAULT_WARN_PCT < m._DEFAULT_CRITICAL_PCT

    def test_default_thresholds_dict_has_bounds(self):
        from tools.genesis.reflexes.circuit_capacity_monitor import _DEFAULT_THRESHOLDS
        for key in ("warn_pct", "critical_pct", "min_warn_pct", "max_warn_pct",
                    "min_critical_pct", "max_critical_pct"):
            assert key in _DEFAULT_THRESHOLDS

    def test_syntax_valid(self):
        """Verify the module can be imported without SyntaxError."""
        import importlib
        import tools.genesis.reflexes.circuit_capacity_monitor as m
        importlib.reload(m)  # re-parse the file
