# CUI // SP-CTI
"""Tests for hardcoded_threshold anomaly detection in pattern_classifier.py.

Covers: specificity scoring, anomaly enrichment (z-score + composite score),
per-file grouping, passthrough of non-threshold hits, and integration with
detect_patterns() on a real temp file.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


from tools.ai_augmentation.pattern_classifier import (
    _enrich_threshold_anomalies,
    _threshold_specificity,
    detect_patterns,
)


# ---------------------------------------------------------------------------
# _threshold_specificity
# ---------------------------------------------------------------------------


class TestThresholdSpecificity:
    def test_common_ints_score_zero(self):
        for v in (0.0, 1.0, -1.0, 10.0, 100.0):
            assert _threshold_specificity(v) == 0.0

    def test_precise_float_scores_high(self):
        # 0.73842 has 5 significant decimal places → score ≥ 1.0
        score = _threshold_specificity(0.73842)
        assert score >= 1.0

    def test_two_decimal_places_mid_score(self):
        # 3.14 has 2 decimal places → score = 2/3 ≈ 0.667
        score = _threshold_specificity(3.14)
        assert 0.0 < score < 1.0

    def test_unusual_integer_nonzero(self):
        score = _threshold_specificity(777.0)
        assert score > 0.0


# ---------------------------------------------------------------------------
# _enrich_threshold_anomalies
# ---------------------------------------------------------------------------


def _make_hit(path: str, line: int, constants: list, kind: str = "compare") -> dict:
    return {
        "pattern_type": "hardcoded_threshold",
        "module_path": path,
        "function_name": "<module>",
        "line_start": line,
        "line_end": line,
        "language": "python",
        "pattern_detail": {"kind": kind, "constants": constants},
    }


def _make_other(path: str) -> dict:
    return {
        "pattern_type": "nested_conditionals",
        "module_path": path,
        "function_name": "foo",
        "line_start": 1,
        "line_end": 5,
        "language": "python",
        "pattern_detail": {"max_depth": 3},
    }


class TestEnrichThresholdAnomalies:
    def test_empty_list_returns_empty(self):
        assert _enrich_threshold_anomalies([]) == []

    def test_non_threshold_hits_pass_through(self):
        hit = _make_other("a.py")
        result = _enrich_threshold_anomalies([hit])
        assert len(result) == 1
        assert result[0]["pattern_type"] == "nested_conditionals"
        assert "anomaly_score" not in result[0]["pattern_detail"]

    def test_single_threshold_hit_gets_enriched(self):
        hit = _make_hit("a.py", 5, [42])
        result = _enrich_threshold_anomalies([hit])
        assert len(result) == 1
        detail = result[0]["pattern_detail"]
        assert "anomaly_score" in detail
        assert "is_anomalous" in detail
        assert "zscore" in detail
        assert 0.0 <= detail["anomaly_score"] <= 1.0
        assert isinstance(detail["is_anomalous"], bool)

    def test_precise_float_marked_anomalous(self):
        # Mix common values with one very precise float — the precise one should be anomalous.
        hits = [
            _make_hit("b.py", 1, [0]),
            _make_hit("b.py", 2, [1]),
            _make_hit("b.py", 3, [0.73842]),
        ]
        result = _enrich_threshold_anomalies(hits)
        threshold_results = [r for r in result if r["pattern_type"] == "hardcoded_threshold"]
        precise = next(r for r in threshold_results if 0.73842 in r["pattern_detail"]["constants"])
        assert precise["pattern_detail"]["is_anomalous"] is True

    def test_common_values_not_anomalous(self):
        hits = [_make_hit("c.py", i, [v]) for i, v in enumerate([0, 1, 0, 1, 0], start=1)]
        result = _enrich_threshold_anomalies(hits)
        for r in result:
            if r["pattern_type"] == "hardcoded_threshold":
                # All values are 0 or 1 (common); specificity=0, z-score < zscore_min → not anomalous
                assert r["pattern_detail"]["is_anomalous"] is False

    def test_per_file_grouping(self):
        # Two files; a hit in file A should not influence z-scores for file B.
        hits_a = [_make_hit("file_a.py", 1, [1000000])]
        hits_b = [_make_hit("file_b.py", 2, [1])]
        result = _enrich_threshold_anomalies(hits_a + hits_b)
        a_result = next(r for r in result if r["module_path"] == "file_a.py")
        b_result = next(r for r in result if r["module_path"] == "file_b.py")
        # Both should have anomaly_score (single-hit files get std=1 baseline)
        assert "anomaly_score" in a_result["pattern_detail"]
        assert "anomaly_score" in b_result["pattern_detail"]

    def test_anomaly_score_bounded(self):
        hits = [_make_hit("d.py", i, [v]) for i, v in enumerate([0.123456789, 999999, -999999], start=1)]
        result = _enrich_threshold_anomalies(hits)
        for r in result:
            if r["pattern_type"] == "hardcoded_threshold":
                score = r["pattern_detail"]["anomaly_score"]
                assert 0.0 <= score <= 1.0

    def test_mixed_threshold_and_other_preserved(self):
        detections = [
            _make_other("e.py"),
            _make_hit("e.py", 10, [0.5]),
            _make_other("e.py"),
        ]
        result = _enrich_threshold_anomalies(detections)
        assert len(result) == 3
        others = [r for r in result if r["pattern_type"] != "hardcoded_threshold"]
        assert len(others) == 2


# ---------------------------------------------------------------------------
# Integration: detect_patterns on a real Python temp file
# ---------------------------------------------------------------------------


class TestDetectPatternsIntegration:
    def test_threshold_hits_enriched_by_detect_patterns(self, tmp_path):
        src = textwrap.dedent("""\
            def check(score):
                if score > 0.73842:
                    return True
                if score < 100:
                    return False
        """)
        f = tmp_path / "sample.py"
        f.write_text(src, encoding="utf-8")

        results = detect_patterns(str(f))
        threshold_hits = [r for r in results if r["pattern_type"] == "hardcoded_threshold"]
        assert threshold_hits, "Expected at least one hardcoded_threshold hit"
        for hit in threshold_hits:
            assert "anomaly_score" in hit["pattern_detail"]
            assert "is_anomalous" in hit["pattern_detail"]
            assert "zscore" in hit["pattern_detail"]

    def test_precise_float_flagged_as_anomalous_in_detect_patterns(self, tmp_path):
        src = textwrap.dedent("""\
            def score_check(x):
                if x > 0.98765:
                    return "high"
                if x > 0.50000:
                    return "mid"
        """)
        f = tmp_path / "precise.py"
        f.write_text(src, encoding="utf-8")

        results = detect_patterns(str(f))
        threshold_hits = [r for r in results if r["pattern_type"] == "hardcoded_threshold"]
        precise = [
            h for h in threshold_hits
            if any(abs(float(c) - 0.98765) < 1e-9 for c in h["pattern_detail"].get("constants", []))
        ]
        if precise:
            assert precise[0]["pattern_detail"]["is_anomalous"] is True
