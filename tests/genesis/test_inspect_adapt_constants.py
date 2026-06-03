# CUI // SP-CTI
"""Tests for constant extraction in the inspect_adapt (Inspect & Adapt) reflex."""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.genesis.reflexes.inspect_adapt import (
    _DEFAULT_CADENCE_DAYS,
    _DEFAULT_MIN_LESSONS,
    _DEFAULT_TRENDING_THRESHOLD,
    _NLP_LESSON_CAP,
    _NLP_REASON_CHARS,
    _NLP_MAX_TOKENS,
    _REPORT_TOP_PATTERNS,
    _REPORT_TOP_OUTCOMES,
    _RECURRENCE_NORMALIZER,
    _RECURRENCE_MAX,
    _PREFIX_FALLBACK_CHARS,
    _ANOMALY_SIGMA_MULTIPLIER,
    _ANOMALY_MIN_DISTINCT,
    _ANOMALY_MAX_THRESHOLD,
    _compute_trending_threshold,
    _detect_trending,
    _task_prefix,
)


class TestInspectAdaptConstants:
    def test_retrospective_defaults_positive(self):
        assert _DEFAULT_CADENCE_DAYS > 0
        assert _DEFAULT_MIN_LESSONS > 0
        assert _DEFAULT_TRENDING_THRESHOLD > 0

    def test_trending_threshold_below_min_lessons(self):
        # A report needs at least min-lessons rows; a single pattern within
        # that window trends once it crosses the (smaller) trending threshold.
        assert _DEFAULT_TRENDING_THRESHOLD <= _DEFAULT_MIN_LESSONS

    def test_nlp_limits_positive(self):
        assert _NLP_LESSON_CAP > 0
        assert _NLP_REASON_CHARS > 0
        assert _NLP_MAX_TOKENS > 0

    def test_report_top_n_ordered(self):
        assert _REPORT_TOP_PATTERNS > 0
        assert _REPORT_TOP_OUTCOMES > 0
        assert _REPORT_TOP_PATTERNS >= _REPORT_TOP_OUTCOMES

    def test_recurrence_bounds(self):
        assert _RECURRENCE_NORMALIZER > 0
        assert _RECURRENCE_MAX == 1.0

    def test_prefix_fallback_positive(self):
        assert _PREFIX_FALLBACK_CHARS > 0

    def test_anomaly_defaults_sane(self):
        assert _ANOMALY_SIGMA_MULTIPLIER > 0
        assert _ANOMALY_MIN_DISTINCT > 0
        # Ceiling must leave room above the static floor.
        assert _ANOMALY_MAX_THRESHOLD >= _DEFAULT_TRENDING_THRESHOLD


class TestAdaptiveTrendingThreshold:
    def test_disabled_returns_static(self):
        out = _compute_trending_threshold([9, 1, 1, 1], 2, {"enabled": False})
        assert out == 2

    def test_too_few_distinct_returns_static(self):
        # Only 2 distinct patterns < min_distinct (3) -> static floor.
        out = _compute_trending_threshold([5, 1], 2, {})
        assert out == 2

    def test_uniform_counts_floor_at_static(self):
        # All patterns equal -> std_dev 0 -> mean(1) < floor -> static threshold.
        out = _compute_trending_threshold([1, 1, 1, 1], 2, {})
        assert out == 2

    def test_outlier_raises_threshold(self):
        # One big spike among many ones: mean+sigma*std rises above the floor,
        # so the cutoff adapts upward to isolate the genuine outlier.
        counts = [10, 1, 1, 1, 1, 1, 1, 1]
        out = _compute_trending_threshold(counts, 2, {"sigma_multiplier": 1.0})
        assert out > 2

    def test_never_below_static_floor(self):
        # Even with a tiny sigma the result never drops below the configured floor.
        out = _compute_trending_threshold([3, 2, 2, 2], 2, {"sigma_multiplier": 0.0})
        assert out >= 2

    def test_capped_at_max_threshold(self):
        counts = [1000, 1, 1, 1]
        out = _compute_trending_threshold(counts, 2, {"max_threshold": 5})
        assert out == 5

    def test_returns_int(self):
        out = _compute_trending_threshold([7, 2, 1, 1, 1], 2, {})
        assert isinstance(out, int)

    def test_adaptive_threshold_feeds_detect_trending(self):
        # End-to-end: an adaptive cutoff of 3 excludes a count-2 pattern that the
        # old fixed threshold of 2 would have flagged.
        lessons = [
            {"pattern": "spike"}, {"pattern": "spike"}, {"pattern": "spike"},
            {"pattern": "minor"}, {"pattern": "minor"},
            {"pattern": "a"}, {"pattern": "b"}, {"pattern": "c"},
        ]
        from collections import Counter
        counts = list(Counter(l["pattern"] for l in lessons).values())
        cutoff = _compute_trending_threshold(counts, 2, {"sigma_multiplier": 1.0})
        trending = _detect_trending(lessons, cutoff)
        assert "spike" in trending
        if cutoff > 2:
            assert "minor" not in trending


class TestInspectAdaptBehavior:
    def test_detect_trending_uses_threshold(self):
        lessons = [
            {"pattern": "flaky_test"},
            {"pattern": "flaky_test"},
            {"pattern": "scope_creep"},
        ]
        # threshold=2 -> only flaky_test qualifies
        trending = _detect_trending(lessons, _DEFAULT_TRENDING_THRESHOLD)
        assert trending == {"flaky_test": 2}

    def test_detect_trending_excludes_below_threshold(self):
        lessons = [{"pattern": "one_off"}]
        assert _detect_trending(lessons, _DEFAULT_TRENDING_THRESHOLD) == {}

    def test_task_prefix_delimited(self):
        assert _task_prefix("aiify-rm-ff651-phase-5291") == "aiify"

    def test_task_prefix_fallback_slice(self):
        # No "-" delimiter -> falls back to first _PREFIX_FALLBACK_CHARS chars.
        long_id = "abcdefghijklmnop"
        assert _task_prefix(long_id) == long_id[:_PREFIX_FALLBACK_CHARS]

    def test_task_prefix_empty(self):
        assert _task_prefix("") == ""
