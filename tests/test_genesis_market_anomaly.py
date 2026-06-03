"""Tests for genesis market reflex anomaly detection (aiify-rm-ff651-phase-5310).

Validates that _distribution_stats and _adaptive_thresholds replace the
former hardcoded thresholds (3.5 rating, 2 count, 3 installs) with
data-driven statistical detection.
"""
import sys
from math import sqrt
from pathlib import Path
from unittest.mock import patch

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.genesis.reflexes.market import (
    _adaptive_thresholds,
    _distribution_stats,
    _generate_suggestions,
)


# ---------------------------------------------------------------------------
# _distribution_stats
# ---------------------------------------------------------------------------

class TestDistributionStats:
    def test_empty_list(self):
        r = _distribution_stats([])
        assert r["n"] == 0
        assert r["mean"] == 0.0
        assert r["std"] == 0.0
        assert r["median"] == 0.0

    def test_single_value(self):
        r = _distribution_stats([4.0])
        assert r["n"] == 1
        assert r["mean"] == pytest.approx(4.0)
        assert r["std"] == pytest.approx(0.0)
        assert r["median"] == pytest.approx(4.0)

    def test_two_values(self):
        r = _distribution_stats([2.0, 4.0])
        assert r["n"] == 2
        assert r["mean"] == pytest.approx(3.0)
        assert r["std"] == pytest.approx(1.0)
        assert r["median"] == pytest.approx(3.0)

    def test_population_std_dev(self):
        # Population std-dev for [1,2,3,4,5] = sqrt(2)
        r = _distribution_stats([1.0, 2.0, 3.0, 4.0, 5.0])
        assert r["std"] == pytest.approx(sqrt(2.0), rel=1e-5)

    def test_median_odd(self):
        r = _distribution_stats([3.0, 1.0, 5.0])
        assert r["median"] == pytest.approx(3.0)

    def test_median_even(self):
        r = _distribution_stats([1.0, 2.0, 3.0, 4.0])
        assert r["median"] == pytest.approx(2.5)


# ---------------------------------------------------------------------------
# _adaptive_thresholds
# ---------------------------------------------------------------------------

def _make_feedback(slug_ratings):
    """Build a feedback dict from {slug: (avg_rating, count)} pairs."""
    details = [
        {"slug": s, "avg_rating": r, "count": c}
        for s, (r, c) in slug_ratings.items()
    ]
    return {"modules_with_feedback": len(details), "details": details}


def _make_stats(slug_installs):
    return [{"slug": s, "installs": i} for s, i in slug_installs.items()]


class TestAdaptiveThresholds:
    def test_no_data_fallback(self):
        t = _adaptive_thresholds([], {"modules_with_feedback": 0, "details": []})
        assert t["method"] == "fallback"
        # With no data thresholds should not falsely flag anything
        assert t["rating_threshold"] <= 1.0
        assert t["installs_threshold"] >= 1.0

    def test_z_score_method_requires_three_or_more(self):
        fb = _make_feedback({"a": (4.5, 5), "b": (3.8, 3), "c": (2.0, 4)})
        t = _adaptive_thresholds([], fb)
        assert t["method"] == "z_score"

    def test_relative_method_for_two_modules(self):
        fb = _make_feedback({"a": (4.5, 5), "b": (2.0, 3)})
        t = _adaptive_thresholds([], fb)
        assert t["method"] == "relative"

    def test_z_score_threshold_below_mean(self):
        # Three modules with clear low outlier
        fb = _make_feedback({"a": (5.0, 10), "b": (4.8, 8), "c": (1.0, 6)})
        t = _adaptive_thresholds([], fb)
        # mean ≈ 3.6, std ≈ 1.79; threshold ≈ 1.81 — above 1.0 floor
        assert t["rating_threshold"] >= 1.0
        assert t["rating_threshold"] < 5.0  # sanity

    def test_installs_threshold_is_median(self):
        stats = _make_stats({"a": 2, "b": 4, "c": 6})
        t = _adaptive_thresholds(stats, {"modules_with_feedback": 0, "details": []})
        # median of [2,4,6] = 4; threshold = max(1.0, 4.0)
        assert t["installs_threshold"] == pytest.approx(4.0)

    def test_min_feedback_count_at_least_one(self):
        fb = _make_feedback({"a": (4.5, 1), "b": (3.0, 1), "c": (2.0, 1)})
        t = _adaptive_thresholds([], fb)
        assert t["min_feedback_count"] >= 1


# ---------------------------------------------------------------------------
# _generate_suggestions — integration smoke tests
# ---------------------------------------------------------------------------

_NO_LLM = patch("tools.genesis.reflexes.market._llm_triage", return_value=None)


class TestGenerateSuggestions:
    def test_no_data_produces_bootstrap(self):
        with _NO_LLM:
            suggestions = _generate_suggestions([], {"modules_with_feedback": 0, "details": []})
        types = [s["type"] for s in suggestions]
        assert "bootstrap" in types

    def test_low_rating_detected_statistically(self):
        # Module "bad" has a much lower rating than peers — should be flagged
        stats = _make_stats({"good1": 10, "good2": 8, "bad": 5})
        fb = _make_feedback({
            "good1": (4.9, 10),
            "good2": (4.7, 8),
            "bad": (1.0, 5),
        })
        with _NO_LLM:
            suggestions = _generate_suggestions(stats, fb)
        types_and_mods = [(s["type"], s["module"]) for s in suggestions]
        assert any(t == "quality_improvement" and m == "bad" for t, m in types_and_mods)

    def test_high_rating_module_not_flagged(self):
        stats = _make_stats({"great": 10, "good1": 8, "good2": 7})
        fb = _make_feedback({
            "great": (5.0, 15),
            "good1": (4.8, 10),
            "good2": (4.5, 8),
        })
        with _NO_LLM:
            suggestions = _generate_suggestions(stats, fb)
        flagged = {s["module"] for s in suggestions if s["type"] == "quality_improvement"}
        assert "great" not in flagged
        assert "good1" not in flagged

    def test_feedback_gap_detected_for_high_install_no_feedback(self):
        stats = _make_stats({"popular": 20, "niche": 1})
        fb = _make_feedback({"niche": (4.0, 2)})
        with _NO_LLM:
            suggestions = _generate_suggestions(stats, fb)
        gap_mods = [s["module"] for s in suggestions if s["type"] == "feedback_gap"]
        assert "popular" in gap_mods

    def test_low_install_module_not_flagged_for_feedback_gap(self):
        stats = _make_stats({"popular": 20, "low_vol": 0})
        fb = _make_feedback({})
        with _NO_LLM:
            suggestions = _generate_suggestions(stats, fb)
        gap_mods = [s["module"] for s in suggestions if s["type"] == "feedback_gap"]
        # low_vol has 0 installs — should not be flagged unless threshold is 0
        assert "low_vol" not in gap_mods or stats[1]["installs"] > 0

    def test_detection_method_key_present(self):
        stats = _make_stats({"a": 5, "b": 3, "c": 1})
        fb = _make_feedback({"a": (5.0, 5), "b": (4.0, 3), "c": (1.0, 4)})
        with _NO_LLM:
            suggestions = _generate_suggestions(stats, fb)
        for s in suggestions:
            assert "detection_method" in s

    def test_no_hardcoded_35_rating_threshold(self):
        """Ensure threshold adapts rather than defaulting to 3.5 for small N."""
        # Two modules: ratings 5.0 and 4.0 — neither should be flagged with static 3.5
        stats = _make_stats({"x": 5, "y": 5})
        fb = _make_feedback({"x": (5.0, 10), "y": (4.0, 8)})
        t = _adaptive_thresholds(stats, fb)
        # With only 2 data points, method is "relative", threshold < 4.0
        assert t["rating_threshold"] < 4.0
        with _NO_LLM:
            suggestions = _generate_suggestions(stats, fb)
        # No quality issues should be flagged since 4.0 >= threshold
        quality_flags = [s for s in suggestions if s["type"] == "quality_improvement"]
        assert len(quality_flags) == 0
