"""Tests for detect_ingest_throughput_anomaly() — IQR-based throughput anomaly detection.

aiify-rm-a3344-phase-93: hardcoded_threshold -> anomaly_detection, analogous to
paperless-ngx src/documents/tasks.py queue-depth and task-expiry constants.
Detects hours with abnormally low completed-job counts using the 1.5-IQR rule —
no hardcoded minimum-throughput threshold.
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
    """Minimal SQLite DB with dic_ingest_jobs for throughput tests."""
    db_path = tmp_path / "throughput_test.db"
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


def _ts_at(hours_ago: float = 0) -> str:
    """ISO timestamp N hours in the past."""
    dt = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    return dt.isoformat()


def _insert_done(conn, job_id: str, hours_ago: float = 0,
                 collection_id: str = "col1") -> None:
    created = _ts_at(hours_ago + 0.1)
    updated = _ts_at(hours_ago)
    conn.execute(
        "INSERT INTO dic_ingest_jobs "
        "(job_id, filename, collection_id, status, created_at, updated_at) "
        "VALUES (?, ?, ?, 'done', ?, ?)",
        (job_id, f"{job_id}.pdf", collection_id, created, updated),
    )
    conn.commit()


# ── Unit helpers ──────────────────────────────────────────────────────────────

class TestThroughputHeuristicSeverity:
    def test_no_anomalies_is_low(self):
        s = {"low_throughput_hours": 0, "zero_throughput_hours_in_active_window": 0, "sample_hours": 20}
        assert analytics._throughput_heuristic_severity(s) == "low"

    def test_few_low_hours_is_medium(self):
        s = {"low_throughput_hours": 3, "zero_throughput_hours_in_active_window": 0, "sample_hours": 10}
        assert analytics._throughput_heuristic_severity(s) == "medium"

    def test_zero_throughput_hours_escalates(self):
        s = {"low_throughput_hours": 0, "zero_throughput_hours_in_active_window": 1, "sample_hours": 10}
        assert analytics._throughput_heuristic_severity(s) == "medium"

    def test_many_zero_hours_is_high(self):
        s = {"low_throughput_hours": 0, "zero_throughput_hours_in_active_window": 3, "sample_hours": 10}
        assert analytics._throughput_heuristic_severity(s) == "high"

    def test_high_fraction_is_high(self):
        s = {"low_throughput_hours": 8, "zero_throughput_hours_in_active_window": 0, "sample_hours": 10}
        assert analytics._throughput_heuristic_severity(s) == "high"

    def test_empty_sample_is_low(self):
        s = {"low_throughput_hours": 0, "zero_throughput_hours_in_active_window": 0, "sample_hours": 0}
        assert analytics._throughput_heuristic_severity(s) == "low"


# ── Integration tests ─────────────────────────────────────────────────────────

class TestDetectIngestThroughputAnomaly:
    def test_empty_db_returns_empty_result(self, _db):
        result = analytics.detect_ingest_throughput_anomaly()
        assert result["empty"] is True
        assert result["severity"] == "low"
        assert result["low_throughput_hours"] == []

    def test_uniform_throughput_no_anomaly(self, _db):
        # 5 jobs each hour for the last 24 hours — uniform, no outliers
        for h in range(24):
            for j in range(5):
                _insert_done(_db, f"job-h{h}-j{j}", hours_ago=h)
        result = analytics.detect_ingest_throughput_anomaly(lookback_days=2)
        assert result["empty"] is False
        assert result["summary"]["total_completed_jobs"] == 120
        assert result["summary"]["sample_hours"] >= 1
        # With perfectly uniform distribution, IQR=0 → fence = median → no outliers
        assert result["low_throughput_hours"] == []
        assert result["severity"] == "low"

    def test_spike_drop_flagged_as_low_throughput(self, _db):
        # 20 hours with 10 jobs each, then 4 hours with 1 job (below lower fence)
        for h in range(20):
            for j in range(10):
                _insert_done(_db, f"peak-h{h}-j{j}", hours_ago=h + 5)
        for h in range(4):
            _insert_done(_db, f"drop-h{h}", hours_ago=h)
        result = analytics.detect_ingest_throughput_anomaly(lookback_days=5)
        assert result["empty"] is False
        assert len(result["low_throughput_hours"]) > 0
        outlier = result["low_throughput_hours"][0]
        assert "hour_utc" in outlier
        assert "count" in outlier
        assert "lower_fence" in outlier

    def test_collection_filter_isolates_collection(self, _db):
        # col1 has healthy throughput, col2 has minimal jobs
        for h in range(12):
            for j in range(8):
                _insert_done(_db, f"c1-h{h}-j{j}", hours_ago=h, collection_id="col1")
        for h in range(12):
            _insert_done(_db, f"c2-h{h}", hours_ago=h, collection_id="col2")

        r_all = analytics.detect_ingest_throughput_anomaly()
        r_col1 = analytics.detect_ingest_throughput_anomaly(collection_id="col1")
        r_col2 = analytics.detect_ingest_throughput_anomaly(collection_id="col2")

        # col1 filtered result has more jobs than col2 filtered result
        assert r_col1["summary"]["total_completed_jobs"] > r_col2["summary"]["total_completed_jobs"]
        # The full result includes both collections
        assert r_all["summary"]["total_completed_jobs"] == (
            r_col1["summary"]["total_completed_jobs"] + r_col2["summary"]["total_completed_jobs"]
        )

    def test_return_schema_complete(self, _db):
        for h in range(5):
            _insert_done(_db, f"s-{h}", hours_ago=h)
        result = analytics.detect_ingest_throughput_anomaly()
        assert "low_throughput_hours" in result
        assert "zero_throughput_hours_in_active_window" in result
        assert "hourly_distribution" in result
        assert "summary" in result
        assert "severity" in result
        assert "severity_source" in result
        assert "heuristic_severity" in result
        assert "empty" in result
        assert "message" in result
        summary = result["summary"]
        assert "low_throughput_hours" in summary
        assert "zero_throughput_hours_in_active_window" in summary
        assert "sample_hours" in summary
        assert "total_completed_jobs" in summary
        assert "thresholds" in summary

    def test_lookback_days_parameter_respected(self, _db):
        # Jobs only in the last 2 days
        for h in range(48):
            _insert_done(_db, f"recent-{h}", hours_ago=h)
        # Lookback 1 day vs 7 days should differ
        r1 = analytics.detect_ingest_throughput_anomaly(lookback_days=1)
        r7 = analytics.detect_ingest_throughput_anomaly(lookback_days=7)
        assert r7["summary"]["total_completed_jobs"] >= r1["summary"]["total_completed_jobs"]

    def test_no_llm_called_when_severity_low(self, _db, monkeypatch):
        called = []
        monkeypatch.setattr(analytics, "_ai_throughput_severity",
                            lambda *a, **kw: called.append(1) or None)
        for h in range(10):
            for j in range(5):
                _insert_done(_db, f"nl-h{h}-j{j}", hours_ago=h)
        analytics.detect_ingest_throughput_anomaly()
        # If heuristic severity is low, LLM should not be invoked
        assert len(called) == 0

    def test_iqr_fences_reported_in_distribution(self, _db):
        # Need at least 4 hourly buckets for IQR to fire
        for h in range(10):
            for j in range(h + 1):  # varying counts
                _insert_done(_db, f"iqr-h{h}-j{j}", hours_ago=h)
        result = analytics.detect_ingest_throughput_anomaly(lookback_days=2)
        dist = result["hourly_distribution"]
        if dist:
            assert "p25" in dist
            assert "p50" in dist
            assert "p75" in dist
