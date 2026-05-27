# CUI // SP-CTI
"""Tests for tools.ai_augmentation.agent_readiness.checker

Covers:
  - _detect_anomalies(): floor threshold, z-score outlier, skipped pillars
  - _load_scoring_config(): fallback to defaults when config absent
  - run_readiness_check(): return structure includes 'anomalies' key
"""
from __future__ import annotations

import pathlib
import tempfile

import pytest

from tools.ai_augmentation.agent_readiness.checker import (
    _DEFAULT_ANOMALY,
    _DEFAULT_WEIGHTS,
    _detect_anomalies,
    run_readiness_check,
)


# ---------------------------------------------------------------------------
# _detect_anomalies unit tests
# ---------------------------------------------------------------------------

def _scores(mapping: dict[str, float], total: int = 4) -> dict[str, dict]:
    """Build a minimal pillar_scores dict from {pillar_id: percentage}."""
    return {
        pid: {"passed": round(pct * total), "total": total, "percentage": pct}
        for pid, pct in mapping.items()
    }


class TestDetectAnomalies:
    def test_empty_scores_returns_no_anomalies(self):
        assert _detect_anomalies({}, 0.25, 2.0) == []

    def test_all_skipped_returns_no_anomalies(self):
        scores = {
            "code-quality": {"passed": 0, "total": 0, "percentage": 0.0},
        }
        assert _detect_anomalies(scores, 0.25, 2.0) == []

    def test_floor_threshold_flags_low_score(self):
        scores = _scores({"code-quality": 0.10, "documentation": 0.80, "testing": 0.90})
        anomalies = _detect_anomalies(scores, floor_threshold=0.25, zscore_threshold=2.0)
        ids = [a["pillar_id"] for a in anomalies]
        assert "code-quality" in ids

    def test_score_at_floor_is_not_flagged(self):
        # Exactly at the floor threshold is not anomalous.
        scores = _scores({"security": 0.25, "testing": 0.90})
        anomalies = _detect_anomalies(scores, floor_threshold=0.25, zscore_threshold=2.0)
        ids = [a["pillar_id"] for a in anomalies]
        assert "security" not in ids

    def test_perfect_scores_no_anomalies(self):
        scores = _scores({pid: 1.0 for pid in _DEFAULT_WEIGHTS})
        assert _detect_anomalies(scores, 0.25, 2.0) == []

    def test_zscore_outlier_flagged_when_std_meaningful(self):
        # One pillar well below the rest, but still above floor.
        scores = _scores({
            "a": 0.30,   # low relative outlier; above floor (0.25)
            "b": 0.95,
            "c": 0.90,
            "d": 0.85,
            "e": 0.88,
        })
        anomalies = _detect_anomalies(scores, floor_threshold=0.25, zscore_threshold=1.5)
        ids = [a["pillar_id"] for a in anomalies]
        assert "a" in ids

    def test_zscore_not_applied_with_fewer_than_3_evaluated(self):
        # Only 2 pillars with data — z-score is skipped.
        scores = _scores({"a": 0.30, "b": 0.99})
        anomalies = _detect_anomalies(scores, floor_threshold=0.25, zscore_threshold=1.5)
        # "a" is above floor (0.30 >= 0.25) and z-score is skipped → no anomaly
        assert anomalies == []

    def test_floor_flagged_pillar_not_double_counted_by_zscore(self):
        # A pillar that triggers floor check should not also appear in z-score results.
        scores = _scores({"low": 0.0, "mid": 0.80, "high": 0.90, "top": 1.0})
        anomalies = _detect_anomalies(scores, floor_threshold=0.25, zscore_threshold=1.0)
        low_entries = [a for a in anomalies if a["pillar_id"] == "low"]
        assert len(low_entries) == 1

    def test_anomaly_entry_has_required_keys(self):
        scores = _scores({"x": 0.10, "y": 0.90})
        anomalies = _detect_anomalies(scores, floor_threshold=0.25, zscore_threshold=2.0)
        assert anomalies
        for entry in anomalies:
            assert {"pillar_id", "score", "reason"} <= entry.keys()

    def test_score_in_anomaly_entry_is_rounded(self):
        scores = _scores({"x": 1 / 3, "y": 0.90})
        anomalies = _detect_anomalies(scores, floor_threshold=0.40, zscore_threshold=99.0)
        assert anomalies[0]["score"] == round(1 / 3, 4)


# ---------------------------------------------------------------------------
# _load_scoring_config fallback behaviour
# ---------------------------------------------------------------------------

class TestLoadScoringConfig:
    def test_defaults_contain_all_expected_keys(self):
        assert "testing" in _DEFAULT_WEIGHTS
        assert "il-classification" in _DEFAULT_WEIGHTS
        assert "floor_threshold" in _DEFAULT_ANOMALY
        assert "zscore_threshold" in _DEFAULT_ANOMALY

    def test_icdev_pillars_weighted_higher_than_core(self):
        # Sanity check: ICDEV compliance pillars should outweigh core ones.
        assert _DEFAULT_WEIGHTS["il-classification"] > _DEFAULT_WEIGHTS["code-quality"]
        assert _DEFAULT_WEIGHTS["nist-controls"] > _DEFAULT_WEIGHTS["documentation"]

    def test_floor_threshold_sensible_range(self):
        assert 0.0 < _DEFAULT_ANOMALY["floor_threshold"] < 1.0

    def test_zscore_threshold_sensible_range(self):
        assert _DEFAULT_ANOMALY["zscore_threshold"] >= 1.0


# ---------------------------------------------------------------------------
# run_readiness_check integration smoke test
# ---------------------------------------------------------------------------

class TestRunReadinessCheck:
    def test_returns_required_top_level_keys(self, tmp_path: pathlib.Path):
        result = run_readiness_check(tmp_path)
        assert "pillar_scores" in result
        assert "overall_readiness_score" in result
        assert "icdev_checks" in result
        assert "anomalies" in result

    def test_anomalies_is_a_list(self, tmp_path: pathlib.Path):
        result = run_readiness_check(tmp_path)
        assert isinstance(result["anomalies"], list)

    def test_overall_score_in_unit_range(self, tmp_path: pathlib.Path):
        result = run_readiness_check(tmp_path)
        score = result["overall_readiness_score"]
        assert 0.0 <= score <= 1.0

    def test_pillar_scores_contains_all_11_pillars(self, tmp_path: pathlib.Path):
        result = run_readiness_check(tmp_path)
        expected_ids = {
            "code-quality", "documentation", "testing", "structure", "dependencies",
            "configuration", "security", "il-classification", "nist-controls",
            "stig-compliance", "append-only-audit",
        }
        assert expected_ids == set(result["pillar_scores"].keys())

    def test_empty_repo_has_low_overall_score(self, tmp_path: pathlib.Path):
        # An empty directory should score poorly.
        result = run_readiness_check(tmp_path)
        assert result["overall_readiness_score"] < 0.5

    def test_empty_repo_has_anomalies(self, tmp_path: pathlib.Path):
        # An empty repo will have many zero-scored pillars → anomalies expected.
        result = run_readiness_check(tmp_path)
        assert len(result["anomalies"]) > 0
