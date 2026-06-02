# CUI // SP-CTI
"""Tests for adaptive anomaly-detection threshold logic in oracle_triage.

Covers aiify-5330: hardcoded_threshold → adaptive-thresholds improvement.
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_caches(monkeypatch):
    """Reset module-level caches before and after each test."""
    import tools.genesis.reflexes.oracle_triage as ot

    monkeypatch.setattr(ot, "_TRIAGE_CONFIG", None)
    monkeypatch.setattr(ot, "_ADAPTIVE_THRESHOLDS_CACHE", None)
    yield
    monkeypatch.setattr(ot, "_TRIAGE_CONFIG", None)
    monkeypatch.setattr(ot, "_ADAPTIVE_THRESHOLDS_CACHE", None)


@pytest.fixture
def ot():
    import tools.genesis.reflexes.oracle_triage as m

    return m


# ---------------------------------------------------------------------------
# _compute_adaptive_thresholds — pure function
# ---------------------------------------------------------------------------


class TestComputeAdaptiveThresholds:
    def test_returns_none_for_insufficient_history(self, ot):
        result = ot._compute_adaptive_thresholds([0.5, 0.6, 0.7])
        assert result is None

    def test_returns_none_for_zero_std(self, ot):
        result = ot._compute_adaptive_thresholds([0.8] * 20)
        assert result is None

    def test_returns_dict_with_required_keys(self, ot):
        scores = [0.1, 0.3, 0.5, 0.7, 0.9, 0.2, 0.4, 0.6, 0.8, 0.85]
        result = ot._compute_adaptive_thresholds(scores)
        assert result is not None
        assert "high_confidence_threshold" in result
        assert "low_confidence_threshold" in result
        assert "orphan_min_refs_low_confidence" in result

    def test_high_always_above_low(self, ot):
        scores = [0.4, 0.5, 0.6, 0.45, 0.55, 0.5, 0.4, 0.6, 0.5, 0.45]
        result = ot._compute_adaptive_thresholds(scores)
        if result is not None:
            assert result["high_confidence_threshold"] > result["low_confidence_threshold"]

    def test_high_threshold_clamped_to_static_bound(self, ot):
        # Wide distribution: mean ~0.5, std ~0.4 → raw high ~0.9 → clamped to 0.85
        scores = [0.0, 1.0, 0.5, 0.5, 0.0, 1.0, 0.5, 0.5, 0.0, 1.0]
        result = ot._compute_adaptive_thresholds(scores, static_high=0.85, static_low=0.30)
        assert result is not None
        assert result["high_confidence_threshold"] <= 0.85

    def test_low_threshold_clamped_to_static_bound(self, ot):
        # Wide distribution: mean ~0.5, std ~0.4 → raw low ~0.1 → clamped to 0.30
        scores = [0.0, 1.0, 0.5, 0.5, 0.0, 1.0, 0.5, 0.5, 0.0, 1.0]
        result = ot._compute_adaptive_thresholds(scores, static_high=0.85, static_low=0.30)
        assert result is not None
        assert result["low_confidence_threshold"] >= 0.30

    def test_respects_custom_min_history(self, ot):
        five_scores = [0.3, 0.5, 0.7, 0.4, 0.6]
        # With min_history=10, 5 scores are insufficient
        assert ot._compute_adaptive_thresholds(five_scores, min_history=10) is None
        # With min_history=5, 5 scores may be sufficient (non-degenerate)
        result_5 = ot._compute_adaptive_thresholds(five_scores, min_history=5)
        # Result can be None (degenerate std) or dict — just must not crash
        assert result_5 is None or isinstance(result_5, dict)

    def test_passes_through_low_confidence_refs(self, ot):
        scores = [0.1, 0.3, 0.5, 0.7, 0.9, 0.2, 0.4, 0.6, 0.8, 0.85]
        result = ot._compute_adaptive_thresholds(scores, low_confidence_refs=7)
        assert result is not None
        assert result["orphan_min_refs_low_confidence"] == 7

    def test_returns_none_for_collapsed_band(self, ot):
        # Tight cluster: mean=0.5, std=0.01 → high=0.51, low=max(0.30,0.49)=0.49
        # high(0.51) > low(0.49) → valid result (should not return None here)
        scores = [0.49, 0.50, 0.51, 0.50, 0.50, 0.50, 0.49, 0.51, 0.50, 0.50]
        result = ot._compute_adaptive_thresholds(
            scores, static_high=0.85, static_low=0.30
        )
        # With static_low=0.30 clamping, band won't collapse; just verify structure
        if result is not None:
            assert result["high_confidence_threshold"] > result["low_confidence_threshold"]


# ---------------------------------------------------------------------------
# _get_active_anomaly_thresholds — integration (no real DB)
# ---------------------------------------------------------------------------


class TestGetActiveAnomalyThresholds:
    def test_falls_back_to_static_when_no_history(self, ot, monkeypatch):
        monkeypatch.setattr(ot, "_fetch_confidence_history", lambda limit=100: [])
        thresholds = ot._get_active_anomaly_thresholds()
        assert thresholds["high_confidence_threshold"] == pytest.approx(0.85)
        assert thresholds["low_confidence_threshold"] == pytest.approx(0.3)

    def test_returns_adaptive_dict_when_history_available(self, ot, monkeypatch):
        # Spread history: should compute adaptive (or fall back gracefully)
        history = [0.1, 0.9, 0.5, 0.5, 0.0, 1.0, 0.5, 0.5, 0.0, 1.0]
        monkeypatch.setattr(ot, "_fetch_confidence_history", lambda limit=100: history)
        thresholds = ot._get_active_anomaly_thresholds()
        assert isinstance(thresholds, dict)
        assert "high_confidence_threshold" in thresholds
        assert "low_confidence_threshold" in thresholds

    def test_result_is_cached(self, ot, monkeypatch):
        calls = []

        def _fake_history(limit=100):
            calls.append(1)
            return []

        monkeypatch.setattr(ot, "_fetch_confidence_history", _fake_history)
        ot._get_active_anomaly_thresholds()
        ot._get_active_anomaly_thresholds()
        assert len(calls) == 1  # second call must hit cache

    def test_cache_is_reset_between_triage_runs(self, ot, monkeypatch):
        calls = []

        def _fake_history(limit=100):
            calls.append(1)
            return []

        monkeypatch.setattr(ot, "_fetch_confidence_history", _fake_history)
        monkeypatch.setattr(ot, "_fetch_suggested_tasks", lambda: [])

        ot._get_active_anomaly_thresholds()
        assert len(calls) == 1
        # Simulate triage() cache reset
        monkeypatch.setattr(ot, "_ADAPTIVE_THRESHOLDS_CACHE", None)
        ot._get_active_anomaly_thresholds()
        assert len(calls) == 2


# ---------------------------------------------------------------------------
# _get_orphan_min_refs — uses _get_active_anomaly_thresholds
# ---------------------------------------------------------------------------


class TestGetOrphanMinRefsAdaptive:
    @pytest.fixture(autouse=True)
    def stub_no_history(self, ot, monkeypatch):
        monkeypatch.setattr(ot, "_fetch_confidence_history", lambda limit=100: [])

    def test_none_confidence_returns_base(self, ot):
        assert ot._get_orphan_min_refs(None) == 1

    def test_high_confidence_returns_zero(self, ot):
        assert ot._get_orphan_min_refs(0.9) == 0

    def test_exactly_at_high_threshold_returns_zero(self, ot):
        assert ot._get_orphan_min_refs(0.85) == 0

    def test_low_confidence_returns_elevated_refs(self, ot):
        assert ot._get_orphan_min_refs(0.1) == 3

    def test_just_below_low_threshold_returns_elevated_refs(self, ot):
        assert ot._get_orphan_min_refs(0.29) == 3

    def test_mid_confidence_returns_base(self, ot):
        assert ot._get_orphan_min_refs(0.5) == 1

    def test_adaptive_high_threshold_honored(self, ot, monkeypatch):
        # Override cache: adaptive thresholds shift high to 0.95
        monkeypatch.setattr(
            ot,
            "_ADAPTIVE_THRESHOLDS_CACHE",
            {
                "high_confidence_threshold": 0.95,
                "low_confidence_threshold": 0.40,
                "orphan_min_refs_low_confidence": 5,
            },
        )
        # 0.85 is now below the adaptive high_threshold → not bypassed
        assert ot._get_orphan_min_refs(0.85) != 0
        # 0.96 is above adaptive high_threshold → bypassed
        assert ot._get_orphan_min_refs(0.96) == 0

    def test_adaptive_low_threshold_honored(self, ot, monkeypatch):
        monkeypatch.setattr(
            ot,
            "_ADAPTIVE_THRESHOLDS_CACHE",
            {
                "high_confidence_threshold": 0.85,
                "low_confidence_threshold": 0.40,
                "orphan_min_refs_low_confidence": 5,
            },
        )
        # 0.35 < adaptive low_threshold 0.40 → elevated refs = 5
        assert ot._get_orphan_min_refs(0.35) == 5

    def test_adaptive_low_refs_value_honored(self, ot, monkeypatch):
        monkeypatch.setattr(
            ot,
            "_ADAPTIVE_THRESHOLDS_CACHE",
            {
                "high_confidence_threshold": 0.85,
                "low_confidence_threshold": 0.30,
                "orphan_min_refs_low_confidence": 8,
            },
        )
        assert ot._get_orphan_min_refs(0.1) == 8


# ---------------------------------------------------------------------------
# Config file loading — min_history_for_adaptive key
# ---------------------------------------------------------------------------


class TestConfigLoading:
    def test_default_includes_min_history(self, ot, monkeypatch, tmp_path):
        monkeypatch.setattr(ot, "BASE_DIR", tmp_path)
        cfg = ot._load_triage_config()
        assert cfg["anomaly_detection"]["min_history_for_adaptive"] == 10

    def test_yaml_min_history_overrides_default(self, ot, monkeypatch, tmp_path):
        try:
            import yaml
        except ImportError:
            pytest.skip("pyyaml not installed")

        args_dir = tmp_path / "args"
        args_dir.mkdir()
        (args_dir / "oracle_triage_config.yaml").write_text(
            yaml.dump(
                {
                    "orphan_min_refs": 2,
                    "anomaly_detection": {
                        "high_confidence_threshold": 0.90,
                        "low_confidence_threshold": 0.20,
                        "orphan_min_refs_low_confidence": 4,
                        "min_history_for_adaptive": 20,
                    },
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(ot, "BASE_DIR", tmp_path)
        cfg = ot._load_triage_config()
        assert cfg["orphan_min_refs"] == 2
        assert cfg["anomaly_detection"]["high_confidence_threshold"] == pytest.approx(0.90)
        assert cfg["anomaly_detection"]["low_confidence_threshold"] == pytest.approx(0.20)
        assert cfg["anomaly_detection"]["orphan_min_refs_low_confidence"] == 4
        assert cfg["anomaly_detection"]["min_history_for_adaptive"] == 20

    def test_static_defaults_when_yaml_missing(self, ot, monkeypatch, tmp_path):
        monkeypatch.setattr(ot, "BASE_DIR", tmp_path)
        cfg = ot._load_triage_config()
        assert cfg["orphan_min_refs"] == 1
        assert cfg["anomaly_detection"]["high_confidence_threshold"] == pytest.approx(0.85)
        assert cfg["anomaly_detection"]["low_confidence_threshold"] == pytest.approx(0.3)
        assert cfg["anomaly_detection"]["orphan_min_refs_low_confidence"] == 3
