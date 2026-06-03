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
