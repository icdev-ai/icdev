#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for quality monitor — RAG evaluation feedback loop (D-KARL-8)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tests._sql_compat import connect as translating_connect


MONITOR_SCHEMA = """
CREATE TABLE IF NOT EXISTS ft_quality_snapshots (
    id TEXT PRIMARY KEY,
    snapshot_type TEXT NOT NULL,
    metric_name TEXT NOT NULL,
    metric_value REAL NOT NULL,
    baseline_value REAL,
    below_threshold INTEGER DEFAULT 0,
    details TEXT DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS rag_retrieval_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query_hash TEXT NOT NULL,
    profile TEXT,
    project_id TEXT,
    node_count INTEGER DEFAULT 0,
    edge_count INTEGER DEFAULT 0,
    top_score REAL DEFAULT 0.0,
    compressed INTEGER DEFAULT 0,
    duration_ms REAL DEFAULT 0.0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


@pytest.fixture
def monitor_db():
    # tools.finetune.quality_monitor authors PostgreSQL ``%s`` placeholders and
    # relies on StorageConnection to rewrite them for SQLite. Handing it a bare
    # sqlite3 connection makes every statement raise ``near "%": syntax error``.
    conn = translating_connect(":memory:")
    conn.executescript(MONITOR_SCHEMA)
    return conn


class TestMetricCollection:
    def test_collect_rag_metrics_empty(self, monitor_db):
        from tools.finetune.quality_monitor import _collect_rag_metrics

        metrics = _collect_rag_metrics(monitor_db)
        # Should return empty or zero metrics, not fail
        assert isinstance(metrics, dict)

    def test_collect_rag_metrics_with_data(self, monitor_db):
        from tools.finetune.quality_monitor import _collect_rag_metrics

        monitor_db.execute(
            "INSERT INTO rag_retrieval_log (query_hash, top_score, created_at) VALUES ('hash1', 0.8, datetime('now'))",
        )
        monitor_db.execute(
            "INSERT INTO rag_retrieval_log (query_hash, top_score, created_at) VALUES ('hash2', 0.6, datetime('now'))",
        )
        monitor_db.commit()
        metrics = _collect_rag_metrics(monitor_db)
        assert "avg_retrieval_score" in metrics
        assert metrics["avg_retrieval_score"] == pytest.approx(0.7, abs=0.01)
        assert metrics["query_count"] == 2


class TestSnapshotRecording:
    def test_record_snapshot(self, monitor_db):
        from tools.finetune.quality_monitor import _record_snapshot, _ensure_tables

        _ensure_tables(monitor_db)
        _record_snapshot(monitor_db, "ndcg", 0.3, 0.5, True)
        row = monitor_db.execute("SELECT * FROM ft_quality_snapshots").fetchone()
        assert row is not None
        assert row["metric_name"] == "ndcg"
        assert row["metric_value"] == 0.3
        assert row["below_threshold"] == 1

    def test_consecutive_failure_count(self, monitor_db):
        from tools.finetune.quality_monitor import _record_snapshot, _count_consecutive_failures, _ensure_tables

        _ensure_tables(monitor_db)
        # Record 3 failures then 1 success
        _record_snapshot(monitor_db, "ndcg", 0.3, 0.5, True)
        _record_snapshot(monitor_db, "ndcg", 0.4, 0.5, True)
        _record_snapshot(monitor_db, "ndcg", 0.35, 0.5, True)
        count = _count_consecutive_failures(monitor_db, "ndcg")
        assert count == 3

    def test_consecutive_resets_on_success(self, monitor_db):
        from tools.finetune.quality_monitor import _ensure_tables, _gen_id

        _ensure_tables(monitor_db)
        # Insert with explicit ordering via created_at timestamps
        for ts, below in [
            ("2026-03-20T01:00:00Z", 1),  # fail
            ("2026-03-20T02:00:00Z", 1),  # fail
            ("2026-03-20T03:00:00Z", 0),  # success
            ("2026-03-20T04:00:00Z", 1),  # fail
        ]:
            monitor_db.execute(
                "INSERT INTO ft_quality_snapshots (id, snapshot_type, metric_name, metric_value, baseline_value, below_threshold, created_at) "
                "VALUES (?, 'rag_eval', 'mrr', 0.3, 0.4, ?, ?)",
                (_gen_id(), below, ts),
            )
        monitor_db.commit()
        from tools.finetune.quality_monitor import _count_consecutive_failures

        count = _count_consecutive_failures(monitor_db, "mrr")
        assert count == 1  # Only 1 since last success


class TestQualityCheck:
    def test_check_disabled(self):
        from tools.finetune.quality_monitor import check_quality

        with patch("tools.finetune.quality_monitor._get_config", return_value={"enabled": False}):
            result = check_quality()
            assert result["status"] == "disabled"

    def test_check_no_data(self, monitor_db):
        from tools.finetune.quality_monitor import check_quality

        with patch("tools.finetune.quality_monitor._get_db", return_value=monitor_db):
            result = check_quality(conn=monitor_db)
            assert result["status"] == "ok"
            assert result["retrain_recommended"] is False

    def test_check_with_low_scores(self, monitor_db):
        from tools.finetune.quality_monitor import check_quality

        # Seed retrieval log with low scores
        for i in range(5):
            monitor_db.execute(
                "INSERT INTO rag_retrieval_log (query_hash, top_score, created_at) VALUES (?, 0.2, datetime('now'))",
                (f"h{i}",),
            )
        monitor_db.commit()
        result = check_quality(conn=monitor_db)
        assert result["status"] == "ok"
        # avg_retrieval_score = 0.2 which is below min_faithfulness 0.6
        if result["alerts"]:
            assert any(a["metric"] == "avg_retrieval_score" for a in result["alerts"])


class TestQualityStatus:
    def test_status_empty(self, monitor_db):
        from tools.finetune.quality_monitor import get_quality_status

        result = get_quality_status(conn=monitor_db)
        assert result["status"] == "ok"
        assert result["total_snapshots"] == 0
