"""Tests for detect_bulk_edit_anomalies() — IQR-based bulk document operation anomaly detection.

aiify-rm-a3344-phase-12: hardcoded_threshold -> anomaly_detection in bulk document editing
(analogous to paperless-ngx bulk_edit.py). Detects oversized batch jobs, error jobs,
abandoned (partial-completion) jobs, and collection failure clusters using IQR-based
outlier detection — no hardcoded chunk-size caps.
"""
from __future__ import annotations

import importlib
import sqlite3

import pytest

analytics = importlib.import_module("tools.document_intelligence.analytics_engine")


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def _db(tmp_path, monkeypatch):
    """Minimal in-memory SQLite DB with dic_ingest_jobs."""
    db_path = tmp_path / "bulk_edit_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE dic_ingest_jobs (
            job_id        TEXT PRIMARY KEY,
            filename      TEXT NOT NULL,
            collection_id TEXT DEFAULT 'default',
            status        TEXT DEFAULT 'queued',
            stage_detail  TEXT DEFAULT '',
            chunks_total  INTEGER DEFAULT 0,
            chunks_done   INTEGER DEFAULT 0,
            doc_id        TEXT,
            errors_json   TEXT DEFAULT '[]',
            created_at    TEXT,
            updated_at    TEXT,
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


def _insert_job(
    conn,
    job_id: str,
    status: str = "done",
    collection_id: str = "col1",
    chunks_total: int = 10,
    chunks_done: int = 10,
    errors_json: str = "[]",
) -> None:
    conn.execute(
        "INSERT INTO dic_ingest_jobs "
        "(job_id, filename, collection_id, status, chunks_total, chunks_done, errors_json) "
        "VALUES (?,?,?,?,?,?,?)",
        (
            job_id,
            f"{job_id}.pdf",
            collection_id,
            status,
            chunks_total,
            chunks_done,
            errors_json,
        ),
    )
    conn.commit()


# ── Heuristic severity ────────────────────────────────────────────────────────

def test_heuristic_severity_low_when_no_anomalies():
    summary = {
        "oversized_batch_count": 0,
        "error_job_count": 0,
        "abandoned_job_count": 0,
        "collection_failure_clusters": 0,
    }
    assert analytics._bulk_edit_heuristic_severity(summary) == "low"


def test_heuristic_severity_high_on_error_jobs():
    summary = {
        "oversized_batch_count": 0,
        "error_job_count": 2,
        "abandoned_job_count": 0,
        "collection_failure_clusters": 0,
    }
    assert analytics._bulk_edit_heuristic_severity(summary) == "high"


def test_heuristic_severity_high_on_collection_failure_clusters():
    summary = {
        "oversized_batch_count": 0,
        "error_job_count": 0,
        "abandoned_job_count": 0,
        "collection_failure_clusters": 1,
    }
    assert analytics._bulk_edit_heuristic_severity(summary) == "high"


def test_heuristic_severity_medium_on_abandoned_jobs():
    summary = {
        "oversized_batch_count": 0,
        "error_job_count": 0,
        "abandoned_job_count": 1,
        "collection_failure_clusters": 0,
    }
    assert analytics._bulk_edit_heuristic_severity(summary) == "medium"


def test_heuristic_severity_medium_on_oversized_batches():
    summary = {
        "oversized_batch_count": 3,
        "error_job_count": 0,
        "abandoned_job_count": 0,
        "collection_failure_clusters": 0,
    }
    assert analytics._bulk_edit_heuristic_severity(summary) == "medium"


# ── Empty corpus ──────────────────────────────────────────────────────────────

def test_empty_corpus_returns_empty_flag(_db):
    result = analytics.detect_bulk_edit_anomalies()
    assert result["empty"] is True
    assert result["severity"] == "low"
    assert result["summary"]["total_jobs"] == 0
    assert result["message"] is not None


# ── Required keys present ─────────────────────────────────────────────────────

def test_required_keys_present(_db):
    _insert_job(_db, "j1", status="done")
    result = analytics.detect_bulk_edit_anomalies()
    for key in (
        "oversized_batches", "error_jobs", "abandoned_jobs",
        "collection_failure_clusters", "summary",
        "severity", "severity_source", "heuristic_severity",
        "empty", "message",
    ):
        assert key in result, f"missing key: {key}"
    for k in (
        "oversized_batch_count", "error_job_count", "abandoned_job_count",
        "collection_failure_clusters", "total_jobs", "thresholds",
    ):
        assert k in result["summary"], f"missing summary key: {k}"


# ── Healthy pipeline ──────────────────────────────────────────────────────────

def test_no_anomalies_on_healthy_jobs(_db):
    for i in range(6):
        _insert_job(_db, f"ok{i}", status="done", chunks_total=10, chunks_done=10)
    result = analytics.detect_bulk_edit_anomalies()
    assert result["empty"] is False
    assert result["summary"]["error_job_count"] == 0
    assert result["summary"]["abandoned_job_count"] == 0
    assert result["severity"] == "low"


# ── Oversized batch detection (IQR-based) ────────────────────────────────────

def test_detects_oversized_batch_job(_db):
    # 6 normal jobs with chunks_total=10, 1 extreme outlier with 500
    for i in range(6):
        _insert_job(_db, f"norm{i}", chunks_total=10, chunks_done=10)
    _insert_job(_db, "huge", chunks_total=500, chunks_done=500)
    result = analytics.detect_bulk_edit_anomalies()
    assert result["summary"]["oversized_batch_count"] >= 1
    ids = [j["job_id"] for j in result["oversized_batches"]]
    assert "huge" in ids


def test_no_oversized_with_fewer_than_4_jobs(_db):
    # IQR requires ≥4 values — below that, no outliers flagged
    for i in range(3):
        _insert_job(_db, f"j{i}", chunks_total=10 * (i + 1), chunks_done=10 * (i + 1))
    result = analytics.detect_bulk_edit_anomalies()
    assert result["summary"]["oversized_batch_count"] == 0


# ── Error job detection ───────────────────────────────────────────────────────

def test_detects_error_status_jobs(_db):
    _insert_job(_db, "ok1", status="done")
    _insert_job(_db, "err1", status="error", errors_json='["stage failed"]')
    result = analytics.detect_bulk_edit_anomalies()
    assert result["summary"]["error_job_count"] == 1
    assert result["error_jobs"][0]["job_id"] == "err1"
    assert result["heuristic_severity"] == "high"


def test_detects_non_empty_errors_json_as_error(_db):
    _insert_job(_db, "done_but_errored", status="done",
                errors_json='["chunk 3 parse error"]')
    result = analytics.detect_bulk_edit_anomalies()
    assert result["summary"]["error_job_count"] == 1
    assert result["error_jobs"][0]["error_count"] == 1


def test_empty_errors_json_not_flagged(_db):
    _insert_job(_db, "clean", status="done", errors_json="[]")
    result = analytics.detect_bulk_edit_anomalies()
    assert result["summary"]["error_job_count"] == 0


# ── Abandoned job detection ───────────────────────────────────────────────────

def test_detects_abandoned_jobs(_db):
    # Job with chunks_total=20 but only 8 done and still in 'processing' state
    _insert_job(_db, "partial", status="processing", chunks_total=20, chunks_done=8)
    _insert_job(_db, "complete", status="done", chunks_total=20, chunks_done=20)
    result = analytics.detect_bulk_edit_anomalies()
    assert result["summary"]["abandoned_job_count"] == 1
    abandoned = result["abandoned_jobs"][0]
    assert abandoned["job_id"] == "partial"
    assert abandoned["chunks_done"] == 8
    assert abandoned["chunks_total"] == 20
    assert abandoned["completion_ratio"] == pytest.approx(0.4, abs=0.01)
    assert result["heuristic_severity"] == "medium"


def test_error_status_not_also_flagged_as_abandoned(_db):
    # Error jobs should be in error_jobs, NOT also in abandoned_jobs
    _insert_job(_db, "err_partial", status="error", chunks_total=20, chunks_done=5,
                errors_json='["failed"]')
    result = analytics.detect_bulk_edit_anomalies()
    assert result["summary"]["error_job_count"] == 1
    assert result["summary"]["abandoned_job_count"] == 0


# ── Collection failure cluster detection ─────────────────────────────────────

def test_detects_collection_failure_cluster(_db):
    # ≥2 error jobs in the same collection trigger a cluster
    _insert_job(_db, "e1", status="error", collection_id="colA",
                errors_json='["err"]')
    _insert_job(_db, "e2", status="error", collection_id="colA",
                errors_json='["err"]')
    _insert_job(_db, "e3", status="error", collection_id="colB",
                errors_json='["err"]')  # Only 1 in colB — no cluster
    result = analytics.detect_bulk_edit_anomalies()
    assert result["summary"]["collection_failure_clusters"] == 1
    cluster_ids = [c["collection_id"] for c in result["collection_failure_clusters"]]
    assert "colA" in cluster_ids
    assert "colB" not in cluster_ids


def test_single_error_per_collection_no_cluster(_db):
    _insert_job(_db, "e1", status="error", collection_id="colA", errors_json='["e"]')
    _insert_job(_db, "e2", status="error", collection_id="colB", errors_json='["e"]')
    result = analytics.detect_bulk_edit_anomalies()
    assert result["summary"]["collection_failure_clusters"] == 0


# ── collection_id filter ──────────────────────────────────────────────────────

def test_collection_id_filter(_db):
    _insert_job(_db, "job_a", status="error", collection_id="colA",
                errors_json='["bad"]')
    _insert_job(_db, "job_b", status="done", collection_id="colB")
    result = analytics.detect_bulk_edit_anomalies(collection_id="colA")
    assert result["summary"]["total_jobs"] == 1
    assert result["summary"]["error_job_count"] == 1


def test_collection_id_filter_excludes_other_collections(_db):
    _insert_job(_db, "x1", status="error", collection_id="colX", errors_json='["e"]')
    _insert_job(_db, "y1", status="done", collection_id="colY")
    result = analytics.detect_bulk_edit_anomalies(collection_id="colY")
    assert result["summary"]["error_job_count"] == 0
    assert result["summary"]["total_jobs"] == 1


# ── Severity source ───────────────────────────────────────────────────────────

def test_severity_source_is_heuristic_on_clean_corpus(_db):
    for i in range(4):
        _insert_job(_db, f"clean{i}", status="done")
    result = analytics.detect_bulk_edit_anomalies()
    assert result["severity_source"] == "heuristic"
    assert result["severity"] == "low"
