# CUI // SP-CTI
"""Tests for log_doc_view() + detect_view_anomalies() — adaptive view anomaly detection.

aiify-opp-105: hardcoded_threshold -> anomaly_detection in document views.
View-count outlier boundaries adapt to the actual corpus distribution using
the 1.5-IQR rule — no hardcoded thresholds.
"""
from __future__ import annotations

import importlib
import sqlite3

import pytest

analytics = importlib.import_module("tools.document_intelligence.analytics_engine")


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def _db(tmp_path, monkeypatch):
    """Minimal SQLite DB with dic_documents + dic_doc_views."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE dic_documents (
            doc_id TEXT PRIMARY KEY,
            collection_id TEXT DEFAULT 'default',
            title TEXT,
            filename TEXT,
            byte_size INTEGER DEFAULT 0,
            page_count INTEGER DEFAULT 1
        );
        CREATE TABLE dic_doc_views (
            view_id TEXT PRIMARY KEY,
            doc_id TEXT NOT NULL,
            user_id TEXT NOT NULL DEFAULT 'anonymous',
            collection_id TEXT NOT NULL DEFAULT 'default',
            viewed_at TEXT DEFAULT (datetime('now')),
            tenant_id TEXT DEFAULT 'default',
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


def _add_doc(conn, doc_id, collection_id="col1"):
    conn.execute(
        "INSERT OR IGNORE INTO dic_documents (doc_id, collection_id, title, filename) "
        "VALUES (?,?,?,?)",
        (doc_id, collection_id, doc_id, f"{doc_id}.pdf"),
    )
    conn.commit()


def _add_views(conn, doc_id, count, collection_id="col1", user_id="alice"):
    for i in range(count):
        vid = f"{doc_id}_v{i}"
        conn.execute(
            "INSERT INTO dic_doc_views (view_id, doc_id, user_id, collection_id) "
            "VALUES (?,?,?,?)",
            (vid, doc_id, user_id, collection_id),
        )
    conn.commit()


# ── log_doc_view ──────────────────────────────────────────────────────────────

def test_log_doc_view_inserts_row(_db):
    _add_doc(_db, "d1")
    analytics.log_doc_view("d1", user_id="bob", collection_id="col1")
    rows = _db.execute("SELECT * FROM dic_doc_views WHERE doc_id = 'd1'").fetchall()
    assert len(rows) == 1
    assert rows[0]["user_id"] == "bob"
    assert rows[0]["collection_id"] == "col1"


def test_log_doc_view_generates_unique_ids(_db):
    _add_doc(_db, "d2")
    analytics.log_doc_view("d2")
    analytics.log_doc_view("d2")
    rows = _db.execute("SELECT view_id FROM dic_doc_views WHERE doc_id = 'd2'").fetchall()
    assert len(rows) == 2
    assert rows[0]["view_id"] != rows[1]["view_id"]


def test_log_doc_view_silently_ignores_errors(monkeypatch, _db):
    def _broken():
        raise RuntimeError("DB down")
    monkeypatch.setattr(analytics, "_conn", _broken)
    # Must not raise
    analytics.log_doc_view("any_doc")


# ── detect_view_anomalies — empty corpus ──────────────────────────────────────

def test_empty_corpus_returns_empty_flag(_db):
    result = analytics.detect_view_anomalies()
    assert result["empty"] is True
    assert "No documents" in result["message"]
    assert result["summary"]["hot_count"] == 0
    assert result["summary"]["unviewed_count"] == 0


# ── detect_view_anomalies — required keys ─────────────────────────────────────

def test_required_keys_present(_db):
    _add_doc(_db, "d1")
    result = analytics.detect_view_anomalies()
    for key in ("hot_docs", "cold_docs", "unviewed_docs", "summary", "thresholds",
                "empty", "message"):
        assert key in result, f"missing key: {key}"
    for key in ("hot_count", "cold_count", "unviewed_count", "total_docs", "window_days"):
        assert key in result["summary"], f"missing summary key: {key}"


# ── detect_view_anomalies — unviewed docs ─────────────────────────────────────

def test_unviewed_docs_reported(_db):
    _add_doc(_db, "viewed")
    _add_doc(_db, "untouched")
    _add_views(_db, "viewed", 5)
    result = analytics.detect_view_anomalies()
    unviewed_ids = [r["doc_id"] for r in result["unviewed_docs"]]
    assert "untouched" in unviewed_ids
    assert "viewed" not in unviewed_ids


# ── detect_view_anomalies — hot doc detection ─────────────────────────────────

def test_detects_hot_document(_db):
    # 6 baseline docs with 5 views each, 1 hot doc with 500 views
    for i in range(6):
        _add_doc(_db, f"normal{i}")
        _add_views(_db, f"normal{i}", 5)
    _add_doc(_db, "viral")
    _add_views(_db, "viral", 500)

    result = analytics.detect_view_anomalies()
    assert result["summary"]["hot_count"] == 1
    assert result["hot_docs"][0]["doc_id"] == "viral"
    assert result["hot_docs"][0]["view_count"] == 500


# ── detect_view_anomalies — uniform distribution, no anomalies ────────────────

def test_no_anomalies_for_uniform_corpus(_db):
    for i in range(8):
        _add_doc(_db, f"d{i}")
        _add_views(_db, f"d{i}", 10)
    result = analytics.detect_view_anomalies()
    assert result["empty"] is False
    assert result["summary"]["hot_count"] == 0
    assert result["summary"]["cold_count"] == 0


# ── detect_view_anomalies — collection filter ─────────────────────────────────

def test_collection_filter_scopes_results(_db):
    for i in range(6):
        _add_doc(_db, f"col1_{i}", collection_id="col1")
        _add_views(_db, f"col1_{i}", 5, collection_id="col1")
    _add_doc(_db, "viral_col2", collection_id="col2")
    _add_views(_db, "viral_col2", 999, collection_id="col2")

    result_col1 = analytics.detect_view_anomalies(collection_id="col1")
    assert result_col1["summary"]["hot_count"] == 0

    result_col2 = analytics.detect_view_anomalies(collection_id="col2")
    # Only 1 doc in col2, not enough for IQR — no hot docs
    assert result_col2["summary"]["hot_count"] == 0


# ── detect_view_anomalies — summary counts correct ────────────────────────────

def test_summary_total_docs_count(_db):
    for i in range(5):
        _add_doc(_db, f"d{i}")
    result = analytics.detect_view_anomalies()
    assert result["summary"]["total_docs"] == 5
    assert result["summary"]["unviewed_count"] == 5


# ── detect_view_anomalies — lists capped at 50 ───────────────────────────────

def test_unviewed_docs_list_capped_at_50(_db):
    for i in range(60):
        _add_doc(_db, f"unseen{i}")
    result = analytics.detect_view_anomalies()
    assert len(result["unviewed_docs"]) <= 50


def test_hot_docs_list_capped_at_50(_db):
    for i in range(6):
        _add_doc(_db, f"normal{i}")
        _add_views(_db, f"normal{i}", 1)
    for i in range(60):
        _add_doc(_db, f"hot{i}")
        _add_views(_db, f"hot{i}", 9999)
    result = analytics.detect_view_anomalies()
    assert len(result["hot_docs"]) <= 50


# ── detect_view_anomalies — window_days param ─────────────────────────────────

def test_window_days_in_summary(_db):
    _add_doc(_db, "d1")
    result = analytics.detect_view_anomalies(window_days=7)
    assert result["summary"]["window_days"] == 7
