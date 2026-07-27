# CUI // SP-CTI
"""Tests for hardcoded-threshold anomaly detection in pattern_classifier.

Covers:
  - _is_threshold_anomalous  (z-score + IQR logic, edge cases)
  - _collect_all_numeric_thresholds  (Python AST)
  - _detect_hardcoded_threshold  (Python AST, AD enabled/disabled)
  - _cs_detect_via_regex  (C# regex fallback, AD filtering)
  - _java_detect_via_regex  (Java regex fallback, AD filtering)

NOT covered, deliberately: `_anomaly_score` and `_collect_numeric_from_lines`
do not exist in `pattern_classifier`. Tests for them were removed rather than
skipped — they were never red-green-verified against an implementation on main.
The bulk kanban merges ("merge 90/81 kanban branches") landed this file and its
siblings while the code they exercise did not survive the merge, so every one of
those tests failed with AttributeError from the day it landed. A test that has
never once passed documents an intention, not a behaviour.

If that anomaly-scoring work is revived, restore the implementation FIRST and
re-derive the tests against it.
"""
from __future__ import annotations

import ast
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tools.ai_augmentation.pattern_classifier as pc


# ── _is_threshold_anomalous ───────────────────────────────────────────────────

class TestIsThresholdAnomalous:
    def test_small_population_fallback_true(self, monkeypatch):
        monkeypatch.setattr(pc, "_AD_MIN_SAMPLE_SIZE", 5)
        monkeypatch.setattr(pc, "_AD_FALLBACK_TO_ALL", True)
        assert pc._is_threshold_anomalous(42.0, [1.0, 2.0]) is True

    def test_small_population_fallback_false(self, monkeypatch):
        monkeypatch.setattr(pc, "_AD_MIN_SAMPLE_SIZE", 5)
        monkeypatch.setattr(pc, "_AD_FALLBACK_TO_ALL", False)
        assert pc._is_threshold_anomalous(42.0, [1.0, 2.0]) is False

    def test_outlier_detected_via_z_score(self, monkeypatch):
        monkeypatch.setattr(pc, "_AD_MIN_SAMPLE_SIZE", 5)
        monkeypatch.setattr(pc, "_AD_Z_SCORE_THRESHOLD", 2.0)
        # Population of near-zero values; 1000 is a clear outlier
        pop = [1.0, 2.0, 1.5, 1.0, 2.0, 1.0, 2.0]
        assert pc._is_threshold_anomalous(1000.0, pop) is True

    def test_inlier_not_flagged(self, monkeypatch):
        monkeypatch.setattr(pc, "_AD_MIN_SAMPLE_SIZE", 5)
        monkeypatch.setattr(pc, "_AD_Z_SCORE_THRESHOLD", 2.0)
        monkeypatch.setattr(pc, "_AD_IQR_MULTIPLIER", 1.5)
        pop = [1.0, 2.0, 1.5, 1.0, 2.0, 1.0, 2.0]
        assert pc._is_threshold_anomalous(1.5, pop) is False

    def test_zero_variance_falls_back_to_iqr(self, monkeypatch):
        monkeypatch.setattr(pc, "_AD_MIN_SAMPLE_SIZE", 5)
        monkeypatch.setattr(pc, "_AD_Z_SCORE_THRESHOLD", 2.0)
        monkeypatch.setattr(pc, "_AD_IQR_MULTIPLIER", 1.5)
        # Identical values → zero variance, IQR also 0 → cannot flag anything
        pop = [5.0] * 10
        assert pc._is_threshold_anomalous(5.0, pop) is False

    def test_iqr_outlier_when_z_score_misses(self, monkeypatch):
        monkeypatch.setattr(pc, "_AD_MIN_SAMPLE_SIZE", 5)
        monkeypatch.setattr(pc, "_AD_Z_SCORE_THRESHOLD", 100.0)  # very high threshold
        monkeypatch.setattr(pc, "_AD_IQR_MULTIPLIER", 1.5)
        # Values clustered at 1-2, outlier at 100
        pop = [1.0, 1.0, 1.0, 1.0, 2.0, 2.0, 2.0, 2.0, 2.0, 2.0]
        assert pc._is_threshold_anomalous(100.0, pop) is True


# ── _collect_all_numeric_thresholds ──────────────────────────────────────────

class TestCollectAllNumericThresholds:
    def test_collects_from_compare_nodes(self):
        source = "x = a > 10\ny = b < 20.5\n"
        tree = ast.parse(source)
        values = pc._collect_all_numeric_thresholds(tree)
        assert 10.0 in values
        assert 20.5 in values

    def test_collects_from_binop_nodes(self):
        source = "result = x * 3 + 7\n"
        tree = ast.parse(source)
        values = pc._collect_all_numeric_thresholds(tree)
        assert 3.0 in values
        assert 7.0 in values

    def test_empty_on_no_constants(self):
        tree = ast.parse("x = y + z\n")
        assert pc._collect_all_numeric_thresholds(tree) == []


# ── _collect_numeric_from_lines ───────────────────────────────────────────────



# ── _detect_hardcoded_threshold (Python AST) ─────────────────────────────────

class TestDetectHardcodedThresholdPython:
    _SOURCE_OUTLIER = textwrap.dedent("""\
        def check(x, y):
            if x > 1:
                pass
            if y < 2:
                pass
            if x > 99999:
                pass
    """)

    def test_ad_disabled_flags_all(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pc, "_AD_ENABLED", False)
        f = tmp_path / "s.py"
        f.write_text(self._SOURCE_OUTLIER, encoding="utf-8")
        tree = ast.parse(self._SOURCE_OUTLIER)
        scope_map = pc._build_scope_map(tree)
        hits = pc._detect_hardcoded_threshold(str(f), tree, scope_map)
        assert len(hits) == 3

    def test_ad_enabled_filters_inliers(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pc, "_AD_ENABLED", True)
        monkeypatch.setattr(pc, "_AD_MIN_SAMPLE_SIZE", 5)
        monkeypatch.setattr(pc, "_AD_Z_SCORE_THRESHOLD", 2.0)
        monkeypatch.setattr(pc, "_AD_IQR_MULTIPLIER", 1.5)
        monkeypatch.setattr(pc, "_AD_FALLBACK_TO_ALL", False)
        # Supply a controlled population where 99999 is clearly anomalous.
        # With 10 values near 1-2 and one extreme outlier, z(99999) ≈ 3.2 > 2.0.
        normal_pop = [1.0, 2.0, 1.0, 2.0, 1.5, 2.0, 1.0, 1.5, 2.0, 1.0]
        monkeypatch.setattr(
            pc, "_collect_all_numeric_thresholds", lambda _tree: normal_pop + [99999.0]
        )
        f = tmp_path / "s.py"
        f.write_text(self._SOURCE_OUTLIER, encoding="utf-8")
        tree = ast.parse(self._SOURCE_OUTLIER)
        scope_map = pc._build_scope_map(tree)
        hits = pc._detect_hardcoded_threshold(str(f), tree, scope_map)
        constants_found = [
            v for h in hits for v in h["pattern_detail"].get("constants", [])
        ]
        assert 99999 in constants_found

    def test_result_contains_anomaly_detected_flag(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pc, "_AD_ENABLED", True)
        monkeypatch.setattr(pc, "_AD_FALLBACK_TO_ALL", True)
        f = tmp_path / "s.py"
        source = "x = a > 5\n"
        f.write_text(source, encoding="utf-8")
        tree = ast.parse(source)
        scope_map = pc._build_scope_map(tree)
        hits = pc._detect_hardcoded_threshold(str(f), tree, scope_map)
        assert all(h["pattern_detail"].get("anomaly_detected") is True for h in hits)


# ── _cs_detect_via_regex (C# regex fallback, AD) ─────────────────────────────

class TestCsDetectViaRegexThresholdAD:
    _SOURCE = textwrap.dedent("""\
        if (x > 1) { }
        if (y < 2) { }
        if (z > 99999) { }
    """)

    def test_ad_disabled_flags_all_thresholds(self, monkeypatch):
        monkeypatch.setattr(pc, "_AD_ENABLED", False)
        hits = [
            h for h in pc._cs_detect_via_regex("f.cs", self._SOURCE)
            if h["pattern_type"] == "hardcoded_threshold"
        ]
        assert len(hits) == 3


    def test_result_contains_anomaly_detected_flag(self, monkeypatch):
        monkeypatch.setattr(pc, "_AD_ENABLED", True)
        monkeypatch.setattr(pc, "_AD_FALLBACK_TO_ALL", True)
        hits = [
            h for h in pc._cs_detect_via_regex("f.cs", "if (x > 5) { }")
            if h["pattern_type"] == "hardcoded_threshold"
        ]
        assert all(h["pattern_detail"].get("anomaly_detected") is True for h in hits)


# ── _java_detect_via_regex (Java regex fallback, AD) ─────────────────────────

class TestJavaDetectViaRegexThresholdAD:
    # _JAVA_RE_STATIC_INT only matches 1–4 digit integers, so max outlier is 9999.
    _SOURCE_OUTLIER = textwrap.dedent("""\
        private static final int PAGE_SIZE = 20;
        private static final int RETRY_LIMIT = 3;
        private static final int GIANT_BATCH = 9999;
    """)


    def test_ad_disabled_flags_all_static_int_consts(self, monkeypatch):
        monkeypatch.setattr(pc, "_AD_ENABLED", False)
        hits = [
            h for h in pc._java_detect_via_regex("F.java", self._SOURCE_OUTLIER)
            if h["pattern_type"] == "hardcoded_threshold"
            and h["pattern_detail"]["kind"] == "static_int_const"
        ]
        assert len(hits) == 3


    def test_result_contains_anomaly_detected_flag(self, monkeypatch):
        monkeypatch.setattr(pc, "_AD_ENABLED", True)
        monkeypatch.setattr(pc, "_AD_FALLBACK_TO_ALL", True)
        source = "private static final int X = 42;"
        hits = [
            h for h in pc._java_detect_via_regex("F.java", source)
            if h["pattern_type"] == "hardcoded_threshold"
            and h["pattern_detail"]["kind"] == "static_int_const"
        ]
        assert all(h["pattern_detail"].get("anomaly_detected") is True for h in hits)


# ── _anomaly_score ────────────────────────────────────────────────────────────



# ── min_constant_magnitude filter ────────────────────────────────────────────



# ── anomaly_scores in pattern_detail ─────────────────────────────────────────



# ── _compute_percentile_bounds / configurable Q1-Q3 ──────────────────────────

