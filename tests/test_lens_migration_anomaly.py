# CUI // SP-CTI
"""Tests for MigrationLens anomaly detection — IQR thresholds and confidence levels."""

import pytest
from tools.oracle.lenses.lens_migration import _MigrationAnomalyThresholds


class TestMigrationAnomalyThresholds:
    """IQR fence math and config-driven multiplier."""

    def _thresholds(self, scores=None, readiness=None, sources=None, orphans=None, cfg=None):
        return _MigrationAnomalyThresholds(
            scores=scores or [],
            readiness=readiness or [],
            source_counts=sources or [],
            orphan_counts=orphans or [],
            cfg=cfg,
        )

    # --- fallback path (below min_samples) ---

    def test_fallback_score_when_too_few_samples(self):
        t = self._thresholds(scores=[40.0, 50.0], cfg={"min_samples": 5, "fallback_score": 60.0})
        assert t.score_is_anomaly(59.0)
        assert not t.score_is_anomaly(60.0)

    def test_fallback_readiness_when_too_few_samples(self):
        t = self._thresholds(readiness=[70.0], cfg={"min_samples": 5, "fallback_readiness": 65.0})
        assert t.readiness_is_anomaly(64.9)
        assert not t.readiness_is_anomaly(65.0)

    def test_fallback_source_count_when_too_few_samples(self):
        t = self._thresholds(sources=[1, 2], cfg={"min_samples": 5, "fallback_source_count": 5.0})
        assert t.source_count_is_anomaly(5)
        assert not t.source_count_is_anomaly(4)

    def test_fallback_orphan_count_when_too_few_samples(self):
        t = self._thresholds(orphans=[0, 1], cfg={"min_samples": 5, "fallback_orphan_count": 3.0})
        assert t.orphan_count_is_anomaly(3)
        assert not t.orphan_count_is_anomaly(2)

    # --- IQR statistical path (sufficient samples) ---

    def test_iqr_score_low_outlier_detected(self):
        # Scores 60–100; IQR fence should flag 5 as low
        scores = [70.0, 75.0, 80.0, 85.0, 90.0, 95.0, 100.0]
        t = self._thresholds(scores=scores, cfg={"min_samples": 5, "fallback_score": 60.0})
        assert t.score_is_anomaly(5.0)

    def test_iqr_score_normal_not_flagged(self):
        scores = [70.0, 75.0, 80.0, 85.0, 90.0, 95.0, 100.0]
        t = self._thresholds(scores=scores, cfg={"min_samples": 5, "fallback_score": 60.0})
        assert not t.score_is_anomaly(72.0)

    def test_iqr_orphan_high_outlier_detected(self):
        # Orphan counts 0–3; IQR fence should flag 50 as high
        orphans = [0, 0, 1, 1, 2, 2, 3]
        t = self._thresholds(orphans=orphans, cfg={"min_samples": 5, "fallback_orphan_count": 3.0})
        assert t.orphan_count_is_anomaly(50)

    def test_iqr_orphan_normal_not_flagged(self):
        orphans = [0, 0, 1, 1, 2, 2, 3]
        t = self._thresholds(orphans=orphans, cfg={"min_samples": 5, "fallback_orphan_count": 3.0})
        assert not t.orphan_count_is_anomaly(2)

    # --- config-driven IQR multiplier ---

    def test_custom_iqr_multiplier_tightens_fence(self):
        # With multiplier=0.0 the fence is exactly Q1/Q3; anything outside IQR is an outlier.
        scores = [60.0, 70.0, 80.0, 90.0, 100.0]
        cfg_tight = {"min_samples": 5, "iqr_multiplier": 0.0, "fallback_score": 60.0}
        t_tight = _MigrationAnomalyThresholds(scores=scores, readiness=[], source_counts=[], orphan_counts=[], cfg=cfg_tight)
        # Q1 = 70.0 with multiplier=0.0 → lower fence = 70.0; 65.0 is below
        assert t_tight.score_is_anomaly(65.0)

    def test_custom_iqr_multiplier_loosens_fence(self):
        # With multiplier=10.0 the fence is very loose; nothing realistic is flagged.
        scores = [60.0, 70.0, 80.0, 90.0, 100.0]
        cfg_loose = {"min_samples": 5, "iqr_multiplier": 10.0, "fallback_score": 60.0}
        t_loose = _MigrationAnomalyThresholds(scores=scores, readiness=[], source_counts=[], orphan_counts=[], cfg=cfg_loose)
        assert not t_loose.score_is_anomaly(1.0)

    def test_default_iqr_multiplier_is_1_5(self):
        # No iqr_multiplier in cfg → defaults to 1.5
        scores = [70.0, 75.0, 80.0, 85.0, 90.0, 95.0, 100.0]
        t = self._thresholds(scores=scores, cfg={"min_samples": 5, "fallback_score": 60.0})
        assert t._iqr_multiplier == 1.5

    # --- percentile helper ---

    def test_percentile_single_element(self):
        t = self._thresholds()
        assert t._percentile([42.0], 0.25) == 42.0
        assert t._percentile([42.0], 0.75) == 42.0

    def test_percentile_empty(self):
        t = self._thresholds()
        assert t._percentile([], 0.5) == 0.0
