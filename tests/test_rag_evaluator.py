#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for RAG evaluator anomaly detection + extracted thresholds (D-RAG-22).

Covers the aiify modernization (aiify-rm-6efad-phase-5480): hardcoded thresholds
extracted to named, config-overridable constants and an adaptive
anomaly-detection floor derived from historical evaluation runs.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.rag.evaluator import (  # noqa: E402
    RAGEvaluator,
    _ANOMALY_STDDEV_K,
    _DEFAULT_K,
    _LLM_ANSWER_CHARS,
    _LLM_CHUNK_CHARS,
    _LLM_CONTEXT_CHARS,
    _LLM_MAX_TOKENS,
    _MRR_ANOMALY_FLOOR,
    _NDCG_ANOMALY_FLOOR,
    _SCORE_PRECISION,
    _compute_eval_anomaly_thresholds,
    mrr,
    ndcg_at_k,
)
from tools.rag.vector_store_provider import SearchResult  # noqa: E402


# ---------------------------------------------------------------------------
# Extracted constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_constants_have_sane_defaults(self) -> None:
        assert _DEFAULT_K == 5
        assert _LLM_MAX_TOKENS == 256
        assert _LLM_CHUNK_CHARS == 300
        assert _LLM_CONTEXT_CHARS == 2000
        assert _LLM_ANSWER_CHARS == 1000
        assert _SCORE_PRECISION == 4

    def test_anomaly_floors_in_unit_range(self) -> None:
        assert 0.0 <= _NDCG_ANOMALY_FLOOR <= 1.0
        assert 0.0 <= _MRR_ANOMALY_FLOOR <= 1.0
        assert _ANOMALY_STDDEV_K > 0

    def test_ndcg_default_k_matches_constant(self) -> None:
        # No explicit k → uses _DEFAULT_K (5). A relevant item only at rank 6
        # should be excluded at the default cutoff.
        retrieved = [f"c{i}" for i in range(10)]
        assert ndcg_at_k(retrieved, ["c5"]) == 0.0  # rank 6 > k=5
        assert ndcg_at_k(retrieved, ["c4"]) > 0.0   # rank 5 <= k=5


# ---------------------------------------------------------------------------
# Adaptive anomaly thresholds
# ---------------------------------------------------------------------------


class TestComputeAnomalyThresholds:
    def test_disabled_returns_fallback(self) -> None:
        th = _compute_eval_anomaly_thresholds({"enabled": False})
        assert th["computed"] is False
        assert th["ndcg_floor"] == _NDCG_ANOMALY_FLOOR
        assert th["mrr_floor"] == _MRR_ANOMALY_FLOOR

    def test_disabled_honors_config_fallbacks(self) -> None:
        th = _compute_eval_anomaly_thresholds(
            {"enabled": False, "fallback_ndcg_floor": 0.42, "fallback_mrr_floor": 0.21}
        )
        assert th["ndcg_floor"] == 0.42
        assert th["mrr_floor"] == 0.21

    def test_none_config_uses_module_defaults(self) -> None:
        th = _compute_eval_anomaly_thresholds(None)
        # No history in a fresh test DB → not computed, module-level floors.
        assert th["ndcg_floor"] == _NDCG_ANOMALY_FLOOR
        assert th["mrr_floor"] == _MRR_ANOMALY_FLOOR

    def test_high_min_samples_forces_fallback(self) -> None:
        # An unreachable min_samples guarantees the fallback branch even if the
        # table happens to hold rows.
        th = _compute_eval_anomaly_thresholds({"enabled": True, "min_samples": 10**9})
        assert th["computed"] is False


# ---------------------------------------------------------------------------
# flag_anomalies
# ---------------------------------------------------------------------------


@pytest.fixture
def evaluator() -> RAGEvaluator:
    # Disable adaptive calibration so floors are deterministic at 0.30.
    return RAGEvaluator(config={"anomaly_detection": {"enabled": False}})


class TestFlagAnomalies:
    def test_below_floor_is_flagged(self, evaluator: RAGEvaluator) -> None:
        result = evaluator.flag_anomalies({"ndcg_at_k": 0.1, "mrr": 0.1})
        assert result["anomalous"] is True
        assert len(result["reasons"]) == 2

    def test_above_floor_is_clean(self, evaluator: RAGEvaluator) -> None:
        result = evaluator.flag_anomalies({"ndcg_at_k": 0.9, "mrr": 0.9})
        assert result["anomalous"] is False
        assert result["reasons"] == []

    def test_partial_metrics_only_flags_present(self, evaluator: RAGEvaluator) -> None:
        result = evaluator.flag_anomalies({"ndcg_at_k": 0.1})
        assert result["anomalous"] is True
        assert all("ndcg" in r for r in result["reasons"])

    def test_missing_metrics_is_clean(self, evaluator: RAGEvaluator) -> None:
        assert evaluator.flag_anomalies({})["anomalous"] is False

    def test_exact_floor_not_flagged(self, evaluator: RAGEvaluator) -> None:
        # Floor comparison is strict "<", so equal-to-floor passes.
        result = evaluator.flag_anomalies({"ndcg_at_k": 0.30, "mrr": 0.30})
        assert result["anomalous"] is False


# ---------------------------------------------------------------------------
# evaluate_retrieval surfaces anomalies + uses precision constant
# ---------------------------------------------------------------------------


def _mk_results(ids):
    return [SearchResult(chunk_id=i, content=f"content for {i}", score=1.0) for i in ids]


class TestEvaluateRetrieval:
    def test_low_quality_retrieval_surfaces_anomaly(self, evaluator: RAGEvaluator) -> None:
        # Relevant chunk is absent from results → ndcg/mrr = 0 → anomaly.
        results = _mk_results(["x1", "x2", "x3"])
        metrics = evaluator.evaluate_retrieval(
            query="AC-2", results=results, ground_truth_ids=["gt-1"]
        )
        assert metrics["ndcg_at_k"] == 0.0
        assert metrics["mrr"] == 0.0
        assert metrics["anomaly"]["anomalous"] is True

    def test_good_retrieval_has_no_anomaly_key(self, evaluator: RAGEvaluator) -> None:
        results = _mk_results(["gt-1", "x2", "x3"])
        metrics = evaluator.evaluate_retrieval(
            query="AC-2", results=results, ground_truth_ids=["gt-1"]
        )
        assert metrics["ndcg_at_k"] == 1.0
        assert "anomaly" not in metrics

    def test_no_ground_truth_skips_deterministic_and_anomaly(
        self, evaluator: RAGEvaluator
    ) -> None:
        results = _mk_results(["x1"])
        metrics = evaluator.evaluate_retrieval(query="AC-2", results=results)
        assert "ndcg_at_k" not in metrics
        assert "anomaly" not in metrics

    def test_scores_rounded_to_precision(self, evaluator: RAGEvaluator) -> None:
        # rank-2 hit → mrr = 0.5, ndcg = 1/log2(3) ≈ 0.6309 (4 dp).
        results = _mk_results(["x1", "gt-1", "x3"])
        metrics = evaluator.evaluate_retrieval(
            query="q", results=results, ground_truth_ids=["gt-1"]
        )
        assert metrics["mrr"] == round(mrr(["x1", "gt-1", "x3"], ["gt-1"]), _SCORE_PRECISION)
        # rounded value must not carry more than _SCORE_PRECISION decimals
        assert metrics["ndcg_at_k"] == round(metrics["ndcg_at_k"], _SCORE_PRECISION)
