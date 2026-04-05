#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for CRAG evaluator (D-RAG-23)."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.rag.crag_evaluator import (
    CRAG_SCORES,
    ENTITY_POPULARITY_TIERS,
    QUESTION_TYPES,
    CRAGBenchmarkRunner,
    CRAGScorer,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def scorer() -> CRAGScorer:
    return CRAGScorer()


@pytest.fixture()
def runner() -> CRAGBenchmarkRunner:
    return CRAGBenchmarkRunner(config={})


# ---------------------------------------------------------------------------
# Scoring tests
# ---------------------------------------------------------------------------


class TestCRAGScorer:
    def test_score_exact_match(self, scorer: CRAGScorer) -> None:
        """Exact answer against ground truth must score perfect (1.0)."""
        result = scorer.score_answer(
            answer="NIST 800-53",
            ground_truth="NIST 800-53",
        )
        assert result["score"] == CRAG_SCORES["perfect"]
        assert result["label"] == "perfect"

    def test_score_exact_match_case_insensitive(self, scorer: CRAGScorer) -> None:
        """Exact match should be case-insensitive after normalisation."""
        result = scorer.score_answer(
            answer="nist 800-53",
            ground_truth="NIST 800-53",
        )
        assert result["score"] == CRAG_SCORES["perfect"]
        assert result["label"] == "perfect"

    def test_score_acceptable_match(self, scorer: CRAGScorer) -> None:
        """An answer that contains the ground truth should score acceptable (0.5)."""
        result = scorer.score_answer(
            answer="NIST 800-53 is a security controls catalog published by NIST.",
            ground_truth="NIST 800-53",
        )
        # Containment check: ground truth is a substring of answer
        assert result["score"] == CRAG_SCORES["acceptable"]
        assert result["label"] == "acceptable"

    def test_score_wrong_answer(self, scorer: CRAGScorer) -> None:
        """Factually wrong answer must score incorrect (-1.0) — hallucination penalty."""
        result = scorer.score_answer(
            answer="The answer is 42 and everything is fine.",
            ground_truth="NIST 800-53 is a catalog of security and privacy controls.",
        )
        assert result["score"] == CRAG_SCORES["incorrect"]
        assert result["label"] == "incorrect"

    def test_score_abstention_empty(self, scorer: CRAGScorer) -> None:
        """Empty answer must score missing (0.0)."""
        result = scorer.score_answer(answer="", ground_truth="NIST 800-53")
        assert result["score"] == CRAG_SCORES["missing"]
        assert result["label"] == "missing"

    def test_score_abstention_i_dont_know(self, scorer: CRAGScorer) -> None:
        """'I don't know' must score missing (0.0)."""
        result = scorer.score_answer(
            answer="I don't know",
            ground_truth="NIST 800-53",
        )
        assert result["score"] == CRAG_SCORES["missing"]
        assert result["label"] == "missing"

    def test_score_abstention_cannot_answer(self, scorer: CRAGScorer) -> None:
        """'Cannot answer' must score missing (0.0)."""
        result = scorer.score_answer(
            answer="cannot answer",
            ground_truth="FedRAMP",
        )
        assert result["score"] == CRAG_SCORES["missing"]
        assert result["label"] == "missing"

    def test_score_false_premise_abstention(self, scorer: CRAGScorer) -> None:
        """Abstaining on a false-premise question must score perfect (1.0)."""
        result = scorer.score_answer(
            answer="I don't know",
            ground_truth="CMMC Level 1 does not require quantum encryption.",
            is_false_premise=True,
        )
        assert result["score"] == CRAG_SCORES["perfect"]
        assert result["label"] == "perfect"

    def test_score_false_premise_abstention_empty(self, scorer: CRAGScorer) -> None:
        """Empty answer on a false-premise question must score perfect (1.0)."""
        result = scorer.score_answer(
            answer="",
            ground_truth="This is a false premise.",
            is_false_premise=True,
        )
        assert result["score"] == CRAG_SCORES["perfect"]
        assert result["label"] == "perfect"

    def test_score_false_premise_wrong(self, scorer: CRAGScorer) -> None:
        """Substantive answer to a false-premise question that accepts the premise
        must score incorrect (-1.0)."""
        result = scorer.score_answer(
            answer="CMMC Level 1 requires AES-256 quantum encryption.",
            ground_truth="CMMC Level 1 does not require quantum encryption.",
            is_false_premise=True,
        )
        assert result["score"] == CRAG_SCORES["incorrect"]
        assert result["label"] == "incorrect"

    def test_score_false_premise_acknowledged(self, scorer: CRAGScorer) -> None:
        """Answer that explicitly identifies the false premise scores perfect (1.0)."""
        result = scorer.score_answer(
            answer="This is a false premise — CMMC Level 1 does not require quantum encryption.",
            ground_truth="CMMC Level 1 does not require quantum encryption.",
            is_false_premise=True,
        )
        assert result["score"] == CRAG_SCORES["perfect"]
        assert result["label"] == "perfect"

    def test_score_none_answer(self, scorer: CRAGScorer) -> None:
        """None answer must be treated as missing (0.0), not crash."""
        result = scorer.score_answer(answer=None, ground_truth="something")  # type: ignore[arg-type]
        assert result["score"] == CRAG_SCORES["missing"]
        assert result["label"] == "missing"


# ---------------------------------------------------------------------------
# Question-type classification tests
# ---------------------------------------------------------------------------


class TestQuestionTypeClassification:
    def test_classify_comparison(self, scorer: CRAGScorer) -> None:
        """'Compare X and Y' must classify as comparison."""
        result = scorer.classify_question_type("Compare FedRAMP and CMMC processes.")
        assert result["question_type"] == "comparison"
        assert result["method"] == "heuristic"
        assert result["confidence"] > 0.5

    def test_classify_aggregation(self, scorer: CRAGScorer) -> None:
        """'How many widgets?' must classify as aggregation."""
        result = scorer.classify_question_type("How many controls are in the AC family?")
        assert result["question_type"] == "aggregation"
        assert result["method"] == "heuristic"

    def test_classify_set(self, scorer: CRAGScorer) -> None:
        """'List all categories' must classify as set."""
        result = scorer.classify_question_type("List all NIST 800-53 control families.")
        assert result["question_type"] == "set"
        assert result["method"] == "heuristic"

    def test_classify_multi_hop(self, scorer: CRAGScorer) -> None:
        """Multi-hop question (sequential 'and then') must classify as multi_hop."""
        result = scorer.classify_question_type(
            "What is the first step of FedRAMP authorization and then what happens after submission?"
        )
        assert result["question_type"] == "multi_hop"
        assert result["method"] == "heuristic"

    def test_classify_multi_hop_multi_question(self, scorer: CRAGScorer) -> None:
        """Question with two '?' markers must classify as multi_hop."""
        result = scorer.classify_question_type("What is AC-2? What are its enhancements?")
        assert result["question_type"] == "multi_hop"
        assert result["method"] == "heuristic"

    def test_classify_condition(self, scorer: CRAGScorer) -> None:
        """Conditional question must classify as simple_condition."""
        result = scorer.classify_question_type("If an organization is at IL4, what encryption standard is required?")
        assert result["question_type"] == "simple_condition"
        assert result["method"] == "heuristic"

    def test_classify_post_processing(self, scorer: CRAGScorer) -> None:
        """Post-processing question must classify as post_processing."""
        result = scorer.classify_question_type("Sort the following controls by priority: AC-2, AC-3, AU-1.")
        assert result["question_type"] == "post_processing"
        assert result["method"] == "heuristic"

    def test_classify_simple(self, scorer: CRAGScorer) -> None:
        """Simple factual question must NOT be classified via regex (falls to LLM/default)."""
        # Mock the LLM classifier to avoid a live API call — the test only verifies
        # that the returned question_type is a valid QUESTION_TYPES value.
        with patch.object(scorer, "_classify_via_llm", return_value=None):
            result = scorer.classify_question_type("What is the capital of France?")
        # Regex has no positive signal for 'simple' — result falls to 'simple' default.
        assert result["question_type"] in QUESTION_TYPES

    def test_classify_versus(self, scorer: CRAGScorer) -> None:
        """'Versus' keyword must classify as comparison."""
        result = scorer.classify_question_type("FedRAMP versus CMMC: which is harder?")
        assert result["question_type"] == "comparison"

    def test_classify_count(self, scorer: CRAGScorer) -> None:
        """'Count' keyword must classify as aggregation."""
        result = scorer.classify_question_type("Count the number of privacy controls in NIST 800-53.")
        assert result["question_type"] == "aggregation"


# ---------------------------------------------------------------------------
# Constants integrity tests
# ---------------------------------------------------------------------------


class TestConstants:
    def test_question_types_constant(self) -> None:
        """QUESTION_TYPES must contain exactly the 8 CRAG question types."""
        expected = {
            "simple",
            "simple_condition",
            "set",
            "comparison",
            "aggregation",
            "multi_hop",
            "post_processing",
            "false_premise",
        }
        assert set(QUESTION_TYPES) == expected
        assert len(QUESTION_TYPES) == 8

    def test_entity_popularity_tiers(self) -> None:
        """ENTITY_POPULARITY_TIERS must contain exactly 3 tiers."""
        assert set(ENTITY_POPULARITY_TIERS) == {"head", "torso", "tail"}
        assert len(ENTITY_POPULARITY_TIERS) == 3

    def test_crag_scores_range(self) -> None:
        """CRAG score values must match the published rubric."""
        assert CRAG_SCORES["perfect"] == 1.0
        assert CRAG_SCORES["acceptable"] == 0.5
        assert CRAG_SCORES["missing"] == 0.0
        assert CRAG_SCORES["incorrect"] == -1.0

    def test_crag_scores_ordering(self) -> None:
        """Scores must be strictly ordered: incorrect < missing < acceptable < perfect."""
        assert CRAG_SCORES["incorrect"] < CRAG_SCORES["missing"] < CRAG_SCORES["acceptable"] < CRAG_SCORES["perfect"]

    def test_all_question_types_are_strings(self) -> None:
        """All QUESTION_TYPES entries must be non-empty strings."""
        for qt in QUESTION_TYPES:
            assert isinstance(qt, str) and qt


# ---------------------------------------------------------------------------
# CRAGBenchmarkRunner tests
# ---------------------------------------------------------------------------


class TestCRAGBenchmarkRunner:
    def test_benchmark_runner_empty_list(self, runner: CRAGBenchmarkRunner, tmp_path: Path) -> None:
        """Empty test set must return a valid dict with zero cases and score 0.0."""
        empty_file = tmp_path / "empty.json"
        empty_file.write_text("[]", encoding="utf-8")

        result = runner.run_campaign(test_set_path=str(empty_file))

        assert isinstance(result, dict)
        assert result["total_cases"] == 0
        assert result["aggregate_crag_score"] == 0.0
        assert result["status"] == "completed"
        assert "campaign_id" in result
        assert "label_distribution" in result

    def test_benchmark_runner_missing_file(self, runner: CRAGBenchmarkRunner) -> None:
        """Missing test-set file must return a valid empty result, not raise."""
        result = runner.run_campaign(test_set_path="/nonexistent/path/test.json")
        assert isinstance(result, dict)
        assert result["total_cases"] == 0
        assert result["status"] == "completed"

    def test_benchmark_runner_single_perfect(self, runner: CRAGBenchmarkRunner, tmp_path: Path) -> None:
        """Single perfect-match case must yield aggregate score of 1.0."""
        test_set = [
            {
                "query": "What is NIST 800-53?",
                "question_type": "simple",
                "entity_popularity": "head",
                "ground_truth": "security controls catalog",
                "answer": "security controls catalog",
                "is_false_premise": False,
            }
        ]
        test_file = tmp_path / "test.json"
        test_file.write_text(json.dumps(test_set), encoding="utf-8")

        result = runner.run_campaign(test_set_path=str(test_file))

        assert result["total_cases"] == 1
        assert result["aggregate_crag_score"] == 1.0
        assert result["label_distribution"]["perfect"] == 1

    def test_benchmark_runner_single_incorrect(self, runner: CRAGBenchmarkRunner, tmp_path: Path) -> None:
        """Single incorrect answer must yield aggregate score of -1.0."""
        test_set = [
            {
                "query": "What is CMMC?",
                "question_type": "simple",
                "entity_popularity": "head",
                "ground_truth": "Cybersecurity Maturity Model Certification",
                "answer": "A type of cheese",
                "is_false_premise": False,
            }
        ]
        test_file = tmp_path / "test.json"
        test_file.write_text(json.dumps(test_set), encoding="utf-8")

        result = runner.run_campaign(test_set_path=str(test_file))

        assert result["total_cases"] == 1
        assert result["aggregate_crag_score"] == -1.0
        assert result["label_distribution"]["incorrect"] == 1

    def test_benchmark_runner_mixed_scores(self, runner: CRAGBenchmarkRunner, tmp_path: Path) -> None:
        """Mixed scores (1.0, -1.0) must average to 0.0."""
        test_set = [
            {
                "query": "What is FedRAMP?",
                "question_type": "simple",
                "entity_popularity": "head",
                "ground_truth": "Federal Risk and Authorization Management Program",
                "answer": "Federal Risk and Authorization Management Program",
                "is_false_premise": False,
            },
            {
                "query": "What is SOC 2?",
                "question_type": "simple",
                "entity_popularity": "torso",
                "ground_truth": "Service Organization Control 2",
                "answer": "A type of database query language",
                "is_false_premise": False,
            },
        ]
        test_file = tmp_path / "test.json"
        test_file.write_text(json.dumps(test_set), encoding="utf-8")

        result = runner.run_campaign(test_set_path=str(test_file))

        assert result["total_cases"] == 2
        assert result["aggregate_crag_score"] == pytest.approx(0.0)

    def test_benchmark_runner_by_question_type_keys(self, runner: CRAGBenchmarkRunner, tmp_path: Path) -> None:
        """by_question_type dict must use valid QUESTION_TYPES as keys."""
        test_set = [
            {
                "query": "How many controls?",
                "question_type": "aggregation",
                "entity_popularity": "head",
                "ground_truth": "25",
                "answer": "25",
                "is_false_premise": False,
            }
        ]
        test_file = tmp_path / "test.json"
        test_file.write_text(json.dumps(test_set), encoding="utf-8")

        result = runner.run_campaign(test_set_path=str(test_file))

        for key in result.get("by_question_type", {}):
            assert key in QUESTION_TYPES

    def test_benchmark_runner_by_entity_popularity_keys(self, runner: CRAGBenchmarkRunner, tmp_path: Path) -> None:
        """by_entity_popularity dict must use valid ENTITY_POPULARITY_TIERS as keys."""
        test_set = [
            {
                "query": "What is NIST?",
                "question_type": "simple",
                "entity_popularity": "tail",
                "ground_truth": "National Institute of Standards and Technology",
                "answer": "i don't know",
                "is_false_premise": False,
            }
        ]
        test_file = tmp_path / "test.json"
        test_file.write_text(json.dumps(test_set), encoding="utf-8")

        result = runner.run_campaign(test_set_path=str(test_file))

        for key in result.get("by_entity_popularity", {}):
            assert key in ENTITY_POPULARITY_TIERS

    def test_benchmark_runner_false_premise_campaign(self, runner: CRAGBenchmarkRunner, tmp_path: Path) -> None:
        """False-premise case where answer abstains must score 1.0."""
        test_set = [
            {
                "query": "What quantum encryption does CMMC Level 1 require?",
                "question_type": "false_premise",
                "entity_popularity": "tail",
                "ground_truth": "CMMC Level 1 does not require quantum encryption.",
                "answer": "I don't know",
                "is_false_premise": True,
            }
        ]
        test_file = tmp_path / "test.json"
        test_file.write_text(json.dumps(test_set), encoding="utf-8")

        result = runner.run_campaign(test_set_path=str(test_file))

        assert result["aggregate_crag_score"] == pytest.approx(1.0)
        assert result["label_distribution"]["perfect"] == 1

    def test_benchmark_runner_invalid_json(self, runner: CRAGBenchmarkRunner, tmp_path: Path) -> None:
        """Malformed JSON test set must return empty result, not raise."""
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("{not valid json}", encoding="utf-8")

        result = runner.run_campaign(test_set_path=str(bad_file))

        assert isinstance(result, dict)
        assert result["total_cases"] == 0

    def test_benchmark_runner_invalid_question_type(self, runner: CRAGBenchmarkRunner, tmp_path: Path) -> None:
        """Unknown question_type in test set must default to 'simple', not raise."""
        test_set = [
            {
                "query": "What is zero trust?",
                "question_type": "UNKNOWN_TYPE",
                "entity_popularity": "head",
                "ground_truth": "zero trust",
                "answer": "zero trust",
                "is_false_premise": False,
            }
        ]
        test_file = tmp_path / "test.json"
        test_file.write_text(json.dumps(test_set), encoding="utf-8")

        result = runner.run_campaign(test_set_path=str(test_file))

        assert result["total_cases"] == 1
        # Result should still score normally
        assert result["aggregate_crag_score"] in [-1.0, 0.0, 0.5, 1.0]
