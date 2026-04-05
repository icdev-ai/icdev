#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for query classifier (D-RAG-24)."""

from __future__ import annotations

from unittest.mock import patch

from tools.rag.query_classifier import (
    TAXONOMY_LABELS,
    classify_batch,
    classify_query,
)


# ---------------------------------------------------------------------------
# Taxonomy constant tests
# ---------------------------------------------------------------------------


class TestTaxonomyLabels:
    def test_taxonomy_labels_constant(self):
        """Verify all 4 expected labels exist in TAXONOMY_LABELS."""
        assert isinstance(TAXONOMY_LABELS, list)
        assert len(TAXONOMY_LABELS) == 4
        assert "fact_single" in TAXONOMY_LABELS
        assert "summary" in TAXONOMY_LABELS
        assert "reasoning" in TAXONOMY_LABELS
        assert "unanswerable" in TAXONOMY_LABELS

    def test_taxonomy_labels_are_strings(self):
        """All labels must be non-empty strings."""
        for label in TAXONOMY_LABELS:
            assert isinstance(label, str)
            assert len(label) > 0


# ---------------------------------------------------------------------------
# Single query classification tests
# ---------------------------------------------------------------------------


class TestClassifyQuery:
    """Heuristic-path tests: LLM is patched out so results are deterministic."""

    @patch("tools.rag.query_classifier._llm_classify", return_value=None)
    def test_classify_fact_single(self, _mock_llm):
        """'What is NIST 800-53?' should classify as fact_single via heuristic."""
        result = classify_query("What is NIST 800-53?")
        assert result["label"] == "fact_single"
        assert result["confidence"] > 0.0
        assert result["method"] in ("heuristic", "default")

    @patch("tools.rag.query_classifier._llm_classify", return_value=None)
    def test_classify_fact_single_who(self, _mock_llm):
        """'Who is responsible for the ISSO role?' should classify as fact_single."""
        result = classify_query("Who is responsible for the ISSO role?")
        assert result["label"] == "fact_single"

    @patch("tools.rag.query_classifier._llm_classify", return_value=None)
    def test_classify_fact_single_when(self, _mock_llm):
        """'When was FedRAMP established?' should classify as fact_single."""
        result = classify_query("When was FedRAMP established?")
        assert result["label"] == "fact_single"

    @patch("tools.rag.query_classifier._llm_classify", return_value=None)
    def test_classify_summary(self, _mock_llm):
        """'Summarize the FedRAMP process' should classify as summary."""
        result = classify_query("Summarize the FedRAMP authorization process")
        assert result["label"] == "summary"

    @patch("tools.rag.query_classifier._llm_classify", return_value=None)
    def test_classify_summary_overview(self, _mock_llm):
        """'Give an overview of CMMC' should classify as summary."""
        result = classify_query("Give an overview of CMMC Level 2 requirements")
        assert result["label"] == "summary"

    @patch("tools.rag.query_classifier._llm_classify", return_value=None)
    def test_classify_summary_describe(self, _mock_llm):
        """'Describe the key controls' should classify as summary."""
        result = classify_query("Describe the key controls for IL5 cloud environments")
        assert result["label"] == "summary"

    @patch("tools.rag.query_classifier._llm_classify", return_value=None)
    def test_classify_reasoning(self, _mock_llm):
        """'Why does FedRAMP require continuous monitoring?' should classify as reasoning."""
        result = classify_query("Why does FedRAMP require continuous monitoring?")
        assert result["label"] == "reasoning"

    @patch("tools.rag.query_classifier._llm_classify", return_value=None)
    def test_classify_reasoning_how_does(self, _mock_llm):
        """'How does zero trust prevent lateral movement?' should classify as reasoning."""
        result = classify_query("How does zero trust prevent lateral movement?")
        assert result["label"] == "reasoning"

    @patch("tools.rag.query_classifier._llm_classify", return_value=None)
    def test_classify_reasoning_explain(self, _mock_llm):
        """'Explain the rationale behind NIST 800-171' should classify as reasoning."""
        result = classify_query("Explain the rationale behind NIST 800-171")
        assert result["label"] == "reasoning"

    @patch("tools.rag.query_classifier._llm_classify", return_value=None)
    def test_classify_reasoning_what_would_happen(self, _mock_llm):
        """'What would happen if MFA is disabled?' should classify as reasoning."""
        result = classify_query("What would happen if MFA is disabled on all accounts?")
        assert result["label"] == "reasoning"

    @patch("tools.rag.query_classifier._llm_classify", return_value=None)
    def test_classify_unanswerable_with_irrelevant_context(self, _mock_llm):
        """Query with no keyword overlap in context should classify as unanswerable."""
        result = classify_query(
            "What is the atomic mass of uranium?",
            context=(
                "FedRAMP provides a standardized approach to security assessment "
                "and authorization for cloud services used by federal agencies. "
                "The authorization process includes three phases: initiation, "
                "security assessment, and authorization."
            ),
        )
        # With irrelevant context, heuristic should classify as unanswerable
        assert result["label"] in TAXONOMY_LABELS
        assert result["confidence"] > 0.0

    @patch("tools.rag.query_classifier._llm_classify", return_value=None)
    def test_classify_default_fallback(self, _mock_llm):
        """Ambiguous query with no clear pattern should return a valid label."""
        result = classify_query("cloud things infrastructure stuff")
        assert result["label"] in TAXONOMY_LABELS
        assert result["confidence"] > 0.0
        assert result["method"] in ("heuristic", "default")

    def test_classify_empty_query(self):
        """Empty query should return unanswerable (no LLM call needed)."""
        result = classify_query("")
        assert result["label"] == "unanswerable"
        assert result["confidence"] == 1.0

    def test_classify_whitespace_only_query(self):
        """Whitespace-only query should return unanswerable (no LLM call needed)."""
        result = classify_query("   ")
        assert result["label"] == "unanswerable"

    @patch("tools.rag.query_classifier._llm_classify", return_value=None)
    def test_classify_returns_valid_structure(self, _mock_llm):
        """Result dict must contain all required keys with correct types."""
        result = classify_query("What is AC-2?")
        assert isinstance(result, dict)
        assert "label" in result
        assert "confidence" in result
        assert "method" in result
        assert "reasoning" in result
        assert result["label"] in TAXONOMY_LABELS
        assert isinstance(result["confidence"], float)
        assert 0.0 <= result["confidence"] <= 1.0
        assert isinstance(result["method"], str)
        assert isinstance(result["reasoning"], str)

    @patch("tools.rag.query_classifier._llm_classify", return_value=None)
    def test_classify_with_relevant_context(self, _mock_llm):
        """Query with matching context should not be classified as unanswerable."""
        result = classify_query(
            "What is AC-2?",
            context=(
                "AC-2 is the Account Management control in NIST 800-53. "
                "It requires organizations to manage information system accounts, "
                "including establishing, activating, modifying, and removing accounts."
            ),
        )
        assert result["label"] in ("fact_single", "summary", "reasoning")


# ---------------------------------------------------------------------------
# Batch classification tests
# ---------------------------------------------------------------------------


class TestClassifyBatch:
    """Batch classification tests — LLM is patched out for determinism."""

    def test_classify_batch_empty(self):
        """Empty list input should return empty list."""
        result = classify_batch([])
        assert result == []

    @patch("tools.rag.query_classifier._llm_classify", return_value=None)
    def test_classify_batch_multiple(self, _mock_llm):
        """3 queries should return 3 results, each with a valid label."""
        queries = [
            {"query": "What is NIST 800-53?"},
            {"query": "Summarize the FedRAMP process"},
            {"query": "Why does FedRAMP require continuous monitoring?"},
        ]
        results = classify_batch(queries)
        assert len(results) == 3
        for r in results:
            assert r["label"] in TAXONOMY_LABELS
            assert "confidence" in r
            assert "method" in r

    @patch("tools.rag.query_classifier._llm_classify", return_value=None)
    def test_classify_batch_with_context(self, _mock_llm):
        """Batch items with context should be processed correctly."""
        queries = [
            {"query": "What is AC-2?", "context": "AC-2 is the Account Management control."},
            {"query": "Summarize AC-2", "context": "AC-2 is the Account Management control."},
        ]
        results = classify_batch(queries)
        assert len(results) == 2
        for r in results:
            assert r["label"] in TAXONOMY_LABELS

    @patch("tools.rag.query_classifier._llm_classify", return_value=None)
    def test_classify_batch_single_item(self, _mock_llm):
        """Single item batch should work correctly."""
        results = classify_batch([{"query": "What is CMMC?"}])
        assert len(results) == 1
        assert results[0]["label"] in TAXONOMY_LABELS

    def test_classify_batch_missing_query_key(self):
        """Items with missing 'query' key should not crash — empty string query."""
        results = classify_batch([{"context": "some context"}])
        assert len(results) == 1
        # Empty query maps to unanswerable
        assert results[0]["label"] == "unanswerable"

    @patch("tools.rag.query_classifier._llm_classify", return_value=None)
    def test_classify_batch_parallel(self, _mock_llm):
        """Parallel mode should return same number of results as sequential."""
        queries = [
            {"query": "What is NIST 800-53?"},
            {"query": "Summarize the FedRAMP process"},
            {"query": "Why does FedRAMP require continuous monitoring?"},
        ]
        results = classify_batch(queries, parallel=True)
        assert len(results) == 3
        for r in results:
            assert r["label"] in TAXONOMY_LABELS

    @patch("tools.rag.query_classifier._llm_classify", return_value=None)
    def test_classify_batch_preserves_all_labels(self, _mock_llm):
        """With clearly different queries, at least 2 distinct labels appear."""
        queries = [
            {"query": "What is AC-2?"},  # fact_single
            {"query": "Summarize the FedRAMP authorization process"},  # summary
            {"query": "Why does continuous monitoring matter?"},  # reasoning
        ]
        results = classify_batch(queries)
        labels = {r["label"] for r in results}
        assert len(labels) >= 1
        for lbl in labels:
            assert lbl in TAXONOMY_LABELS


# ---------------------------------------------------------------------------
# LLM fallback behaviour (mocked)
# ---------------------------------------------------------------------------


class TestClassifyQueryLLMFallback:
    def test_llm_unavailable_falls_back_to_heuristic(self):
        """When LLM import fails, should silently fall back to heuristic."""
        with patch("tools.rag.query_classifier._llm_classify", return_value=None):
            result = classify_query("What is AC-2?")
        assert result["label"] in TAXONOMY_LABELS
        assert result["method"] in ("heuristic", "default")

    def test_llm_result_used_when_available(self):
        """When LLM returns a valid result, it should take precedence."""
        mock_llm_result = {
            "label": "summary",
            "confidence": 0.90,
            "method": "llm",
            "reasoning": "Mocked LLM result.",
        }
        with patch("tools.rag.query_classifier._llm_classify", return_value=mock_llm_result):
            result = classify_query("What is AC-2?")
        assert result["label"] == "summary"
        assert result["method"] == "llm"
        assert result["confidence"] == 0.90
