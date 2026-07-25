# CUI // SP-CTI
"""Tests for genesis reflex health trends + critical-failure alerting (crx-gen-02).

Covers tools/genesis/reflex_health.py:
  * compute_reflex_health — per-reflex failure rate + duration percentiles,
    sorted highest-error-rate first.
  * recent_failures — the "all failures last 7 days" query with parsed error.
  * open_critical_reflex_alerts — opens one firing `alerts` row per critical
    reflex over threshold (cooldown: refreshed not duplicated), auto-resolves on
    recovery, and ignores non-critical reflexes.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from tools.db.storage import get_connection
from tools.genesis import reflex_health as rh


def _iso(minutes_ago: float = 0) -> str:
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()


@pytest.fixture()
def conn():
    c = get_connection()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS genesis_audit (
            id TEXT PRIMARY KEY, event_type TEXT NOT NULL, reflex_name TEXT,
            risk_tier TEXT, details TEXT, success INTEGER, duration_ms INTEGER,
            metric_name TEXT, metric_value REAL, gkp_id TEXT, created_at TEXT NOT NULL
        )
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT,
            severity TEXT NOT NULL CHECK(severity IN ('critical','warning','info')),
            source TEXT NOT NULL, title TEXT NOT NULL, description TEXT,
            status TEXT DEFAULT 'firing' CHECK(status IN ('firing','acknowledged','resolved')),
            acknowledged_by TEXT, resolved_at TIMESTAMP, auto_healed BOOLEAN DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    # Isolate: clear any rows a prior test/run left behind.
    c.execute("DELETE FROM genesis_audit")
    c.execute("DELETE FROM alerts WHERE source LIKE 'genesis-reflex-health%'")
    c.commit()
    yield c
    c.close()


def _audit(c, reflex, event_type, success=None, duration_ms=None, error=None, minutes_ago=1):
    details = json.dumps({"error": error}) if error else None
    c.execute(
        "INSERT INTO genesis_audit (id, event_type, reflex_name, success, duration_ms, details, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        [f"aud-{uuid.uuid4()}", event_type, reflex, success, duration_ms, details, _iso(minutes_ago)],
    )


class TestComputeReflexHealth:
    def test_failure_rate_and_percentiles(self, conn):
        # reflex "bad": 4 attempts, 1 completed (dur 100), 3 failed.
        _audit(conn, "bad", rh.EVENT_STARTED)
        _audit(conn, "bad", rh.EVENT_STARTED)
        _audit(conn, "bad", rh.EVENT_STARTED)
        _audit(conn, "bad", rh.EVENT_STARTED)
        _audit(conn, "bad", rh.EVENT_COMPLETED, success=1, duration_ms=100)
        _audit(conn, "bad", rh.EVENT_FAILED, success=0, error="boom")
        _audit(conn, "bad", rh.EVENT_FAILED, success=0, error="boom")
        _audit(conn, "bad", rh.EVENT_TRIPPED, success=0, error="boom")
        # reflex "good": 2 attempts, 2 completed (dur 10, 30), 0 failures.
        _audit(conn, "good", rh.EVENT_STARTED)
        _audit(conn, "good", rh.EVENT_STARTED)
        _audit(conn, "good", rh.EVENT_COMPLETED, success=1, duration_ms=10)
        _audit(conn, "good", rh.EVENT_COMPLETED, success=1, duration_ms=30)
        conn.commit()

        report = rh.compute_reflex_health(days=7, conn=conn)
        by_name = {r["reflex"]: r for r in report["reflexes"]}

        assert by_name["bad"]["attempts"] == 4
        assert by_name["bad"]["failures"] == 3
        assert by_name["bad"]["circuit_breaker_trips"] == 1
        assert by_name["bad"]["failure_rate"] == 0.75
        assert by_name["bad"]["p50_duration_ms"] == 100.0

        assert by_name["good"]["failures"] == 0
        assert by_name["good"]["failure_rate"] == 0.0
        assert by_name["good"]["p50_duration_ms"] == 20.0  # midpoint of [10,30]

        # Highest error rate first.
        assert report["reflexes"][0]["reflex"] == "bad"
        assert report["total_failures"] == 3

    def test_window_excludes_old_rows(self, conn):
        _audit(conn, "bad", rh.EVENT_FAILED, success=0, error="old", minutes_ago=60 * 24 * 10)
        conn.commit()
        report = rh.compute_reflex_health(days=7, conn=conn)
        assert report["total_failures"] == 0


class TestRecentFailures:
    def test_lists_parsed_errors_desc(self, conn):
        _audit(conn, "bad", rh.EVENT_FAILED, success=0, error="err-old", minutes_ago=5)
        _audit(conn, "bad", rh.EVENT_TRIPPED, success=0, error="err-new", minutes_ago=1)
        conn.commit()
        out = rh.recent_failures(days=7, conn=conn)
        assert len(out) == 2
        assert out[0]["error"] == "err-new"  # most recent first
        assert out[0]["tripped"] is True
        assert out[1]["error"] == "err-old"


_CFG = {
    "reflex_health": {
        "enabled": True,
        "default_severity": "critical",
        "cooldown_minutes": 60,
        "failure_threshold": 3,
        "critical_reflexes": ["heal", {"redaction_scan_reflex": "warning"}],
    }
}


class TestOpenCriticalReflexAlerts:
    def test_opens_alert_over_threshold(self, conn):
        for _ in range(3):
            _audit(conn, "heal", rh.EVENT_FAILED, success=0, error="heal broke", minutes_ago=2)
        conn.commit()
        res = rh.open_critical_reflex_alerts(_CFG, conn=conn)
        assert res["opened"] == 1

        row = conn.execute(
            "SELECT severity, status, title FROM alerts WHERE source = %s",
            ["genesis-reflex-health:heal"],
        ).fetchone()
        sev = row["severity"] if isinstance(row, dict) else row[0]
        status = row["status"] if isinstance(row, dict) else row[1]
        assert sev == "critical"
        assert status == "firing"

    def test_cooldown_refreshes_not_duplicates(self, conn):
        for _ in range(3):
            _audit(conn, "heal", rh.EVENT_FAILED, success=0, error="x", minutes_ago=2)
        conn.commit()
        rh.open_critical_reflex_alerts(_CFG, conn=conn)
        res2 = rh.open_critical_reflex_alerts(_CFG, conn=conn)
        assert res2["opened"] == 0
        assert res2["refreshed"] == 1
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM alerts WHERE source = %s AND status = 'firing'",
            ["genesis-reflex-health:heal"],
        ).fetchone()
        n = count["n"] if isinstance(count, dict) else count[0]
        assert n == 1

    def test_auto_resolves_on_recovery(self, conn):
        for _ in range(3):
            _audit(conn, "heal", rh.EVENT_FAILED, success=0, error="x", minutes_ago=2)
        conn.commit()
        rh.open_critical_reflex_alerts(_CFG, conn=conn)
        # No new failures → next pass sees zero in window → auto-resolve.
        conn.execute("DELETE FROM genesis_audit")
        conn.commit()
        res = rh.open_critical_reflex_alerts(_CFG, conn=conn)
        assert res["resolved"] == 1
        row = conn.execute(
            "SELECT status FROM alerts WHERE source = %s",
            ["genesis-reflex-health:heal"],
        ).fetchone()
        status = row["status"] if isinstance(row, dict) else row[0]
        assert status == "resolved"

    def test_below_threshold_no_alert(self, conn):
        _audit(conn, "heal", rh.EVENT_FAILED, success=0, error="x", minutes_ago=2)
        conn.commit()
        res = rh.open_critical_reflex_alerts(_CFG, conn=conn)
        assert res["opened"] == 0

    def test_non_critical_reflex_ignored(self, conn):
        for _ in range(5):
            _audit(conn, "some_noncritical", rh.EVENT_FAILED, success=0, error="x", minutes_ago=2)
        conn.commit()
        res = rh.open_critical_reflex_alerts(_CFG, conn=conn)
        assert res["opened"] == 0
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM alerts WHERE source = %s",
            ["genesis-reflex-health:some_noncritical"],
        ).fetchone()
        n = row["n"] if isinstance(row, dict) else row[0]
        assert n == 0

    def test_warning_severity_override(self, conn):
        for _ in range(3):
            _audit(conn, "redaction_scan_reflex", rh.EVENT_FAILED, success=0, error="x", minutes_ago=2)
        conn.commit()
        rh.open_critical_reflex_alerts(_CFG, conn=conn)
        row = conn.execute(
            "SELECT severity FROM alerts WHERE source = %s",
            ["genesis-reflex-health:redaction_scan_reflex"],
        ).fetchone()
        sev = row["severity"] if isinstance(row, dict) else row[0]
        assert sev == "warning"
