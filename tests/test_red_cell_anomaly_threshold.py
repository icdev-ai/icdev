# CUI // SP-CTI
"""RED-PHASE tests: anomaly-detection-based threshold for red_cell reflex.

Covers:
  - _load_config()
  - _fetch_recent_vuln_scores()
  - _compute_anomaly_threshold()
  - Threshold injection in _surface_oracle() / _build_counter_description()
"""
from __future__ import annotations

import math
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import sys
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.genesis.reflexes.strategos import red_cell


# ── _load_config ─────────────────────────────────────────────────────────────

class TestLoadConfig:
    def test_returns_dict(self):
        cfg = red_cell._load_config()
        assert isinstance(cfg, dict)

    def test_contains_red_cell_section(self):
        cfg = red_cell._load_config()
        assert "red_cell" in cfg

    def test_vuln_threshold_key_present(self):
        cfg = red_cell._load_config()
        assert "vuln_threshold" in cfg["red_cell"]

    def test_anomaly_detection_section_is_dict(self):
        cfg = red_cell._load_config()
        ad = cfg.get("red_cell", {}).get("anomaly_detection", {})
        assert isinstance(ad, dict)

    def test_fallback_on_missing_file_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(red_cell, "_CONFIG_PATH", tmp_path / "missing.yaml")
        cfg = red_cell._load_config()
        assert cfg == {}


# ── _fetch_recent_vuln_scores ────────────────────────────────────────────────

class TestFetchRecentVulnScores:
    def _make_conn(self, scores: list[float]) -> sqlite3.Connection:
        conn = sqlite3.connect(":memory:")
        conn.execute("""
            CREATE TABLE sg_red_cell_assessments (
                id TEXT PRIMARY KEY,
                coa_id TEXT NOT NULL,
                vulnerability_score REAL NOT NULL DEFAULT 0,
                counter_probability REAL NOT NULL DEFAULT 0,
                generated_at TEXT NOT NULL
            )""")
        for i, s in enumerate(scores):
            conn.execute(
                "INSERT INTO sg_red_cell_assessments VALUES (?,?,?,?,?)",
                (f"rc-{i:04d}", f"coa-{i}", s, 0.5, f"2026-01-{i % 28 + 1:02d}T00:00:00Z"),
            )
        conn.commit()
        return conn

    def test_returns_all_scores_when_within_window(self):
        conn = self._make_conn([3.0, 7.5, 5.2])
        with patch.object(red_cell, "get_connection", return_value=conn), \
             patch.object(red_cell, "is_pg", return_value=False):
            scores = red_cell._fetch_recent_vuln_scores(window=10)
        assert set(scores) == {3.0, 7.5, 5.2}

    def test_respects_window_limit(self):
        conn = self._make_conn([float(i) for i in range(20)])
        with patch.object(red_cell, "get_connection", return_value=conn), \
             patch.object(red_cell, "is_pg", return_value=False):
            scores = red_cell._fetch_recent_vuln_scores(window=5)
        assert len(scores) == 5

    def test_returns_empty_list_on_db_error(self):
        bad_conn = MagicMock()
        bad_conn.execute.side_effect = Exception("table missing")
        with patch.object(red_cell, "get_connection", return_value=bad_conn), \
             patch.object(red_cell, "is_pg", return_value=False):
            scores = red_cell._fetch_recent_vuln_scores(window=10)
        assert scores == []

    def test_returns_list_of_floats(self):
        conn = self._make_conn([4.5, 6.1, 8.0])
        with patch.object(red_cell, "get_connection", return_value=conn), \
             patch.object(red_cell, "is_pg", return_value=False):
            scores = red_cell._fetch_recent_vuln_scores(window=10)
        assert all(isinstance(s, float) for s in scores)


# ── _compute_anomaly_threshold ────────────────────────────────────────────────

class TestComputeAnomalyThreshold:
    def _cfg(self, **overrides):
        ad = {
            "enabled": True,
            "min_records": 5,
            "baseline_window": 20,
            "zscore_k": 1.0,
            "min_floor": 5.0,
            "max_cap": 9.5,
        }
        ad.update(overrides)
        return {"red_cell": {"vuln_threshold": 7.0, "anomaly_detection": ad}}

    def test_static_fallback_when_no_history(self):
        with patch.object(red_cell, "_fetch_recent_vuln_scores", return_value=[]):
            result = red_cell._compute_anomaly_threshold(self._cfg())
        assert result == pytest.approx(7.0)

    def test_static_fallback_when_insufficient_records(self):
        with patch.object(red_cell, "_fetch_recent_vuln_scores", return_value=[5.0, 6.0, 7.0]):
            result = red_cell._compute_anomaly_threshold(self._cfg(min_records=10))
        assert result == pytest.approx(7.0)

    def test_dynamic_threshold_is_mean_plus_k_std(self):
        scores = [4.0, 5.0, 6.0, 7.0, 8.0]
        with patch.object(red_cell, "_fetch_recent_vuln_scores", return_value=scores):
            result = red_cell._compute_anomaly_threshold(self._cfg(min_records=3, zscore_k=1.0))
        mean = sum(scores) / len(scores)
        std = math.sqrt(sum((s - mean) ** 2 for s in scores) / len(scores))
        expected = max(5.0, min(9.5, mean + 1.0 * std))
        assert result == pytest.approx(expected, abs=0.01)

    def test_clamps_to_min_floor(self):
        scores = [1.0, 1.0, 1.0, 1.0, 1.0]
        with patch.object(red_cell, "_fetch_recent_vuln_scores", return_value=scores):
            result = red_cell._compute_anomaly_threshold(self._cfg(min_records=3, zscore_k=0.0, min_floor=5.0))
        assert result >= 5.0

    def test_clamps_to_max_cap(self):
        scores = [9.0, 9.2, 9.1, 9.3, 9.0]
        with patch.object(red_cell, "_fetch_recent_vuln_scores", return_value=scores):
            result = red_cell._compute_anomaly_threshold(self._cfg(min_records=3, zscore_k=10.0, max_cap=9.5))
        assert result <= 9.5

    def test_handles_zero_std(self):
        scores = [7.0, 7.0, 7.0, 7.0, 7.0]
        with patch.object(red_cell, "_fetch_recent_vuln_scores", return_value=scores):
            result = red_cell._compute_anomaly_threshold(self._cfg(min_records=3))
        assert result == pytest.approx(max(5.0, min(9.5, 7.0)))

    def test_disabled_uses_static_threshold(self):
        scores = [5.0, 6.0, 7.0, 8.0, 9.0, 5.0, 6.0]
        with patch.object(red_cell, "_fetch_recent_vuln_scores", return_value=scores):
            result = red_cell._compute_anomaly_threshold(self._cfg(enabled=False))
        assert result == pytest.approx(7.0)

    def test_uses_zscore_k_from_config(self):
        scores = [5.0, 6.0, 7.0, 8.0, 9.0]
        mean = sum(scores) / len(scores)
        std = math.sqrt(sum((s - mean) ** 2 for s in scores) / len(scores))
        for k in [0.5, 1.0, 2.0]:
            expected = max(5.0, min(9.5, mean + k * std))
            with patch.object(red_cell, "_fetch_recent_vuln_scores", return_value=scores):
                result = red_cell._compute_anomaly_threshold(self._cfg(min_records=3, zscore_k=k))
            assert result == pytest.approx(expected, abs=0.01), f"k={k}"


# ── Config-driven thresholds in _build_counter_description ───────────────────

class TestBuildCounterDescriptionUsesConfig:
    """Severity labels must reflect the config severity bands, not hardcoded values."""

    def test_critical_label_at_config_threshold(self):
        friendly = {"a": 0.8, "b": 0.1, "c": 0.1}
        adversary = {"a": 0.1, "b": 0.45, "c": 0.45}
        desc = red_cell._build_counter_description(
            "TestCOA", friendly, adversary, score=9.0,
            severity_config={"critical": 8.5, "high": 7.0, "moderate": 4.0},
        )
        assert "CRITICAL" in desc

    def test_high_label_at_config_threshold(self):
        friendly = {"a": 0.5, "b": 0.3, "c": 0.2}
        adversary = {"a": 0.2, "b": 0.4, "c": 0.4}
        desc = red_cell._build_counter_description(
            "TestCOA", friendly, adversary, score=7.5,
            severity_config={"critical": 8.5, "high": 7.0, "moderate": 4.0},
        )
        assert "HIGH" in desc

    def test_moderate_label_at_config_threshold(self):
        friendly = {"a": 0.4, "b": 0.4, "c": 0.2}
        adversary = {"a": 0.25, "b": 0.35, "c": 0.4}
        desc = red_cell._build_counter_description(
            "TestCOA", friendly, adversary, score=5.0,
            severity_config={"critical": 8.5, "high": 7.0, "moderate": 4.0},
        )
        assert "MODERATE" in desc

    def test_low_label_below_moderate(self):
        friendly = {"a": 0.35, "b": 0.35, "c": 0.30}
        adversary = {"a": 0.33, "b": 0.33, "c": 0.34}
        desc = red_cell._build_counter_description(
            "TestCOA", friendly, adversary, score=2.0,
            severity_config={"critical": 8.5, "high": 7.0, "moderate": 4.0},
        )
        assert "LOW" in desc


# ── _calibrate_vuln_threshold — config-driven clamps ─────────────────────────

class TestCalibrateVulnThresholdConfigClamps:
    """_calibrate_vuln_threshold must use min_floor / max_cap from anomaly_detection
    config, not hardcoded 4.0 / 9.5."""

    def _cfg(self, min_floor: float = 5.0, max_cap: float = 9.5,
             zscore_threshold: float = 1.5, min_history: int = 5,
             baseline_window: int = 20) -> dict:
        return {
            "vuln_threshold": 7.0,
            "anomaly_detection": {
                "enabled": True,
                "baseline_window": baseline_window,
                "min_history": min_history,
                "zscore_threshold": zscore_threshold,
                "adapt_vuln_threshold": True,
                "min_floor": min_floor,
                "max_cap": max_cap,
            },
        }

    def _make_db_conn(self, scores: list):
        import sqlite3
        conn = sqlite3.connect(":memory:")
        conn.execute("""
            CREATE TABLE sg_red_cell_assessments (
                id TEXT PRIMARY KEY, coa_id TEXT NOT NULL,
                vulnerability_score REAL NOT NULL DEFAULT 0,
                counter_probability REAL NOT NULL DEFAULT 0,
                generated_at TEXT NOT NULL
            )""")
        for i, s in enumerate(scores):
            conn.execute(
                "INSERT INTO sg_red_cell_assessments VALUES (?,?,?,?,?)",
                (f"rc-{i:04d}", f"coa-{i}", s, 0.5,
                 f"2026-01-{i % 28 + 1:02d}T00:00:00Z"),
            )
        conn.commit()
        return conn

    def test_clamps_adaptive_to_config_min_floor(self):
        """When mean+zscore*std falls below min_floor, result == min_floor (not 4.0)."""
        # scores with non-zero std but adaptive value < 6.0
        scores = [1.0, 2.0, 1.0, 2.0, 1.0]
        conn = self._make_db_conn(scores)
        cfg = self._cfg(min_floor=6.0, max_cap=9.5, zscore_threshold=0.0, min_history=3)
        with patch.object(red_cell, "get_connection", return_value=conn), \
             patch.object(red_cell, "is_pg", return_value=False):
            result = red_cell._calibrate_vuln_threshold(cfg)
        # adaptive = mean + 0*std = ~1.4 → must clamp to min_floor=6.0, not 4.0
        assert result["threshold"] == pytest.approx(6.0, abs=0.01), (
            f"Expected threshold clamped to min_floor=6.0, got {result['threshold']}"
        )
        assert result["adapted"] is True

    def test_clamps_adaptive_to_config_max_cap(self):
        """When mean+zscore*std exceeds max_cap, result == max_cap (not 9.5)."""
        scores = [9.0, 9.2, 9.1, 9.0, 9.2]
        conn = self._make_db_conn(scores)
        cfg = self._cfg(min_floor=5.0, max_cap=8.0, zscore_threshold=10.0, min_history=3)
        with patch.object(red_cell, "get_connection", return_value=conn), \
             patch.object(red_cell, "is_pg", return_value=False):
            result = red_cell._calibrate_vuln_threshold(cfg)
        # adaptive = ~9.1 + 10*~0.089 >> 8.0 → must clamp to max_cap=8.0, not 9.5
        assert result["threshold"] == pytest.approx(8.0, abs=0.01), (
            f"Expected threshold clamped to max_cap=8.0, got {result['threshold']}"
        )
        assert result["adapted"] is True

    def test_llm_recommendation_clamped_to_config_min_floor(self):
        """LLM-recommended threshold below min_floor is clamped to config min_floor."""
        scores = [6.0, 7.0, 6.5, 7.5, 6.0]
        conn = self._make_db_conn(scores)
        cfg = self._cfg(min_floor=6.0, max_cap=9.5, zscore_threshold=1.0, min_history=3)

        llm_json = '{"verdict": "adaptive_ok", "reason": "ok", "recommended_threshold": 3.0}'

        mock_resp = MagicMock()
        mock_resp.text = llm_json
        mock_router = MagicMock()
        mock_router.complete.return_value = mock_resp

        with patch.object(red_cell, "get_connection", return_value=conn), \
             patch.object(red_cell, "is_pg", return_value=False), \
             patch("tools.genesis.reflexes.strategos.red_cell.LLMRouter",
                   return_value=mock_router, create=True), \
             patch("tools.genesis.reflexes.strategos.red_cell.LLMRequest",
                   side_effect=lambda **kw: kw, create=True):
            result = red_cell._calibrate_vuln_threshold(cfg)
        # LLM says 3.0, which is below min_floor=6.0 → must clamp to 6.0, not 4.0
        assert result["threshold"] >= 6.0, (
            f"LLM recommendation 3.0 should clamp to min_floor=6.0, got {result['threshold']}"
        )

    def test_llm_recommendation_clamped_to_config_max_cap(self):
        """LLM-recommended threshold above max_cap is clamped to config max_cap."""
        scores = [6.0, 7.0, 6.5, 7.5, 6.0]
        conn = self._make_db_conn(scores)
        cfg = self._cfg(min_floor=5.0, max_cap=8.0, zscore_threshold=1.0, min_history=3)

        llm_json = '{"verdict": "adaptive_ok", "reason": "ok", "recommended_threshold": 9.9}'

        mock_resp = MagicMock()
        mock_resp.text = llm_json
        mock_router = MagicMock()
        mock_router.complete.return_value = mock_resp

        with patch.object(red_cell, "get_connection", return_value=conn), \
             patch.object(red_cell, "is_pg", return_value=False), \
             patch("tools.genesis.reflexes.strategos.red_cell.LLMRouter",
                   return_value=mock_router, create=True), \
             patch("tools.genesis.reflexes.strategos.red_cell.LLMRequest",
                   side_effect=lambda **kw: kw, create=True):
            result = red_cell._calibrate_vuln_threshold(cfg)
        # LLM says 9.9, which is above max_cap=8.0 → must clamp to 8.0, not 9.5
        assert result["threshold"] <= 8.0, (
            f"LLM recommendation 9.9 should clamp to max_cap=8.0, got {result['threshold']}"
        )
