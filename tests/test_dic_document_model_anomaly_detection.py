"""Tests for detect_document_model_anomalies() — IQR-based model-level attribute
anomaly detection in the DIC corpus.

aiify-rm-a3344-phase-58: hardcoded_threshold -> anomaly_detection in document
model attributes (analogous to paperless-ngx models.py).  Detects NULL provider,
NULL content_type at DB level, incomplete chunk-embedding ratios, and orphaned
ingest jobs — no hardcoded cut-offs.
"""
from __future__ import annotations

import importlib
import sqlite3

import pytest

analytics = importlib.import_module("tools.document_intelligence.analytics_engine")


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def _db(tmp_path, monkeypatch):
    """Minimal SQLite DB with dic_documents and dic_ingest_jobs."""
    db_path = tmp_path / "model_anomaly_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE dic_documents (
            doc_id        TEXT PRIMARY KEY,
            collection_id TEXT DEFAULT 'default',
            title         TEXT,
            filename      TEXT,
            content_type  TEXT,
            provider      TEXT,
            byte_size     INTEGER DEFAULT 0,
            page_count    INTEGER DEFAULT 1,
            tenant_id     TEXT DEFAULT 'default',
            classification TEXT DEFAULT 'CUI'
        );
        CREATE TABLE dic_ingest_jobs (
            job_id        TEXT PRIMARY KEY,
            filename      TEXT NOT NULL,
            collection_id TEXT DEFAULT 'default',
            status        TEXT DEFAULT 'queued',
            doc_id        TEXT,
            chunks_done   INTEGER DEFAULT 0,
            chunks_total  INTEGER DEFAULT 0,
            tenant_id     TEXT DEFAULT 'default'
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


def _insert_doc(conn, doc_id: str, title: str = "Test Title",
                filename: str = "doc.pdf", content_type: str = "application/pdf",
                provider: str = "local", collection_id: str = "col1") -> None:
    conn.execute(
        "INSERT INTO dic_documents "
        "(doc_id, collection_id, title, filename, content_type, provider) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (doc_id, collection_id, title, filename, content_type, provider),
    )
    conn.commit()


def _insert_job(conn, job_id: str, status: str = "done", doc_id: str = "doc-1",
                chunks_done: int = 10, chunks_total: int = 10,
                collection_id: str = "col1", filename: str = "doc.pdf") -> None:
    conn.execute(
        "INSERT INTO dic_ingest_jobs "
        "(job_id, filename, collection_id, status, doc_id, chunks_done, chunks_total) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (job_id, filename, collection_id, status, doc_id, chunks_done, chunks_total),
    )
    conn.commit()


# ── Empty corpus ──────────────────────────────────────────────────────────────

def test_empty_corpus_returns_empty_flag(_db):
    result = analytics.detect_document_model_anomalies()
    assert result["empty"] is True
    assert result["severity"] == "low"
    assert result["summary"]["total_docs"] == 0
    assert result["summary"]["total_jobs"] == 0


# ── NULL provider detection ───────────────────────────────────────────────────

def test_null_provider_detected(_db):
    _insert_doc(_db, "doc-1", provider="local")
    conn = _db
    conn.execute(
        "INSERT INTO dic_documents "
        "(doc_id, collection_id, title, filename, content_type, provider) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("doc-noprov", "col1", "No Provider Doc", "noprov.pdf", "application/pdf", None),
    )
    conn.commit()
    result = analytics.detect_document_model_anomalies()
    assert result["summary"]["null_provider_count"] == 1
    assert any(d["doc_id"] == "doc-noprov" for d in result["null_provider"])
    assert result["severity"] in ("medium", "high")


def test_all_providers_set_no_alert(_db):
    _insert_doc(_db, "doc-1", provider="local")
    _insert_doc(_db, "doc-2", provider="s3")
    result = analytics.detect_document_model_anomalies()
    assert result["summary"]["null_provider_count"] == 0


# ── NULL content_type at DB level ─────────────────────────────────────────────

def test_null_content_type_detected(_db):
    _insert_doc(_db, "doc-1", content_type="application/pdf")
    conn = _db
    conn.execute(
        "INSERT INTO dic_documents "
        "(doc_id, collection_id, title, filename, content_type, provider) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("doc-notype", "col1", "No Type Doc", "notype.doc", None, "local"),
    )
    conn.commit()
    result = analytics.detect_document_model_anomalies()
    assert result["summary"]["null_content_type_count"] == 1
    assert any(d["doc_id"] == "doc-notype" for d in result["null_content_type"])


# ── Orphaned jobs: done status but no doc_id ──────────────────────────────────

def test_orphaned_job_detected(_db):
    _insert_doc(_db, "doc-1")
    _insert_job(_db, "job-good", doc_id="doc-1")
    _insert_job(_db, "job-orphan", doc_id=None, status="done")
    # Override doc_id to NULL explicitly
    _db.execute("UPDATE dic_ingest_jobs SET doc_id = NULL WHERE job_id = 'job-orphan'")
    _db.commit()
    result = analytics.detect_document_model_anomalies()
    assert result["summary"]["orphaned_job_count"] >= 1
    assert any(j["job_id"] == "job-orphan" for j in result["orphaned_jobs"])
    assert result["severity"] == "high"


def test_no_orphaned_jobs_when_all_linked(_db):
    _insert_doc(_db, "doc-1")
    _insert_job(_db, "job-1", doc_id="doc-1")
    result = analytics.detect_document_model_anomalies()
    assert result["summary"]["orphaned_job_count"] == 0


def test_queued_job_with_no_doc_id_not_flagged(_db):
    # Only "done" jobs without doc_id are orphaned
    _insert_job(_db, "job-queued", status="queued", doc_id=None)
    _db.execute("UPDATE dic_ingest_jobs SET doc_id = NULL WHERE job_id = 'job-queued'")
    _db.commit()
    result = analytics.detect_document_model_anomalies()
    assert result["summary"]["orphaned_job_count"] == 0


# ── Incomplete embedding ratio (IQR-based) ────────────────────────────────────

def test_incomplete_embedding_detected(_db):
    _insert_doc(_db, "doc-x")
    # 8 jobs with full embedding, 1 with very low ratio
    for i in range(8):
        _insert_job(_db, f"job-full-{i}", chunks_done=100, chunks_total=100)
    _insert_job(_db, "job-partial", chunks_done=2, chunks_total=100)
    result = analytics.detect_document_model_anomalies()
    assert result["summary"]["incomplete_embedding_count"] >= 1
    assert any(j["job_id"] == "job-partial" for j in result["incomplete_embedding"])
    assert result["severity"] in ("medium", "high")


def test_uniform_embedding_no_incomplete(_db):
    _insert_doc(_db, "doc-x")
    for i in range(6):
        _insert_job(_db, f"job-{i}", chunks_done=50, chunks_total=50)
    result = analytics.detect_document_model_anomalies()
    assert result["summary"]["incomplete_embedding_count"] == 0


def test_zero_chunk_total_jobs_excluded_from_ratio(_db):
    _insert_doc(_db, "doc-x")
    _insert_job(_db, "job-zero-total", chunks_done=0, chunks_total=0)
    result = analytics.detect_document_model_anomalies()
    assert result["summary"]["incomplete_embedding_count"] == 0


# ── Collection scoping ─────────────────────────────────────────────────────────

def test_collection_filter_scopes_docs(_db):
    _db.execute(
        "INSERT INTO dic_documents "
        "(doc_id, collection_id, title, filename, content_type, provider) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("doc-a", "col-a", "Title", "a.pdf", "application/pdf", None),
    )
    _insert_doc(_db, "doc-b", collection_id="col-b", provider="local")
    _db.commit()
    result = analytics.detect_document_model_anomalies(collection_id="col-b")
    assert result["summary"]["null_provider_count"] == 0
    assert result["summary"]["total_docs"] == 1


# ── Return shape ──────────────────────────────────────────────────────────────

def test_return_shape_complete(_db):
    _insert_doc(_db, "doc-1")
    _insert_job(_db, "job-1", doc_id="doc-1")
    result = analytics.detect_document_model_anomalies()
    for key in ("null_provider", "null_content_type", "incomplete_embedding",
                "orphaned_jobs", "summary", "severity", "severity_source",
                "heuristic_severity", "empty", "message"):
        assert key in result, f"missing key: {key}"
    for key in ("null_provider_count", "null_content_type_count",
                "incomplete_embedding_count", "orphaned_job_count",
                "total_docs", "total_jobs", "thresholds"):
        assert key in result["summary"], f"missing summary key: {key}"


# ── Heuristic severity ─────────────────────────────────────────────────────────

def test_heuristic_severity_clean(_db):
    _insert_doc(_db, "doc-1")
    result = analytics.detect_document_model_anomalies()
    assert result["heuristic_severity"] == "low"
    assert result["severity_source"] == "heuristic"


def test_heuristic_severity_high_for_orphaned_job():
    sev = analytics._model_attr_heuristic_severity({"orphaned_job_count": 1})
    assert sev == "high"


def test_heuristic_severity_medium_for_null_provider():
    sev = analytics._model_attr_heuristic_severity({"null_provider_count": 3})
    assert sev == "medium"


def test_heuristic_severity_medium_for_null_content_type():
    sev = analytics._model_attr_heuristic_severity({"null_content_type_count": 1})
    assert sev == "medium"


def test_heuristic_severity_medium_for_incomplete_embedding():
    sev = analytics._model_attr_heuristic_severity({"incomplete_embedding_count": 2})
    assert sev == "medium"
