# CUI // SP-CTI
"""Tests for hardcoded-threshold anomaly detection in pattern_classifier.

Covers:
  - _is_threshold_anomalous  (z-score + IQR logic, edge cases)
  - _collect_all_numeric_thresholds  (Python AST)
  - _collect_numeric_from_lines  (regex fallback for C# / Java)
  - _detect_hardcoded_threshold  (Python AST, AD enabled/disabled)
  - _cs_detect_via_regex  (C# regex fallback, AD filtering)
  - _java_detect_via_regex  (Java regex fallback, AD filtering)
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

class TestCollectNumericFromLines:
    def test_extracts_integers_and_floats(self):
        lines = ["int x = 5;", "double y = 3.14;"]
        values = pc._collect_numeric_from_lines(lines)
        assert 5.0 in values
        assert 3.14 in values  # regex matches "3.14" as one float token

    def test_skips_comment_lines(self):
        lines = ["// int x = 999;", "* block comment 888;", "# python comment 777"]
        values = pc._collect_numeric_from_lines(lines)
        assert 999.0 not in values
        assert 888.0 not in values
        assert 777.0 not in values

    def test_empty_on_no_numbers(self):
        assert pc._collect_numeric_from_lines(["int x = y;"]) == []

    def test_handles_empty_lines(self):
        assert pc._collect_numeric_from_lines([]) == []
        assert pc._collect_numeric_from_lines([""]) == []


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

    def test_ad_enabled_filters_inliers(self, monkeypatch):
        monkeypatch.setattr(pc, "_AD_ENABLED", True)
        monkeypatch.setattr(pc, "_AD_MIN_SAMPLE_SIZE", 5)
        monkeypatch.setattr(pc, "_AD_Z_SCORE_THRESHOLD", 2.0)
        monkeypatch.setattr(pc, "_AD_IQR_MULTIPLIER", 1.5)
        monkeypatch.setattr(pc, "_AD_FALLBACK_TO_ALL", False)
        # Patch the collector to return a controlled population so z-score is reliable.
        normal_pop = [1.0, 2.0, 1.0, 2.0, 1.5, 2.0, 1.0, 1.5, 2.0, 1.0]
        monkeypatch.setattr(
            pc, "_collect_numeric_from_lines", lambda _lines: normal_pop + [99999.0]
        )
        hits = [
            h for h in pc._cs_detect_via_regex("f.cs", self._SOURCE)
            if h["pattern_type"] == "hardcoded_threshold"
        ]
        constants = [h["pattern_detail"]["constants"][0] for h in hits]
        assert "99999" in constants

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

    def test_pagerequest_always_flagged(self, monkeypatch):
        monkeypatch.setattr(pc, "_AD_ENABLED", True)
        monkeypatch.setattr(pc, "_AD_FALLBACK_TO_ALL", False)
        monkeypatch.setattr(pc, "_AD_MIN_SAMPLE_SIZE", 1)
        source = "PageRequest.of(0, 25);"
        hits = [
            h for h in pc._java_detect_via_regex("F.java", source)
            if h["pattern_type"] == "hardcoded_threshold"
        ]
        assert any(h["pattern_detail"]["kind"] == "PageRequest.of" for h in hits)

    def test_ad_disabled_flags_all_static_int_consts(self, monkeypatch):
        monkeypatch.setattr(pc, "_AD_ENABLED", False)
        hits = [
            h for h in pc._java_detect_via_regex("F.java", self._SOURCE_OUTLIER)
            if h["pattern_type"] == "hardcoded_threshold"
            and h["pattern_detail"]["kind"] == "static_int_const"
        ]
        assert len(hits) == 3

    def test_ad_enabled_filters_inlier_static_int_consts(self, monkeypatch):
        monkeypatch.setattr(pc, "_AD_ENABLED", True)
        monkeypatch.setattr(pc, "_AD_MIN_SAMPLE_SIZE", 5)
        monkeypatch.setattr(pc, "_AD_Z_SCORE_THRESHOLD", 2.0)
        monkeypatch.setattr(pc, "_AD_IQR_MULTIPLIER", 1.5)
        monkeypatch.setattr(pc, "_AD_FALLBACK_TO_ALL", False)
        # Patch the population so 9999 is clearly anomalous relative to 20 and 3.
        normal_pop = [20.0, 3.0, 20.0, 3.0, 20.0, 3.0, 20.0, 3.0, 20.0, 3.0]
        monkeypatch.setattr(
            pc, "_collect_numeric_from_lines", lambda _lines: normal_pop + [9999.0]
        )
        hits = [
            h for h in pc._java_detect_via_regex("F.java", self._SOURCE_OUTLIER)
            if h["pattern_type"] == "hardcoded_threshold"
            and h["pattern_detail"]["kind"] == "static_int_const"
        ]
        values = [h["pattern_detail"]["value"] for h in hits]
        assert 9999 in values

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

class TestAnomalyScore:
    def test_small_population_fallback_true_returns_one(self, monkeypatch):
        monkeypatch.setattr(pc, "_AD_MIN_SAMPLE_SIZE", 5)
        monkeypatch.setattr(pc, "_AD_FALLBACK_TO_ALL", True)
        assert pc._anomaly_score(42.0, [1.0, 2.0]) == 1.0

    def test_small_population_fallback_false_returns_zero(self, monkeypatch):
        monkeypatch.setattr(pc, "_AD_MIN_SAMPLE_SIZE", 5)
        monkeypatch.setattr(pc, "_AD_FALLBACK_TO_ALL", False)
        assert pc._anomaly_score(42.0, [1.0, 2.0]) == 0.0

    def test_outlier_score_exceeds_one(self, monkeypatch):
        monkeypatch.setattr(pc, "_AD_MIN_SAMPLE_SIZE", 5)
        monkeypatch.setattr(pc, "_AD_Z_SCORE_THRESHOLD", 2.0)
        pop = [1.0, 2.0, 1.5, 1.0, 2.0, 1.0, 2.0]
        score = pc._anomaly_score(1000.0, pop)
        assert score > 1.0

    def test_inlier_score_below_one(self, monkeypatch):
        monkeypatch.setattr(pc, "_AD_MIN_SAMPLE_SIZE", 5)
        monkeypatch.setattr(pc, "_AD_Z_SCORE_THRESHOLD", 2.0)
        monkeypatch.setattr(pc, "_AD_IQR_MULTIPLIER", 1.5)
        pop = [1.0, 2.0, 1.5, 1.0, 2.0, 1.0, 2.0]
        score = pc._anomaly_score(1.5, pop)
        assert score < 1.0

    def test_score_consistent_with_is_threshold_anomalous(self, monkeypatch):
        monkeypatch.setattr(pc, "_AD_MIN_SAMPLE_SIZE", 5)
        monkeypatch.setattr(pc, "_AD_Z_SCORE_THRESHOLD", 2.0)
        monkeypatch.setattr(pc, "_AD_IQR_MULTIPLIER", 1.5)
        monkeypatch.setattr(pc, "_AD_FALLBACK_TO_ALL", False)
        pop = [1.0, 2.0, 1.5, 1.0, 2.0, 1.0, 2.0]
        for v in [1.0, 1.5, 2.0, 1000.0]:
            flagged = pc._is_threshold_anomalous(v, pop)
            score = pc._anomaly_score(v, pop)
            if flagged:
                assert score >= 1.0, f"score {score} < 1.0 for flagged value {v}"
            else:
                assert score < 1.0, f"score {score} >= 1.0 for non-flagged value {v}"

    def test_zero_variance_iqr_fallback(self, monkeypatch):
        monkeypatch.setattr(pc, "_AD_MIN_SAMPLE_SIZE", 5)
        monkeypatch.setattr(pc, "_AD_Z_SCORE_THRESHOLD", 2.0)
        monkeypatch.setattr(pc, "_AD_IQR_MULTIPLIER", 1.5)
        # Identical population → zero variance and zero IQR → score = 0
        pop = [5.0] * 10
        assert pc._anomaly_score(5.0, pop) == 0.0


# ── min_constant_magnitude filter ────────────────────────────────────────────

class TestMagnitudeFilter:
    """AD-enabled detection skips constants with |value| <= _AD_MIN_CONSTANT_MAGNITUDE."""

    def test_zero_constant_skipped_when_ad_enabled(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pc, "_AD_ENABLED", True)
        monkeypatch.setattr(pc, "_AD_MIN_CONSTANT_MAGNITUDE", 1.0)
        monkeypatch.setattr(pc, "_AD_FALLBACK_TO_ALL", True)
        source = "if x > 0:\n    pass\n"
        f = tmp_path / "s.py"
        f.write_text(source, encoding="utf-8")
        import ast as _ast
        tree = _ast.parse(source)
        scope_map = pc._build_scope_map(tree)
        hits = pc._detect_hardcoded_threshold(str(f), tree, scope_map)
        assert hits == [], "zero-boundary check should be filtered"

    def test_one_constant_skipped_when_ad_enabled(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pc, "_AD_ENABLED", True)
        monkeypatch.setattr(pc, "_AD_MIN_CONSTANT_MAGNITUDE", 1.0)
        monkeypatch.setattr(pc, "_AD_FALLBACK_TO_ALL", True)
        source = "if n < 1:\n    pass\n"
        f = tmp_path / "s.py"
        f.write_text(source, encoding="utf-8")
        import ast as _ast
        tree = _ast.parse(source)
        scope_map = pc._build_scope_map(tree)
        hits = pc._detect_hardcoded_threshold(str(f), tree, scope_map)
        assert hits == [], "magnitude-1 boundary check should be filtered"

    def test_zero_constant_flagged_when_ad_disabled(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pc, "_AD_ENABLED", False)
        monkeypatch.setattr(pc, "_AD_MIN_CONSTANT_MAGNITUDE", 1.0)
        source = "if x > 0:\n    pass\n"
        f = tmp_path / "s.py"
        f.write_text(source, encoding="utf-8")
        import ast as _ast
        tree = _ast.parse(source)
        scope_map = pc._build_scope_map(tree)
        hits = pc._detect_hardcoded_threshold(str(f), tree, scope_map)
        assert len(hits) == 1, "magnitude filter must not apply when AD is disabled"

    def test_large_constant_still_flagged(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pc, "_AD_ENABLED", True)
        monkeypatch.setattr(pc, "_AD_MIN_CONSTANT_MAGNITUDE", 1.0)
        monkeypatch.setattr(pc, "_AD_FALLBACK_TO_ALL", True)
        source = "if score > 100:\n    pass\n"
        f = tmp_path / "s.py"
        f.write_text(source, encoding="utf-8")
        import ast as _ast
        tree = _ast.parse(source)
        scope_map = pc._build_scope_map(tree)
        hits = pc._detect_hardcoded_threshold(str(f), tree, scope_map)
        constants = [v for h in hits for v in h["pattern_detail"].get("constants", [])]
        assert 100 in constants


# ── anomaly_scores in pattern_detail ─────────────────────────────────────────

class TestAnomalyScoresInDetail:
    def test_anomaly_scores_present_when_ad_enabled(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pc, "_AD_ENABLED", True)
        monkeypatch.setattr(pc, "_AD_FALLBACK_TO_ALL", True)
        monkeypatch.setattr(pc, "_AD_MIN_CONSTANT_MAGNITUDE", 1.0)
        source = "if limit > 500:\n    pass\n"
        f = tmp_path / "s.py"
        f.write_text(source, encoding="utf-8")
        import ast as _ast
        tree = _ast.parse(source)
        scope_map = pc._build_scope_map(tree)
        hits = pc._detect_hardcoded_threshold(str(f), tree, scope_map)
        assert hits, "should detect limit > 500"
        assert "anomaly_scores" in hits[0]["pattern_detail"]

    def test_anomaly_scores_absent_when_ad_disabled(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pc, "_AD_ENABLED", False)
        source = "if limit > 500:\n    pass\n"
        f = tmp_path / "s.py"
        f.write_text(source, encoding="utf-8")
        import ast as _ast
        tree = _ast.parse(source)
        scope_map = pc._build_scope_map(tree)
        hits = pc._detect_hardcoded_threshold(str(f), tree, scope_map)
        assert hits
        assert "anomaly_scores" not in hits[0]["pattern_detail"]

    def test_cs_regex_anomaly_score_present_when_ad_enabled(self, monkeypatch):
        monkeypatch.setattr(pc, "_AD_ENABLED", True)
        monkeypatch.setattr(pc, "_AD_FALLBACK_TO_ALL", True)
        monkeypatch.setattr(pc, "_AD_MIN_CONSTANT_MAGNITUDE", 1.0)
        hits = [
            h for h in pc._cs_detect_via_regex("f.cs", "if (limit > 500) { }")
            if h["pattern_type"] == "hardcoded_threshold"
        ]
        assert hits
        assert "anomaly_score" in hits[0]["pattern_detail"]

    def test_java_anomaly_score_present_when_ad_enabled(self, monkeypatch):
        monkeypatch.setattr(pc, "_AD_ENABLED", True)
        monkeypatch.setattr(pc, "_AD_FALLBACK_TO_ALL", True)
        monkeypatch.setattr(pc, "_AD_MIN_CONSTANT_MAGNITUDE", 1.0)
        hits = [
            h for h in pc._java_detect_via_regex("F.java", "private static final int MAX = 500;")
            if h["pattern_type"] == "hardcoded_threshold"
            and h["pattern_detail"]["kind"] == "static_int_const"
        ]
        assert hits
        assert "anomaly_score" in hits[0]["pattern_detail"]


# ── _compute_percentile_bounds / configurable Q1-Q3 ──────────────────────────

class TestConfigurablePercentileBounds:
    """IQR bounds are driven by _AD_Q1_PERCENTILE / _AD_Q3_PERCENTILE."""

    def _pop(self) -> list[float]:
        return [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]

    def test_default_25_75_flags_extreme_outlier(self, monkeypatch):
        monkeypatch.setattr(pc, "_AD_Q1_PERCENTILE", 25.0)
        monkeypatch.setattr(pc, "_AD_Q3_PERCENTILE", 75.0)
        monkeypatch.setattr(pc, "_AD_MIN_SAMPLE_SIZE", 5)
        monkeypatch.setattr(pc, "_AD_Z_SCORE_THRESHOLD", 100.0)  # disable z-score path
        monkeypatch.setattr(pc, "_AD_IQR_MULTIPLIER", 1.5)
        monkeypatch.setattr(pc, "_AD_FALLBACK_TO_ALL", False)
        assert pc._is_threshold_anomalous(100.0, self._pop()) is True

    def test_wider_percentiles_expand_fence(self, monkeypatch):
        # With Q1=10 and Q3=90, the IQR fence is wider: fewer values are flagged.
        monkeypatch.setattr(pc, "_AD_Q1_PERCENTILE", 10.0)
        monkeypatch.setattr(pc, "_AD_Q3_PERCENTILE", 90.0)
        monkeypatch.setattr(pc, "_AD_MIN_SAMPLE_SIZE", 5)
        monkeypatch.setattr(pc, "_AD_Z_SCORE_THRESHOLD", 100.0)
        monkeypatch.setattr(pc, "_AD_IQR_MULTIPLIER", 1.5)
        monkeypatch.setattr(pc, "_AD_FALLBACK_TO_ALL", False)
        # 100 is still clearly outside even a wide fence around [1-10]
        assert pc._is_threshold_anomalous(100.0, self._pop()) is True
        # A value just outside the 25/75 fence should be within the 10/90 fence
        # Q1(10%)=1, Q3(90%)=9, IQR=8, lower=1-12=-11, upper=9+12=21 → 11 is inlier
        assert pc._is_threshold_anomalous(11.0, self._pop()) is False

    def test_narrower_percentiles_shrink_fence(self, monkeypatch):
        # With Q1=40, Q3=60: IQR fence is [2.0, 10.0] for pop [1-10].
        # With Q1=25, Q3=75: IQR fence is [-4.5, 15.5] for the same pop.
        # 11.0 is outside the narrow (40/60) fence but inside the wide (25/75) fence.
        monkeypatch.setattr(pc, "_AD_MIN_SAMPLE_SIZE", 5)
        monkeypatch.setattr(pc, "_AD_Z_SCORE_THRESHOLD", 100.0)  # disable z-score path
        monkeypatch.setattr(pc, "_AD_IQR_MULTIPLIER", 1.5)
        monkeypatch.setattr(pc, "_AD_FALLBACK_TO_ALL", False)

        monkeypatch.setattr(pc, "_AD_Q1_PERCENTILE", 40.0)
        monkeypatch.setattr(pc, "_AD_Q3_PERCENTILE", 60.0)
        assert pc._is_threshold_anomalous(11.0, self._pop()) is True, "narrow fence should flag 11.0"

        monkeypatch.setattr(pc, "_AD_Q1_PERCENTILE", 25.0)
        monkeypatch.setattr(pc, "_AD_Q3_PERCENTILE", 75.0)
        assert pc._is_threshold_anomalous(11.0, self._pop()) is False, "wide fence should not flag 11.0"

    def test_compute_percentile_bounds_default_matches_legacy(self, monkeypatch):
        monkeypatch.setattr(pc, "_AD_Q1_PERCENTILE", 25.0)
        monkeypatch.setattr(pc, "_AD_Q3_PERCENTILE", 75.0)
        n = 10
        sorted_pop = sorted(self._pop())
        q1, q3, iqr = pc._compute_percentile_bounds(sorted_pop, n)
        # Legacy: q1=sorted[n//4]=sorted[2]=3, q3=sorted[(3*n)//4]=sorted[7]=8
        assert q1 == sorted_pop[n // 4]
        assert q3 == sorted_pop[(3 * n) // 4]
        assert iqr == q3 - q1

    def test_anomaly_score_consistent_with_configurable_percentiles(self, monkeypatch):
        monkeypatch.setattr(pc, "_AD_Q1_PERCENTILE", 25.0)
        monkeypatch.setattr(pc, "_AD_Q3_PERCENTILE", 75.0)
        monkeypatch.setattr(pc, "_AD_MIN_SAMPLE_SIZE", 5)
        monkeypatch.setattr(pc, "_AD_Z_SCORE_THRESHOLD", 2.0)
        monkeypatch.setattr(pc, "_AD_IQR_MULTIPLIER", 1.5)
        monkeypatch.setattr(pc, "_AD_FALLBACK_TO_ALL", False)
        pop = self._pop()
        for v in [2.0, 5.0, 9.0, 100.0]:
            flagged = pc._is_threshold_anomalous(v, pop)
            score = pc._anomaly_score(v, pop)
            if flagged:
                assert score >= 1.0, f"score {score} < 1.0 for flagged value {v}"
            else:
                assert score < 1.0, f"score {score} >= 1.0 for non-flagged value {v}"

    def test_percentile_scale_fraction_mode(self, monkeypatch):
        # percentile_scale=1.0 lets q1/q3 be expressed as fractions (0.25, 0.75)
        # and should produce the same index results as percentage mode (25, 75) / 100.
        monkeypatch.setattr(pc, "_AD_PERCENTILE_SCALE", 1.0)
        monkeypatch.setattr(pc, "_AD_Q1_PERCENTILE", 0.25)
        monkeypatch.setattr(pc, "_AD_Q3_PERCENTILE", 0.75)
        n = 10
        sorted_pop = sorted(self._pop())
        q1, q3, iqr = pc._compute_percentile_bounds(sorted_pop, n)
        assert q1 == sorted_pop[n // 4]
        assert q3 == sorted_pop[(3 * n) // 4]
        assert iqr == q3 - q1

    def test_percentile_scale_default_100_loads_from_config(self):
        # Default config has percentile_scale: 100 — verify module-level constant.
        assert pc._AD_PERCENTILE_SCALE == 100.0
