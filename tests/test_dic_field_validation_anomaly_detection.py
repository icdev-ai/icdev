"""Tests for detect_field_validation_anomalies() — IQR-based field validation
anomaly detection in the DIC corpus.

aiify-rm-a3344-phase-137: hardcoded_threshold -> anomaly_detection in document
field validation (analogous to paperless-ngx validators.py).  Detects empty
titles, empty filenames, title/filename length outliers, and unsupported
content types — no hardcoded length cut-offs.
"""
from __future__ import annotations

import importlib
import sqlite3

import pytest

analytics = importlib.import_module("tools.document_intelligence.analytics_engine")


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def _db(tmp_path, monkeypatch):
    """Minimal SQLite DB with dic_documents."""
    db_path = tmp_path / "field_val_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE dic_documents (
            doc_id        TEXT PRIMARY KEY,
            collection_id TEXT DEFAULT 'default',
            title         TEXT,
            filename      TEXT,
            content_type  TEXT,
            byte_size     INTEGER DEFAULT 0,
            page_count    INTEGER DEFAULT 1,
            tenant_id     TEXT DEFAULT 'default',
            classification TEXT DEFAULT 'CUI'
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


def _insert_doc(conn, doc_id: str, title: str = "Normal Document Title",
                filename: str = "document.pdf",
                content_type: str = "application/pdf",
                collection_id: str = "col1") -> None:
    conn.execute(
        "INSERT INTO dic_documents (doc_id, collection_id, title, filename, content_type) "
        "VALUES (?, ?, ?, ?, ?)",
        (doc_id, collection_id, title, filename, content_type),
    )
    conn.commit()


# ── Empty corpus ──────────────────────────────────────────────────────────────

def test_empty_returns_empty_flag(_db):
    result = analytics.detect_field_validation_anomalies()
    assert result["empty"] is True
    assert result["severity"] == "low"
    assert result["summary"]["total_docs"] == 0
    assert result["message"] == "No documents ingested yet."


# ── Empty title detection ─────────────────────────────────────────────────────

def test_empty_title_detected(_db):
    _insert_doc(_db, "doc-1", title="Normal Title")
    _insert_doc(_db, "doc-2", title="")
    result = analytics.detect_field_validation_anomalies()
    assert result["summary"]["empty_title_count"] == 1
    assert any(d["doc_id"] == "doc-2" for d in result["empty_title"])
    assert result["severity"] in ("medium", "high")


def test_null_title_detected(_db):
    _insert_doc(_db, "doc-1", title="Normal Title")
    conn = _db
    conn.execute(
        "INSERT INTO dic_documents (doc_id, collection_id, title, filename, content_type) "
        "VALUES (?, ?, ?, ?, ?)",
        ("doc-null", "col1", None, "file.pdf", "application/pdf"),
    )
    conn.commit()
    result = analytics.detect_field_validation_anomalies()
    assert result["summary"]["empty_title_count"] >= 1


# ── Empty filename detection ──────────────────────────────────────────────────

def test_empty_filename_flagged_high(_db):
    _insert_doc(_db, "doc-1", filename="normal.pdf")
    _insert_doc(_db, "doc-2", filename="")
    result = analytics.detect_field_validation_anomalies()
    assert result["summary"]["empty_filename_count"] == 1
    assert result["severity"] == "high"
    assert any(d["doc_id"] == "doc-2" for d in result["empty_filename"])


# ── IQR-based title length outliers — no hardcoded threshold ──────────────────

def test_title_too_long_detected(_db):
    for i in range(8):
        _insert_doc(_db, f"doc-{i}", title="Short")
    _insert_doc(_db, "doc-big", title="X" * 500)
    result = analytics.detect_field_validation_anomalies()
    assert result["summary"]["title_too_long_count"] >= 1
    assert any(d["doc_id"] == "doc-big" for d in result["title_too_long"])


def test_title_too_short_detected(_db):
    for i in range(8):
        _insert_doc(_db, f"doc-{i}", title="A Very Reasonably Sized Document Title")
    _insert_doc(_db, "doc-tiny", title="X")
    result = analytics.detect_field_validation_anomalies()
    assert result["summary"]["title_too_short_count"] >= 1
    assert any(d["doc_id"] == "doc-tiny" for d in result["title_too_short"])


def test_uniform_titles_no_outlier(_db):
    for i in range(6):
        _insert_doc(_db, f"doc-{i}", title="Consistent Title Length Here")
    result = analytics.detect_field_validation_anomalies()
    assert result["summary"]["title_too_long_count"] == 0
    assert result["summary"]["title_too_short_count"] == 0


# ── IQR-based filename length outliers ────────────────────────────────────────

def test_filename_too_long_detected(_db):
    for i in range(8):
        _insert_doc(_db, f"doc-{i}", filename="report.pdf")
    _insert_doc(_db, "doc-fname", filename="a" * 300 + ".pdf")
    result = analytics.detect_field_validation_anomalies()
    assert result["summary"]["filename_too_long_count"] >= 1
    assert any(d["doc_id"] == "doc-fname" for d in result["filename_too_long"])


def test_uniform_filenames_no_outlier(_db):
    for i in range(6):
        _insert_doc(_db, f"doc-{i}", filename="report.pdf")
    result = analytics.detect_field_validation_anomalies()
    assert result["summary"]["filename_too_long_count"] == 0


# ── Unsupported content type detection ────────────────────────────────────────

def test_unsupported_content_type_detected(_db):
    _insert_doc(_db, "doc-1", content_type="application/pdf")
    _insert_doc(_db, "doc-bad", content_type="application/x-executable")
    result = analytics.detect_field_validation_anomalies()
    assert result["summary"]["unsupported_type_count"] >= 1
    assert any(d["doc_id"] == "doc-bad" for d in result["unsupported_type"])
    assert result["severity"] == "high"


def test_supported_content_types_not_flagged(_db):
    for ct in ("application/pdf", "text/plain", "image/png", "image/jpeg"):
        _insert_doc(_db, f"doc-{ct.replace('/', '-')}", content_type=ct)
    result = analytics.detect_field_validation_anomalies()
    assert result["summary"]["unsupported_type_count"] == 0


def test_empty_content_type_not_flagged(_db):
    _insert_doc(_db, "doc-1", content_type="")
    result = analytics.detect_field_validation_anomalies()
    assert result["summary"]["unsupported_type_count"] == 0


# ── Collection scoping ─────────────────────────────────────────────────────────

def test_collection_filter_scopes_results(_db):
    _insert_doc(_db, "doc-a", collection_id="col-a", title="")
    _insert_doc(_db, "doc-b", collection_id="col-b", title="Good Title")
    result = analytics.detect_field_validation_anomalies(collection_id="col-b")
    assert result["summary"]["empty_title_count"] == 0
    assert result["summary"]["total_docs"] == 1


# ── Return shape ──────────────────────────────────────────────────────────────

def test_return_shape_complete(_db):
    _insert_doc(_db, "doc-1")
    result = analytics.detect_field_validation_anomalies()
    for key in ("empty_title", "title_too_long", "title_too_short",
                "empty_filename", "filename_too_long", "unsupported_type",
                "summary", "severity", "severity_source", "heuristic_severity",
                "empty", "message"):
        assert key in result, f"missing key: {key}"
    for key in ("empty_title_count", "title_too_long_count", "title_too_short_count",
                "empty_filename_count", "filename_too_long_count",
                "unsupported_type_count", "total_docs", "thresholds"):
        assert key in result["summary"], f"missing summary key: {key}"


def test_thresholds_populated_from_corpus(_db):
    for i in range(6):
        _insert_doc(_db, f"doc-{i}", title=f"Title of varying size {i * 5}")
    result = analytics.detect_field_validation_anomalies()
    thresholds = result["summary"]["thresholds"]
    assert isinstance(thresholds, dict)
    # IQR thresholds should be computed when corpus has ≥4 titled docs
    assert "title_len_high" in thresholds


# ── Heuristic severity ─────────────────────────────────────────────────────────

def test_heuristic_severity_clean(_db):
    for i in range(4):
        _insert_doc(_db, f"doc-{i}", title="Consistent Title")
    result = analytics.detect_field_validation_anomalies()
    assert result["heuristic_severity"] == "low"
    assert result["severity_source"] == "heuristic"


def test_heuristic_severity_medium_for_empty_title(_db):
    _insert_doc(_db, "doc-1", title="Present")
    _insert_doc(_db, "doc-2", title="")
    sev = analytics._field_validator_heuristic_severity({"empty_title_count": 1})
    assert sev == "medium"


def test_heuristic_severity_high_for_empty_filename(_db):
    sev = analytics._field_validator_heuristic_severity({"empty_filename_count": 1})
    assert sev == "high"


def test_heuristic_severity_high_for_unsupported_type(_db):
    sev = analytics._field_validator_heuristic_severity({"unsupported_type_count": 2})
    assert sev == "high"
