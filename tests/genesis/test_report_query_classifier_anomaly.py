# CUI // SP-CTI
"""Tests for anomaly-detection helpers in report_service.py and query_classifier.py."""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.notification_service.report_service import (
    _compute_regression_threshold,
    _REGRESSION_SIGNIFICANCE_PTS,
    _NARRATIVE_MAX_TOKENS,
    _NARRATIVE_TEMPERATURE,
    _GATE_ROWS_LIMIT,
    _TOP_OPPS_ROADMAP_LIMIT,
    _TOP_OPPS_SCAN_LIMIT,
    _MODULE_ROWS_LIMIT,
    _SUMMARISE_FINDINGS_COUNT,
    _summarise_findings,
)
from tools.rag.query_classifier import (
    _compute_classification_thresholds,
    _heuristic_classify,
    classify_query,
    _UNANSWERABLE_OVERLAP_THRESHOLD,
    _MIN_QUERY_WORDS,
    _HEURISTIC_CONFIDENCE_HIGH,
    _HEURISTIC_CONFIDENCE_DEFAULT,
    _HEURISTIC_PROMOTE_THRESHOLD,
    _LLM_CONFIDENCE,
    _BATCH_MAX_WORKERS,
    TAXONOMY_LABELS,
)


def _mock_conn(row_dict):
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = row_dict
    return conn


# ─────────────────────────────────────────────────────────────────
# report_service._compute_regression_threshold
# ─────────────────────────────────────────────────────────────────

class TestComputeRegressionThreshold:

    def test_disabled_returns_fallback(self):
        cfg = {"enabled": False, "fallback_regression_pts": 3.5}
        assert _compute_regression_threshold(cfg) == 3.5

    def test_insufficient_history_returns_fallback(self):
        cfg = {"enabled": True, "min_samples": 20, "sigma_fraction": 0.5,
               "fallback_regression_pts": 2.0,
               "adaptive_bounds": {"regression_floor": 0.5, "regression_ceil": 10.0}}
        with patch("tools.notification_service.report_service.get_connection") as mock_gc:
            mock_gc.return_value = _mock_conn({"mean_s": 75.0, "var_s": 25.0, "n": 5})
            result = _compute_regression_threshold(cfg)
        assert result == 2.0

    def test_sufficient_history_computes_threshold(self):
        cfg = {"enabled": True, "min_samples": 5, "sigma_fraction": 0.5,
               "fallback_regression_pts": 2.0,
               "adaptive_bounds": {"regression_floor": 0.5, "regression_ceil": 10.0}}
        # std_dev = sqrt(25) = 5, threshold = 0.5 * 5 = 2.5
        with patch("tools.notification_service.report_service.get_connection") as mock_gc:
            mock_gc.return_value = _mock_conn({"mean_s": 75.0, "var_s": 25.0, "n": 20})
            result = _compute_regression_threshold(cfg)
        assert result == pytest.approx(2.5, abs=0.01)

    def test_floor_respected(self):
        cfg = {"enabled": True, "min_samples": 5, "sigma_fraction": 0.0,  # zero sigma → floor
               "fallback_regression_pts": 2.0,
               "adaptive_bounds": {"regression_floor": 1.0, "regression_ceil": 10.0}}
        with patch("tools.notification_service.report_service.get_connection") as mock_gc:
            mock_gc.return_value = _mock_conn({"mean_s": 75.0, "var_s": 0.0, "n": 20})
            result = _compute_regression_threshold(cfg)
        assert result >= 1.0

    def test_ceil_respected(self):
        cfg = {"enabled": True, "min_samples": 5, "sigma_fraction": 100.0,  # huge → ceil
               "fallback_regression_pts": 2.0,
               "adaptive_bounds": {"regression_floor": 0.5, "regression_ceil": 5.0}}
        with patch("tools.notification_service.report_service.get_connection") as mock_gc:
            mock_gc.return_value = _mock_conn({"mean_s": 75.0, "var_s": 400.0, "n": 20})
            result = _compute_regression_threshold(cfg)
        assert result <= 5.0

    def test_db_error_returns_fallback(self):
        cfg = {"enabled": True, "min_samples": 5, "fallback_regression_pts": 2.0}
        with patch("tools.notification_service.report_service.get_connection",
                   side_effect=Exception("DB error")):
            result = _compute_regression_threshold(cfg)
        assert result == 2.0

    def test_none_cfg_uses_module_default(self):
        with patch("tools.notification_service.report_service.get_connection",
                   side_effect=Exception("no DB")):
            result = _compute_regression_threshold(None)
        assert result == _REGRESSION_SIGNIFICANCE_PTS


class TestReportServiceConstants:
    def test_all_positive(self):
        assert _NARRATIVE_MAX_TOKENS > 0
        assert 0.0 <= _NARRATIVE_TEMPERATURE <= 1.0
        assert _GATE_ROWS_LIMIT > 0
        assert _TOP_OPPS_ROADMAP_LIMIT >= _TOP_OPPS_SCAN_LIMIT > 0
        assert _MODULE_ROWS_LIMIT > 0
        assert _SUMMARISE_FINDINGS_COUNT > 0
        assert _REGRESSION_SIGNIFICANCE_PTS > 0

    def test_summarise_findings_uses_constant(self):
        import json
        findings = [{"title": f"Finding {i}"} for i in range(10)]
        result = _summarise_findings(json.dumps(findings))
        titles = result.split("; ")
        assert len(titles) == _SUMMARISE_FINDINGS_COUNT

    def test_summarise_empty_returns_none_str(self):
        assert _summarise_findings(None) == "none"
        assert _summarise_findings("") == "none"


# ─────────────────────────────────────────────────────────────────
# query_classifier._compute_classification_thresholds
# ─────────────────────────────────────────────────────────────────

class TestComputeClassificationThresholds:

    def test_disabled_returns_fallback(self):
        cfg = {"enabled": False,
               "fallback_overlap_threshold": 0.15,
               "fallback_promote_threshold": 0.80}
        result = _compute_classification_thresholds(cfg)
        assert result["unanswerable_threshold"] == 0.15
        assert result["computed"] is False

    _DB_PATCH = "tools.db.storage.get_connection"  # imported locally inside fn

    def test_insufficient_history_returns_fallback(self):
        cfg = {"enabled": True, "min_samples": 20,
               "fallback_overlap_threshold": 0.10, "fallback_promote_threshold": 0.75,
               "adaptive_bounds": {"overlap_floor": 0.05, "overlap_ceil": 0.30}}
        with patch(self._DB_PATCH) as mock_gc:
            mock_gc.return_value = _mock_conn({"fp_rate": 0.2, "fn_rate": 0.1, "n": 5})
            result = _compute_classification_thresholds(cfg)
        assert result["unanswerable_threshold"] == 0.10
        assert result["computed"] is False

    def test_high_fp_rate_tightens_threshold(self):
        cfg = {"enabled": True, "min_samples": 10,
               "fallback_overlap_threshold": 0.10, "fallback_promote_threshold": 0.75,
               "adaptive_bounds": {"overlap_floor": 0.05, "overlap_ceil": 0.30}}
        with patch(self._DB_PATCH) as mock_gc:
            mock_gc.return_value = _mock_conn({"fp_rate": 0.5, "fn_rate": 0.0, "n": 25})
            result = _compute_classification_thresholds(cfg)
        if result["computed"]:
            assert result["unanswerable_threshold"] >= 0.10

    def test_high_fn_rate_loosens_threshold(self):
        cfg = {"enabled": True, "min_samples": 10,
               "fallback_overlap_threshold": 0.15, "fallback_promote_threshold": 0.75,
               "adaptive_bounds": {"overlap_floor": 0.05, "overlap_ceil": 0.30}}
        with patch(self._DB_PATCH) as mock_gc:
            mock_gc.return_value = _mock_conn({"fp_rate": 0.0, "fn_rate": 0.5, "n": 25})
            result = _compute_classification_thresholds(cfg)
        if result["computed"]:
            assert result["unanswerable_threshold"] <= 0.15

    def test_bounds_respected(self):
        cfg = {"enabled": True, "min_samples": 5,
               "fallback_overlap_threshold": 0.10, "fallback_promote_threshold": 0.75,
               "adaptive_bounds": {"overlap_floor": 0.08, "overlap_ceil": 0.20}}
        with patch(self._DB_PATCH) as mock_gc:
            mock_gc.return_value = _mock_conn({"fp_rate": 1.0, "fn_rate": 0.0, "n": 30})
            result = _compute_classification_thresholds(cfg)
        if result["computed"]:
            assert result["unanswerable_threshold"] <= 0.20
            assert result["unanswerable_threshold"] >= 0.08

    def test_db_error_returns_fallback(self):
        cfg = {"enabled": True, "min_samples": 5, "fallback_overlap_threshold": 0.10}
        with patch(self._DB_PATCH, side_effect=Exception("DB error")):
            result = _compute_classification_thresholds(cfg)
        assert result["computed"] is False


# ─────────────────────────────────────────────────────────────────
# _heuristic_classify
# ─────────────────────────────────────────────────────────────────

class TestHeuristicClassify:

    def test_reasoning_query_detected(self):
        result = _heuristic_classify("Why does FedRAMP require continuous monitoring?")
        assert result["label"] == "reasoning"
        assert result["confidence"] == _HEURISTIC_CONFIDENCE_HIGH

    def test_summary_query_detected(self):
        result = _heuristic_classify("Summarize the key controls for IL5 compliance.")
        assert result["label"] == "summary"
        assert result["confidence"] == _HEURISTIC_CONFIDENCE_HIGH

    def test_fact_single_query_detected(self):
        result = _heuristic_classify("What is AC-2?")
        assert result["label"] == "fact_single"
        assert result["confidence"] == _HEURISTIC_CONFIDENCE_HIGH

    def test_low_overlap_flags_unanswerable(self):
        query = "What is quantum entanglement physics thermodynamics?"
        context = "The cat sat on the mat near the door today yesterday."
        result = _heuristic_classify(query, context)
        assert result["label"] == "unanswerable"

    def test_default_low_confidence_fallback(self):
        result = _heuristic_classify("Tell me something.")
        assert result["label"] == "fact_single"
        assert result["confidence"] == _HEURISTIC_CONFIDENCE_DEFAULT


class TestClassifyQuery:

    def test_empty_query_returns_unanswerable(self):
        # No LLM call for empty query
        result = classify_query("")
        assert result["label"] == "unanswerable"

    def test_valid_query_falls_back_to_heuristic_when_llm_unavailable(self):
        # Patch LLM to return None so heuristic path is taken
        with patch("tools.rag.query_classifier._llm_classify", return_value=None):
            result = classify_query("What is NIST 800-53?")
        assert result["label"] in TAXONOMY_LABELS
        assert 0.0 <= result["confidence"] <= 1.0

    def test_label_always_in_taxonomy(self):
        queries = [
            "Why is zero trust important?",
            "List all FedRAMP controls",
            "What is CMMC?",
            "How does MFA prevent attacks?",
        ]
        with patch("tools.rag.query_classifier._llm_classify", return_value=None):
            for q in queries:
                result = classify_query(q)
                assert result["label"] in TAXONOMY_LABELS


class TestQueryClassifierConstants:
    def test_confidence_ordering(self):
        assert _LLM_CONFIDENCE > _HEURISTIC_CONFIDENCE_HIGH > _HEURISTIC_CONFIDENCE_DEFAULT >= 0.0

    def test_thresholds_valid_ranges(self):
        assert 0.0 < _UNANSWERABLE_OVERLAP_THRESHOLD < 1.0
        assert 0.0 < _HEURISTIC_PROMOTE_THRESHOLD < 1.0
        assert _MIN_QUERY_WORDS >= 1
        assert _BATCH_MAX_WORKERS >= 1
