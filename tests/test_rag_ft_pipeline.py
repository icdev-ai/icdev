#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for RAG-to-FT automated pipeline (D-KARL-5)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tests._sql_compat import connect as translating_connect


PIPELINE_SCHEMA = """
CREATE TABLE IF NOT EXISTS ft_pipeline_runs (
    id TEXT PRIMARY KEY,
    run_type TEXT NOT NULL DEFAULT 'rag_to_ft',
    source_type TEXT,
    chunks_processed INTEGER DEFAULT 0,
    pairs_generated INTEGER DEFAULT 0,
    pairs_approved INTEGER DEFAULT 0,
    retrain_triggered INTEGER DEFAULT 0,
    status TEXT DEFAULT 'running',
    error TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP
);
CREATE TABLE IF NOT EXISTS rag_chunks (
    id TEXT PRIMARY KEY,
    content TEXT,
    source_id TEXT,
    source_table TEXT,
    tier TEXT DEFAULT 'hot',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS ft_datasets (
    id TEXT PRIMARY KEY,
    name TEXT,
    purpose TEXT DEFAULT 'general',
    description TEXT,
    status TEXT DEFAULT 'draft',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS ft_dataset_examples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_id TEXT,
    system_prompt TEXT DEFAULT '',
    user_input TEXT,
    expected_output TEXT,
    source TEXT DEFAULT 'rag_auto_generated',
    quality_score REAL DEFAULT 0.0,
    compliance_score REAL DEFAULT 0.0,
    relevance_score REAL DEFAULT 0.0,
    approved INTEGER DEFAULT 0,
    labeled_by TEXT,
    labeled_at TIMESTAMP,
    content_hash TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


@pytest.fixture
def pipeline_db():
    # tools.finetune.rag_ft_pipeline authors PostgreSQL ``%s`` placeholders and
    # relies on StorageConnection to rewrite them for SQLite. Handing it a bare
    # sqlite3 connection makes every statement raise ``near "%": syntax error``.
    conn = translating_connect(":memory:")
    conn.executescript(PIPELINE_SCHEMA)
    return conn


class TestPipelineDetection:
    """Test new chunk detection."""

    def test_detect_new_chunks_empty(self, pipeline_db):
        from tools.finetune.rag_ft_pipeline import _detect_new_chunks

        chunks = _detect_new_chunks(pipeline_db, "innovation_signals")
        assert chunks == []

    def test_detect_new_chunks_found(self, pipeline_db):
        from tools.finetune.rag_ft_pipeline import _detect_new_chunks

        pipeline_db.execute(
            "INSERT INTO rag_chunks (id, content, source_table, tier, created_at) "
            "VALUES ('c1', 'test content', 'innovation_signals', 'hot', '2026-03-20T00:00:00Z')",
        )
        pipeline_db.commit()
        chunks = _detect_new_chunks(pipeline_db, "innovation_signals", since="2026-01-01T00:00:00Z")
        assert len(chunks) == 1
        assert chunks[0]["content"] == "test content"

    def test_detect_respects_since(self, pipeline_db):
        from tools.finetune.rag_ft_pipeline import _detect_new_chunks

        pipeline_db.execute(
            "INSERT INTO rag_chunks (id, content, source_table, tier, created_at) "
            "VALUES ('c1', 'old content', 'signals', 'hot', '2025-01-01T00:00:00Z')",
        )
        pipeline_db.commit()
        chunks = _detect_new_chunks(pipeline_db, "signals", since="2026-01-01T00:00:00Z")
        assert len(chunks) == 0


class TestAutoApprove:
    """Test auto-approval of high-quality pairs."""

    def test_auto_approve_above_threshold(self, pipeline_db):
        from tools.finetune.rag_ft_pipeline import _auto_approve_pairs

        pipeline_db.execute(
            "INSERT INTO ft_dataset_examples (dataset_id, user_input, expected_output, quality_score, approved) "
            "VALUES ('ds1', 'q1', 'a1', 0.9, 0)",
        )
        pipeline_db.execute(
            "INSERT INTO ft_dataset_examples (dataset_id, user_input, expected_output, quality_score, approved) "
            "VALUES ('ds1', 'q2', 'a2', 0.5, 0)",
        )
        pipeline_db.commit()
        approved = _auto_approve_pairs(pipeline_db, "ds1", threshold=0.8)
        assert approved == 1

    def test_auto_approve_none_above(self, pipeline_db):
        from tools.finetune.rag_ft_pipeline import _auto_approve_pairs

        pipeline_db.execute(
            "INSERT INTO ft_dataset_examples (dataset_id, user_input, expected_output, quality_score, approved) "
            "VALUES ('ds1', 'q1', 'a1', 0.3, 0)",
        )
        pipeline_db.commit()
        approved = _auto_approve_pairs(pipeline_db, "ds1", threshold=0.8)
        assert approved == 0


class TestEnsureDataset:
    """Test dataset creation/lookup."""

    def test_creates_new_dataset(self, pipeline_db):
        from tools.finetune.rag_ft_pipeline import _ensure_dataset

        # Mock create_dataset to simulate import failure, forcing fallback path
        with patch("tools.finetune.rag_ft_pipeline._get_db", return_value=pipeline_db):
            with patch.dict("sys.modules", {"tools.finetune.dataset_manager": None}):
                ds_id = _ensure_dataset(pipeline_db, "test-dataset", "general")
                assert ds_id  # Non-empty
                row = pipeline_db.execute("SELECT * FROM ft_datasets WHERE id = ?", (ds_id,)).fetchone()
                assert row is not None

    def test_finds_existing_dataset(self, pipeline_db):
        from tools.finetune.rag_ft_pipeline import _ensure_dataset

        pipeline_db.execute(
            "INSERT INTO ft_datasets (id, name, purpose, status) VALUES ('ds-existing', 'my-ds', 'general', 'ready')",
        )
        pipeline_db.commit()
        ds_id = _ensure_dataset(pipeline_db, "my-ds", "general")
        assert ds_id == "ds-existing"


class TestPipelineStatus:
    """Test pipeline status reporting."""

    def test_status_empty(self, pipeline_db):
        from tools.finetune.rag_ft_pipeline import get_pipeline_status

        with patch("tools.finetune.rag_ft_pipeline._get_db", return_value=pipeline_db):
            with patch("tools.finetune.rag_ft_pipeline._ensure_tables"):
                result = get_pipeline_status()
                assert result["status"] == "ok"
                assert result["total_runs"] == 0


class TestPipelineRun:
    """Test full pipeline execution with mocks."""

    def test_dry_run(self, pipeline_db):
        from tools.finetune.rag_ft_pipeline import run_pipeline

        # Add a chunk
        pipeline_db.execute(
            "INSERT INTO rag_chunks (id, content, source_table, tier, created_at) "
            "VALUES ('c1', 'test content about NIST compliance', 'innovation_signals', 'hot', '2026-03-20T00:00:00Z')",
        )
        pipeline_db.commit()

        with patch("tools.finetune.rag_ft_pipeline._get_db", return_value=pipeline_db):
            with patch("tools.finetune.rag_ft_pipeline._ensure_tables"):
                result = run_pipeline(source_type="innovation_signals", dry_run=True)
                assert result["status"] == "ok"
                assert result["dry_run"] is True
                assert result["total_chunks"] >= 1

    def test_disabled_pipeline(self):
        from tools.finetune.rag_ft_pipeline import run_pipeline

        with patch("tools.finetune.rag_ft_pipeline._get_config", return_value={"enabled": False}):
            result = run_pipeline()
            assert result["status"] == "disabled"
