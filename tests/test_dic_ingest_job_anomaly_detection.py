"""Tests for detect_ingest_job_anomalies() — IQR-based task processing anomaly detection.

aiify-opp-91: hardcoded_threshold -> anomaly_detection in document ingestion task
processing (analogous to paperless-ngx tasks.py). Detects failed jobs, stale queued/
processing jobs, and latency outliers using the 1.5-IQR rule — no hardcoded thresholds.
"""
from __future__ import annotations

import importlib
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

analytics = importlib.import_module("tools.document_intelligence.analytics_engine")


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def _db(tmp_path, monkeypatch):
    """Minimal in-memory SQLite DB with dic_ingest_jobs."""
    db_path = tmp_path / "jobs_test.db"
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


def _ts(minutes_ago: float = 0) -> str:
    """ISO timestamp N minutes in the past."""
    dt = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return dt.isoformat()


def _insert_job(conn, job_id: str, status: str = "queued",
                collection_id: str = "col1",
                created_minutes_ago: float = 5,
                updated_minutes_ago: float = 5,
                stage_detail: str = "") -> None:
    conn.execute(
        "INSERT INTO dic_ingest_jobs "
        "(job_id, filename, collection_id, status, stage_detail, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (
            job_id,
            f"{job_id}.pdf",
            collection_id,
            status,
            stage_detail,
            _ts(created_minutes_ago),
            _ts(updated_minutes_ago),
        ),
    )
    conn.commit()


# ── Heuristic severity ────────────────────────────────────────────────────────

def test_heuristic_severity_low_when_no_anomalies():
    summary = {"failed_count": 0, "stale_queued_count": 0,
               "stale_processing_count": 0, "latency_outlier_count": 0}
    assert analytics._job_task_heuristic_severity(summary) == "low"


def test_heuristic_severity_high_on_failed_jobs():
    summary = {"failed_count": 1, "stale_queued_count": 0,
               "stale_processing_count": 0, "latency_outlier_count": 0}
    assert analytics._job_task_heuristic_severity(summary) == "high"


def test_heuristic_severity_high_on_stale_processing():
    summary = {"failed_count": 0, "stale_queued_count": 0,
               "stale_processing_count": 2, "latency_outlier_count": 0}
    assert analytics._job_task_heuristic_severity(summary) == "high"


def test_heuristic_severity_medium_on_stale_queued():
    summary = {"failed_count": 0, "stale_queued_count": 3,
               "stale_processing_count": 0, "latency_outlier_count": 0}
    assert analytics._job_task_heuristic_severity(summary) == "medium"


def test_heuristic_severity_medium_on_latency_outliers():
    summary = {"failed_count": 0, "stale_queued_count": 0,
               "stale_processing_count": 0, "latency_outlier_count": 1}
    assert analytics._job_task_heuristic_severity(summary) == "medium"


# ── Empty corpus ──────────────────────────────────────────────────────────────

def test_empty_corpus_returns_empty_flag(_db):
    result = analytics.detect_ingest_job_anomalies()
    assert result["empty"] is True
    assert "No ingestion jobs" in result["message"]
    assert result["severity"] == "low"
    assert result["summary"]["total_jobs"] == 0


# ── Required keys ─────────────────────────────────────────────────────────────

def test_required_keys_present(_db):
    _insert_job(_db, "j1", status="done", created_minutes_ago=10, updated_minutes_ago=5)
    result = analytics.detect_ingest_job_anomalies()
    for key in ("failed", "stale_queued", "stale_processing", "latency_outliers",
                "summary", "severity", "severity_source", "heuristic_severity",
                "empty", "message"):
        assert key in result, f"missing key: {key}"
    for k in ("failed_count", "stale_queued_count", "stale_processing_count",
               "latency_outlier_count", "total_jobs", "thresholds"):
        assert k in result["summary"], f"missing summary key: {k}"


# ── Healthy pipeline ──────────────────────────────────────────────────────────

def test_no_anomalies_on_healthy_jobs(_db):
    for i in range(6):
        _insert_job(_db, f"done{i}", status="done",
                    created_minutes_ago=15, updated_minutes_ago=5)
    result = analytics.detect_ingest_job_anomalies()
    assert result["empty"] is False
    assert result["summary"]["failed_count"] == 0
    assert result["summary"]["stale_queued_count"] == 0
    assert result["summary"]["stale_processing_count"] == 0
    assert result["severity"] == "low"


# ── Failed job detection ──────────────────────────────────────────────────────

def test_detects_failed_jobs(_db):
    _insert_job(_db, "ok1", status="done", created_minutes_ago=10, updated_minutes_ago=5)
    _insert_job(_db, "fail1", status="error", stage_detail="ocr_failed",
                updated_minutes_ago=2)
    result = analytics.detect_ingest_job_anomalies()
    assert result["summary"]["failed_count"] == 1
    assert result["failed"][0]["job_id"] == "fail1"
    assert result["failed"][0]["stage_detail"] == "ocr_failed"
    assert result["heuristic_severity"] == "high"


def test_failed_status_variants(_db):
    _insert_job(_db, "f1", status="error")
    _insert_job(_db, "f2", status="failed")
    result = analytics.detect_ingest_job_anomalies()
    assert result["summary"]["failed_count"] == 2


# ── Stale queued detection ────────────────────────────────────────────────────

def test_detects_stale_queued_jobs(_db):
    # Job queued 90 minutes ago (stale_minutes default=60)
    _insert_job(_db, "stale_q", status="queued", updated_minutes_ago=90)
    # Recent queued job — should NOT be flagged
    _insert_job(_db, "fresh_q", status="queued", updated_minutes_ago=5)
    result = analytics.detect_ingest_job_anomalies()
    assert result["summary"]["stale_queued_count"] == 1
    assert result["stale_queued"][0]["job_id"] == "stale_q"
    assert result["heuristic_severity"] == "medium"


def test_stale_queued_respects_stale_minutes_param(_db):
    _insert_job(_db, "q1", status="queued", updated_minutes_ago=30)
    # With stale_minutes=20, the 30-min-old job should be flagged
    result20 = analytics.detect_ingest_job_anomalies(stale_minutes=20)
    assert result20["summary"]["stale_queued_count"] == 1
    # With stale_minutes=60, the same job should NOT be flagged
    result60 = analytics.detect_ingest_job_anomalies(stale_minutes=60)
    assert result60["summary"]["stale_queued_count"] == 0


# ── Stale processing detection ────────────────────────────────────────────────

def test_detects_stale_processing_jobs(_db):
    _insert_job(_db, "stuck", status="processing", updated_minutes_ago=120)
    _insert_job(_db, "active", status="processing", updated_minutes_ago=10)
    result = analytics.detect_ingest_job_anomalies()
    assert result["summary"]["stale_processing_count"] == 1
    assert result["stale_processing"][0]["job_id"] == "stuck"
    assert result["heuristic_severity"] == "high"


# ── Latency outlier detection ─────────────────────────────────────────────────

def test_detects_latency_outliers(_db):
    # 6 fast jobs (~5s each) + 1 extremely slow job
    for i in range(6):
        _insert_job(_db, f"fast{i}", status="done",
                    created_minutes_ago=20, updated_minutes_ago=19)  # ~1 min = 60s
    # This job took 300 minutes → 18000 seconds (extreme outlier)
    _insert_job(_db, "slow", status="done",
                created_minutes_ago=310, updated_minutes_ago=10)
    result = analytics.detect_ingest_job_anomalies()
    assert result["summary"]["latency_outlier_count"] >= 1
    outlier_ids = [j["job_id"] for j in result["latency_outliers"]]
    assert "slow" in outlier_ids


def test_no_latency_outliers_when_all_uniform(_db):
    # All jobs take roughly the same time — no outliers expected
    for i in range(8):
        _insert_job(_db, f"u{i}", status="done",
                    created_minutes_ago=20, updated_minutes_ago=19)
    result = analytics.detect_ingest_job_anomalies()
    assert result["summary"]["latency_outlier_count"] == 0


def test_no_latency_outliers_when_fewer_than_4_completed(_db):
    # IQR returns -inf/+inf with <4 values — no outliers flagged
    for i in range(3):
        _insert_job(_db, f"c{i}", status="done",
                    created_minutes_ago=60, updated_minutes_ago=1)
    result = analytics.detect_ingest_job_anomalies()
    assert result["summary"]["latency_outlier_count"] == 0


# ── collection_id filter ──────────────────────────────────────────────────────

def test_collection_id_filter(_db):
    _insert_job(_db, "job_a", status="error", collection_id="colA")
    _insert_job(_db, "job_b", status="done", collection_id="colB",
                created_minutes_ago=10, updated_minutes_ago=5)
    result = analytics.detect_ingest_job_anomalies(collection_id="colA")
    assert result["summary"]["total_jobs"] == 1
    assert result["summary"]["failed_count"] == 1


# ── Severity source ───────────────────────────────────────────────────────────

def test_severity_source_is_heuristic_without_llm(_db):
    # No anomalies → heuristic path, severity=low
    _insert_job(_db, "ok", status="done", created_minutes_ago=5, updated_minutes_ago=2)
    result = analytics.detect_ingest_job_anomalies()
    assert result["severity_source"] == "heuristic"
    assert result["severity"] == "low"
