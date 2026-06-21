# CUI // SP-CTI
"""Tests for anomaly detection in hardcoded threshold detection.

Verifies that the C# (tree-sitter + regex fallback) and Java (regex)
threshold detectors apply statistical anomaly detection to filter common
numeric constants, matching the existing Python AST path behaviour.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from tools.ai_augmentation.pattern_classifier import (
    _AD_ENABLED,
    _collect_all_numeric_thresholds,
    _is_threshold_anomalous,
    _build_scope_map,
    _detect_hardcoded_threshold,
    _cs_detect_via_regex,
    _java_detect_via_regex,
)


# ---------------------------------------------------------------------------
# TestCollectAllNumericThresholds — Python AST population collector
# ---------------------------------------------------------------------------


class TestCollectAllNumericThresholds:
    def test_collects_from_comparison(self):
        tree = ast.parse("if x > 100: pass")
        assert 100.0 in _collect_all_numeric_thresholds(tree)

    def test_collects_from_binop(self):
        tree = ast.parse("x = y + 50")
        assert 50.0 in _collect_all_numeric_thresholds(tree)

    def test_no_numerics_returns_empty(self):
        tree = ast.parse("x = 'hello'")
        assert _collect_all_numeric_thresholds(tree) == []

    def test_collects_float_values(self):
        tree = ast.parse("if score > 0.95: pass")
        assert 0.95 in _collect_all_numeric_thresholds(tree)

    def test_multiple_comparators_collected(self):
        tree = ast.parse("if 10 < x < 20: pass")
        values = _collect_all_numeric_thresholds(tree)
        assert 10.0 in values
        assert 20.0 in values


# ---------------------------------------------------------------------------
# TestIsThresholdAnomalous — statistical anomaly function
# ---------------------------------------------------------------------------


class TestIsThresholdAnomalous:
    def test_small_sample_returns_fallback_true(self):
        # _AD_MIN_SAMPLE_SIZE is 5; with 2 values fallback applies
        assert _is_threshold_anomalous(100.0, [1.0, 2.0]) is True

    def test_empty_population_returns_fallback_true(self):
        assert _is_threshold_anomalous(5.0, []) is True

    def test_zscore_outlier_flagged(self):
        # 99999 vs 5 repeated five times — z >> 2.0 → anomalous
        population = [5.0, 5.0, 5.0, 5.0, 5.0, 99999.0]
        assert _is_threshold_anomalous(99999.0, population) is True

    def test_common_value_not_flagged(self):
        population = [5.0, 5.0, 5.0, 5.0, 5.0, 99999.0]
        assert _is_threshold_anomalous(5.0, population) is False

    def test_iqr_fence_outlier_flagged(self):
        population = [1.0, 2.0, 1.0, 2.0, 3.0, 1000.0]
        assert _is_threshold_anomalous(1000.0, population) is True

    def test_exact_mean_not_flagged(self):
        population = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
        mean = sum(population) / len(population)  # 3.5
        assert _is_threshold_anomalous(mean, population) is False


# ---------------------------------------------------------------------------
# TestDetectHardcodedThresholdAD — Python AST path (verifying existing behaviour)
# ---------------------------------------------------------------------------


class TestDetectHardcodedThresholdAD:
    def test_outlier_detected(self, tmp_path):
        src = (
            "if x > 1: pass\n"
            "if x > 1: pass\n"
            "if x > 1: pass\n"
            "if x > 1: pass\n"
            "if x > 1: pass\n"
            "if x > 99999: pass\n"
        )
        f = tmp_path / "t.py"
        f.write_text(src, encoding="utf-8")
        tree = ast.parse(src)
        scope_map = _build_scope_map(tree)
        results = _detect_hardcoded_threshold(str(f), tree, scope_map)
        constants_found = [c for r in results for c in r["pattern_detail"]["constants"]]
        assert 99999 in constants_found

    def test_common_value_suppressed_when_ad_enabled(self, tmp_path):
        if not _AD_ENABLED:
            pytest.skip("Anomaly detection disabled in config")
        src = "\n".join(["if x > 5: pass"] * 7)
        f = tmp_path / "t.py"
        f.write_text(src, encoding="utf-8")
        tree = ast.parse(src)
        scope_map = _build_scope_map(tree)
        results = _detect_hardcoded_threshold(str(f), tree, scope_map)
        # All values are 5 — z-score = 0 → suppressed by anomaly detection
        assert results == [], f"Expected no results with uniform population, got: {results}"

    def test_result_includes_anomaly_detected_field(self, tmp_path):
        src = "\n".join(["if x > 1: pass"] * 5) + "\nif x > 99999: pass\n"
        f = tmp_path / "t.py"
        f.write_text(src, encoding="utf-8")
        tree = ast.parse(src)
        scope_map = _build_scope_map(tree)
        results = _detect_hardcoded_threshold(str(f), tree, scope_map)
        for r in results:
            assert "anomaly_detected" in r["pattern_detail"]


# ---------------------------------------------------------------------------
# TestCsRegexThresholdAD — C# regex fallback path (new AD behaviour)
# ---------------------------------------------------------------------------


class TestCsRegexThresholdAD:
    def test_outlier_flagged(self):
        if not _AD_ENABLED:
            pytest.skip("Anomaly detection disabled in config")
        source = (
            "if (x < 5) {}\n"
            "if (y < 5) {}\n"
            "if (z < 5) {}\n"
            "if (a < 5) {}\n"
            "if (b < 5) {}\n"
            "if (c < 99999) {}\n"
        )
        hits = [
            r for r in _cs_detect_via_regex("t.cs", source)
            if r["pattern_type"] == "hardcoded_threshold"
        ]
        constants = [c for h in hits for c in h["pattern_detail"]["constants"]]
        assert any(str(c) in ("99999", "99999.0") or c == "99999" for c in constants), (
            f"Expected outlier 99999 in threshold results; constants found: {constants}"
        )

    def test_common_value_suppressed(self):
        if not _AD_ENABLED:
            pytest.skip("Anomaly detection disabled in config")
        source = "\n".join(["if (x < 5) {}"] * 7)
        hits = [
            r for r in _cs_detect_via_regex("t.cs", source)
            if r["pattern_type"] == "hardcoded_threshold"
        ]
        assert hits == [], f"Expected AD to suppress common value; got: {hits}"

    def test_result_includes_anomaly_detected_field(self):
        source = "\n".join(["if (x < 5) {}"] * 5) + "\nif (c < 99999) {}\n"
        hits = [
            r for r in _cs_detect_via_regex("t.cs", source)
            if r["pattern_type"] == "hardcoded_threshold"
        ]
        for h in hits:
            assert "anomaly_detected" in h["pattern_detail"]

    def test_ad_disabled_reports_all(self, monkeypatch):
        """When AD is disabled via module global, every threshold is reported."""
        import tools.ai_augmentation.pattern_classifier as pc
        monkeypatch.setattr(pc, "_AD_ENABLED", False)
        source = "\n".join(["if (x < 5) {}"] * 3)
        hits = [
            r for r in pc._cs_detect_via_regex("t.cs", source)
            if r["pattern_type"] == "hardcoded_threshold"
        ]
        assert len(hits) == 3


# ---------------------------------------------------------------------------
# TestJavaRegexThresholdAD — Java regex fallback path (new AD behaviour)
# ---------------------------------------------------------------------------


class TestJavaRegexThresholdAD:
    def test_outlier_static_int_flagged(self):
        if not _AD_ENABLED:
            pytest.skip("Anomaly detection disabled in config")
        # _JAVA_RE_STATIC_INT matches \d{1,4} — max 4 digits; use 9999 as outlier
        source = (
            "private static int A = 5;\n"
            "private static int B = 5;\n"
            "private static int C = 5;\n"
            "private static int D = 5;\n"
            "private static int E = 5;\n"
            "private static int THRESHOLD = 9999;\n"
        )
        hits = [
            r for r in _java_detect_via_regex("t.java", source)
            if r["pattern_type"] == "hardcoded_threshold"
        ]
        values = [h["pattern_detail"].get("value") for h in hits]
        assert 9999 in values, f"Expected 9999 in values, got: {values}"

    def test_common_static_int_suppressed(self):
        if not _AD_ENABLED:
            pytest.skip("Anomaly detection disabled in config")
        source = "\n".join(["private static int X = 5;"] * 7)
        hits = [
            r for r in _java_detect_via_regex("t.java", source)
            if r["pattern_type"] == "hardcoded_threshold"
        ]
        assert hits == [], f"Expected AD to suppress common value; got: {hits}"

    def test_result_includes_anomaly_detected_field(self):
        # 9999 is 4-digit max for _JAVA_RE_STATIC_INT
        source = "\n".join(["private static int X = 5;"] * 5) + "\nprivate static int BIG = 9999;\n"
        hits = [
            r for r in _java_detect_via_regex("t.java", source)
            if r["pattern_type"] == "hardcoded_threshold"
        ]
        for h in hits:
            assert "anomaly_detected" in h["pattern_detail"]

    def test_ad_disabled_reports_all(self, monkeypatch):
        """When AD is disabled via module global, every threshold is reported."""
        import tools.ai_augmentation.pattern_classifier as pc
        monkeypatch.setattr(pc, "_AD_ENABLED", False)
        source = "\n".join(["private static int X = 5;"] * 3)
        hits = [
            r for r in pc._java_detect_via_regex("t.java", source)
            if r["pattern_type"] == "hardcoded_threshold"
        ]
        assert len(hits) == 3
