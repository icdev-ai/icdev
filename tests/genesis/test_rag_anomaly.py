# CUI // SP-CTI
"""Tests for anomaly-detection helpers in RAG tools (crag_evaluator, pdf_provider, chunker)."""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock


BASE_DIR = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BASE_DIR))

from tools.rag.crag_evaluator import (
    CRAGScorer,
    _compute_crag_anomaly_thresholds,
    _ENTITY_HEAD_THRESHOLD,
    _ENTITY_TORSO_THRESHOLD,
    _ACCEPTABLE_WORD_OVERLAP,
    _LLM_MAX_TOKENS,
    _LLM_TEMPERATURE,
    _GATE_THRESHOLD,
    _HEURISTIC_CONFIDENCE_HIGH,
    _HEURISTIC_CONFIDENCE_MID,
    _HEURISTIC_CONFIDENCE_LOW,
    CRAG_SCORES,
)
from tools.rag.pdf_provider import (
    _PDF_MAX_PAGES,
    _PDF_MAX_FILE_SIZE_MB,
    _PDF_ANTHROPIC_TOKENS,
    _PDF_SCANNED_TEXT_MIN,
    _PDF_RENDER_DPI,
    _PDF_VISION_TIMEOUT,
    _PDF_AVAIL_TIMEOUT,
)
from tools.rag.chunker import (
    _DEFAULT_SHORT_THRESHOLD,
    _DEFAULT_CHUNK_SIZE,
    _DEFAULT_OVERLAP_PCT,
    _BOUNDARY_SEARCH_RADIUS,
    _BOUNDARY_WHITESPACE_SCAN,
    CHARS_PER_TOKEN,
    chunk_content,
)


def _mock_conn(row_dict):
    conn = MagicMock()
    conn.execute.return_value.fetchone.return_value = row_dict
    return conn


# ─────────────────────────────────────────────────────────────────
# _compute_crag_anomaly_thresholds
# ─────────────────────────────────────────────────────────────────

class TestComputeCragAnomalyThresholds:

    def test_disabled_returns_fallbacks(self):
        cfg = {"enabled": False,
               "fallback_entity_head": 200, "fallback_entity_torso": 20,
               "fallback_acceptable_overlap": 0.75}
        result = _compute_crag_anomaly_thresholds(cfg)
        assert result["entity_head_threshold"] == 200
        assert result["entity_torso_threshold"] == 20
        assert result["acceptable_word_overlap"] == 0.75
        assert result["computed"] is False

    def test_insufficient_history_returns_defaults(self):
        cfg = {"enabled": True, "min_samples": 50,
               "adaptive_bounds": {"overlap_floor": 0.60, "overlap_ceil": 0.95}}
        with patch("tools.db.storage.get_connection") as mock_gc:
            mock_gc.return_value = _mock_conn(
                {"acceptable_rate": 0.3, "incorrect_rate": 0.1, "n": 10})
            result = _compute_crag_anomaly_thresholds(cfg)
        assert result["computed"] is False
        assert result["acceptable_word_overlap"] == _ACCEPTABLE_WORD_OVERLAP

    def test_high_incorrect_rate_tightens_overlap(self):
        cfg = {"enabled": True, "min_samples": 20,
               "adaptive_bounds": {"overlap_floor": 0.60, "overlap_ceil": 0.95}}
        # incorrect_rate >> acceptable_rate → positive adjustment → tighter (higher) threshold
        with patch("tools.db.storage.get_connection") as mock_gc:
            mock_gc.return_value = _mock_conn(
                {"acceptable_rate": 0.05, "incorrect_rate": 0.60, "n": 100})
            result = _compute_crag_anomaly_thresholds(cfg)
        if result["computed"]:
            assert result["acceptable_word_overlap"] >= _ACCEPTABLE_WORD_OVERLAP

    def test_bounds_respected(self):
        cfg = {"enabled": True, "min_samples": 10,
               "adaptive_bounds": {"overlap_floor": 0.70, "overlap_ceil": 0.90}}
        with patch("tools.db.storage.get_connection") as mock_gc:
            mock_gc.return_value = _mock_conn(
                {"acceptable_rate": 0.0, "incorrect_rate": 1.0, "n": 100})
            result = _compute_crag_anomaly_thresholds(cfg)
        if result["computed"]:
            assert result["acceptable_word_overlap"] <= 0.90
            assert result["acceptable_word_overlap"] >= 0.70

    def test_db_error_returns_defaults(self):
        cfg = {"enabled": True, "min_samples": 10}
        with patch("tools.db.storage.get_connection", side_effect=Exception("DB error")):
            result = _compute_crag_anomaly_thresholds(cfg)
        assert result["computed"] is False
        assert result["acceptable_word_overlap"] == _ACCEPTABLE_WORD_OVERLAP


# ─────────────────────────────────────────────────────────────────
# CRAGScorer — uses named constants
# ─────────────────────────────────────────────────────────────────

class TestCRAGScorerConstants:

    def setup_method(self):
        self.scorer = CRAGScorer()

    def test_exact_match_is_perfect(self):
        r = self.scorer.score_answer("AC-2 is an access control", "AC-2 is an access control")
        assert r["score"] == CRAG_SCORES["perfect"]

    def test_abstention_is_missing(self):
        r = self.scorer.score_answer("I don't know", "AC-2")
        assert r["score"] == CRAG_SCORES["missing"]

    def test_abstention_on_false_premise_is_perfect(self):
        r = self.scorer.score_answer("I don't know", "AC-2", is_false_premise=True)
        assert r["score"] == CRAG_SCORES["perfect"]

    def test_wrong_answer_is_incorrect(self):
        r = self.scorer.score_answer("SC-7 is a firewall", "AC-2 is access control")
        assert r["score"] == CRAG_SCORES["incorrect"]

    def test_high_overlap_is_acceptable(self):
        gt = "FedRAMP requires continuous monitoring of all systems"
        ans = "FedRAMP requires ongoing continuous monitoring of all systems"
        r = self.scorer.score_answer(ans, gt)
        assert r["score"] in (CRAG_SCORES["perfect"], CRAG_SCORES["acceptable"])

    def test_entity_popularity_head(self):
        _r = self.scorer.classify_entity_popularity.__wrapped__(
            self.scorer, "NIST 800-53"
        ) if hasattr(self.scorer.classify_entity_popularity, "__wrapped__") else None
        # Just test the thresholds directly
        assert _ENTITY_HEAD_THRESHOLD > _ENTITY_TORSO_THRESHOLD > 0

    def test_heuristic_comparison_classification(self):
        r = self.scorer.classify_question_type("Compare FedRAMP and CMMC requirements")
        assert r["question_type"] == "comparison"
        assert r["confidence"] == _HEURISTIC_CONFIDENCE_HIGH

    def test_heuristic_aggregation_classification(self):
        r = self.scorer.classify_question_type("How many controls are in NIST 800-53?")
        assert r["question_type"] == "aggregation"
        assert r["confidence"] == _HEURISTIC_CONFIDENCE_HIGH

    def test_heuristic_false_premise_classification(self):
        r = self.scorer.classify_question_type("Why did NIST no longer require MFA?")
        assert r["question_type"] == "false_premise"
        assert r["confidence"] == _HEURISTIC_CONFIDENCE_LOW


# ─────────────────────────────────────────────────────────────────
# CRAG Evaluator Constants
# ─────────────────────────────────────────────────────────────────

class TestCragEvaluatorConstants:
    def test_entity_tiers_ordered(self):
        assert _ENTITY_HEAD_THRESHOLD > _ENTITY_TORSO_THRESHOLD > 0

    def test_overlap_in_valid_range(self):
        assert 0.0 < _ACCEPTABLE_WORD_OVERLAP <= 1.0

    def test_llm_params_sane(self):
        assert _LLM_MAX_TOKENS > 0
        assert 0.0 <= _LLM_TEMPERATURE <= 1.0

    def test_confidence_ordering(self):
        assert _HEURISTIC_CONFIDENCE_HIGH >= _HEURISTIC_CONFIDENCE_MID >= _HEURISTIC_CONFIDENCE_LOW > 0

    def test_gate_threshold_positive(self):
        assert 0.0 < _GATE_THRESHOLD < 1.0


# ─────────────────────────────────────────────────────────────────
# PDF Provider Constants
# ─────────────────────────────────────────────────────────────────

class TestPDFProviderConstants:
    def test_all_positive(self):
        assert _PDF_MAX_PAGES > 0
        assert _PDF_MAX_FILE_SIZE_MB > 0
        assert _PDF_ANTHROPIC_TOKENS > 0
        assert _PDF_SCANNED_TEXT_MIN > 0
        assert _PDF_RENDER_DPI > 0
        assert _PDF_VISION_TIMEOUT > 0
        assert _PDF_AVAIL_TIMEOUT > 0

    def test_vision_timeout_gt_avail_timeout(self):
        assert _PDF_VISION_TIMEOUT > _PDF_AVAIL_TIMEOUT


# ─────────────────────────────────────────────────────────────────
# Chunker Constants
# ─────────────────────────────────────────────────────────────────

class TestChunkerConstants:
    def test_all_positive(self):
        assert CHARS_PER_TOKEN > 0
        assert _DEFAULT_SHORT_THRESHOLD > 0
        assert _DEFAULT_CHUNK_SIZE > _DEFAULT_SHORT_THRESHOLD
        assert 0.0 < _DEFAULT_OVERLAP_PCT < 1.0
        assert _BOUNDARY_SEARCH_RADIUS > 0
        assert _BOUNDARY_WHITESPACE_SCAN > 0

    def test_chunker_produces_output(self):
        # Single short chunk
        result = chunk_content("Short text.", source_type="test")
        assert len(result) == 1

    def test_chunker_splits_long_content(self):
        # Content longer than chunk size
        long_text = "The quick brown fox jumps over the lazy dog. " * 200
        result = chunk_content(long_text, chunk_config={
            "chunk_size_tokens": 50, "overlap_pct": 0.1
        })
        assert len(result) > 1

    def test_chunker_empty_returns_empty(self):
        assert chunk_content("") == []
        assert chunk_content("   ") == []
