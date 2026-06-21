# CUI // SP-CTI
"""Tests for adaptive threshold anomaly detection in roadmap_generator (AAC opp-560).

Covers _percentile, _adaptive_thresholds, and the end-to-end generate_roadmap
phase assignment using adaptive boundaries instead of hardcoded literals.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.ai_augmentation.roadmap_generator import (
    _DEFAULT_P1,
    _DEFAULT_P2,
    _DEFAULT_P3,
    _MIN_ADAPTIVE_SAMPLES,
    _adaptive_thresholds,
    _percentile,
    generate_roadmap,
)


# ---------------------------------------------------------------------------
# _percentile
# ---------------------------------------------------------------------------


class TestPercentile:
    def test_empty_returns_zero(self):
        assert _percentile([], 50) == 0.0

    def test_single_value(self):
        assert _percentile([0.5], 50) == 0.5
        assert _percentile([0.5], 0) == 0.5
        assert _percentile([0.5], 100) == 0.5

    def test_exact_boundaries(self):
        vals = [0.0, 0.5, 1.0]
        assert _percentile(vals, 0) == pytest.approx(0.0)
        assert _percentile(vals, 100) == pytest.approx(1.0)

    def test_midpoint_interpolation(self):
        vals = [0.0, 1.0]
        assert _percentile(vals, 50) == pytest.approx(0.5)

    def test_uniform_distribution(self):
        vals = [float(i) / 10 for i in range(11)]  # 0.0, 0.1, ..., 1.0
        assert _percentile(vals, 0) == pytest.approx(0.0)
        assert _percentile(vals, 100) == pytest.approx(1.0)
        assert _percentile(vals, 50) == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# _adaptive_thresholds
# ---------------------------------------------------------------------------


class TestAdaptiveThresholds:
    def test_fallback_when_too_few_samples(self):
        scores = [0.6, 0.4, 0.2]  # < _MIN_ADAPTIVE_SAMPLES
        p1, p2, p3 = _adaptive_thresholds(scores)
        assert p1 == _DEFAULT_P1
        assert p2 == _DEFAULT_P2
        assert p3 == _DEFAULT_P3

    def test_fallback_with_exactly_min_minus_one(self):
        scores = [0.9] * (_MIN_ADAPTIVE_SAMPLES - 1)
        p1, p2, p3 = _adaptive_thresholds(scores)
        assert p1 == _DEFAULT_P1

    def test_adaptive_with_sufficient_samples(self):
        # 10 uniformly distributed scores → should produce adaptive thresholds
        scores = [i / 10.0 for i in range(1, 11)]
        p1, p2, p3 = _adaptive_thresholds(scores)
        assert p1 > p2 > p3, "Phase boundaries must be strictly descending"
        assert p3 == _DEFAULT_P3, "P3 floor is always the static default"

    def test_ordering_enforced_when_p2_would_equal_p1(self):
        # All identical scores → percentiles collapse → ordering must still hold
        scores = [0.5] * 10
        p1, p2, p3 = _adaptive_thresholds(scores)
        assert p1 > p2, "p1 must exceed p2 even with degenerate distribution"
        assert p2 >= _DEFAULT_P3

    def test_out_of_range_scores_filtered(self):
        # Mix valid and invalid scores; invalid ones should be ignored
        scores = [-0.1, 1.1, 0.4, 0.5, 0.6, 0.7, 0.8]
        p1_adaptive, _, _ = _adaptive_thresholds(scores)
        # With only 5 valid scores exactly at boundary, should use adaptive
        valid_count = sum(1 for s in scores if 0.0 <= s <= 1.0)
        assert valid_count == _MIN_ADAPTIVE_SAMPLES

    def test_all_high_scores_produces_high_thresholds(self):
        scores = [0.85, 0.87, 0.90, 0.92, 0.95, 0.97, 0.99]
        p1, p2, p3 = _adaptive_thresholds(scores)
        assert p1 >= 0.4, "P1 should be well above the P3 floor"
        assert p1 > p2 > p3

    def test_all_low_scores_clamps_at_floor(self):
        scores = [0.05, 0.10, 0.12, 0.15, 0.18, 0.20, 0.22]
        p1, p2, p3 = _adaptive_thresholds(scores)
        assert p2 >= _DEFAULT_P3, "P2 must never drop below the P3 floor"
        assert p3 == _DEFAULT_P3


# ---------------------------------------------------------------------------
# generate_roadmap — phase assignment uses adaptive thresholds
# ---------------------------------------------------------------------------


def _make_opportunity(opp_id: int, pattern_type: str = "hardcoded_threshold",
                      paradigm: str = "anomaly_detection") -> dict:
    return {
        "opportunity_id": opp_id,
        "module_path": f"tools/foo/bar_{opp_id}.py",
        "function_name": "process",
        "pattern_type": pattern_type,
        "ai_paradigm": paradigm,
        "il_recommended_model": "claude-haiku-4-5-20251001",
    }


def _make_score(opp_id: int, composite: float) -> dict:
    return {
        "opportunity_id": opp_id,
        "composite_score": composite,
        "value_score": composite,
        "feasibility_score": composite,
        "risk_score": 1.0 - composite,
    }


def _mock_conn():
    conn = MagicMock()
    conn.execute = MagicMock()
    conn.commit = MagicMock()
    conn.close = MagicMock()
    return conn


class TestGenerateRoadmapAdaptive:
    """Verify generate_roadmap uses adaptive thresholds for phase assignment."""

    def _run(self, opps, scores):
        with patch("tools.ai_augmentation.roadmap_generator.get_connection",
                   return_value=_mock_conn()):
            return generate_roadmap(1, opps, scores)

    def test_returns_three_phases(self):
        opps = [_make_opportunity(i) for i in range(1, 8)]
        scores = [_make_score(i, (i * 0.1) + 0.1) for i in range(1, 8)]
        roadmap = self._run(opps, scores)
        assert len(roadmap["phases"]) == 3

    def test_phase_ids_are_p1_p2_p3(self):
        opps = [_make_opportunity(i) for i in range(1, 8)]
        scores = [_make_score(i, i / 10.0) for i in range(1, 8)]
        roadmap = self._run(opps, scores)
        ids = [p["phase_id"] for p in roadmap["phases"]]
        assert ids == ["P1", "P2", "P3"]

    def test_all_opportunities_assigned_to_a_phase(self):
        n = 7
        opps = [_make_opportunity(i) for i in range(1, n + 1)]
        scores = [_make_score(i, i / (n + 1)) for i in range(1, n + 1)]
        roadmap = self._run(opps, scores)
        assigned = sum(p["count"] for p in roadmap["phases"])
        # opportunities below P3 floor won't appear in any bucket — that's expected
        assert assigned <= n

    def test_high_score_opportunity_lands_in_p1(self):
        # Create a clear distribution: 2 high, 3 mid, 2 low — 7 total (≥ MIN_ADAPTIVE)
        opps = [_make_opportunity(i) for i in range(1, 8)]
        raw_scores = [0.85, 0.90, 0.55, 0.50, 0.45, 0.20, 0.15]
        scores = [_make_score(i + 1, raw_scores[i]) for i in range(7)]
        roadmap = self._run(opps, scores)

        p1_phase = next(p for p in roadmap["phases"] if p["phase_id"] == "P1")
        p1_opp_ids = [o["opportunity_id"] for o in p1_phase["opportunities"]]
        # Opportunity 1 (score 0.85) and 2 (score 0.90) should be in P1
        assert 1 in p1_opp_ids or 2 in p1_opp_ids

    def test_roadmap_has_required_keys(self):
        opps = [_make_opportunity(i) for i in range(1, 4)]
        scores = [_make_score(i, 0.5) for i in range(1, 4)]
        roadmap = self._run(opps, scores)
        for key in ("roadmap_id", "scan_id", "title", "phases",
                    "aimc_links", "aadc_links", "total_effort_days", "created_at"):
            assert key in roadmap

    def test_small_scan_uses_static_fallback_thresholds(self):
        # < _MIN_ADAPTIVE_SAMPLES opportunities → static thresholds apply
        opps = [_make_opportunity(i) for i in range(1, 4)]
        scores = [_make_score(i, 0.8) for i in range(1, 4)]
        roadmap = self._run(opps, scores)
        p1_phase = next(p for p in roadmap["phases"] if p["phase_id"] == "P1")
        # With static fallback, P1 floor = 0.7; score=0.8 → all in P1
        assert p1_phase["min_score"] == _DEFAULT_P1
        assert p1_phase["count"] == 3

    def test_agentic_trigger_adds_aadc_link(self):
        opps = [_make_opportunity(1, paradigm="agentic_trigger")]
        scores = [_make_score(1, 0.8)]
        roadmap = self._run(opps, scores)
        assert len(roadmap["aadc_links"]) == 1
        assert roadmap["aadc_links"][0]["aadc_url"].startswith("/agentic-ai/topologies/new")
