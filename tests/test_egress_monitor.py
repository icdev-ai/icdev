#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for Network Egress Monitor — NemoClaw child network tracking (D-NC-6)."""

import json
import sqlite3
import subprocess
import sys
import uuid

import pytest

from tools.registry.egress_monitor import EgressMonitor


@pytest.fixture
def monitor_db(tmp_path):
    """Create temp DB with child_telemetry and egress policy tables."""
    db_path = tmp_path / "test_egress_mon.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS child_telemetry (
            id TEXT PRIMARY KEY,
            child_id TEXT,
            metric_type TEXT,
            metric_data TEXT,
            health_status TEXT,
            collected_at TEXT
        );
        CREATE TABLE IF NOT EXISTS egress_policy_audit (
            id TEXT, agent_role TEXT, policy_hash TEXT, action TEXT,
            applied_by TEXT, diff_summary TEXT, created_at TEXT
        );
    """)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def monitor(monitor_db):
    return EgressMonitor(db_path=monitor_db)


def _insert_egress_data(
    db_path,
    child_id="child-1",
    connections=None,
    collected_at="2026-01-01T00:00:00Z",
    health_status="healthy",
    error=None,
):
    """Insert egress telemetry row.  Supports custom timestamp and error flag."""
    conn = sqlite3.connect(str(db_path))
    data = {
        "child_id": child_id,
        "connections": connections or [],
        "collected_at": collected_at,
    }
    if error:
        data["error"] = error
    conn.execute(
        """INSERT INTO child_telemetry
           (id, child_id, metric_type, metric_data, health_status, collected_at)
           VALUES (?, ?, 'egress', ?, ?, ?)""",
        (str(uuid.uuid4()), child_id, json.dumps(data), health_status, collected_at),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# TestCollectEgress — endpoint polling
# ---------------------------------------------------------------------------
class TestCollectEgress:
    def test_collect_unreachable_endpoint(self, monitor):
        result = monitor.collect_egress("child-1", "http://localhost:99999/health/egress")
        assert result["child_id"] == "child-1"
        assert "error" in result

    def test_collect_returns_child_id(self, monitor):
        result = monitor.collect_egress("child-abc", "http://invalid:1234/health/egress")
        assert result["child_id"] == "child-abc"
        assert "collected_at" in result

    def test_collect_unreachable_returns_empty_connections(self, monitor):
        """Unreachable endpoint must still return an empty connections list."""
        result = monitor.collect_egress("child-99", "http://192.0.2.1:1/health/egress")
        assert result["connections"] == []
        assert "error" in result

    def test_collect_unreachable_includes_source(self, monitor):
        """Even on failure the source field must be present."""
        result = monitor.collect_egress("child-x", "http://127.0.0.1:1/health/egress")
        assert result["source"] == "health_endpoint"

    def test_collect_bad_scheme_returns_error(self, monitor):
        """Non-http scheme should be caught gracefully."""
        result = monitor.collect_egress("child-z", "ftp://badscheme:21/health/egress")
        assert "error" in result
        assert result["child_id"] == "child-z"


# ---------------------------------------------------------------------------
# TestStoreEgress — persistence
# ---------------------------------------------------------------------------
class TestStoreEgress:
    def test_store_egress_data(self, monitor, monitor_db):
        data = {
            "child_id": "child-1",
            "connections": [{"dest": "pypi.org", "port": 443}],
            "collected_at": "2026-01-01T00:00:00Z",
        }
        result = monitor.store_egress(data)
        assert result["stored"] is True

        conn = sqlite3.connect(str(monitor_db))
        count = conn.execute("SELECT COUNT(*) FROM child_telemetry WHERE metric_type = 'egress'").fetchone()[0]
        conn.close()
        assert count == 1

    def test_store_sets_healthy_status(self, monitor, monitor_db):
        """No error field -> health_status = healthy."""
        data = {"child_id": "c1", "connections": [], "collected_at": "2026-01-01T00:00:00Z"}
        monitor.store_egress(data)
        conn = sqlite3.connect(str(monitor_db))
        row = conn.execute("SELECT health_status FROM child_telemetry WHERE child_id = 'c1'").fetchone()
        conn.close()
        assert row[0] == "healthy"

    def test_store_sets_degraded_on_error(self, monitor, monitor_db):
        """Presence of error field -> health_status = degraded."""
        data = {
            "child_id": "c2",
            "connections": [],
            "collected_at": "2026-01-01T00:00:00Z",
            "error": "endpoint_unreachable",
        }
        monitor.store_egress(data)
        conn = sqlite3.connect(str(monitor_db))
        row = conn.execute("SELECT health_status FROM child_telemetry WHERE child_id = 'c2'").fetchone()
        conn.close()
        assert row[0] == "degraded"

    def test_store_returns_telemetry_id(self, monitor):
        data = {"child_id": "c3", "connections": [], "collected_at": "2026-01-01T00:00:00Z"}
        result = monitor.store_egress(data)
        assert result["stored"] is True
        assert "telemetry_id" in result
        assert len(result["telemetry_id"]) == 36  # UUID length


# ---------------------------------------------------------------------------
# TestEvaluateEgress — policy evaluation
# ---------------------------------------------------------------------------
class TestEvaluateEgress:
    def test_evaluate_no_data(self, monitor):
        result = monitor.evaluate_egress("child-1")
        assert result["evaluated"] is False
        assert result["reason"] == "no_egress_data"

    def test_evaluate_clean_egress(self, monitor, monitor_db):
        _insert_egress_data(
            monitor_db,
            connections=[
                {"dest": "localhost", "port": 11434},
            ],
        )
        result = monitor.evaluate_egress("child-1")
        assert result["evaluated"] is True
        assert result["violation_count"] == 0

    def test_evaluate_detects_violation(self, monitor, monitor_db):
        _insert_egress_data(
            monitor_db,
            connections=[
                {"dest": "evil.com", "port": 443},
            ],
        )
        result = monitor.evaluate_egress("child-1", role="security")
        assert result["evaluated"] is True
        assert result["violation_count"] >= 1
        assert result["violations"][0]["dest"] == "evil.com"

    def test_dns_always_allowed(self, monitor, monitor_db):
        _insert_egress_data(
            monitor_db,
            connections=[
                {"dest": "8.8.8.8", "port": 53},
            ],
        )
        result = monitor.evaluate_egress("child-1", role="security")
        assert result["violation_count"] == 0

    def test_localhost_always_allowed(self, monitor, monitor_db):
        """localhost on any port is implicitly allowed."""
        _insert_egress_data(
            monitor_db,
            connections=[
                {"dest": "localhost", "port": 9999},
            ],
        )
        result = monitor.evaluate_egress("child-1", role="security")
        assert result["violation_count"] == 0

    def test_loopback_ip_always_allowed(self, monitor, monitor_db):
        """127.0.0.1 on any port is implicitly allowed."""
        _insert_egress_data(
            monitor_db,
            connections=[
                {"dest": "127.0.0.1", "port": 12345},
            ],
        )
        result = monitor.evaluate_egress("child-1", role="security")
        assert result["violation_count"] == 0

    def test_evaluate_multiple_violations(self, monitor, monitor_db):
        """Multiple disallowed hosts should all be flagged."""
        _insert_egress_data(
            monitor_db,
            connections=[
                {"dest": "evil.com", "port": 443},
                {"dest": "malware.net", "port": 80},
                {"dest": "localhost", "port": 11434},  # allowed
            ],
        )
        result = monitor.evaluate_egress("child-1", role="security")
        assert result["violation_count"] == 2
        assert result["allowed_connections"] == 1
        assert result["total_connections"] == 3

    def test_evaluate_invalid_json_data(self, monitor, monitor_db):
        """Corrupted metric_data should be handled gracefully."""
        conn = sqlite3.connect(str(monitor_db))
        conn.execute(
            """INSERT INTO child_telemetry
               (id, child_id, metric_type, metric_data, health_status, collected_at)
               VALUES (?, 'child-bad', 'egress', 'NOT JSON', 'healthy', '2026-01-01T00:00:00Z')""",
            (str(uuid.uuid4()),),
        )
        conn.commit()
        conn.close()
        result = monitor.evaluate_egress("child-bad")
        assert result["evaluated"] is False
        assert result["reason"] == "invalid_egress_data"

    def test_evaluate_host_key_alias(self, monitor, monitor_db):
        """Connection entries may use 'host' instead of 'dest'."""
        _insert_egress_data(
            monitor_db,
            connections=[
                {"host": "evil.com", "port": 443},
            ],
        )
        result = monitor.evaluate_egress("child-1", role="security")
        assert result["violation_count"] >= 1
        assert result["violations"][0]["dest"] == "evil.com"

    def test_evaluate_returns_role(self, monitor, monitor_db):
        """Result must echo back the role used for evaluation."""
        _insert_egress_data(monitor_db, connections=[])
        result = monitor.evaluate_egress("child-1", role="builder")
        assert result["role"] == "builder"

    def test_evaluate_returns_evaluated_at(self, monitor, monitor_db):
        _insert_egress_data(monitor_db, connections=[])
        result = monitor.evaluate_egress("child-1")
        assert result["evaluated"] is True
        assert "evaluated_at" in result


# ---------------------------------------------------------------------------
# TestIsAllowed — unit-level policy matching
# ---------------------------------------------------------------------------
class TestIsAllowed:
    """Direct tests for the _is_allowed helper method."""

    def test_wildcard_host_any_port(self, monitor):
        """host='*' with port=0 should allow everything."""
        allowed = [{"host": "*", "port": 0}]
        assert monitor._is_allowed("anything.com", 443, allowed) is True
        assert monitor._is_allowed("another.org", 8080, allowed) is True

    def test_wildcard_host_specific_port(self, monitor):
        """host='*' with specific port should only match that port."""
        allowed = [{"host": "*", "port": 9090}]
        assert monitor._is_allowed("prom.internal", 9090, allowed) is True
        assert monitor._is_allowed("prom.internal", 443, allowed) is False

    def test_specific_host_any_port(self, monitor):
        """Specific host with port=0 means all ports allowed for that host."""
        allowed = [{"host": "pypi.org", "port": 0}]
        assert monitor._is_allowed("pypi.org", 443, allowed) is True
        assert monitor._is_allowed("pypi.org", 80, allowed) is True
        assert monitor._is_allowed("evil.com", 443, allowed) is False

    def test_specific_host_specific_port(self, monitor):
        allowed = [{"host": "pypi.org", "port": 443}]
        assert monitor._is_allowed("pypi.org", 443, allowed) is True
        assert monitor._is_allowed("pypi.org", 80, allowed) is False

    def test_empty_allowed_list(self, monitor):
        """Empty policy means only implicit allows (localhost/DNS)."""
        assert monitor._is_allowed("evil.com", 443, []) is False
        # localhost and DNS are always allowed
        assert monitor._is_allowed("localhost", 443, []) is True
        assert monitor._is_allowed("8.8.8.8", 53, []) is True


# ---------------------------------------------------------------------------
# TestSummary — time-window and aggregation
# ---------------------------------------------------------------------------
class TestSummary:
    def test_summary_empty(self, monitor):
        result = monitor.get_summary("child-1")
        assert result["total_collections"] == 0
        assert result["total_connections"] == 0

    def test_summary_with_data(self, monitor, monitor_db):
        _insert_egress_data(
            monitor_db,
            connections=[
                {"dest": "pypi.org", "port": 443},
                {"dest": "github.com", "port": 443},
            ],
        )
        result = monitor.get_summary("child-1", hours=8760)  # 1 year
        assert result["total_collections"] == 1
        assert result["total_connections"] == 2

    def test_summary_excludes_old_data(self, monitor, monitor_db):
        """Data outside the time window must be excluded."""
        _insert_egress_data(
            monitor_db,
            connections=[{"dest": "old.com", "port": 443}],
            collected_at="2020-01-01T00:00:00Z",
        )
        result = monitor.get_summary("child-1", hours=24)
        assert result["total_collections"] == 0
        assert result["total_connections"] == 0

    def test_summary_counts_errors(self, monitor, monitor_db):
        """Error entries should be tallied in the errors field."""
        _insert_egress_data(
            monitor_db,
            connections=[],
            error="endpoint_unreachable: timeout",
            health_status="degraded",
            collected_at="2026-03-18T00:00:00Z",
        )
        result = monitor.get_summary("child-1", hours=8760)
        assert result["total_collections"] == 1
        assert result["errors"] == 1

    def test_summary_multiple_children(self, monitor, monitor_db):
        """Summary for one child must not include another child's data."""
        _insert_egress_data(
            monitor_db,
            child_id="child-A",
            connections=[{"dest": "a.com", "port": 443}],
            collected_at="2026-03-18T00:00:00Z",
        )
        _insert_egress_data(
            monitor_db,
            child_id="child-B",
            connections=[
                {"dest": "b1.com", "port": 443},
                {"dest": "b2.com", "port": 443},
            ],
            collected_at="2026-03-18T00:00:00Z",
        )
        sum_a = monitor.get_summary("child-A", hours=8760)
        sum_b = monitor.get_summary("child-B", hours=8760)
        assert sum_a["total_collections"] == 1
        assert sum_a["total_connections"] == 1
        assert sum_b["total_collections"] == 1
        assert sum_b["total_connections"] == 2

    def test_summary_returns_hours_field(self, monitor):
        result = monitor.get_summary("child-1", hours=48)
        assert result["hours"] == 48


# ---------------------------------------------------------------------------
# TestCLI — subprocess invocation and --json structure
# ---------------------------------------------------------------------------
class TestCLI:
    def test_cli_summary_json(self, monitor_db):
        """--summary --json must produce valid JSON with expected keys."""
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.registry.egress_monitor",
                "--summary",
                "--child-id",
                "child-1",
                "--hours",
                "24",
                "--json",
                "--db-path",
                str(monitor_db),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "child_id" in data
        assert "total_collections" in data
        assert "total_connections" in data
        assert "errors" in data

    def test_cli_evaluate_json(self, monitor_db):
        """--evaluate --json must produce valid JSON with expected keys."""
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.registry.egress_monitor",
                "--evaluate",
                "--child-id",
                "child-1",
                "--json",
                "--db-path",
                str(monitor_db),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "child_id" in data
        assert "evaluated" in data

    def test_cli_collect_unreachable_json(self, monitor_db):
        """--collect on unreachable endpoint must still produce valid JSON."""
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.registry.egress_monitor",
                "--collect",
                "--child-id",
                "child-1",
                "--endpoint",
                "http://127.0.0.1:1/health/egress",
                "--json",
                "--db-path",
                str(monitor_db),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["child_id"] == "child-1"
        assert "error" in data
