"""Tests for detect_ingest_anomalies() — IQR-based pipeline anomaly detection.

aiify-opp-19: hardcoded_threshold -> anomaly_detection in the document
consumer pipeline.  The function detects outlier documents by byte_size,
page_count, and chunk count using the 1.5-IQR rule — no hardcoded thresholds.
"""
from __future__ import annotations

import importlib
import sqlite3

import pytest

analytics = importlib.import_module("tools.document_intelligence.analytics_engine")


# ── IQR helper ───────────────────────────────────────────────────────────────

def test_iqr_outliers_returns_inf_on_too_few_values():
    lo, hi = analytics._iqr_outliers([1.0, 2.0, 3.0])  # < 4 values
    assert lo == float("-inf")
    assert hi == float("inf")


def test_iqr_outliers_symmetric_distribution():
    lo, hi = analytics._iqr_outliers([1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0])
    assert lo < 1.0
    assert hi > 8.0


def test_iqr_outliers_detects_upper_outlier():
    base = [10.0, 11.0, 12.0, 13.0, 14.0, 15.0]
    lo, hi = analytics._iqr_outliers(base)
    assert hi < 1000.0, "fence should be finite for a tight distribution"


def test_iqr_outliers_four_identical_values():
    lo, hi = analytics._iqr_outliers([5.0, 5.0, 5.0, 5.0])
    # IQR = 0, fences collapse to 5.0
    assert lo == 5.0
    assert hi == 5.0


# ── detect_ingest_anomalies with real SQLite DB ───────────────────────────────

@pytest.fixture()
def _db(tmp_path, monkeypatch):
    """Minimal in-memory SQLite DB with dic_documents + dic_chunk_links."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE dic_documents (
            doc_id TEXT PRIMARY KEY,
            collection_id TEXT,
            title TEXT,
            filename TEXT,
            byte_size INTEGER,
            page_count INTEGER
        );
        CREATE TABLE dic_chunk_links (
            id TEXT PRIMARY KEY,
            doc_id TEXT,
            version_id TEXT,
            rag_chunk_id TEXT,
            chunk_index INTEGER
        );
    """)
    conn.commit()

    def _fake_conn():
        # Must be a StorageConnection, NOT a raw sqlite3 connection. The module
        # under test authors PostgreSQL-dialect SQL (`%s` placeholders), which
        # sqlite3 cannot parse; the statement raised, the caller's bare `except`
        # swallowed it, and the assertion saw zero rows. A raw connection here
        # tests a dialect the production code does not speak.
        from tools.db.storage import get_connection
        return get_connection(str(db_path))

    monkeypatch.setattr(analytics, "_conn", _fake_conn)
    return conn


def _insert_doc(conn, doc_id, byte_size, page_count, collection_id="col1",
                title=None, filename=None, chunk_count=0):
    conn.execute(
        "INSERT INTO dic_documents VALUES (?,?,?,?,?,?)",
        (doc_id, collection_id, title or doc_id, filename or f"{doc_id}.txt",
         byte_size, page_count),
    )
    for i in range(chunk_count):
        conn.execute(
            "INSERT INTO dic_chunk_links VALUES (?,?,?,?,?)",
            (f"{doc_id}_cl{i}", doc_id, f"{doc_id}_v1", f"chunk_{i}", i),
        )
    conn.commit()


def test_empty_corpus_returns_empty_flag(_db):
    result = analytics.detect_ingest_anomalies()
    assert result["empty"] is True
    assert "No documents" in result["message"]
    assert result["summary"]["oversized_count"] == 0


def test_required_keys_present(_db):
    _insert_doc(_db, "d1", 1000, 1, chunk_count=5)
    result = analytics.detect_ingest_anomalies()
    for key in ("oversized", "undersized", "page_outliers_high", "page_outliers_low",
                "chunk_poor", "chunk_rich", "summary", "thresholds", "empty", "message"):
        assert key in result, f"missing key: {key}"


def test_no_outliers_when_corpus_is_uniform(_db):
    for i in range(8):
        _insert_doc(_db, f"d{i}", 10_000, 5, chunk_count=10)
    result = analytics.detect_ingest_anomalies()
    assert result["empty"] is False
    assert result["summary"]["oversized_count"] == 0
    assert result["summary"]["undersized_count"] == 0
    assert result["summary"]["page_outliers_high_count"] == 0
    assert result["summary"]["chunk_poor_count"] == 0


def test_detects_oversized_document(_db):
    for i in range(6):
        _insert_doc(_db, f"normal{i}", 10_000, 5, chunk_count=10)
    # One document 100× the typical size
    _insert_doc(_db, "giant", 1_000_000, 5, chunk_count=10)
    result = analytics.detect_ingest_anomalies()
    assert result["summary"]["oversized_count"] == 1
    assert result["oversized"][0]["doc_id"] == "giant"
    assert result["oversized"][0]["byte_size"] == 1_000_000


def test_detects_undersized_document(_db):
    for i in range(6):
        _insert_doc(_db, f"normal{i}", 100_000, 5, chunk_count=10)
    _insert_doc(_db, "tiny", 1, 5, chunk_count=10)
    result = analytics.detect_ingest_anomalies()
    assert result["summary"]["undersized_count"] == 1
    assert result["undersized"][0]["doc_id"] == "tiny"


def test_detects_chunk_poor_document(_db):
    for i in range(6):
        _insert_doc(_db, f"normal{i}", 50_000, 5, chunk_count=20)
    # A document with 0 chunks — embedding likely failed
    _insert_doc(_db, "empty_chunks", 50_000, 5, chunk_count=0)
    result = analytics.detect_ingest_anomalies()
    assert result["summary"]["chunk_poor_count"] >= 1
    poor_ids = [r["doc_id"] for r in result["chunk_poor"]]
    assert "empty_chunks" in poor_ids


def test_detects_page_count_high_outlier(_db):
    for i in range(6):
        _insert_doc(_db, f"normal{i}", 10_000, 5, chunk_count=10)
    _insert_doc(_db, "huge_doc", 10_000, 5000, chunk_count=10)
    result = analytics.detect_ingest_anomalies()
    assert result["summary"]["page_outliers_high_count"] == 1
    assert result["page_outliers_high"][0]["doc_id"] == "huge_doc"


def test_collection_filter_scopes_results(_db):
    for i in range(6):
        _insert_doc(_db, f"col1_{i}", 10_000, 5, collection_id="col1", chunk_count=10)
    # Outlier in col2 — should not appear when filtering for col1
    _insert_doc(_db, "col2_giant", 1_000_000, 5, collection_id="col2", chunk_count=10)
    result_col1 = analytics.detect_ingest_anomalies(collection_id="col1")
    assert result_col1["summary"]["oversized_count"] == 0

    result_col2 = analytics.detect_ingest_anomalies(collection_id="col2")
    # Only 1 doc in col2 — too few for IQR → no outliers (need ≥4)
    assert result_col2["summary"]["oversized_count"] == 0


def test_thresholds_are_present_and_named(_db):
    for i in range(6):
        _insert_doc(_db, f"d{i}", 10_000, 5, chunk_count=10)
    result = analytics.detect_ingest_anomalies()
    th = result["thresholds"]
    for key in ("byte_size_low", "byte_size_high", "page_low", "page_high",
                "chunk_low", "chunk_high"):
        assert key in th, f"missing threshold: {key}"


def test_outlier_lists_capped_at_50(_db):
    # Insert 60 zero-chunk docs to force chunk_poor list
    for i in range(60):
        _insert_doc(_db, f"empty_{i}", 10_000, 5, chunk_count=0)
    result = analytics.detect_ingest_anomalies()
    assert len(result["chunk_poor"]) <= 50
