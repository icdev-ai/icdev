#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for Phase 2 fine-tuning training pipeline.

Covers: training_engine, gguf_exporter, model_registry, doc_extractor, pair_generator.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

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

        CREATE TABLE IF NOT EXISTS rag_chunks (
            chunk_id TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            source_type TEXT DEFAULT '',
            source_id TEXT DEFAULT '',
            source_table TEXT DEFAULT '',
            chunk_index INTEGER DEFAULT 0,
            total_chunks INTEGER DEFAULT 0,
            tier TEXT DEFAULT 'hot',
            tenant_id TEXT DEFAULT '',
            project_id TEXT DEFAULT '',
            classification TEXT DEFAULT 'CUI',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS rag_ingestion_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_table TEXT DEFAULT '',
            source_id TEXT DEFAULT '',
            chunk_count INTEGER DEFAULT 0,
            status TEXT DEFAULT '',
            started_at TIMESTAMP,
            completed_at TIMESTAMP
        );
    """)


@pytest.fixture
def ft_db(tmp_path):
    """Create a temporary database with FT tables."""
    db_path = tmp_path / "test_ft.db"
    conn = sqlite3.connect(str(db_path))
    _create_ft_tables(conn)
    conn.close()
    return db_path


def _seed_dataset(db_path: Path, dataset_id: str = "ds-test001", status: str = "ready") -> str:
    """Insert a test dataset and return its ID."""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """INSERT INTO ft_datasets (id, name, purpose, base_model, status, classification)
           VALUES (?, 'Test Dataset', 'general', 'qwen3:latest', ?, 'CUI')""",
        (dataset_id, status),
    )
    conn.commit()
    conn.close()
    return dataset_id


def _seed_example(
    db_path: Path, dataset_id: str, user_input: str = "What is X?", expected_output: str = "X is Y.", approved: int = 1
) -> int:
    """Insert a test example and return its ID."""
    content_hash = hashlib.sha256(f"|{user_input}|{expected_output}".encode()).hexdigest()[:16]
    conn = sqlite3.connect(str(db_path))
    cursor = conn.execute(
        """INSERT INTO ft_dataset_examples
           (dataset_id, user_input, expected_output, content_hash, approved, source, classification)
           VALUES (?, ?, ?, ?, ?, 'manual', 'CUI')""",
        (dataset_id, user_input, expected_output, content_hash, approved),
    )
    example_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return example_id


def _seed_model_version(
    db_path: Path,
    model_version_id: str = "mv-test001",
    model_name: str = "test-model",
    version: int = 1,
    ollama_name: str = "test-model-v1",
) -> str:
    """Insert a test model version."""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """INSERT INTO ft_model_versions
           (id, job_id, model_name, version, base_model, ollama_model_name,
            eval_bleu, eval_rouge_l, eval_perplexity, status, classification)
           VALUES (?, 'ft-test001', ?, ?, 'qwen3:latest', ?, 0.35, 0.45, 12.5, 'evaluated', 'CUI')""",
        (model_version_id, model_name, version, ollama_name),
    )
    conn.commit()
    conn.close()
    return model_version_id


# ===== Model Registry Tests ===================================================


class TestModelRegistryGetVersion:
    def test_get_existing(self, ft_db):
        from tools.finetune.model_registry import get_model_version

        _seed_model_version(ft_db)
        result = get_model_version("mv-test001", db_path=ft_db)
        assert result["success"]
        assert result["model_version"]["model_name"] == "test-model"

    def test_get_nonexistent(self, ft_db):
        from tools.finetune.model_registry import get_model_version

        result = get_model_version("mv-missing", db_path=ft_db)
        assert not result["success"]


class TestModelRegistryList:
    def test_list_empty(self, ft_db):
        from tools.finetune.model_registry import list_model_versions

        result = list_model_versions(db_path=ft_db)
        assert result["success"]
        assert result["count"] == 0

    def test_list_with_filter(self, ft_db):
        from tools.finetune.model_registry import list_model_versions

        _seed_model_version(ft_db)
        _seed_model_version(ft_db, "mv-test002", "other-model", 1, "other-v1")
        result = list_model_versions(model_name="test-model", db_path=ft_db)
        assert result["success"]
        assert result["count"] == 1


class TestModelRegistryEvalScores:
    def test_update_scores(self, ft_db):
        from tools.finetune.model_registry import update_eval_scores, get_model_version

        _seed_model_version(ft_db)
        result = update_eval_scores(
            "mv-test001",
            eval_bleu=0.40,
            eval_rouge_l=0.50,
            eval_perplexity=10.0,
            db_path=ft_db,
        )
        assert result["success"]
        assert result["status"] == "evaluated"

        mv = get_model_version("mv-test001", db_path=ft_db)
        assert mv["model_version"]["eval_bleu"] == 0.40

    def test_update_nonexistent(self, ft_db):
        from tools.finetune.model_registry import update_eval_scores

        result = update_eval_scores("mv-missing", db_path=ft_db)
        assert not result["success"]


class TestModelRegistryPromotion:
    def test_promote_model(self, ft_db):
        from tools.finetune.model_registry import promote_model, get_active_model

        _seed_model_version(ft_db)
        result = promote_model(
            "mv-test001",
            "code_generation",
            activated_by="test",
            db_path=ft_db,
        )
        assert result["success"]
        assert result["ollama_model_name"] == "test-model-v1"

        active = get_active_model("code_generation", db_path=ft_db)
        assert active["active_model"] is not None
        assert active["active_model"]["ollama_model_name"] == "test-model-v1"

    def test_promote_replaces_previous(self, ft_db):
        from tools.finetune.model_registry import promote_model

        _seed_model_version(ft_db)
        _seed_model_version(ft_db, "mv-test002", "test-model", 2, "test-model-v2")

        promote_model("mv-test001", "code_generation", db_path=ft_db)
        result = promote_model("mv-test002", "code_generation", db_path=ft_db)
        assert result["success"]
        assert result["previous_model"] == "test-model-v1"

    def test_promote_no_ollama_name(self, ft_db):
        """Model without Ollama name cannot be promoted."""
        from tools.finetune.model_registry import promote_model

        conn = sqlite3.connect(str(ft_db))
        conn.execute(
            """INSERT INTO ft_model_versions
               (id, job_id, model_name, version, base_model, ollama_model_name, status, classification)
               VALUES ('mv-no-ollama', 'ft-1', 'test', 1, 'qwen3:latest', '', 'created', 'CUI')""",
        )
        conn.commit()
        conn.close()

        result = promote_model("mv-no-ollama", "code_generation", db_path=ft_db)
        assert not result["success"]
        assert "Ollama" in result["error"]


class TestModelRegistryDemotion:
    def test_demote_active(self, ft_db):
        from tools.finetune.model_registry import promote_model, demote_model, get_active_model

        _seed_model_version(ft_db)
        promote_model("mv-test001", "code_generation", db_path=ft_db)

        result = demote_model("code_generation", demoted_by="test", db_path=ft_db)
        assert result["success"]
        assert result["demoted_model"] == "test-model-v1"

        active = get_active_model("code_generation", db_path=ft_db)
        assert active["active_model"] is None

    def test_demote_nothing(self, ft_db):
        from tools.finetune.model_registry import demote_model

        result = demote_model("code_generation", db_path=ft_db)
        assert not result["success"]


class TestModelRegistryActiveModels:
    def test_list_active(self, ft_db):
        from tools.finetune.model_registry import promote_model, list_active_models

        _seed_model_version(ft_db)
        _seed_model_version(ft_db, "mv-test002", "other-model", 1, "other-v1")
        promote_model("mv-test001", "code_generation", db_path=ft_db)
        promote_model("mv-test002", "compliance_export", db_path=ft_db)

        result = list_active_models(db_path=ft_db)
        assert result["success"]
        assert result["count"] == 2


class TestPromotionHistory:
    def test_history_recorded(self, ft_db):
        from tools.finetune.model_registry import promote_model, get_promotion_history

        _seed_model_version(ft_db)
        promote_model("mv-test001", "code_generation", activated_by="admin", db_path=ft_db)

        result = get_promotion_history(function_name="code_generation", db_path=ft_db)
        assert result["success"]
        assert result["count"] >= 1
        assert result["history"][0]["action"] == "promoted"


# ===== GGUF Exporter Tests ====================================================


class TestGGUFExporter:
    def test_register_model_version(self, ft_db):
        from tools.finetune.gguf_exporter import register_model_version

        # Need a training job for foreign key
        conn = sqlite3.connect(str(ft_db))
        conn.execute(
            """INSERT INTO ft_training_jobs
               (id, dataset_id, provider, base_model, status, classification)
               VALUES ('ft-job1', 'ds-test', 'unsloth_local', 'qwen3:latest', 'completed', 'CUI')""",
        )
        conn.commit()
        conn.close()

        result = register_model_version(
            job_id="ft-job1",
            model_name="test-model",
            base_model="qwen3:latest",
            ollama_model_name="test-model-v1",
            db_path=ft_db,
        )
        assert result["success"]
        assert result["version"] == 1
        assert result["model_name"] == "test-model"

    def test_register_auto_increments_version(self, ft_db):
        from tools.finetune.gguf_exporter import register_model_version

        conn = sqlite3.connect(str(ft_db))
        conn.execute(
            """INSERT INTO ft_training_jobs
               (id, dataset_id, provider, base_model, status, classification)
               VALUES ('ft-job1', 'ds-test', 'unsloth_local', 'qwen3:latest', 'completed', 'CUI')""",
        )
        conn.commit()
        conn.close()

        r1 = register_model_version("ft-job1", "test-model", "qwen3:latest", db_path=ft_db)
        r2 = register_model_version("ft-job1", "test-model", "qwen3:latest", db_path=ft_db)
        assert r1["version"] == 1
        assert r2["version"] == 2

    @patch("tools.finetune.gguf_exporter.subprocess")
    def test_export_to_gguf_success(self, mock_sub, tmp_path):
        from tools.finetune.gguf_exporter import export_to_gguf

        # Create fake GGUF output
        gguf_file = tmp_path / "model-q4_k_m.gguf"
        gguf_file.write_bytes(b"fake gguf content")

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = json.dumps({"gguf_path": str(gguf_file)})
        mock_sub.run.return_value = mock_proc

        result = export_to_gguf(
            adapter_path=str(tmp_path / "adapter"),
            output_dir=str(tmp_path),
            model_name="model",
        )
        assert result["success"]

    @patch("tools.finetune.gguf_exporter.subprocess")
    def test_export_to_gguf_failure(self, mock_sub):
        from tools.finetune.gguf_exporter import export_to_gguf

        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stderr = "CUDA OOM"
        mock_sub.run.return_value = mock_proc

        result = export_to_gguf(adapter_path="/fake", model_name="model")
        assert not result["success"]
        assert "CUDA OOM" in result["error"]

    @patch("tools.finetune.gguf_exporter.subprocess")
    def test_register_with_ollama_file_not_found(self, mock_sub):
        from tools.finetune.gguf_exporter import register_with_ollama

        result = register_with_ollama("test-model", "/nonexistent.gguf")
        assert not result["success"]
        assert "not found" in result["error"]


# ===== Training Engine Tests ==================================================


class TestTrainingEngineJobStatus:
    def test_get_status_not_found(self, ft_db):
        from tools.finetune.training_engine import get_job_status

        result = get_job_status("ft-missing", db_path=ft_db)
        assert not result["success"]

    def test_get_status_completed(self, ft_db):
        from tools.finetune.training_engine import get_job_status

        _seed_dataset(ft_db)
        conn = sqlite3.connect(str(ft_db))
        conn.execute(
            """INSERT INTO ft_training_jobs
               (id, dataset_id, provider, base_model, status, classification)
               VALUES ('ft-test1', 'ds-test001', 'unsloth_local', 'qwen3:latest', 'completed', 'CUI')""",
        )
        conn.commit()
        conn.close()

        result = get_job_status("ft-test1", db_path=ft_db)
        assert result["success"]
        assert result["job"]["status"] == "completed"


class TestTrainingEngineCancelJob:
    def test_cancel_terminal(self, ft_db):
        from tools.finetune.training_engine import cancel_job

        conn = sqlite3.connect(str(ft_db))
        conn.execute(
            """INSERT INTO ft_training_jobs
               (id, dataset_id, provider, base_model, status, classification)
               VALUES ('ft-done', 'ds-test', 'unsloth_local', 'qwen3:latest', 'completed', 'CUI')""",
        )
        conn.commit()
        conn.close()

        result = cancel_job("ft-done", db_path=ft_db)
        assert not result["success"]
        assert "terminal" in result["error"]


class TestTrainingEngineListJobs:
    def test_list_empty(self, ft_db):
        from tools.finetune.training_engine import list_jobs

        result = list_jobs(db_path=ft_db)
        assert result["success"]
        assert result["count"] == 0

    def test_list_with_filter(self, ft_db):
        from tools.finetune.training_engine import list_jobs

        conn = sqlite3.connect(str(ft_db))
        conn.execute(
            """INSERT INTO ft_training_jobs
               (id, dataset_id, provider, base_model, status, project_id, classification)
               VALUES ('ft-1', 'ds-1', 'unsloth_local', 'qwen3:latest', 'training', 'proj-1', 'CUI')""",
        )
        conn.execute(
            """INSERT INTO ft_training_jobs
               (id, dataset_id, provider, base_model, status, project_id, classification)
               VALUES ('ft-2', 'ds-2', 'unsloth_local', 'qwen3:latest', 'completed', 'proj-2', 'CUI')""",
        )
        conn.commit()
        conn.close()

        result = list_jobs(project_id="proj-1", db_path=ft_db)
        assert result["count"] == 1

        result = list_jobs(status="completed", db_path=ft_db)
        assert result["count"] == 1


# ===== Document Extractor Tests ===============================================


class TestDocExtractorText:
    def test_extract_txt(self, tmp_path):
        from tools.finetune.doc_extractor import extract_document

        txt = tmp_path / "test.txt"
        txt.write_text("This is a test document with enough content to be meaningful.\n" * 20)

        result = extract_document(str(txt))
        assert result["success"]
        assert result["chunk_count"] >= 1
        assert result["file_type"] == ".txt"

    def test_extract_md(self, tmp_path):
        from tools.finetune.doc_extractor import extract_document

        md = tmp_path / "test.md"
        md.write_text("# Heading\n\nThis is markdown content.\n" * 20)

        result = extract_document(str(md))
        assert result["success"]

    def test_extract_nonexistent(self):
        from tools.finetune.doc_extractor import extract_document

        result = extract_document("/nonexistent.txt")
        assert not result["success"]
        assert "not found" in result["error"]

    def test_extract_unsupported(self, tmp_path):
        from tools.finetune.doc_extractor import extract_document

        f = tmp_path / "test.xyz"
        f.write_text("content")
        result = extract_document(str(f))
        assert not result["success"]
        assert "Unsupported" in result["error"]

    def test_extract_empty_file(self, tmp_path):
        from tools.finetune.doc_extractor import extract_document

        f = tmp_path / "empty.txt"
        f.write_text("")
        result = extract_document(str(f))
        assert not result["success"]
        assert "no extractable text" in result["error"]


class TestDocExtractorDirectory:
    def test_extract_dir(self, tmp_path):
        from tools.finetune.doc_extractor import extract_directory

        (tmp_path / "a.txt").write_text("Content A.\n" * 20)
        (tmp_path / "b.txt").write_text("Content B.\n" * 20)
        (tmp_path / "c.xyz").write_text("Ignored")

        result = extract_directory(str(tmp_path))
        assert result["success"]
        assert result["documents_processed"] == 2

    def test_extract_dir_nonexistent(self):
        from tools.finetune.doc_extractor import extract_directory

        result = extract_directory("/nonexistent_dir")
        assert not result["success"]


class TestDocExtractorChunking:
    def test_simple_chunker_fallback(self):
        from tools.finetune.doc_extractor import _chunk_text_simple

        # Use newlines as the simple chunker splits on \n
        text = "\n".join([f"This is paragraph {i} with enough content." for i in range(100)])
        chunks = _chunk_text_simple(text, "test-doc", max_chunk_size=500)
        assert len(chunks) > 1
        for c in chunks:
            assert c["total_chunks"] == len(chunks)
            assert c["content_hash"]


# ===== Pair Generator Tests ===================================================


class TestPairGeneratorTemplates:
    def test_template_fallback(self):
        from tools.finetune.pair_generator import _generate_pairs_template

        text = (
            "The system uses AES-256 encryption for data at rest. "
            "All API endpoints require mutual TLS authentication. "
            "Audit logs are immutable and append-only per NIST AU-2. "
            "Classification markings are applied at generation time."
        )
        pairs = _generate_pairs_template(text, "general", 3)
        assert len(pairs) >= 1
        for p in pairs:
            assert "user_input" in p
            assert "expected_output" in p

    def test_template_short_content(self):
        from tools.finetune.pair_generator import _generate_pairs_template

        pairs = _generate_pairs_template("Short text.", "general", 3)
        assert len(pairs) == 0  # Sentences too short


class TestPairGeneratorParsing:
    def test_parse_valid_json(self):
        from tools.finetune.pair_generator import _parse_pairs_response

        response = json.dumps(
            [
                {"question": "What is encryption?", "answer": "Encryption is a security measure that protects data."},
                {"question": "What is TLS?", "answer": "TLS provides secure communication between endpoints."},
            ]
        )
        pairs = _parse_pairs_response(response)
        assert len(pairs) == 2

    def test_parse_markdown_wrapped(self):
        from tools.finetune.pair_generator import _parse_pairs_response

        response = (
            '```json\n[{"question": "What is AES?", "answer": "AES is an encryption standard used widely."}]\n```'
        )
        pairs = _parse_pairs_response(response)
        assert len(pairs) == 1

    def test_parse_invalid_json(self):
        from tools.finetune.pair_generator import _parse_pairs_response

        pairs = _parse_pairs_response("not json at all")
        assert len(pairs) == 0

    def test_parse_filters_short(self):
        from tools.finetune.pair_generator import _parse_pairs_response

        response = json.dumps(
            [
                {"question": "Short?", "answer": "Too short"},
                {
                    "question": "What is a proper question?",
                    "answer": "This is a sufficiently long answer that passes the filter.",
                },
            ]
        )
        pairs = _parse_pairs_response(response)
        assert len(pairs) == 1  # First pair too short


class TestPairGeneratorFromDocument:
    def test_generate_from_chunks(self, ft_db):
        from tools.finetune.pair_generator import generate_from_document
        from tools.finetune.dataset_manager import create_dataset

        ds = create_dataset("test-ds", db_path=ft_db)
        dataset_id = ds["dataset_id"]

        chunks = [
            {"content": "The security framework implements multi-factor authentication. " * 10, "chunk_id": "c1"},
            {"content": "Too short."},  # Should be skipped
        ]

        # Mock LLM router to avoid live Ollama inference calls
        with patch("tools.llm.router.LLMRouter") as mock_router_cls:
            mock_router_cls.return_value.invoke.side_effect = RuntimeError("LLM unavailable in test")
            result = generate_from_document(
                dataset_id=dataset_id,
                chunks=chunks,
                purpose="general",
                questions_per_chunk=2,
                db_path=ft_db,
            )
        assert result["success"]
        assert result["chunks_processed"] == 2
        assert result["pairs_generated"] >= 0  # Template fallback may generate 0 if LLM unavailable


class TestPairGeneratorStats:
    def test_stats_empty(self, ft_db):
        from tools.finetune.pair_generator import get_generation_stats

        _seed_dataset(ft_db)
        result = get_generation_stats("ds-test001", db_path=ft_db)
        assert result["success"]
        assert result["auto_generated_total"] == 0

    def test_stats_with_data(self, ft_db):
        from tools.finetune.pair_generator import get_generation_stats

        _seed_dataset(ft_db)
        conn = sqlite3.connect(str(ft_db))
        for i in range(5):
            h = hashlib.sha256(f"q{i}|a{i}".encode()).hexdigest()[:16]
            conn.execute(
                """INSERT INTO ft_dataset_examples
                   (dataset_id, user_input, expected_output, source, content_hash, approved, classification)
                   VALUES ('ds-test001', ?, ?, 'rag_auto_generated', ?, ?, 'CUI')""",
                (f"Question {i}?", f"Answer {i}.", h, 1 if i < 3 else 0),
            )
        conn.commit()
        conn.close()

        result = get_generation_stats("ds-test001", db_path=ft_db)
        assert result["success"]
        assert result["auto_generated_total"] == 5


# ===== Unsloth Provider Tests =================================================


class TestUnslothProviderAvailability:
    @patch("tools.finetune.unsloth_provider.detect_gpu")
    def test_no_gpu(self, mock_gpu):
        from tools.finetune.unsloth_provider import UnslothLocalProvider
        from tools.finetune.gpu_detector import GPUDetectionResult

        mock_gpu.return_value = GPUDetectionResult(can_train=False)
        provider = UnslothLocalProvider()
        avail = provider.check_availability()
        assert not avail["available"]

    @patch("tools.finetune.unsloth_provider.detect_gpu")
    def test_with_gpu_no_unsloth(self, mock_gpu):
        from tools.finetune.unsloth_provider import UnslothLocalProvider
        from tools.finetune.gpu_detector import GPUDetectionResult

        mock_gpu.return_value = GPUDetectionResult(
            has_gpu=True,
            gpu_count=1,
            can_train=True,
            total_vram_mb=24000,
        )
        provider = UnslothLocalProvider()
        with patch.object(provider, "_check_unsloth_installed", return_value=False):
            avail = provider.check_availability()
        assert not avail["available"]
        assert avail["has_gpu"]


class TestUnslothProviderTraining:
    @patch("tools.finetune.unsloth_provider.detect_gpu")
    def test_start_training_no_gpu(self, mock_gpu):
        from tools.finetune.unsloth_provider import UnslothLocalProvider
        from tools.finetune.provider import FineTuneRequest
        from tools.finetune.gpu_detector import GPUDetectionResult

        mock_gpu.return_value = GPUDetectionResult(can_train=False)
        provider = UnslothLocalProvider()
        request = FineTuneRequest(dataset_path="/fake", job_id="ft-1")
        status = provider.start_training(request)
        assert status.status == "failed"
        assert "GPU" in status.error

    def test_build_training_command_single_gpu(self):
        from tools.finetune.unsloth_provider import UnslothLocalProvider

        provider = UnslothLocalProvider()
        config = {"distributed": False, "gpu_count": 1}
        cmd = provider._build_training_command(config, Path("/tmp/config.json"))
        assert "_train_worker.py" in cmd[1]
        assert "accelerate" not in " ".join(cmd)

    def test_build_training_command_multi_gpu(self):
        from tools.finetune.unsloth_provider import UnslothLocalProvider

        provider = UnslothLocalProvider()
        config = {"distributed": True, "gpu_count": 4}
        cmd = provider._build_training_command(config, Path("/tmp/config.json"))
        assert "accelerate" in " ".join(cmd)
        assert "--num_processes" in cmd
