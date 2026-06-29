"""
PGP-TX-02: JSON portability — _aggregate_chat_sources

Validates that the chat-sources aggregation reads JSON metadata fields in
Python rather than via SQLite-only json_extract(), ensuring the query runs on
PostgreSQL without modification (pgp-linter high-severity finding).
"""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.dashboard.app import _aggregate_chat_sources


# ---------------------------------------------------------------------------
# Minimal in-memory SQLite fixture (no RLS / no conftest deps)
# ---------------------------------------------------------------------------

@pytest.fixture()
def mem_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE rag_chunks (
            id TEXT PRIMARY KEY,
            content TEXT DEFAULT '',
            content_hash TEXT DEFAULT '',
            source_type TEXT NOT NULL,
            source_id TEXT NOT NULL DEFAULT '',
            source_table TEXT NOT NULL DEFAULT '',
            chunk_index INTEGER NOT NULL DEFAULT 0,
            total_chunks INTEGER NOT NULL DEFAULT 1,
            metadata TEXT DEFAULT '{}',
            tier TEXT NOT NULL DEFAULT 'hot',
            tenant_id TEXT DEFAULT '',
            project_id TEXT DEFAULT '',
            classification TEXT NOT NULL DEFAULT 'CUI',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    yield conn
    conn.close()


def _insert(conn, source_id, filename, *, context_id="ctx1", tenant_id="t1",
            source_type="chat_upload", created_at="2024-01-01 00:00:00"):
    meta = json.dumps({"filename": filename, "context_id": context_id})
    conn.execute(
        "INSERT INTO rag_chunks "
        "(id, content, content_hash, source_type, source_id, metadata, tenant_id, created_at) "
        "VALUES (?, '', '', ?, ?, ?, ?, ?)",
        (f"{source_id}-{filename}", source_type, source_id, meta, tenant_id, created_at),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Chunk-count aggregation
# ---------------------------------------------------------------------------

class TestAggregateCountsChunks:
    def test_single_source_single_chunk(self, mem_conn):
        _insert(mem_conn, "s1", "doc.pdf")
        result = _aggregate_chat_sources(mem_conn, "t1", "")
        assert len(result) == 1
        assert result[0]["source_id"] == "s1"
        assert result[0]["chunk_count"] == 1
        assert result[0]["filename"] == "doc.pdf"

    def test_multiple_chunks_same_source(self, mem_conn):
        for i in range(3):
            _insert(mem_conn, "s1", f"doc{i}.pdf")
        result = _aggregate_chat_sources(mem_conn, "t1", "")
        assert len(result) == 1
        assert result[0]["chunk_count"] == 3

    def test_two_distinct_sources(self, mem_conn):
        _insert(mem_conn, "s1", "a.pdf")
        _insert(mem_conn, "s2", "b.pdf")
        result = _aggregate_chat_sources(mem_conn, "t1", "")
        assert len(result) == 2
        assert {r["source_id"] for r in result} == {"s1", "s2"}

    def test_empty_table(self, mem_conn):
        assert _aggregate_chat_sources(mem_conn, "", "") == []


# ---------------------------------------------------------------------------
# Tenant filter
# ---------------------------------------------------------------------------

class TestTenantFilter:
    def test_filters_by_tenant(self, mem_conn):
        _insert(mem_conn, "s1", "a.pdf", tenant_id="t1")
        _insert(mem_conn, "s2", "b.pdf", tenant_id="t2")
        result = _aggregate_chat_sources(mem_conn, "t1", "")
        assert len(result) == 1
        assert result[0]["source_id"] == "s1"

    def test_empty_tenant_returns_all(self, mem_conn):
        _insert(mem_conn, "s1", "a.pdf", tenant_id="t1")
        _insert(mem_conn, "s2", "b.pdf", tenant_id="t2")
        result = _aggregate_chat_sources(mem_conn, "", "")
        assert len(result) == 2


# ---------------------------------------------------------------------------
# Context-ID filter (Python-side, replaces json_extract WHERE clause)
# ---------------------------------------------------------------------------

class TestContextIdFilter:
    def test_filters_by_context_id(self, mem_conn):
        _insert(mem_conn, "s1", "a.pdf", context_id="ctx1")
        _insert(mem_conn, "s2", "b.pdf", context_id="ctx2")
        result = _aggregate_chat_sources(mem_conn, "t1", "ctx1")
        assert len(result) == 1
        assert result[0]["context_id"] == "ctx1"

    def test_empty_context_returns_all(self, mem_conn):
        _insert(mem_conn, "s1", "a.pdf", context_id="ctx1")
        _insert(mem_conn, "s2", "b.pdf", context_id="ctx2")
        result = _aggregate_chat_sources(mem_conn, "", "")
        assert len(result) == 2

    def test_unknown_context_returns_empty(self, mem_conn):
        _insert(mem_conn, "s1", "a.pdf", context_id="ctx1")
        result = _aggregate_chat_sources(mem_conn, "t1", "ctx_nope")
        assert result == []


# ---------------------------------------------------------------------------
# Non-chat source_type excluded
# ---------------------------------------------------------------------------

class TestSourceTypeExclusion:
    def test_non_chat_upload_excluded(self, mem_conn):
        _insert(mem_conn, "s1", "a.pdf", source_type="rag_ingest")
        result = _aggregate_chat_sources(mem_conn, "", "")
        assert result == []

    def test_mixed_types_only_chat_returned(self, mem_conn):
        _insert(mem_conn, "s1", "a.pdf", source_type="chat_upload")
        _insert(mem_conn, "s2", "b.pdf", source_type="rag_ingest")
        result = _aggregate_chat_sources(mem_conn, "", "")
        assert len(result) == 1
        assert result[0]["source_id"] == "s1"


# ---------------------------------------------------------------------------
# Malformed / missing metadata
# ---------------------------------------------------------------------------

class TestMalformedMetadata:
    def test_invalid_json_does_not_raise(self, mem_conn):
        mem_conn.execute(
            "INSERT INTO rag_chunks "
            "(id, content, content_hash, source_type, source_id, metadata, tenant_id) "
            "VALUES ('x', '', '', 'chat_upload', 'sx', 'NOT_JSON', 't1')"
        )
        mem_conn.commit()
        result = _aggregate_chat_sources(mem_conn, "t1", "")
        assert len(result) == 1
        assert result[0]["filename"] == ""
        assert result[0]["context_id"] == ""

    def test_null_metadata_does_not_raise(self, mem_conn):
        mem_conn.execute(
            "INSERT INTO rag_chunks "
            "(id, content, content_hash, source_type, source_id, metadata, tenant_id) "
            "VALUES ('y', '', '', 'chat_upload', 'sy', NULL, 't1')"
        )
        mem_conn.commit()
        result = _aggregate_chat_sources(mem_conn, "t1", "")
        assert len(result) == 1


# ---------------------------------------------------------------------------
# 50-row cap
# ---------------------------------------------------------------------------

class TestLimit:
    def test_returns_at_most_50(self, mem_conn):
        for i in range(60):
            day = (i % 28) + 1
            _insert(mem_conn, f"s{i}", f"doc{i}.pdf",
                    context_id="ctx1", created_at=f"2024-01-{day:02d} 00:00:00")
        result = _aggregate_chat_sources(mem_conn, "t1", "")
        assert len(result) <= 50


# ---------------------------------------------------------------------------
# Return shape
# ---------------------------------------------------------------------------

class TestReturnShape:
    def test_result_has_required_keys(self, mem_conn):
        _insert(mem_conn, "s1", "readme.pdf")
        row = _aggregate_chat_sources(mem_conn, "t1", "")[0]
        for key in ("source_id", "filename", "context_id", "chunk_count", "indexed_at"):
            assert key in row, f"Missing key: {key}"

    def test_result_is_list_of_dicts(self, mem_conn):
        _insert(mem_conn, "s1", "readme.pdf")
        result = _aggregate_chat_sources(mem_conn, "t1", "")
        assert isinstance(result, list)
        assert isinstance(result[0], dict)
