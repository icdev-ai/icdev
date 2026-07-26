"""Tests for detect_output_export_anomalies() — IQR-based output export anomaly detection.

aiify-rm-a3344-phase-41: hardcoded_threshold -> anomaly_detection in document
output/export pipeline (analogous to paperless-ngx document_exporter.py).
Detects oversized content_json payloads, error-status outputs, stale in-progress
outputs, and provider overconcentration — no hardcoded size thresholds.
"""
from __future__ import annotations

import importlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

analytics = importlib.import_module("tools.document_intelligence.analytics_engine")


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def _db(tmp_path, monkeypatch):
    """Minimal SQLite DB with dic_generated_outputs."""
    db_path = tmp_path / "outputs_test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE dic_generated_outputs (
            id            TEXT PRIMARY KEY,
            output_type   TEXT NOT NULL,
            collection_id TEXT DEFAULT 'default',
            provider      TEXT DEFAULT '',
            status        TEXT DEFAULT 'done',
            content_json  TEXT DEFAULT '{}',
            audio_path    TEXT,
            created_at    TEXT,
            updated_at    TEXT,
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


def _ts(minutes_ago: float = 0) -> str:
    dt = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return dt.isoformat()


def _insert_output(conn, oid: str, status: str = "done",
                   collection_id: str = "col1", provider: str = "anthropic",
                   content_json: str | None = None,
                   output_type: str = "study_guide",
                   updated_ago_minutes: float = 0) -> None:
    content = content_json if content_json is not None else json.dumps({"text": "ok"})
    conn.execute(
        "INSERT INTO dic_generated_outputs "
        "(id, output_type, collection_id, provider, status, content_json, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (oid, output_type, collection_id, provider, status, content,
         _ts(updated_ago_minutes + 1), _ts(updated_ago_minutes)),
    )
    conn.commit()


# ── Empty corpus ──────────────────────────────────────────────────────────────

def test_empty_returns_empty_flag(_db):
    result = analytics.detect_output_export_anomalies()
    assert result["empty"] is True
    assert result["severity"] == "low"
    assert result["summary"]["total_outputs"] == 0


# ── Error detection ───────────────────────────────────────────────────────────

def test_error_status_detected(_db):
    _insert_output(_db, "out-1", status="done")
    _insert_output(_db, "out-2", status="error")
    result = analytics.detect_output_export_anomalies()
    assert result["summary"]["error_count"] == 1
    assert result["severity"] == "high"
    assert any(e["id"] == "out-2" for e in result["errors"])


def test_failed_status_detected(_db):
    _insert_output(_db, "out-1", status="failed")
    result = analytics.detect_output_export_anomalies()
    assert result["summary"]["error_count"] == 1
    assert result["severity"] == "high"


# ── Stale detection ───────────────────────────────────────────────────────────

def test_stale_in_progress_detected(_db):
    _insert_output(_db, "out-1", status="processing", updated_ago_minutes=60)
    result = analytics.detect_output_export_anomalies(stale_minutes=30)
    assert result["summary"]["stale_count"] == 1
    assert result["severity"] in ("medium", "high")


def test_recent_in_progress_not_stale(_db):
    _insert_output(_db, "out-1", status="processing", updated_ago_minutes=5)
    result = analytics.detect_output_export_anomalies(stale_minutes=30)
    assert result["summary"]["stale_count"] == 0


# ── IQR-based size detection — no hardcoded threshold ─────────────────────────

def test_oversized_content_detected(_db):
    normal_content = json.dumps({"text": "x" * 100})
    for i in range(8):
        _insert_output(_db, f"out-{i}", content_json=normal_content)
    huge_content = json.dumps({"text": "x" * 100_000})
    _insert_output(_db, "out-big", content_json=huge_content)
    result = analytics.detect_output_export_anomalies()
    assert result["summary"]["oversized_count"] >= 1
    assert any(e["id"] == "out-big" for e in result["oversized"])


def test_uniform_sizes_no_outlier(_db):
    content = json.dumps({"text": "hello world"})
    for i in range(6):
        _insert_output(_db, f"out-{i}", content_json=content)
    result = analytics.detect_output_export_anomalies()
    assert result["summary"]["oversized_count"] == 0


# ── Provider concentration ─────────────────────────────────────────────────────

def test_provider_overconcentration_detected(_db):
    for i in range(9):
        _insert_output(_db, f"out-{i}", provider="anthropic")
    _insert_output(_db, "out-other", provider="openai")
    result = analytics.detect_output_export_anomalies()
    assert result["summary"]["overconcentrated_providers"] >= 1
    assert result["provider_concentration"]["anthropic"] == 9


def test_no_overconcentration_below_threshold(_db):
    for i in range(3):
        _insert_output(_db, f"out-{i}", provider="anthropic")
    result = analytics.detect_output_export_anomalies()
    # fewer than 5 total — overconcentration guard requires min 5
    assert result["summary"]["overconcentrated_providers"] == 0


# ── Collection scoping ─────────────────────────────────────────────────────────

def test_collection_filter_scopes_results(_db):
    _insert_output(_db, "out-a", collection_id="col-a", status="error")
    _insert_output(_db, "out-b", collection_id="col-b", status="done")
    result = analytics.detect_output_export_anomalies(collection_id="col-b")
    assert result["summary"]["error_count"] == 0
    assert result["summary"]["total_outputs"] == 1


# ── Return shape ──────────────────────────────────────────────────────────────

def test_return_shape_complete(_db):
    _insert_output(_db, "out-1")
    result = analytics.detect_output_export_anomalies()
    for key in ("oversized", "errors", "stale", "provider_concentration",
                "summary", "severity", "severity_source", "heuristic_severity",
                "empty", "message"):
        assert key in result, f"missing key: {key}"
    for key in ("oversized_count", "error_count", "stale_count",
                "overconcentrated_providers", "total_outputs", "thresholds"):
        assert key in result["summary"], f"missing summary key: {key}"


# ── Heuristic severity ────────────────────────────────────────────────────────

def test_heuristic_severity_clean(_db):
    _insert_output(_db, "out-1", status="done")
    result = analytics.detect_output_export_anomalies()
    assert result["heuristic_severity"] == "low"
    assert result["severity_source"] == "heuristic"
