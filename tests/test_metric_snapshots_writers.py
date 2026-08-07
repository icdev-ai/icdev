#!/usr/bin/env python3
# CUI // SP-CTI
"""Regression tests for the metric_snapshots writers (obs-metric-01).

metric_snapshots shipped with two INSERT sites and zero rows in the live
database. Both sites were correct — they were simply unreachable: each is
callable only from its own CLI, and each needs a metrics backend (Prometheus /
ELK) that is not deployed. Four reader surfaces query the table regardless
(project_status, infra_status, the dashboard metrics API, mcp/core_server).

These tests hold the writers to their contract, so a future edit that stops the
table being written fails here instead of silently emptying it again:

  * store_snapshot actually persists the rows it claims to have written
  * log_analyzer._record_findings persists and reports its row count
  * analyze_logs records findings WITHOUT requiring a local SQLite file to
    exist — the guard that made every write a no-op on a PG-primary stack
  * self_monitor, the one scheduled path, records a snapshot every cycle

They run against a dedicated temp SQLite file rather than the shared icdev
fixture: get_connection() routes a non-primary '.db' path straight to SQLite
with RLS off, which keeps each test in the millisecond range.
"""

import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.db.storage import get_connection  # noqa: E402
from tools.genesis.reflexes import self_monitor  # noqa: E402
from tools.monitor import log_analyzer  # noqa: E402
from tools.monitor.metric_collector import store_snapshot  # noqa: E402

PROJECT_ID = "proj-obs-metric-01"

# Mirrors the live DDL in tools/db/init_icdev_db.py. The projects table is
# created too because metric_snapshots.project_id is FK-constrained on SQLite
# and storage.py turns PRAGMA foreign_keys ON.
_DDL = """
CREATE TABLE projects (
    id TEXT PRIMARY KEY,
    name TEXT
);
CREATE TABLE metric_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id),
    metric_name TEXT NOT NULL,
    metric_value REAL NOT NULL,
    labels TEXT,
    source TEXT DEFAULT 'prometheus',
    collected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""


@pytest.fixture()
def metrics_db(tmp_path, monkeypatch):
    """A throwaway SQLite database carrying the metric_snapshots schema."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    db = tmp_path / "metrics.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(_DDL)
    conn.execute("INSERT INTO projects (id, name) VALUES (?, ?)", (PROJECT_ID, "obs-metric-01"))
    conn.commit()
    conn.close()
    return db


def _rows(db: Path) -> list:
    conn = sqlite3.connect(str(db))
    try:
        return conn.execute(
            "SELECT project_id, metric_name, metric_value, source FROM metric_snapshots"
        ).fetchall()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# metric_collector.store_snapshot
# ---------------------------------------------------------------------------


def test_store_snapshot_persists_the_rows_it_reports(metrics_db):
    """The returned count must equal rows actually on disk, not rows attempted."""
    written = store_snapshot(
        PROJECT_ID,
        {"request_rate": 12.5, "error_rate": 0.01},
        db_path=metrics_db,
    )

    rows = _rows(metrics_db)
    assert written == 2
    assert len(rows) == 2, "store_snapshot reported a write that did not persist"
    assert {r[1] for r in rows} == {"request_rate", "error_rate"}
    assert {r[3] for r in rows} == {"prometheus"}


def test_store_snapshot_skips_unconvertible_values_without_inflating_count(metrics_db):
    """metric_value is NOT NULL REAL — non-numeric input is dropped, not counted."""
    written = store_snapshot(
        PROJECT_ID,
        {"good": 1.0, "bad": "not-a-number", "missing": None},
        db_path=metrics_db,
    )

    assert written == 1
    assert len(_rows(metrics_db)) == 1


# ---------------------------------------------------------------------------
# log_analyzer._record_findings / analyze_logs
# ---------------------------------------------------------------------------


def test_record_findings_persists_and_reports_its_row_count(metrics_db):
    analysis = {
        "error_rate": 0.25,
        "severity_counts": {"error": 5, "warning": 2},
        "total_logs": 20,
        "matched_patterns": [{"count": 3}],
        "frequency_anomalies": [],
        "silence_gaps": [],
        "query": "level:ERROR",
        "time_range": "24h",
    }

    written = log_analyzer._record_findings(PROJECT_ID, analysis, db_path=metrics_db)

    rows = _rows(metrics_db)
    assert written == len(rows) > 0, "_record_findings must report what it persisted"
    assert {r[3] for r in rows} == {"log_analyzer"}
    by_name = {r[1]: r[2] for r in rows}
    assert by_name["log_error_rate"] == pytest.approx(0.25)
    assert by_name["log_error_count"] == pytest.approx(5.0)
    assert by_name["log_total_count"] == pytest.approx(20.0)


def test_record_findings_returns_zero_when_the_write_fails(tmp_path, monkeypatch):
    """A failed write must report 0 rather than implying success."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    empty = tmp_path / "no_schema.db"
    sqlite3.connect(str(empty)).close()  # exists, but has no metric_snapshots table

    written = log_analyzer._record_findings(PROJECT_ID, {"error_rate": 0.1}, db_path=empty)

    assert written == 0


def test_analyze_logs_records_findings_without_a_local_sqlite_file(metrics_db, monkeypatch):
    """Regression: the write used to be gated on data/icdev.db existing on disk.

    On a PostgreSQL-primary stack that file is absent by design, so every
    finding was silently dropped even with ELK reachable and a project_id set.
    """
    def _storage_conn(db_path=None):
        # A real StorageConnection (never a bare sqlite3 handle) so the %s
        # placeholders in the INSERT are translated exactly as in production.
        return get_connection(db_path=str(metrics_db))

    monkeypatch.setattr(log_analyzer, "DB_PATH", Path("/nonexistent/icdev.db"))
    monkeypatch.setattr(log_analyzer, "_get_db", _storage_conn)
    # No log backend in the test environment — analyze_logs still produces a
    # (zero-count) analysis, which is exactly the case that must still persist.
    monkeypatch.setattr(log_analyzer, "_query_elk", lambda *a, **k: {"status": "error", "logs": []})

    result = log_analyzer.analyze_logs(
        source="elk",
        query="level:ERROR",
        time_range="1h",
        db_path=None,
        project_id=PROJECT_ID,
    )

    assert result["metrics_recorded"] > 0, "findings dropped when the SQLite file is absent"
    assert len(_rows(metrics_db)) == result["metrics_recorded"]


# ---------------------------------------------------------------------------
# self_monitor — the scheduled path that gives the table a live producer
# ---------------------------------------------------------------------------


def test_self_monitor_record_metrics_writes_a_snapshot(metrics_db, monkeypatch):
    monkeypatch.setattr(
        self_monitor,
        "_store_snapshot",
        lambda project_id, metrics, source: store_snapshot(
            project_id, metrics, source=source, db_path=metrics_db
        ),
    )

    written = self_monitor._record_metrics(
        PROJECT_ID,
        total_failing=3,
        alert_counts={"firing": 2, "opened": 1, "resolved": 0, "updated": 0},
        failures_logged=3,
        elapsed_ms=412,
    )

    rows = _rows(metrics_db)
    assert written == len(rows) > 0
    assert {r[3] for r in rows} == {"self_monitor"}
    by_name = {r[1]: r[2] for r in rows}
    assert by_name["self_monitor_failing_components"] == pytest.approx(3.0)
    assert by_name["self_monitor_alerts_firing"] == pytest.approx(2.0)
    assert by_name["self_monitor_cycle_ms"] == pytest.approx(412.0)


def test_self_monitor_record_metrics_survives_a_writer_failure(monkeypatch):
    """The reflex's job is alerting — a metrics failure must not raise."""
    def _boom(*_a, **_k):
        raise RuntimeError("metric_snapshots unavailable")

    monkeypatch.setattr(self_monitor, "_store_snapshot", _boom)

    assert self_monitor._record_metrics(PROJECT_ID, 1, {"firing": 0}, 0, 10) == 0


def test_self_monitor_is_wired_to_the_writer():
    """Guards the reachability fix itself: run() must still call _record_metrics."""
    import inspect

    source = inspect.getsource(self_monitor.run)
    assert "_record_metrics(" in source, "self_monitor stopped recording metric_snapshots"
    assert "metrics_recorded" in source
