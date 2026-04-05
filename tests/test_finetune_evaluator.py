#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for Phase 3 fine-tuning evaluation, promotion, and retrain trigger.

Covers: evaluator (BLEU/ROUGE-L/perplexity), ab_evaluator, promotion_manager, retrain_trigger.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------- Fixtures ----------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent


def _create_ft_tables(conn: sqlite3.Connection) -> None:
    """Create all FT tables for testing."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS ft_datasets (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            purpose TEXT DEFAULT 'general',
            base_model TEXT DEFAULT 'qwen3:latest',
            version INTEGER DEFAULT 1,
            example_count INTEGER DEFAULT 0,
            content_hash TEXT DEFAULT '',
            classification TEXT NOT NULL DEFAULT 'CUI',
            tenant_id TEXT DEFAULT '',
            project_id TEXT DEFAULT '',
            created_by TEXT DEFAULT '',
            status TEXT DEFAULT 'draft',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS ft_dataset_examples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dataset_id TEXT NOT NULL,
            system_prompt TEXT DEFAULT '',
            user_input TEXT NOT NULL,
            expected_output TEXT NOT NULL,
            source TEXT DEFAULT 'manual',
            source_chunk_id TEXT DEFAULT '',
            source_document_id TEXT DEFAULT '',
            quality_score REAL DEFAULT 0.0,
            compliance_score REAL DEFAULT 0.0,
            relevance_score REAL DEFAULT 0.0,
            approved INTEGER DEFAULT 0,
            labeled_by TEXT DEFAULT '',
            labeled_at TIMESTAMP,
            content_hash TEXT NOT NULL,
            classification TEXT NOT NULL DEFAULT 'CUI',
            tenant_id TEXT DEFAULT '',
            project_id TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS ft_training_jobs (
            id TEXT PRIMARY KEY,
            dataset_id TEXT NOT NULL,
            provider TEXT NOT NULL DEFAULT 'unsloth_local',
            base_model TEXT NOT NULL DEFAULT 'qwen3:latest',
            status TEXT NOT NULL DEFAULT 'pending',
            hyperparams TEXT DEFAULT '{}',
            lora_rank INTEGER DEFAULT 16,
            learning_rate REAL DEFAULT 2e-4,
            epochs INTEGER DEFAULT 3,
            batch_size INTEGER DEFAULT 2,
            max_seq_length INTEGER DEFAULT 2048,
            gpu_count INTEGER DEFAULT 1,
            distributed INTEGER DEFAULT 0,
            output_dir TEXT DEFAULT '',
            adapter_path TEXT DEFAULT '',
            gguf_path TEXT DEFAULT '',
            ollama_model_name TEXT DEFAULT '',
            cloud_job_id TEXT DEFAULT '',
            loss_history TEXT DEFAULT '[]',
            training_duration_seconds INTEGER DEFAULT 0,
            total_steps INTEGER DEFAULT 0,
            error_message TEXT DEFAULT '',
            classification TEXT NOT NULL DEFAULT 'CUI',
            tenant_id TEXT DEFAULT '',
            project_id TEXT DEFAULT '',
            created_by TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            started_at TIMESTAMP,
            completed_at TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS ft_training_job_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            details TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS ft_model_versions (
            id TEXT PRIMARY KEY,
            job_id TEXT NOT NULL,
            model_name TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            base_model TEXT NOT NULL,
            adapter_path TEXT DEFAULT '',
            gguf_path TEXT DEFAULT '',
            ollama_model_name TEXT DEFAULT '',
            adapter_hash TEXT DEFAULT '',
            file_size_bytes INTEGER DEFAULT 0,
            eval_bleu REAL DEFAULT 0.0,
            eval_rouge_l REAL DEFAULT 0.0,
            eval_perplexity REAL DEFAULT 0.0,
            eval_custom TEXT DEFAULT '{}',
            status TEXT DEFAULT 'created',
            classification TEXT NOT NULL DEFAULT 'CUI',
            tenant_id TEXT DEFAULT '',
            project_id TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(model_name, version)
        );

        CREATE TABLE IF NOT EXISTS ft_active_models (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            function_name TEXT NOT NULL,
            model_version_id TEXT NOT NULL,
            ollama_model_name TEXT NOT NULL,
            routing_tier TEXT DEFAULT 'worker',
            activated_by TEXT DEFAULT '',
            activation_reason TEXT DEFAULT '',
            tenant_id TEXT DEFAULT '',
            project_id TEXT DEFAULT '',
            activated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            deactivated_at TIMESTAMP,
            UNIQUE(function_name, tenant_id, project_id)
        );

        CREATE TABLE IF NOT EXISTS ft_evaluations (
            id TEXT PRIMARY KEY,
            model_version_id TEXT NOT NULL,
            eval_type TEXT NOT NULL DEFAULT 'automated',
            test_set_size INTEGER DEFAULT 0,
            bleu_score REAL DEFAULT 0.0,
            rouge_l_score REAL DEFAULT 0.0,
            perplexity REAL DEFAULT 0.0,
            custom_metrics TEXT DEFAULT '{}',
            comparison_model TEXT DEFAULT '',
            comparison_scores TEXT DEFAULT '{}',
            statistical_significance REAL DEFAULT 0.0,
            pass_threshold INTEGER DEFAULT 0,
            details TEXT DEFAULT '{}',
            classification TEXT NOT NULL DEFAULT 'CUI',
            tenant_id TEXT DEFAULT '',
            project_id TEXT DEFAULT '',
            evaluated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS ft_promotion_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model_version_id TEXT NOT NULL,
            action TEXT NOT NULL,
            function_name TEXT NOT NULL,
            previous_model TEXT DEFAULT '',
            eval_score_summary TEXT DEFAULT '{}',
            reason TEXT DEFAULT '',
            actor TEXT DEFAULT '',
            tenant_id TEXT DEFAULT '',
            project_id TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS ft_hyperparam_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            search_id TEXT NOT NULL,
            job_id TEXT,
            hyperparams TEXT NOT NULL DEFAULT '{}',
            eval_bleu REAL DEFAULT 0.0,
            eval_rouge_l REAL DEFAULT 0.0,
            eval_perplexity REAL DEFAULT 0.0,
            composite_score REAL DEFAULT 0.0,
            is_best INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()


@pytest.fixture()
def ft_db(tmp_path):
    """Create a temp DB with all FT tables."""
    db_path = tmp_path / "test_ft.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    _create_ft_tables(conn)
    conn.close()
    return db_path


def _seed_dataset(db_path, ds_id="ds-test", name="Test Dataset", status="ready", purpose="general"):
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """INSERT INTO ft_datasets (id, name, status, purpose)
           VALUES (?, ?, ?, ?)""",
        (ds_id, name, status, purpose),
    )
    conn.commit()
    conn.close()


def _seed_example(db_path, ds_id="ds-test", user_input="Q", expected_output="A", approved=1, created_at=None):
    conn = sqlite3.connect(str(db_path))
    ch = hashlib.sha256(f"|{user_input}|{expected_output}".encode()).hexdigest()[:16]
    if created_at:
        conn.execute(
            """INSERT INTO ft_dataset_examples
               (dataset_id, user_input, expected_output, content_hash, approved, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (ds_id, user_input, expected_output, ch, approved, created_at),
        )
    else:
        conn.execute(
            """INSERT INTO ft_dataset_examples
               (dataset_id, user_input, expected_output, content_hash, approved)
               VALUES (?, ?, ?, ?, ?)""",
            (ds_id, user_input, expected_output, ch, approved),
        )
    conn.commit()
    conn.close()


def _seed_model_version(
    db_path,
    mv_id="mv-test",
    job_id="job-test",
    model_name="test-model",
    version=1,
    ollama_name="test:v1",
    eval_bleu=0.5,
    eval_rouge_l=0.6,
    eval_perplexity=5.0,
):
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """INSERT INTO ft_model_versions
           (id, job_id, model_name, version, base_model, ollama_model_name,
            eval_bleu, eval_rouge_l, eval_perplexity)
           VALUES (?, ?, ?, ?, 'qwen3:latest', ?, ?, ?, ?)""",
        (mv_id, job_id, model_name, version, ollama_name, eval_bleu, eval_rouge_l, eval_perplexity),
    )
    conn.commit()
    conn.close()


def _seed_training_job(db_path, job_id="job-test", ds_id="ds-test", status="completed", completed_at=None):
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """INSERT INTO ft_training_jobs (id, dataset_id, status, completed_at)
           VALUES (?, ?, ?, ?)""",
        (job_id, ds_id, status, completed_at),
    )
    conn.commit()
    conn.close()


def _seed_active_model(db_path, function_name="code_generation", mv_id="mv-test", ollama_name="test:v1"):
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """INSERT INTO ft_active_models
           (function_name, model_version_id, ollama_model_name, activated_by)
           VALUES (?, ?, ?, 'test')""",
        (function_name, mv_id, ollama_name),
    )
    conn.commit()
    conn.close()


# =============================================================================
# EVALUATOR — BLEU
# =============================================================================


class TestComputeBleu:
    """Test pure Python BLEU implementation."""

    def test_identical_strings(self):
        from tools.finetune.evaluator import compute_bleu

        score = compute_bleu("the cat sat on the mat", "the cat sat on the mat")
        assert score > 0.9

    def test_completely_different(self):
        from tools.finetune.evaluator import compute_bleu

        score = compute_bleu("hello world", "goodbye universe")
        assert score == 0.0

    def test_partial_overlap(self):
        from tools.finetune.evaluator import compute_bleu

        score = compute_bleu("the cat sat on the mat", "the cat is on the mat")
        assert 0.3 < score < 1.0

    def test_empty_reference(self):
        from tools.finetune.evaluator import compute_bleu

        assert compute_bleu("", "hello world") == 0.0

    def test_empty_hypothesis(self):
        from tools.finetune.evaluator import compute_bleu

        assert compute_bleu("hello world", "") == 0.0

    def test_both_empty(self):
        from tools.finetune.evaluator import compute_bleu

        assert compute_bleu("", "") == 0.0

    def test_case_insensitive(self):
        from tools.finetune.evaluator import compute_bleu

        score = compute_bleu(
            "The Cat SAT on the big green Mat today",
            "the cat sat on the big green mat today",
        )
        assert score > 0.9

    def test_brevity_penalty(self):
        from tools.finetune.evaluator import compute_bleu

        # Short hypothesis should get brevity penalty
        short = compute_bleu("the cat sat on the big green mat", "the cat")
        full = compute_bleu("the cat sat on the big green mat", "the cat sat on the big green mat")
        assert short < full


# =============================================================================
# EVALUATOR — ROUGE-L
# =============================================================================


class TestComputeRougeL:
    """Test pure Python ROUGE-L implementation."""

    def test_identical_strings(self):
        from tools.finetune.evaluator import compute_rouge_l

        score = compute_rouge_l("the cat sat on the mat", "the cat sat on the mat")
        assert score == 1.0

    def test_completely_different(self):
        from tools.finetune.evaluator import compute_rouge_l

        score = compute_rouge_l("hello world", "goodbye universe")
        assert score == 0.0

    def test_partial_overlap(self):
        from tools.finetune.evaluator import compute_rouge_l

        score = compute_rouge_l("the cat sat on the mat", "the cat is sitting on the mat")
        assert 0.3 < score < 1.0

    def test_empty_inputs(self):
        from tools.finetune.evaluator import compute_rouge_l

        assert compute_rouge_l("", "hello") == 0.0
        assert compute_rouge_l("hello", "") == 0.0

    def test_subsequence_detection(self):
        from tools.finetune.evaluator import compute_rouge_l

        # "a c e" is a common subsequence of both
        score = compute_rouge_l("a b c d e", "a x c y e")
        assert score > 0.0


# =============================================================================
# EVALUATOR — LCS
# =============================================================================


class TestLcsLength:
    """Test LCS helper function."""

    def test_basic_lcs(self):
        from tools.finetune.evaluator import _lcs_length

        assert _lcs_length(["a", "b", "c"], ["a", "c"]) == 2

    def test_no_common(self):
        from tools.finetune.evaluator import _lcs_length

        assert _lcs_length(["a", "b"], ["c", "d"]) == 0

    def test_empty_input(self):
        from tools.finetune.evaluator import _lcs_length

        assert _lcs_length([], ["a", "b"]) == 0
        assert _lcs_length(["a"], []) == 0


# =============================================================================
# EVALUATOR — Perplexity
# =============================================================================


class TestPerplexityEstimate:
    """Test perplexity estimation."""

    def test_fallback_returns_zero(self):
        """When LLM unavailable, returns 0.0 fallback."""
        from unittest.mock import patch
        from tools.finetune.evaluator import compute_perplexity_estimate

        # Mock the local import of LLMRouter to simulate unavailability
        with patch.dict("sys.modules", {"tools.llm.router": None}):
            score = compute_perplexity_estimate("nonexistent-model", ["hello world"])
            assert score == 0.0

    def test_empty_texts(self):
        from tools.finetune.evaluator import compute_perplexity_estimate

        score = compute_perplexity_estimate("model", [])
        assert score == 0.0


# =============================================================================
# EVALUATOR — evaluate_model pipeline
# =============================================================================


class TestEvaluateModel:
    """Test full evaluation pipeline."""

    def test_no_test_data_no_model(self, ft_db):
        from tools.finetune.evaluator import evaluate_model

        result = evaluate_model("nonexistent", db_path=ft_db)
        assert not result["success"]

    def test_no_test_data(self, ft_db):
        _seed_model_version(ft_db)
        from tools.finetune.evaluator import evaluate_model

        result = evaluate_model("mv-test", db_path=ft_db)
        assert not result["success"]
        assert "No test data" in result["error"]

    def test_evaluate_with_test_file(self, ft_db, tmp_path):
        _seed_model_version(ft_db)
        # Create a test JSONL file
        test_file = tmp_path / "test.jsonl"
        entries = [
            {
                "messages": [
                    {"role": "user", "content": "What is Python?"},
                    {"role": "assistant", "content": "Python is a programming language"},
                ]
            },
            {
                "messages": [
                    {"role": "user", "content": "What is Java?"},
                    {"role": "assistant", "content": "Java is a programming language"},
                ]
            },
        ]
        with open(test_file, "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

        from tools.finetune.evaluator import evaluate_model

        # Mock LLM-dependent helpers to avoid live Ollama/API inference calls
        with (
            patch("tools.finetune.evaluator.compute_perplexity_estimate", return_value=0.0),
            patch(
                "tools.finetune.evaluator._generate_predictions",
                side_effect=lambda _model, pairs: [p["expected_output"] for p in pairs],
            ),
        ):
            result = evaluate_model(
                "mv-test",
                test_set_path=str(test_file),
                db_path=ft_db,
            )
        assert result["success"]
        assert result["test_set_size"] == 2
        assert "bleu_score" in result
        assert "rouge_l_score" in result
        assert "eval_id" in result

    def test_evaluate_stores_in_db(self, ft_db, tmp_path):
        _seed_model_version(ft_db)
        test_file = tmp_path / "test.jsonl"
        entry = {"messages": [{"role": "user", "content": "Hello"}, {"role": "assistant", "content": "Hi there"}]}
        with open(test_file, "w") as f:
            f.write(json.dumps(entry) + "\n")

        from tools.finetune.evaluator import evaluate_model

        # Mock LLM-dependent helpers to avoid live Ollama/API inference calls
        with (
            patch("tools.finetune.evaluator.compute_perplexity_estimate", return_value=0.0),
            patch(
                "tools.finetune.evaluator._generate_predictions",
                side_effect=lambda _model, pairs: [p["expected_output"] for p in pairs],
            ),
        ):
            result = evaluate_model("mv-test", test_set_path=str(test_file), db_path=ft_db)
        assert result["success"]

        # Verify stored in ft_evaluations
        conn = sqlite3.connect(str(ft_db))
        row = conn.execute("SELECT * FROM ft_evaluations WHERE id = ?", (result["eval_id"],)).fetchone()
        conn.close()
        assert row is not None


class TestGetEvaluation:
    """Test evaluation retrieval."""

    def test_get_existing(self, ft_db):
        conn = sqlite3.connect(str(ft_db))
        conn.execute(
            """INSERT INTO ft_evaluations (id, model_version_id, eval_type, bleu_score)
               VALUES ('eval-test', 'mv-test', 'automated', 0.5)"""
        )
        conn.commit()
        conn.close()

        from tools.finetune.evaluator import get_evaluation

        result = get_evaluation("eval-test", db_path=ft_db)
        assert result["success"]
        assert result["evaluation"]["bleu_score"] == 0.5

    def test_get_nonexistent(self, ft_db):
        from tools.finetune.evaluator import get_evaluation

        result = get_evaluation("nope", db_path=ft_db)
        assert not result["success"]


class TestListEvaluations:
    """Test evaluation listing."""

    def test_list_empty(self, ft_db):
        from tools.finetune.evaluator import list_evaluations

        result = list_evaluations(db_path=ft_db)
        assert result["success"]
        assert result["count"] == 0

    def test_list_filtered(self, ft_db):
        conn = sqlite3.connect(str(ft_db))
        conn.execute(
            """INSERT INTO ft_evaluations (id, model_version_id, eval_type)
               VALUES ('eval-1', 'mv-a', 'automated')"""
        )
        conn.execute(
            """INSERT INTO ft_evaluations (id, model_version_id, eval_type)
               VALUES ('eval-2', 'mv-b', 'automated')"""
        )
        conn.commit()
        conn.close()

        from tools.finetune.evaluator import list_evaluations

        result = list_evaluations(model_version_id="mv-a", db_path=ft_db)
        assert result["success"]
        assert result["count"] == 1


# =============================================================================
# A/B EVALUATOR
# =============================================================================


class TestPairedTTest:
    """Test paired t-test implementation."""

    def test_identical_scores(self):
        from tools.finetune.ab_evaluator import _paired_t_test

        p = _paired_t_test([0.5, 0.6, 0.7], [0.5, 0.6, 0.7])
        assert p == 1.0  # No difference

    def test_very_different_scores(self):
        from tools.finetune.ab_evaluator import _paired_t_test

        p = _paired_t_test([0.9, 0.9, 0.9, 0.9], [0.1, 0.1, 0.1, 0.1])
        assert p < 0.05  # Highly significant

    def test_too_few_samples(self):
        from tools.finetune.ab_evaluator import _paired_t_test

        p = _paired_t_test([0.5], [0.3])
        assert p == 1.0  # Can't compute with < 2 samples

    def test_mismatched_lengths(self):
        from tools.finetune.ab_evaluator import _paired_t_test

        p = _paired_t_test([0.5, 0.6], [0.3])
        assert p == 1.0

    def test_zero_variance(self):
        from tools.finetune.ab_evaluator import _paired_t_test

        # All differences are the same (zero stdev of diffs)
        p = _paired_t_test([0.5, 0.6], [0.3, 0.4])
        assert p == 0.0  # Perfect difference, zero stdev


class TestNormalCdf:
    """Test normal CDF approximation."""

    def test_zero(self):
        from tools.finetune.ab_evaluator import _normal_cdf

        assert abs(_normal_cdf(0) - 0.5) < 0.001

    def test_large_positive(self):
        from tools.finetune.ab_evaluator import _normal_cdf

        assert _normal_cdf(3.0) > 0.99

    def test_large_negative(self):
        from tools.finetune.ab_evaluator import _normal_cdf

        assert _normal_cdf(-3.0) < 0.01


class TestCompareModels:
    """Test A/B model comparison."""

    def test_model_a_not_found(self, ft_db):
        from tools.finetune.ab_evaluator import compare_models

        result = compare_models("nope-a", "nope-b", db_path=ft_db)
        assert not result["success"]
        assert "Model A not found" in result["error"]

    def test_model_b_not_found(self, ft_db):
        _seed_model_version(ft_db, mv_id="mv-a")
        from tools.finetune.ab_evaluator import compare_models

        result = compare_models("mv-a", "nope-b", db_path=ft_db)
        assert not result["success"]
        assert "Model B not found" in result["error"]

    def test_no_test_data(self, ft_db):
        _seed_model_version(ft_db, mv_id="mv-a", model_name="model-a", version=1)
        _seed_model_version(ft_db, mv_id="mv-b", model_name="model-b", version=1)
        from tools.finetune.ab_evaluator import compare_models

        result = compare_models("mv-a", "mv-b", db_path=ft_db)
        assert not result["success"]
        assert "No test data" in result["error"]

    def test_comparison_with_test_file(self, ft_db, tmp_path):
        _seed_model_version(ft_db, mv_id="mv-a", model_name="model-a", version=1)
        _seed_model_version(ft_db, mv_id="mv-b", model_name="model-b", version=1)

        test_file = tmp_path / "test.jsonl"
        entries = [
            {
                "messages": [
                    {"role": "user", "content": "What is X?"},
                    {"role": "assistant", "content": "X is a thing that does stuff"},
                ]
            }
            for _ in range(5)
        ]
        with open(test_file, "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

        from tools.finetune.ab_evaluator import compare_models

        # Mock LLM-dependent helpers to avoid live Ollama/API inference calls.
        # ab_evaluator imports _generate_predictions directly so patch its own reference.
        with (
            patch("tools.finetune.evaluator.compute_perplexity_estimate", return_value=0.0),
            patch(
                "tools.finetune.evaluator._generate_predictions",
                side_effect=lambda _model, pairs: [p["expected_output"] for p in pairs],
            ),
            patch(
                "tools.finetune.ab_evaluator._generate_predictions",
                side_effect=lambda _model, pairs: [p["expected_output"] for p in pairs],
            ),
        ):
            result = compare_models(
                "mv-a",
                "mv-b",
                test_set_path=str(test_file),
                db_path=ft_db,
            )
        assert result["success"]
        assert "winner" in result
        assert result["winner"] in ("model_a", "model_b", "tie")
        assert "statistical_significance" in result
        assert result["test_set_size"] == 5


# =============================================================================
# PROMOTION MANAGER — Eligibility
# =============================================================================


class TestPromotionEligibility:
    """Test promotion eligibility checking."""

    def test_model_not_found(self, ft_db):
        from tools.finetune.promotion_manager import check_promotion_eligibility

        result = check_promotion_eligibility("nope", db_path=ft_db)
        assert not result["success"]

    def test_eligible_model(self, ft_db):
        _seed_model_version(
            ft_db,
            eval_bleu=0.5,
            eval_rouge_l=0.6,
            eval_perplexity=3.0,
            ollama_name="test:v1",
        )
        from tools.finetune.promotion_manager import check_promotion_eligibility

        result = check_promotion_eligibility("mv-test", db_path=ft_db)
        assert result["success"]
        assert result["eligible"]
        assert result["checks"]["bleu"]["pass"]
        assert result["checks"]["rouge_l"]["pass"]

    def test_ineligible_low_bleu(self, ft_db):
        _seed_model_version(
            ft_db,
            eval_bleu=0.1,
            eval_rouge_l=0.6,
            eval_perplexity=3.0,
            ollama_name="test:v1",
        )
        from tools.finetune.promotion_manager import check_promotion_eligibility

        result = check_promotion_eligibility("mv-test", db_path=ft_db)
        assert result["success"]
        assert not result["eligible"]
        assert not result["checks"]["bleu"]["pass"]

    def test_ineligible_low_rouge(self, ft_db):
        _seed_model_version(
            ft_db,
            eval_bleu=0.5,
            eval_rouge_l=0.2,
            eval_perplexity=3.0,
            ollama_name="test:v1",
        )
        from tools.finetune.promotion_manager import check_promotion_eligibility

        result = check_promotion_eligibility("mv-test", db_path=ft_db)
        assert result["success"]
        assert not result["eligible"]
        assert not result["checks"]["rouge_l"]["pass"]

    def test_ineligible_no_ollama(self, ft_db):
        _seed_model_version(
            ft_db,
            eval_bleu=0.5,
            eval_rouge_l=0.6,
            eval_perplexity=3.0,
            ollama_name="",  # No Ollama model
        )
        from tools.finetune.promotion_manager import check_promotion_eligibility

        result = check_promotion_eligibility("mv-test", db_path=ft_db)
        assert result["success"]
        assert not result["eligible"]
        assert not result["has_ollama_model"]

    def test_perplexity_improvement_check(self, ft_db):
        """Test perplexity compared against current active model."""
        # Seed current active model with high perplexity
        _seed_model_version(
            ft_db,
            mv_id="mv-current",
            model_name="current",
            version=1,
            ollama_name="current:v1",
            eval_bleu=0.3,
            eval_rouge_l=0.4,
            eval_perplexity=10.0,
        )
        _seed_active_model(ft_db, function_name="code_generation", mv_id="mv-current", ollama_name="current:v1")

        # Seed candidate with much better (lower) perplexity
        _seed_model_version(
            ft_db,
            mv_id="mv-new",
            model_name="new",
            version=1,
            ollama_name="new:v1",
            eval_bleu=0.5,
            eval_rouge_l=0.6,
            eval_perplexity=5.0,
        )

        from tools.finetune.promotion_manager import check_promotion_eligibility

        result = check_promotion_eligibility(
            "mv-new",
            function_name="code_generation",
            db_path=ft_db,
        )
        assert result["success"]
        assert result["eligible"]
        assert result["checks"]["perplexity"]["pass"]
        assert result["checks"]["perplexity"]["improvement_pct"] == 50.0


# =============================================================================
# PROMOTION MANAGER — Auto-Promote
# =============================================================================


class TestAutoPromote:
    """Test auto-promotion."""

    def test_auto_promote_ineligible(self, ft_db):
        _seed_model_version(
            ft_db,
            eval_bleu=0.1,
            eval_rouge_l=0.2,
            eval_perplexity=3.0,
            ollama_name="test:v1",
        )
        from tools.finetune.promotion_manager import auto_promote

        result = auto_promote("mv-test", "code_generation", db_path=ft_db)
        assert not result["success"]
        assert "thresholds" in result["error"]

    @patch("tools.finetune.model_registry.promote_model")
    def test_auto_promote_eligible(self, mock_promote, ft_db):
        mock_promote.return_value = {"success": True, "model_version_id": "mv-test"}
        _seed_model_version(
            ft_db,
            eval_bleu=0.5,
            eval_rouge_l=0.6,
            eval_perplexity=3.0,
            ollama_name="test:v1",
        )
        from tools.finetune.promotion_manager import auto_promote

        result = auto_promote("mv-test", "code_generation", db_path=ft_db)
        assert result["success"]
        mock_promote.assert_called_once()

    def test_auto_promote_model_not_found(self, ft_db):
        from tools.finetune.promotion_manager import auto_promote

        result = auto_promote("nope", "code_generation", db_path=ft_db)
        assert not result["success"]


# =============================================================================
# PROMOTION MANAGER — Force-Promote
# =============================================================================


class TestForcePromote:
    """Test force-promotion."""

    @patch("tools.finetune.model_registry.promote_model")
    def test_force_promote_bypasses_thresholds(self, mock_promote, ft_db):
        mock_promote.return_value = {"success": True, "model_version_id": "mv-test"}
        _seed_model_version(
            ft_db,
            eval_bleu=0.1,
            eval_rouge_l=0.1,
            eval_perplexity=99.0,
            ollama_name="test:v1",
        )
        from tools.finetune.promotion_manager import force_promote

        result = force_promote(
            "mv-test",
            "code_generation",
            reason="Admin override",
            activated_by="admin",
            db_path=ft_db,
        )
        assert result["success"]

    @patch("tools.finetune.model_registry.promote_model")
    def test_force_promote_logs_override(self, mock_promote, ft_db):
        mock_promote.return_value = {"success": True, "model_version_id": "mv-test"}
        _seed_model_version(ft_db, ollama_name="test:v1")
        from tools.finetune.promotion_manager import force_promote

        result = force_promote(
            "mv-test",
            "code_generation",
            reason="Testing",
            activated_by="tester",
            db_path=ft_db,
        )
        assert result["success"]
        # Verify promotion log
        conn = sqlite3.connect(str(ft_db))
        log = conn.execute("SELECT * FROM ft_promotion_log WHERE action = 'override_promoted'").fetchone()
        conn.close()
        assert log is not None


# =============================================================================
# RETRAIN TRIGGER — Check
# =============================================================================


class TestRetrainCheck:
    """Test retrain trigger checking."""

    def test_no_datasets(self, ft_db):
        from tools.finetune.retrain_trigger import check_retrain_needed

        result = check_retrain_needed(db_path=ft_db)
        assert result["success"]
        assert result["datasets_checked"] == 0
        assert len(result["datasets_needing_retrain"]) == 0

    def test_dataset_below_threshold(self, ft_db):
        _seed_dataset(ft_db)
        # Add 10 approved examples (below 50 threshold)
        for i in range(10):
            _seed_example(ft_db, user_input=f"Q{i}", expected_output=f"A{i}")

        from tools.finetune.retrain_trigger import check_retrain_needed

        result = check_retrain_needed(db_path=ft_db)
        assert result["success"]
        assert result["datasets_checked"] == 1
        assert len(result["datasets_needing_retrain"]) == 0

    def test_dataset_above_threshold(self, ft_db):
        _seed_dataset(ft_db)
        # Add 60 approved examples (above 50 threshold)
        for i in range(60):
            _seed_example(ft_db, user_input=f"Q{i}", expected_output=f"A{i}")

        from tools.finetune.retrain_trigger import check_retrain_needed

        result = check_retrain_needed(db_path=ft_db)
        assert result["success"]
        assert len(result["datasets_needing_retrain"]) == 1
        assert result["datasets_needing_retrain"][0]["new_examples"] == 60

    def test_cooldown_respected(self, ft_db):
        _seed_dataset(ft_db)
        for i in range(60):
            _seed_example(ft_db, user_input=f"Q{i}", expected_output=f"A{i}")

        # Add a recently completed job (within 24h cooldown)
        recent = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        _seed_training_job(ft_db, status="completed", completed_at=recent)

        from tools.finetune.retrain_trigger import check_retrain_needed

        result = check_retrain_needed(db_path=ft_db)
        assert result["success"]
        assert len(result["datasets_needing_retrain"]) == 0

    def test_cooldown_expired(self, ft_db):
        _seed_dataset(ft_db)
        for i in range(60):
            _seed_example(ft_db, user_input=f"Q{i}", expected_output=f"A{i}")

        # Add an old completed job (48h ago, beyond 24h cooldown)
        old = (datetime.now(timezone.utc) - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ")
        _seed_training_job(ft_db, status="completed", completed_at=old)

        from tools.finetune.retrain_trigger import check_retrain_needed

        result = check_retrain_needed(db_path=ft_db)
        assert result["success"]
        assert len(result["datasets_needing_retrain"]) == 1

    def test_active_job_prevents_trigger(self, ft_db):
        _seed_dataset(ft_db)
        for i in range(60):
            _seed_example(ft_db, user_input=f"Q{i}", expected_output=f"A{i}")

        # Add an active training job
        _seed_training_job(ft_db, status="training")

        from tools.finetune.retrain_trigger import check_retrain_needed

        result = check_retrain_needed(db_path=ft_db)
        assert result["success"]
        assert len(result["datasets_needing_retrain"]) == 0

    def test_only_counts_new_examples(self, ft_db):
        _seed_dataset(ft_db)
        # Add an old completed job
        old = (datetime.now(timezone.utc) - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%SZ")
        _seed_training_job(ft_db, status="completed", completed_at=old)

        # Add only 10 examples AFTER the job (below threshold)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        for i in range(10):
            _seed_example(ft_db, user_input=f"Q{i}", expected_output=f"A{i}", created_at=now)

        from tools.finetune.retrain_trigger import check_retrain_needed

        result = check_retrain_needed(db_path=ft_db)
        assert result["success"]
        assert len(result["datasets_needing_retrain"]) == 0

    def test_filter_by_dataset_id(self, ft_db):
        _seed_dataset(ft_db, ds_id="ds-a", name="Dataset A")
        _seed_dataset(ft_db, ds_id="ds-b", name="Dataset B")
        for i in range(60):
            _seed_example(ft_db, ds_id="ds-a", user_input=f"QA{i}", expected_output=f"AA{i}")

        from tools.finetune.retrain_trigger import check_retrain_needed

        result = check_retrain_needed(dataset_id="ds-b", db_path=ft_db)
        assert result["success"]
        assert len(result["datasets_needing_retrain"]) == 0

    def test_draft_datasets_excluded(self, ft_db):
        _seed_dataset(ft_db, status="draft")
        for i in range(60):
            _seed_example(ft_db, user_input=f"Q{i}", expected_output=f"A{i}")

        from tools.finetune.retrain_trigger import check_retrain_needed

        result = check_retrain_needed(db_path=ft_db)
        assert result["success"]
        assert result["datasets_checked"] == 0


# =============================================================================
# RETRAIN TRIGGER — Trigger
# =============================================================================


class TestRetrainTrigger:
    """Test retrain triggering."""

    def test_trigger_below_threshold(self, ft_db):
        _seed_dataset(ft_db)
        from tools.finetune.retrain_trigger import trigger_retrain

        result = trigger_retrain("ds-test", db_path=ft_db)
        assert not result["success"]
        assert "threshold" in result["error"]

    @patch("tools.finetune.training_engine.create_training_job")
    def test_trigger_above_threshold(self, mock_start, ft_db):
        mock_start.return_value = {"success": True, "job_id": "job-new"}
        _seed_dataset(ft_db)
        for i in range(60):
            _seed_example(ft_db, user_input=f"Q{i}", expected_output=f"A{i}")

        from tools.finetune.retrain_trigger import trigger_retrain

        result = trigger_retrain("ds-test", db_path=ft_db)
        assert result["success"]
        assert "trigger_reason" in result
        mock_start.assert_called_once()


# =============================================================================
# RETRAIN TRIGGER — Heartbeat
# =============================================================================


class TestHeartbeatCheck:
    """Test heartbeat daemon integration."""

    def test_heartbeat_no_datasets(self, ft_db):
        from tools.finetune.retrain_trigger import heartbeat_check_retrain

        result = heartbeat_check_retrain(db_path=ft_db)
        assert result["check_type"] == "retrain_trigger"
        assert result["status"] == "ok"
        assert result["issues"] == 0

    def test_heartbeat_with_retrain_needed(self, ft_db):
        _seed_dataset(ft_db)
        for i in range(60):
            _seed_example(ft_db, user_input=f"Q{i}", expected_output=f"A{i}")

        from tools.finetune.retrain_trigger import heartbeat_check_retrain

        result = heartbeat_check_retrain(db_path=ft_db)
        assert result["check_type"] == "retrain_trigger"
        assert result["status"] == "warning"
        assert result["issues"] == 1

    @patch("tools.finetune.retrain_trigger._load_retrain_config")
    def test_heartbeat_disabled(self, mock_config, ft_db):
        mock_config.return_value = {
            "new_example_threshold": 50,
            "cooldown_hours": 24,
            "auto_trigger": False,
        }
        from tools.finetune.retrain_trigger import heartbeat_check_retrain

        result = heartbeat_check_retrain(db_path=ft_db)
        assert result["status"] == "ok"
        assert "disabled" in result["details"]


# =============================================================================
# EVALUATOR — Load Test Data
# =============================================================================


class TestLoadTestData:
    """Test test data loading."""

    def test_load_from_jsonl(self, tmp_path):
        test_file = tmp_path / "test.jsonl"
        entries = [
            {"messages": [{"role": "user", "content": "Q1"}, {"role": "assistant", "content": "A1"}]},
            {"messages": [{"role": "user", "content": "Q2"}, {"role": "assistant", "content": "A2"}]},
        ]
        with open(test_file, "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

        from tools.finetune.evaluator import _load_test_data

        pairs = _load_test_data(str(test_file), "", 0.2, None)
        assert len(pairs) == 2
        assert pairs[0]["user_input"] == "Q1"
        assert pairs[0]["expected_output"] == "A1"

    def test_load_from_dataset(self, ft_db):
        _seed_dataset(ft_db)
        for i in range(10):
            _seed_example(ft_db, user_input=f"Q{i}", expected_output=f"A{i}")

        from tools.finetune.evaluator import _load_test_data

        pairs = _load_test_data("", "ds-test", 0.5, ft_db)
        assert len(pairs) > 0

    def test_load_empty_path(self):
        from tools.finetune.evaluator import _load_test_data

        pairs = _load_test_data("", "", 0.2, None)
        assert pairs == []

    def test_load_invalid_jsonl(self, tmp_path):
        test_file = tmp_path / "bad.jsonl"
        with open(test_file, "w") as f:
            f.write("not valid json\n")
            f.write("{}\n")  # Missing messages

        from tools.finetune.evaluator import _load_test_data

        pairs = _load_test_data(str(test_file), "", 0.2, None)
        assert len(pairs) == 0  # Both lines should be skipped
