#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for dataset lineage tracker (D1)."""
from __future__ import annotations

import hashlib
import sqlite3

import pytest

from icdev.tools.finetune.dataset_lineage import DatasetLineage, LineageRecord, get_lineage

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ft_source_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id TEXT NOT NULL UNIQUE,
    title TEXT DEFAULT '',
    source_type TEXT DEFAULT 'document',
    content_hash TEXT NOT NULL,
    tenant_id TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS ft_pair_lineage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    example_id INTEGER NOT NULL,
    doc_id TEXT NOT NULL,
    dataset_id TEXT NOT NULL,
    generation_method TEXT DEFAULT 'llm_generated',
    generator_model_id TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ft_pair_lineage_doc ON ft_pair_lineage(doc_id);
CREATE INDEX IF NOT EXISTS idx_ft_pair_lineage_example ON ft_pair_lineage(example_id);
CREATE INDEX IF NOT EXISTS idx_ft_pair_lineage_dataset ON ft_pair_lineage(dataset_id);
CREATE TABLE IF NOT EXISTS ft_dataset_examples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset_id TEXT NOT NULL,
    system_prompt TEXT DEFAULT '',
    user_input TEXT NOT NULL DEFAULT '',
    expected_output TEXT NOT NULL DEFAULT '',
    source TEXT DEFAULT 'manual',
    source_chunk_id TEXT DEFAULT '',
    source_document_id TEXT DEFAULT '',
    quality_score REAL DEFAULT 0.0,
    compliance_score REAL DEFAULT 0.0,
    relevance_score REAL DEFAULT 0.0,
    approved INTEGER DEFAULT 0,
    content_hash TEXT NOT NULL DEFAULT '',
    classification TEXT DEFAULT 'CUI',
    tenant_id TEXT DEFAULT '',
    project_id TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


@pytest.fixture
def mem_conn():
    """In-memory SQLite connection with lineage schema (dataset_lineage.py uses ? placeholders)."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def lineage(mem_conn):
    """DatasetLineage bound to in-memory SQLite connection."""
    return DatasetLineage(conn=mem_conn)


# ── register_source ───────────────────────────────────────────────────


class TestRegisterSource:
    def test_returns_doc_id(self, lineage):
        result = lineage.register_source("doc-001", "Hello world", title="Test Doc")
        assert result == "doc-001"

    def test_stores_content_hash(self, lineage, mem_conn):
        lineage.register_source("doc-002", "Sample content")
        row = mem_conn.execute(
            "SELECT content_hash FROM ft_source_documents WHERE doc_id = 'doc-002'"
        ).fetchone()
        assert row is not None
        expected_hash = hashlib.sha256("Sample content".encode()).hexdigest()
        assert row["content_hash"] == expected_hash

    def test_upsert_updates_hash(self, lineage, mem_conn):
        lineage.register_source("doc-003", "Version 1")
        lineage.register_source("doc-003", "Version 2")
        rows = mem_conn.execute(
            "SELECT COUNT(*) FROM ft_source_documents WHERE doc_id = 'doc-003'"
        ).fetchone()
        assert rows[0] == 1  # only one record after upsert

    def test_stores_title_and_source_type(self, lineage, mem_conn):
        lineage.register_source("doc-004", "data", title="My Doc", source_type="rag_chunk")
        row = mem_conn.execute(
            "SELECT title, source_type FROM ft_source_documents WHERE doc_id = 'doc-004'"
        ).fetchone()
        assert row["title"] == "My Doc"
        assert row["source_type"] == "rag_chunk"


# ── record_pair_lineage ───────────────────────────────────────────────


class TestRecordPairLineage:
    def test_inserts_record(self, lineage, mem_conn):
        lineage.register_source("doc-10", "content")
        lineage.record_pair_lineage(42, "doc-10", "ds-abc", generation_method="manual")
        row = mem_conn.execute(
            "SELECT * FROM ft_pair_lineage WHERE example_id = 42"
        ).fetchone()
        assert row is not None
        assert row["doc_id"] == "doc-10"
        assert row["dataset_id"] == "ds-abc"
        assert row["generation_method"] == "manual"


# ── get_stale_pairs ───────────────────────────────────────────────────


class TestGetStalePairs:
    def test_returns_empty_when_hash_matches(self, lineage, mem_conn):
        content = "original content"
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        lineage.register_source("doc-20", content)
        lineage.record_pair_lineage(1, "doc-20", "ds-001")
        result = lineage.get_stale_pairs("doc-20", content_hash)
        assert result == []

    def test_returns_example_ids_when_hash_differs(self, lineage, mem_conn):
        lineage.register_source("doc-21", "original content")
        lineage.record_pair_lineage(10, "doc-21", "ds-002")
        lineage.record_pair_lineage(11, "doc-21", "ds-002")
        new_hash = hashlib.sha256("modified content".encode()).hexdigest()
        result = lineage.get_stale_pairs("doc-21", new_hash)
        assert set(result) == {10, 11}

    def test_returns_empty_when_doc_not_found(self, lineage):
        result = lineage.get_stale_pairs("doc-nonexistent", "anyhash")
        assert result == []

    def test_single_stale_pair(self, lineage):
        lineage.register_source("doc-22", "text A")
        lineage.record_pair_lineage(99, "doc-22", "ds-003")
        new_hash = hashlib.sha256("text B".encode()).hexdigest()
        result = lineage.get_stale_pairs("doc-22", new_hash)
        assert result == [99]


# ── lineage_report ────────────────────────────────────────────────────


class TestLineageReport:
    def test_report_structure(self, lineage):
        report = lineage.lineage_report("ds-empty")
        assert "dataset_id" in report
        assert "total_pairs" in report
        assert "sources" in report
        assert isinstance(report["sources"], list)

    def test_empty_dataset(self, lineage):
        report = lineage.lineage_report("ds-nothing")
        assert report["dataset_id"] == "ds-nothing"
        assert report["total_pairs"] == 0
        assert report["sources"] == []

    def test_populated_dataset(self, lineage):
        lineage.register_source("doc-30", "content A", title="Doc A")
        lineage.register_source("doc-31", "content B", title="Doc B")
        lineage.record_pair_lineage(1, "doc-30", "ds-100")
        lineage.record_pair_lineage(2, "doc-30", "ds-100")
        lineage.record_pair_lineage(3, "doc-31", "ds-100")

        report = lineage.lineage_report("ds-100")
        assert report["total_pairs"] == 3
        assert len(report["sources"]) == 2
        # Most-paired doc first
        assert report["sources"][0]["pair_count"] == 2


# ── invalidate_pairs ──────────────────────────────────────────────────


class TestInvalidatePairs:
    def test_empty_list(self, lineage):
        result = lineage.invalidate_pairs([], "ds-any")
        assert result == 0

    def test_returns_count(self, lineage, mem_conn):
        # Insert example rows so UPDATE has something to hit
        mem_conn.execute(
            "INSERT INTO ft_dataset_examples (id, dataset_id, user_input, expected_output, content_hash, approved) VALUES (?, ?, ?, ?, ?, ?)",
            (5, "ds-200", "q", "a", "abc", 1),
        )
        mem_conn.execute(
            "INSERT INTO ft_dataset_examples (id, dataset_id, user_input, expected_output, content_hash, approved) VALUES (?, ?, ?, ?, ?, ?)",
            (6, "ds-200", "q2", "a2", "def", 1),
        )
        mem_conn.commit()
        result = lineage.invalidate_pairs([5, 6], "ds-200")
        assert result == 2


# ── get_lineage factory ───────────────────────────────────────────────


def test_get_lineage_returns_instance():
    obj = get_lineage()
    assert isinstance(obj, DatasetLineage)


# ── LineageRecord dataclass ───────────────────────────────────────────


def test_lineage_record_defaults():
    rec = LineageRecord(example_id=1, doc_id="doc-x", dataset_id="ds-x")
    assert rec.generation_method == "llm_generated"
    assert rec.generator_model_id == ""
