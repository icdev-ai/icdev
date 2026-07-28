"""Tests for detect_document_routing_anomalies() — IQR-based routing
anomaly detection in the DIC corpus.

aiify-rm-a3344-phase-53: hardcoded_threshold -> anomaly_detection in document
routing (analogous to paperless-ngx matching.py).  Detects unrouted docs,
collection over-concentration, and content-type imbalance — no hardcoded
match-score thresholds.
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
    db_path = tmp_path / "routing_anomaly_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE dic_documents (
            doc_id        TEXT PRIMARY KEY,
            collection_id TEXT DEFAULT 'default',
            filename      TEXT,
            content_type  TEXT,
            provider      TEXT,
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


def _insert_doc(conn, doc_id: str, collection_id: str = "col1",
                content_type: str = "application/pdf",
                filename: str = "doc.pdf") -> None:
    conn.execute(
        "INSERT INTO dic_documents (doc_id, collection_id, filename, content_type) "
        "VALUES (?, ?, ?, ?)",
        (doc_id, collection_id, filename, content_type),
    )
    conn.commit()


# ── Empty corpus ──────────────────────────────────────────────────────────────

def test_empty_corpus(_db):
    result = analytics.detect_document_routing_anomalies()
    assert result["empty"] is True
    assert result["severity"] == "low"
    assert result["unrouted"] == []
    assert result["dominant_collection"] == []


# ── Unrouted documents ────────────────────────────────────────────────────────

def test_detects_unrouted_docs(_db):
    _insert_doc(_db, "d1", collection_id="default")
    _insert_doc(_db, "d2", collection_id="default")
    _insert_doc(_db, "d3", collection_id="reports")
    result = analytics.detect_document_routing_anomalies()
    assert result["empty"] is False
    unrouted_ids = {d["doc_id"] for d in result["unrouted"]}
    assert "d1" in unrouted_ids
    assert "d2" in unrouted_ids
    assert "d3" not in unrouted_ids
    assert result["summary"]["unrouted_count"] == 2


def test_no_unrouted_when_all_routed(_db):
    _insert_doc(_db, "d1", collection_id="reports")
    _insert_doc(_db, "d2", collection_id="contracts")
    result = analytics.detect_document_routing_anomalies()
    assert result["summary"]["unrouted_count"] == 0
    assert result["unrouted"] == []


# ── Severity escalation from unrouted ────────────────────────────────────────

def test_severity_medium_with_unrouted(_db):
    _insert_doc(_db, "d1", collection_id="default")
    result = analytics.detect_document_routing_anomalies()
    assert result["heuristic_severity"] == "medium"


def test_severity_low_all_routed(_db):
    _insert_doc(_db, "d1", collection_id="col-a")
    _insert_doc(_db, "d2", collection_id="col-b")
    result = analytics.detect_document_routing_anomalies()
    assert result["heuristic_severity"] == "low"


# ── Collection over-concentration (IQR-based) ─────────────────────────────────

def test_single_collection_concentration_flagged(_db):
    for i in range(10):
        _insert_doc(_db, f"d{i}", collection_id="mega-col")
    result = analytics.detect_document_routing_anomalies()
    assert result["summary"]["over_concentrated_collection"] is True
    assert any(d["collection_id"] == "mega-col" for d in result["dominant_collection"])
    assert result["heuristic_severity"] == "high"


def test_balanced_collections_not_flagged(_db):
    for col in ("a", "b", "c", "d", "e", "f", "g", "h"):
        _insert_doc(_db, f"d-{col}", collection_id=col)
    result = analytics.detect_document_routing_anomalies()
    assert result["summary"]["over_concentrated_collection"] is False
    assert result["dominant_collection"] == []


# ── Content-type imbalance (IQR-based) ───────────────────────────────────────

def test_content_type_distribution_populated(_db):
    for i in range(5):
        _insert_doc(_db, f"pdf-{i}", collection_id="col1", content_type="application/pdf")
    _insert_doc(_db, "docx-1", collection_id="col2", content_type="application/docx")
    result = analytics.detect_document_routing_anomalies()
    assert "application/pdf" in result["type_distribution"]
    assert result["type_distribution"]["application/pdf"] == 5


# ── Collection-scoped query ───────────────────────────────────────────────────

def test_collection_scope_filter(_db):
    _insert_doc(_db, "d1", collection_id="default")
    _insert_doc(_db, "d2", collection_id="reports")
    result = analytics.detect_document_routing_anomalies(collection_id="reports")
    assert result["summary"]["total_docs"] == 1
    assert result["summary"]["unrouted_count"] == 0


# ── Return-shape completeness ─────────────────────────────────────────────────

def test_return_shape_complete(_db):
    _insert_doc(_db, "d1", collection_id="reports")
    result = analytics.detect_document_routing_anomalies()
    required = {
        "unrouted", "dominant_collection", "dominant_type",
        "collection_distribution", "type_distribution",
        "summary", "severity", "severity_source", "heuristic_severity",
        "empty", "message",
    }
    assert required <= result.keys()
    summary_keys = {
        "unrouted_count", "total_docs", "collection_count",
        "dominant_collection_docs", "dominant_type_docs",
        "over_concentrated_collection", "thresholds",
    }
    assert summary_keys <= result["summary"].keys()
