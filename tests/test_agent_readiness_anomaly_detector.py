# CUI // SP-CTI
"""Tests for ThresholdAnomalyDetector in _base.py."""
from __future__ import annotations

import json
import pathlib

import pytest

from tools.ai_augmentation.agent_readiness.pillars._base import (
    ThresholdAnomalyDetector,
    _mean_std,
    get_anomaly_detector,
)


# ---------------------------------------------------------------------------
# _mean_std helper
# ---------------------------------------------------------------------------

class TestMeanStd:
    def test_single_value(self):
        mean, std = _mean_std([5.0])
        assert mean == 5.0
        assert std == 0.0

    def test_uniform_values(self):
        mean, std = _mean_std([3.0, 3.0, 3.0])
        assert mean == 3.0
        assert std == 0.0

    def test_known_values(self):
        mean, std = _mean_std([2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0])
        assert abs(mean - 5.0) < 1e-9
        assert abs(std - 2.0) < 1e-9


# ---------------------------------------------------------------------------
# ThresholdAnomalyDetector — core behaviour
# ---------------------------------------------------------------------------

class TestThresholdAnomalyDetectorBasic:
    def test_returns_default_when_insufficient_samples(self):
        det = ThresholdAnomalyDetector()
        for v in [0.9, 0.85, 0.92]:  # only 3 < MIN_SAMPLES=5
            det.observe("ratio", v)
        assert det.suggest_threshold("ratio", 0.8) == 0.8

    def test_returns_default_for_unknown_metric(self):
        det = ThresholdAnomalyDetector()
        assert det.suggest_threshold("unknown.metric", 0.5) == 0.5

    def test_adapts_threshold_upward_when_observations_are_high(self):
        det = ThresholdAnomalyDetector()
        for _ in range(20):
            det.observe("ratio", 0.95)
        threshold = det.suggest_threshold("ratio", 0.8)
        # Mean=0.95, std≈0 → adaptive≈0.95, clamped at max=0.8*2=1.6 → ~0.95
        assert threshold > 0.8

    def test_adapts_threshold_downward_when_observations_are_low(self):
        det = ThresholdAnomalyDetector()
        for _ in range(20):
            det.observe("ratio", 0.55)
        threshold = det.suggest_threshold("ratio", 0.8)
        # Mean=0.55, std≈0 → adaptive≈0.55, clamped at min=0.8*0.5=0.4 → 0.55
        assert threshold < 0.8

    def test_clamp_prevents_threshold_above_2x_default(self):
        det = ThresholdAnomalyDetector()
        for _ in range(20):
            det.observe("ratio", 0.99)
        threshold = det.suggest_threshold("ratio", 0.5)
        assert threshold <= 0.5 * 2.0

    def test_clamp_prevents_threshold_below_half_default(self):
        det = ThresholdAnomalyDetector()
        for _ in range(20):
            det.observe("ratio", 0.01)
        threshold = det.suggest_threshold("ratio", 0.8)
        assert threshold >= 0.8 * 0.5

    def test_is_anomaly_returns_false_when_above_adaptive_threshold(self):
        det = ThresholdAnomalyDetector()
        # Spread of observations so std > 0; adaptive ≈ mean - sigma*std < mean
        for v in [0.80, 0.82, 0.84, 0.86, 0.88, 0.90, 0.92, 0.94, 0.96, 0.98]:
            det.observe("ratio", v)
        # mean=0.89, std≈0.056 → adaptive≈0.89-1.5*0.056≈0.806; 0.85 > 0.806 → not anomaly
        assert not det.is_anomaly("ratio", 0.85, 0.8)

    def test_is_anomaly_returns_true_when_below_adaptive_threshold(self):
        det = ThresholdAnomalyDetector()
        # Tight cluster at 0.95; adaptive threshold ≈ 0.95 (clamped to 0.8*2=1.6 → 0.95)
        for _ in range(10):
            det.observe("ratio", 0.95)
        assert det.is_anomaly("ratio", 0.40, 0.8)

    def test_is_anomaly_falls_back_to_default_when_sparse(self):
        det = ThresholdAnomalyDetector()
        det.observe("ratio", 0.99)
        assert not det.is_anomaly("ratio", 0.85, 0.8)   # 0.85 >= 0.8 (default)
        assert det.is_anomaly("ratio", 0.75, 0.8)        # 0.75 < 0.8 (default)


# ---------------------------------------------------------------------------
# ThresholdAnomalyDetector — stats()
# ---------------------------------------------------------------------------

class TestThresholdAnomalyDetectorStats:
    def test_stats_empty_metric(self):
        det = ThresholdAnomalyDetector()
        s = det.stats("no.such.metric")
        assert s["n"] == 0
        assert s["mean"] is None

    def test_stats_populated_metric(self):
        det = ThresholdAnomalyDetector()
        for v in [0.7, 0.8, 0.9]:
            det.observe("m", v)
        s = det.stats("m")
        assert s["n"] == 3
        assert abs(s["mean"] - 0.8) < 1e-6
        assert s["min"] == pytest.approx(0.7)
        assert s["max"] == pytest.approx(0.9)


# ---------------------------------------------------------------------------
# ThresholdAnomalyDetector — rolling window
# ---------------------------------------------------------------------------

class TestThresholdAnomalyDetectorWindow:
    def test_window_trims_to_max_size(self):
        det = ThresholdAnomalyDetector()
        det.WINDOW_SIZE = 5
        for i in range(10):
            det.observe("m", float(i))
        assert det.stats("m")["n"] == 5
        assert det.stats("m")["min"] == pytest.approx(5.0)


# ---------------------------------------------------------------------------
# ThresholdAnomalyDetector — persistence
# ---------------------------------------------------------------------------

class TestThresholdAnomalyDetectorPersistence:
    def test_flush_writes_json_to_store(self, tmp_path):
        store = tmp_path / "thresholds.json"
        det = ThresholdAnomalyDetector(store_path=store)
        for v in [0.8, 0.9, 0.85]:
            det.observe("ratio", v)
        det.flush()
        assert store.exists()
        data = json.loads(store.read_text(encoding="utf-8"))
        assert "ratio" in data
        assert len(data["ratio"]) == 3

    def test_reload_restores_observations(self, tmp_path):
        store = tmp_path / "thresholds.json"
        det1 = ThresholdAnomalyDetector(store_path=store)
        for v in [0.9, 0.91, 0.89, 0.92, 0.88]:
            det1.observe("ratio", v)
        det1.flush()

        det2 = ThresholdAnomalyDetector(store_path=store)
        assert det2.stats("ratio")["n"] == 5

    def test_flush_noop_when_no_store_path(self):
        det = ThresholdAnomalyDetector(store_path=None)
        det.observe("m", 0.5)
        det.flush()  # should not raise

    def test_load_ignores_corrupt_json(self, tmp_path):
        store = tmp_path / "bad.json"
        store.write_text("not json {{", encoding="utf-8")
        det = ThresholdAnomalyDetector(store_path=store)
        assert det.stats("any")["n"] == 0

    def test_no_dirty_write_when_no_observations(self, tmp_path):
        store = tmp_path / "empty.json"
        det = ThresholdAnomalyDetector(store_path=store)
        det.flush()
        assert not store.exists()


# ---------------------------------------------------------------------------
# get_anomaly_detector singleton
# ---------------------------------------------------------------------------

class TestGetAnomalyDetector:
    def test_returns_same_instance(self, monkeypatch):
        import tools.ai_augmentation.agent_readiness.pillars._base as mod
        monkeypatch.setattr(mod, "_DETECTOR", None)
        d1 = get_anomaly_detector()
        d2 = get_anomaly_detector()
        assert d1 is d2

    def test_resets_cleanly_on_monkeypatch(self, monkeypatch):
        import tools.ai_augmentation.agent_readiness.pillars._base as mod
        monkeypatch.setattr(mod, "_DETECTOR", None)
        det = get_anomaly_detector()
        assert isinstance(det, ThresholdAnomalyDetector)


# ---------------------------------------------------------------------------
# Integration: dependencies pillar uses adaptive thresholds
# ---------------------------------------------------------------------------

class TestDependenciesAdaptiveThreshold:
    """Verify that _check_pinned_versions records observations and uses the detector."""

    def _make_detector(self, observations: list[float]) -> ThresholdAnomalyDetector:
        det = ThresholdAnomalyDetector()
        for v in observations:
            det.observe("dependencies.pin_ratio", v)
        return det

    def _write(self, tmp_path: pathlib.Path, content: str) -> None:
        (tmp_path / "requirements.txt").write_text(content, encoding="utf-8")

    def test_adaptive_threshold_tightens_when_history_is_high(self, tmp_path, monkeypatch):
        import tools.ai_augmentation.agent_readiness.pillars.dependencies as dep_mod
        # Historical observations cluster at 0.95 → adaptive threshold tightens above 0.8
        det = self._make_detector([0.95] * 20)
        monkeypatch.setattr(dep_mod, "_load_thresholds", lambda: {"max_age_months": 6, "min_pin_ratio": 0.8})
        monkeypatch.setattr(dep_mod, "get_anomaly_detector", lambda: det)
        # 85% pinned — passes the 0.8 static threshold but may fail the tighter adaptive one
        self._write(tmp_path, "a==1\nb==2\nc==3\nd==4\ne==5\nf==6\ng==7\nh\ni\nj\n")
        from tools.ai_augmentation.agent_readiness.pillars.dependencies import _check_pinned_versions
        result = _check_pinned_versions(tmp_path)
        # adaptive threshold ≈ 0.95 (clamped at 0.8*2=1.6); 7/10=0.7 < 0.95 → fail
        assert not result.passed

    def test_adaptive_threshold_relaxes_when_history_is_low(self, tmp_path, monkeypatch):
        import tools.ai_augmentation.agent_readiness.pillars.dependencies as dep_mod
        # Historical observations cluster at 0.55 → adaptive threshold relaxes below 0.8
        det = self._make_detector([0.55] * 20)
        monkeypatch.setattr(dep_mod, "_load_thresholds", lambda: {"max_age_months": 6, "min_pin_ratio": 0.8})
        monkeypatch.setattr(dep_mod, "get_anomaly_detector", lambda: det)
        # 60% pinned — would fail the 0.8 static threshold but passes relaxed adaptive
        self._write(tmp_path, "a==1\nb==2\nc==3\nd\ne\n")  # 3/5 = 60%
        from tools.ai_augmentation.agent_readiness.pillars.dependencies import _check_pinned_versions
        result = _check_pinned_versions(tmp_path)
        # adaptive threshold ≈ 0.55 (< 0.8); 3/5=0.6 >= 0.55 → pass
        assert result.passed

    def test_detector_observe_called_on_check(self, tmp_path, monkeypatch):
        import tools.ai_augmentation.agent_readiness.pillars.dependencies as dep_mod
        det = ThresholdAnomalyDetector()
        monkeypatch.setattr(dep_mod, "_load_thresholds", lambda: {"max_age_months": 6, "min_pin_ratio": 0.8})
        monkeypatch.setattr(dep_mod, "get_anomaly_detector", lambda: det)
        self._write(tmp_path, "a==1\nb==2\n")
        from tools.ai_augmentation.agent_readiness.pillars.dependencies import _check_pinned_versions
        _check_pinned_versions(tmp_path)
        # Observation should have been recorded
        assert det.stats("dependencies.pin_ratio")["n"] == 1
