#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for fine-tuning dataset manager and labeler (D-FT-9, D-FT-12)."""

from __future__ import annotations

import json
import sqlite3

import pytest

# Import after path setup
from tools.finetune.dataset_manager import (
    add_example,
    add_examples_batch,
    create_dataset,
    export_jsonl,
    get_dataset,
    get_dataset_stats,
    list_datasets,
    list_examples,
    update_dataset_status,
)
from tools.finetune.labeler import (
    batch_approve,
    batch_reject,
    get_labeling_summary,
    get_unlabeled,
    label_example,
)


# ---- Fixtures ----


@pytest.fixture
def ft_db(tmp_path):
    """Create a temp DB with fine-tuning tables."""
    db_path = tmp_path / "test_ft.db"
    conn = sqlite3.connect(str(db_path))

    # Create minimal schema for FT tables
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS audit_trail (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT,
            event_type TEXT NOT NULL,
            details TEXT,
            actor TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS ft_datasets (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            purpose TEXT DEFAULT 'general'
                CHECK(purpose IN ('general','proposal_drafting','compliance_export','code_generation','custom')),
            base_model TEXT DEFAULT 'qwen3:latest',
            version INTEGER DEFAULT 1,
            example_count INTEGER DEFAULT 0,
            content_hash TEXT DEFAULT '',
            classification TEXT NOT NULL DEFAULT 'CUI',
            tenant_id TEXT DEFAULT '',
            project_id TEXT DEFAULT '',
            created_by TEXT DEFAULT '',
            status TEXT DEFAULT 'draft' CHECK(status IN ('draft','labeling','ready','archived')),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS ft_dataset_examples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dataset_id TEXT NOT NULL REFERENCES ft_datasets(id),
            system_prompt TEXT DEFAULT '',
            user_input TEXT NOT NULL,
            expected_output TEXT NOT NULL,
            source TEXT DEFAULT 'manual'
                CHECK(source IN ('manual','rag_auto_generated','document_extraction','imported','marketplace')),
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
        CREATE INDEX IF NOT EXISTS idx_ft_examples_dataset
            ON ft_dataset_examples(dataset_id);
        CREATE INDEX IF NOT EXISTS idx_ft_examples_hash
            ON ft_dataset_examples(content_hash);
    """)
    conn.commit()
    conn.close()
    return db_path


# ---- Dataset CRUD tests ----


class TestCreateDataset:
    def test_create_basic(self, ft_db):
        result = create_dataset(name="test-ds", db_path=ft_db)
        assert result["success"] is True
        assert result["dataset_id"].startswith("ds-")
        assert result["name"] == "test-ds"

    def test_create_with_purpose(self, ft_db):
        result = create_dataset(name="proposals", purpose="proposal_drafting", db_path=ft_db)
        assert result["success"] is True
        assert result["purpose"] == "proposal_drafting"

    def test_create_invalid_purpose(self, ft_db):
        result = create_dataset(name="bad", purpose="invalid_purpose", db_path=ft_db)
        assert result["success"] is False
        assert "Invalid purpose" in result["error"]

    def test_create_with_all_fields(self, ft_db):
        result = create_dataset(
            name="full",
            purpose="code_generation",
            description="Test dataset",
            base_model="llama3.1:8b",
            classification="CUI",
            tenant_id="t1",
            project_id="p1",
            created_by="tester",
            db_path=ft_db,
        )
        assert result["success"] is True


class TestListDatasets:
    def test_list_empty(self, ft_db):
        result = list_datasets(db_path=ft_db)
        assert result["success"] is True
        assert result["count"] == 0

    def test_list_after_create(self, ft_db):
        create_dataset(name="ds1", db_path=ft_db)
        create_dataset(name="ds2", db_path=ft_db)
        result = list_datasets(db_path=ft_db)
        assert result["count"] == 2

    def test_list_filter_by_project(self, ft_db):
        create_dataset(name="ds1", project_id="p1", db_path=ft_db)
        create_dataset(name="ds2", project_id="p2", db_path=ft_db)
        result = list_datasets(project_id="p1", db_path=ft_db)
        assert result["count"] == 1


class TestGetDataset:
    def test_get_existing(self, ft_db):
        created = create_dataset(name="test", db_path=ft_db)
        result = get_dataset(created["dataset_id"], db_path=ft_db)
        assert result["success"] is True
        assert result["dataset"]["name"] == "test"

    def test_get_nonexistent(self, ft_db):
        result = get_dataset("ds-nonexistent", db_path=ft_db)
        assert result["success"] is False


class TestUpdateStatus:
    def test_update_to_ready(self, ft_db):
        created = create_dataset(name="test", db_path=ft_db)
        result = update_dataset_status(created["dataset_id"], "ready", db_path=ft_db)
        assert result["success"] is True
        assert result["status"] == "ready"

    def test_update_invalid_status(self, ft_db):
        created = create_dataset(name="test", db_path=ft_db)
        result = update_dataset_status(created["dataset_id"], "invalid", db_path=ft_db)
        assert result["success"] is False


# ---- Example Management tests ----


class TestAddExample:
    def test_add_basic(self, ft_db):
        ds = create_dataset(name="test", db_path=ft_db)
        result = add_example(
            dataset_id=ds["dataset_id"],
            user_input="What is NIST 800-53?",
            expected_output="NIST 800-53 is a security controls framework.",
            db_path=ft_db,
        )
        assert result["success"] is True
        assert result["example_count"] == 1

    def test_add_with_system_prompt(self, ft_db):
        ds = create_dataset(name="test", db_path=ft_db)
        result = add_example(
            dataset_id=ds["dataset_id"],
            user_input="Explain AC-2",
            expected_output="AC-2 is Account Management...",
            system_prompt="You are a compliance expert.",
            db_path=ft_db,
        )
        assert result["success"] is True

    def test_dedup_same_content(self, ft_db):
        ds = create_dataset(name="test", db_path=ft_db)
        add_example(
            dataset_id=ds["dataset_id"],
            user_input="Q1",
            expected_output="A1",
            db_path=ft_db,
        )
        result = add_example(
            dataset_id=ds["dataset_id"],
            user_input="Q1",
            expected_output="A1",
            db_path=ft_db,
        )
        assert result["success"] is False
        assert "Duplicate" in result["error"]

    def test_add_invalid_source(self, ft_db):
        ds = create_dataset(name="test", db_path=ft_db)
        result = add_example(
            dataset_id=ds["dataset_id"],
            user_input="Q",
            expected_output="A",
            source="invalid_source",
            db_path=ft_db,
        )
        assert result["success"] is False

    def test_example_count_increments(self, ft_db):
        ds = create_dataset(name="test", db_path=ft_db)
        for i in range(5):
            add_example(
                dataset_id=ds["dataset_id"],
                user_input=f"Q{i}",
                expected_output=f"A{i}",
                db_path=ft_db,
            )
        result = get_dataset(ds["dataset_id"], db_path=ft_db)
        assert result["dataset"]["example_count"] == 5


class TestAddExamplesBatch:
    def test_batch_add(self, ft_db):
        ds = create_dataset(name="test", db_path=ft_db)
        examples = [{"user_input": f"Q{i}", "expected_output": f"A{i}"} for i in range(10)]
        result = add_examples_batch(ds["dataset_id"], examples, db_path=ft_db)
        assert result["success"] is True
        assert result["added"] == 10
        assert result["skipped"] == 0

    def test_batch_with_duplicates(self, ft_db):
        ds = create_dataset(name="test", db_path=ft_db)
        examples = [
            {"user_input": "Same Q", "expected_output": "Same A"},
            {"user_input": "Same Q", "expected_output": "Same A"},
            {"user_input": "Different Q", "expected_output": "Different A"},
        ]
        result = add_examples_batch(ds["dataset_id"], examples, db_path=ft_db)
        assert result["added"] == 2
        assert result["skipped"] == 1


class TestListExamples:
    def test_list_all(self, ft_db):
        ds = create_dataset(name="test", db_path=ft_db)
        for i in range(3):
            add_example(ds["dataset_id"], f"Q{i}", f"A{i}", db_path=ft_db)
        result = list_examples(ds["dataset_id"], db_path=ft_db)
        assert result["count"] == 3
        assert result["total"] == 3


class TestGetDatasetStats:
    def test_stats_empty(self, ft_db):
        ds = create_dataset(name="test", db_path=ft_db)
        result = get_dataset_stats(ds["dataset_id"], db_path=ft_db)
        assert result["success"] is True
        assert result["total_examples"] == 0

    def test_stats_with_examples(self, ft_db):
        ds = create_dataset(name="test", db_path=ft_db)
        for i in range(5):
            add_example(ds["dataset_id"], f"Q{i}", f"A{i}", db_path=ft_db)
        result = get_dataset_stats(ds["dataset_id"], db_path=ft_db)
        assert result["total_examples"] == 5
        assert result["approved_examples"] == 0


# ---- JSONL Export tests ----


class TestExportJsonl:
    def test_export_approved(self, ft_db, tmp_path):
        ds = create_dataset(name="test", db_path=ft_db)
        for i in range(3):
            add_example(ds["dataset_id"], f"Q{i}", f"A{i}", db_path=ft_db)

        # Approve via direct DB update (labeler tested separately)
        conn = sqlite3.connect(str(ft_db))
        conn.execute(
            "UPDATE ft_dataset_examples SET approved = 1 WHERE dataset_id = ?",
            (ds["dataset_id"],),
        )
        conn.commit()
        conn.close()

        output = tmp_path / "export.jsonl"
        result = export_jsonl(ds["dataset_id"], str(output), db_path=ft_db)
        assert result["success"] is True
        assert result["exported_examples"] == 3

        # Verify JSONL format
        with open(output) as f:
            lines = f.readlines()
        assert len(lines) == 3
        first = json.loads(lines[0])
        assert "messages" in first
        assert first["messages"][-1]["role"] == "assistant"

    def test_export_empty_dataset(self, ft_db, tmp_path):
        ds = create_dataset(name="test", db_path=ft_db)
        output = tmp_path / "export.jsonl"
        result = export_jsonl(ds["dataset_id"], str(output), db_path=ft_db)
        assert result["success"] is False

    def test_export_with_system_prompt(self, ft_db, tmp_path):
        ds = create_dataset(name="test", db_path=ft_db)
        add_example(
            ds["dataset_id"],
            "Q1",
            "A1",
            system_prompt="You are an expert.",
            db_path=ft_db,
        )
        conn = sqlite3.connect(str(ft_db))
        conn.execute("UPDATE ft_dataset_examples SET approved = 1")
        conn.commit()
        conn.close()

        output = tmp_path / "export.jsonl"
        result = export_jsonl(ds["dataset_id"], str(output), db_path=ft_db)
        assert result["success"] is True

        with open(output) as f:
            entry = json.loads(f.readline())
        assert entry["messages"][0]["role"] == "system"
        assert entry["messages"][0]["content"] == "You are an expert."


# ---- Labeler tests ----


class TestLabelExample:
    def test_label_single(self, ft_db):
        ds = create_dataset(name="test", db_path=ft_db)
        add_example(ds["dataset_id"], "Q1", "A1", db_path=ft_db)

        # Get example ID
        conn = sqlite3.connect(str(ft_db))
        conn.row_factory = sqlite3.Row
        ex = conn.execute("SELECT id FROM ft_dataset_examples LIMIT 1").fetchone()
        conn.close()

        result = label_example(
            example_id=ex["id"],
            quality_score=4.0,
            compliance_score=5.0,
            relevance_score=3.5,
            approved=True,
            labeled_by="tester",
            db_path=ft_db,
        )
        assert result["success"] is True
        assert result["approved"] is True

    def test_label_invalid_score(self, ft_db):
        result = label_example(
            example_id=1,
            quality_score=6.0,
            db_path=ft_db,
        )
        assert result["success"] is False
        assert "must be 0-5" in result["error"]

    def test_label_nonexistent(self, ft_db):
        result = label_example(example_id=99999, quality_score=3.0, db_path=ft_db)
        assert result["success"] is False


class TestBatchApprove:
    def test_batch_approve_by_threshold(self, ft_db):
        ds = create_dataset(name="test", db_path=ft_db)
        for i in range(5):
            add_example(ds["dataset_id"], f"Q{i}", f"A{i}", db_path=ft_db)

        # Label some above threshold, some below
        conn = sqlite3.connect(str(ft_db))
        rows = conn.execute("SELECT id FROM ft_dataset_examples ORDER BY id").fetchall()
        for i, row in enumerate(rows):
            score = 4.0 if i < 3 else 2.0
            conn.execute(
                "UPDATE ft_dataset_examples SET quality_score = ?, compliance_score = ?, relevance_score = ? WHERE id = ?",  # noqa: E501
                (score, score, score, row[0]),
            )
        conn.commit()
        conn.close()

        result = batch_approve(
            ds["dataset_id"],
            min_quality=3.0,
            min_compliance=3.0,
            min_relevance=3.0,
            db_path=ft_db,
        )
        assert result["success"] is True
        assert result["approved_count"] == 3


class TestBatchReject:
    def test_batch_reject_low_quality(self, ft_db):
        ds = create_dataset(name="test", db_path=ft_db)
        for i in range(3):
            add_example(ds["dataset_id"], f"Q{i}", f"A{i}", db_path=ft_db)

        conn = sqlite3.connect(str(ft_db))
        conn.execute("UPDATE ft_dataset_examples SET quality_score = 1.5")
        conn.commit()
        conn.close()

        result = batch_reject(ds["dataset_id"], max_quality=2.0, db_path=ft_db)
        assert result["success"] is True
        assert result["rejected_count"] == 3


class TestGetUnlabeled:
    def test_all_unlabeled(self, ft_db):
        ds = create_dataset(name="test", db_path=ft_db)
        for i in range(3):
            add_example(ds["dataset_id"], f"Q{i}", f"A{i}", db_path=ft_db)
        result = get_unlabeled(ds["dataset_id"], db_path=ft_db)
        assert result["success"] is True
        assert result["total_unlabeled"] == 3


class TestLabelingSummary:
    def test_summary_empty(self, ft_db):
        ds = create_dataset(name="test", db_path=ft_db)
        result = get_labeling_summary(ds["dataset_id"], db_path=ft_db)
        assert result["success"] is True
        assert result["total_examples"] == 0

    def test_summary_with_data(self, ft_db):
        ds = create_dataset(name="test", db_path=ft_db)
        for i in range(5):
            add_example(ds["dataset_id"], f"Q{i}", f"A{i}", db_path=ft_db)

        # Label 3 of 5
        conn = sqlite3.connect(str(ft_db))
        rows = conn.execute("SELECT id FROM ft_dataset_examples ORDER BY id LIMIT 3").fetchall()
        for row in rows:
            conn.execute(
                "UPDATE ft_dataset_examples SET quality_score = 4, compliance_score = 4, relevance_score = 4, approved = 1 WHERE id = ?",  # noqa: E501
                (row[0],),
            )
        conn.commit()
        conn.close()

        result = get_labeling_summary(ds["dataset_id"], db_path=ft_db)
        assert result["total_examples"] == 5
        assert result["labeled"] == 3
        assert result["approved"] == 3
        assert result["label_progress_pct"] == 60.0
