# CUI // SP-CTI
"""Tests for tools/db/query_health.py — DB observability (crx-db-02).

Focus (per task acceptance): the SQLite graceful no-op path and the JSON shape
of the report / CLI. PG-specific catalog queries are exercised with a fake
connection so the DB-observability sections can be validated without a live
PostgreSQL server.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.db import query_health  # noqa: E402


# ---------------------------------------------------------------------------
# SQLite graceful no-op
# ---------------------------------------------------------------------------
def test_collect_sqlite_is_graceful_noop(monkeypatch):
    """On SQLite, collect() returns available=False with a clear reason — no raise."""
    # is_pg() (imported inside collect() from storage) resolves via get_backend(),
    # which reads ICDEV_STORAGE_BACKEND directly from the environment.
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")

    report = query_health.collect()
    assert report["available"] is False
    assert report["backend"] == "sqlite"
    assert "PostgreSQL" in report["reason"]
    # Shape: always carries these top-level keys regardless of backend.
    assert "generated_at" in report
    assert "idle_in_txn_alert_threshold_seconds" in report


def test_cli_json_sqlite(monkeypatch, capsys):
    """CLI --json emits valid JSON and exits 0 on SQLite."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")

    rc = query_health.main(["--json"])
    assert rc == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["backend"] == "sqlite"
    assert data["available"] is False


def test_cli_json_via_subprocess():
    """End-to-end: run the module as a script; must emit parseable JSON, exit 0.

    Forces SQLite so the test never touches a real PostgreSQL server.
    """
    import os
    env = dict(os.environ)
    env["ICDEV_STORAGE_BACKEND"] = "sqlite"
    env["PYTHONPATH"] = str(REPO_ROOT)
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "db" / "query_health.py"), "--json"],
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["available"] is False
    assert data["backend"] == "sqlite"


# ---------------------------------------------------------------------------
# Fake PG connection to validate section shapes without a live server
# ---------------------------------------------------------------------------
class _FakeCursor:
    def __init__(self, script):
        self._script = script
        self._rows = []

    def execute(self, sql, params=None):
        for needle, rows in self._script:
            if needle in sql:
                self._rows = rows
                return
        self._rows = []

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeConn:
    def __init__(self, script):
        self._script = script

    def cursor(self):
        return _FakeCursor(self._script)


def test_get_slow_queries_extension_absent():
    """No pg_stat_statements extension -> available False, empty lists, no raise."""
    conn = _FakeConn([("pg_extension", [])])  # extension check returns nothing
    result = query_health.get_slow_queries(conn, top_n=5)
    assert result["available"] is False
    assert "pg_stat_statements" in result["reason"]
    assert result["by_total_time"] == []


def test_get_slow_queries_present():
    script = [
        ("pg_extension", [{"?column?": 1}]),  # extension present
        ("information_schema.columns",
         [{"column_name": "total_exec_time"}, {"column_name": "mean_exec_time"}]),
        ("ORDER BY total_exec_time",
         [{"queryid": 123, "calls": 10, "total_ms": 50.0, "mean_ms": 5.0,
           "rows": 100, "query": "SELECT 1"}]),
        ("ORDER BY mean_exec_time",
         [{"queryid": 456, "calls": 2, "total_ms": 40.0, "mean_ms": 20.0,
           "rows": 4, "query": "SELECT 2"}]),
    ]
    conn = _FakeConn(script)
    result = query_health.get_slow_queries(conn, top_n=5)
    assert result["available"] is True
    assert result["by_total_time"][0]["queryid"] == "123"  # normalised to str
    assert result["by_mean_time"][0]["mean_ms"] == 20.0


def test_get_pool_health_emits_alert_over_threshold(monkeypatch):
    monkeypatch.setattr(query_health, "IDLE_IN_TXN_ALERT_THRESHOLD_SECONDS", 100.0)
    script = [
        ("GROUP BY state", [{"state": "active", "n": 3}, {"state": "idle", "n": 5},
                             {"state": "idle in transaction", "n": 1}]),
        ("wait_event_type", [{"waiting": 2}]),
        ("min(state_change)", [{"max_age_s": 500.0}]),
    ]
    conn = _FakeConn(script)
    result = query_health.get_pool_health(conn)
    server = result["server"]
    assert server["active"] == 3
    assert server["idle_in_transaction"] == 1
    assert server["waiting_on_lock"] == 2
    assert server["oldest_idle_in_txn_seconds"] == 500.0
    assert len(result["alerts"]) == 1
    assert result["alerts"][0]["metric"] == "oldest_idle_in_txn_seconds"


def test_get_pool_health_no_alert_under_threshold(monkeypatch):
    monkeypatch.setattr(query_health, "IDLE_IN_TXN_ALERT_THRESHOLD_SECONDS", 1000.0)
    script = [
        ("GROUP BY state", [{"state": "active", "n": 1}]),
        ("wait_event_type", [{"waiting": 0}]),
        ("min(state_change)", [{"max_age_s": 10.0}]),
    ]
    conn = _FakeConn(script)
    result = query_health.get_pool_health(conn)
    assert result["alerts"] == []


def test_get_seq_scan_tables_suggestion():
    script = [
        ("pg_stat_user_tables",
         [{"table_name": "big", "seq_scan": 500, "idx_scan": 1, "live_rows": 100000},
          {"table_name": "small", "seq_scan": 3, "idx_scan": 0, "live_rows": 5}]),
    ]
    conn = _FakeConn(script)
    result = query_health.get_seq_scan_tables(conn, top_n=10)
    assert result["available"] is True
    tables = {t["table_name"]: t for t in result["tables"]}
    assert "review index coverage" in tables["big"]["suggestion"].lower()
    assert "OK" in tables["small"]["suggestion"]


# ---------------------------------------------------------------------------
# health_check integration
# ---------------------------------------------------------------------------
def test_health_check_db_observability_section(monkeypatch):
    """check_db_observability is non-fatal and always success=True."""
    from tools.testing import health_check

    # Force SQLite so collect() returns a graceful no-op.
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")

    result = health_check.check_db_observability()
    assert result.success is True
    assert result.details["available"] is False
    assert result.details["backend"] == "sqlite"
    # Registered in the health-check registry.
    assert "db_observability" in health_check._HEALTH_CHECKS
