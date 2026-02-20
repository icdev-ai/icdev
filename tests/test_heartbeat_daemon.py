# CUI // SP-CTI
"""Tests for tools/monitor/heartbeat_daemon.py — proactive heartbeat daemon."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from tools.monitor.heartbeat_daemon import (
    CHECK_REGISTRY,
    _ensure_table,
    _load_config,
    check_agent_health,
    check_cato_evidence,
    check_cve_sla,
    check_expiring_isas,
    check_failing_tests,
    check_memory_maintenance,
    check_pending_intake,
    get_check_status,
    run_all_checks,
    run_single_check,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _init_test_db(db_path: Path) -> None:
    """Create the minimal tables the heartbeat checks query against."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS cato_evidence (
            id INTEGER PRIMARY KEY,
            control_id TEXT,
            evidence_type TEXT,
            collected_at TEXT,
            automation_frequency TEXT DEFAULT 'daily'
        );
        CREATE TABLE IF NOT EXISTS agents (
            agent_id TEXT PRIMARY KEY,
            name TEXT,
            status TEXT DEFAULT 'active',
            last_heartbeat TEXT
        );
        CREATE TABLE IF NOT EXISTS cve_triage (
            id TEXT PRIMARY KEY,
            cve_id TEXT,
            component TEXT,
            severity TEXT,
            status TEXT DEFAULT 'open',
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS intake_sessions (
            session_id TEXT PRIMARY KEY,
            customer_name TEXT,
            customer_org TEXT,
            session_status TEXT DEFAULT 'active',
            updated_at TEXT
        );
        CREATE TABLE IF NOT EXISTS failure_log (
            id INTEGER PRIMARY KEY,
            failure_type TEXT,
            error_summary TEXT,
            resolved INTEGER DEFAULT 0,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS isa_agreements (
            id TEXT PRIMARY KEY,
            partner_org TEXT,
            status TEXT DEFAULT 'active',
            expiry_date TEXT
        );
    """)
    conn.commit()
    conn.close()


def _init_memory_db(db_path: Path) -> None:
    """Create the minimal memory.db tables for check_memory_maintenance."""
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS memory_entries (
            id TEXT PRIMARY KEY,
            content_type TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS memory_access_log (
            id INTEGER PRIMARY KEY,
            entry_id TEXT,
            accessed_at TEXT
        );
    """)
    conn.commit()
    conn.close()


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _hours_ago(n: int) -> str:
    dt = datetime.now(timezone.utc) - timedelta(hours=n)
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _days_ago(n: int) -> str:
    dt = datetime.now(timezone.utc) - timedelta(days=n)
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


# ===================================================================
# TestEnsureTable
# ===================================================================
class TestEnsureTable:
    def test_creates_table(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        _ensure_table(db)
        conn = sqlite3.connect(str(db))
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        conn.close()
        assert "heartbeat_checks" in tables

    def test_idempotent(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        _ensure_table(db)
        _ensure_table(db)  # no error on second call


# ===================================================================
# TestLoadConfig
# ===================================================================
class TestLoadConfig:
    def test_returns_dict(self) -> None:
        cfg = _load_config()
        assert isinstance(cfg, dict)
        assert "enabled" in cfg
        assert "base_interval_seconds" in cfg
        assert "checks" in cfg

    def test_has_default_checks(self) -> None:
        cfg = _load_config()
        checks = cfg["checks"]
        expected_names = {
            "cato_evidence",
            "agent_health",
            "cve_sla",
            "pending_intake",
            "failing_tests",
            "expiring_isas",
            "memory_maintenance",
        }
        assert expected_names.issubset(set(checks.keys()))


# ===================================================================
# TestCheckRegistry
# ===================================================================
class TestCheckRegistry:
    def test_all_seven_checks_registered(self) -> None:
        assert len(CHECK_REGISTRY) == 7

    def test_all_callables(self) -> None:
        for name, fn in CHECK_REGISTRY.items():
            assert callable(fn), f"{name} is not callable"


# ===================================================================
# TestCheckFunctions
# ===================================================================
class TestCheckFunctions:
    def test_check_cato_evidence_ok(self, tmp_path: Path) -> None:
        db = tmp_path / "icdev.db"
        _init_test_db(db)
        # Insert recent evidence so nothing is overdue
        conn = sqlite3.connect(str(db))
        conn.execute(
            "INSERT INTO cato_evidence (control_id, evidence_type, collected_at) "
            "VALUES ('AC-2', 'scan', ?)",
            (_utcnow(),),
        )
        conn.commit()
        conn.close()
        result = check_cato_evidence(config={}, db_path=db)
        assert result["status"] == "ok"
        assert result["count"] == 0

    def test_check_agent_health_ok(self, tmp_path: Path) -> None:
        db = tmp_path / "icdev.db"
        _init_test_db(db)
        conn = sqlite3.connect(str(db))
        conn.execute(
            "INSERT INTO agents (agent_id, name, last_heartbeat) VALUES ('a1', 'builder', ?)",
            (_utcnow(),),
        )
        conn.commit()
        conn.close()
        result = check_agent_health(config={"stale_threshold_seconds": 600}, db_path=db)
        assert result["status"] == "ok"
        assert result["count"] == 0

    def test_check_agent_health_stale(self, tmp_path: Path) -> None:
        db = tmp_path / "icdev.db"
        _init_test_db(db)
        # Use SQLite datetime format (space separator) for proper comparison
        # with datetime('now', '-N seconds') which also returns space-separated
        conn = sqlite3.connect(str(db))
        conn.execute(
            "INSERT INTO agents (agent_id, name, last_heartbeat) "
            "VALUES ('a1', 'builder', datetime('now', '-7200 seconds'))"
        )
        conn.commit()
        conn.close()
        result = check_agent_health(config={"stale_threshold_seconds": 600}, db_path=db)
        assert result["status"] in ("warning", "critical")
        assert result["count"] >= 1

    def test_check_cve_sla_ok(self, tmp_path: Path) -> None:
        db = tmp_path / "icdev.db"
        _init_test_db(db)
        # No overdue CVEs (recent critical, within 7-day SLA)
        conn = sqlite3.connect(str(db))
        conn.execute(
            "INSERT INTO cve_triage (id, cve_id, component, severity, status, created_at) "
            "VALUES ('c1', 'CVE-2025-0001', 'openssl', 'critical', 'open', ?)",
            (_utcnow(),),
        )
        conn.commit()
        conn.close()
        result = check_cve_sla(config={}, db_path=db)
        assert result["status"] == "ok"
        assert result["count"] == 0

    def test_check_pending_intake_ok(self, tmp_path: Path) -> None:
        db = tmp_path / "icdev.db"
        _init_test_db(db)
        # Active session updated recently
        conn = sqlite3.connect(str(db))
        conn.execute(
            "INSERT INTO intake_sessions (session_id, customer_name, customer_org, "
            "session_status, updated_at) VALUES ('s1', 'Jane', 'DoD', 'active', ?)",
            (_utcnow(),),
        )
        conn.commit()
        conn.close()
        result = check_pending_intake(
            config={"idle_threshold_hours": 48}, db_path=db
        )
        assert result["status"] == "ok"
        assert result["count"] == 0

    def test_check_failing_tests_ok(self, tmp_path: Path) -> None:
        db = tmp_path / "icdev.db"
        _init_test_db(db)
        # No unresolved failures
        result = check_failing_tests(config={"lookback_hours": 24}, db_path=db)
        assert result["status"] == "ok"
        assert result["count"] == 0

    def test_check_expiring_isas_ok(self, tmp_path: Path) -> None:
        db = tmp_path / "icdev.db"
        _init_test_db(db)
        # ISA with a far-future expiry
        future = (datetime.now(timezone.utc) + timedelta(days=365)).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )
        conn = sqlite3.connect(str(db))
        conn.execute(
            "INSERT INTO isa_agreements (id, partner_org, status, expiry_date) "
            "VALUES ('isa1', 'Partner Inc', 'active', ?)",
            (future,),
        )
        conn.commit()
        conn.close()
        result = check_expiring_isas(
            config={"expiry_warning_days": 90}, db_path=db
        )
        assert result["status"] == "ok"
        assert result["count"] == 0

    def test_check_memory_maintenance_ok(self, tmp_path: Path) -> None:
        # check_memory_maintenance looks for memory.db in db_path.parent
        db = tmp_path / "icdev.db"
        mem_db = tmp_path / "memory.db"
        _init_test_db(db)
        _init_memory_db(mem_db)
        result = check_memory_maintenance(config={"stale_days": 90}, db_path=db)
        assert isinstance(result, dict)
        assert "status" in result
        assert "count" in result
        assert "items" in result

    def test_check_returns_expected_keys(self, tmp_path: Path) -> None:
        """Every check function returns dict with status, count, items."""
        db = tmp_path / "icdev.db"
        mem_db = tmp_path / "memory.db"
        _init_test_db(db)
        _init_memory_db(mem_db)
        for name, fn in CHECK_REGISTRY.items():
            result = fn(config={}, db_path=db)
            assert "status" in result, f"{name} missing 'status'"
            assert "count" in result, f"{name} missing 'count'"
            assert "items" in result, f"{name} missing 'items'"


# ===================================================================
# TestRunSingleCheck
# ===================================================================
class TestRunSingleCheck:
    def test_records_in_db(self, tmp_path: Path) -> None:
        db = tmp_path / "icdev.db"
        _init_test_db(db)
        run_single_check("agent_health", config=_load_config(), db_path=db)
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM heartbeat_checks WHERE check_type = 'agent_health'"
        ).fetchall()
        conn.close()
        assert len(rows) >= 1

    def test_returns_result_dict(self, tmp_path: Path) -> None:
        db = tmp_path / "icdev.db"
        _init_test_db(db)
        result = run_single_check("cve_sla", config=_load_config(), db_path=db)
        assert isinstance(result, dict)
        assert "check_type" in result
        assert result["check_type"] == "cve_sla"
        assert "status" in result


# ===================================================================
# TestRunAllChecks
# ===================================================================
class TestRunAllChecks:
    def test_runs_and_returns_summary(self, tmp_path: Path) -> None:
        db = tmp_path / "icdev.db"
        mem_db = tmp_path / "memory.db"
        _init_test_db(db)
        _init_memory_db(mem_db)
        summary = run_all_checks(config=_load_config(), db_path=db)
        assert isinstance(summary, dict)
        assert "checks_run" in summary
        assert "warnings" in summary
        assert "criticals" in summary
        assert "results" in summary

    def test_skips_disabled_checks(self, tmp_path: Path) -> None:
        db = tmp_path / "icdev.db"
        mem_db = tmp_path / "memory.db"
        _init_test_db(db)
        _init_memory_db(mem_db)
        cfg = _load_config()
        # Disable all checks except agent_health
        for name in cfg["checks"]:
            cfg["checks"][name]["enabled"] = False
        cfg["checks"]["agent_health"]["enabled"] = True
        summary = run_all_checks(config=cfg, db_path=db)
        results = summary.get("results", {})
        # Only agent_health should have run
        assert "agent_health" in results
        # Disabled checks should not appear
        for name in ("cato_evidence", "cve_sla", "pending_intake"):
            assert name not in results


# ===================================================================
# TestGetCheckStatus
# ===================================================================
class TestGetCheckStatus:
    def test_empty_returns_empty(self, tmp_path: Path) -> None:
        db = tmp_path / "test.db"
        statuses = get_check_status(db_path=db)
        assert statuses == []

    def test_returns_latest(self, tmp_path: Path) -> None:
        db = tmp_path / "icdev.db"
        _init_test_db(db)
        # Run a check so there is at least one row
        run_single_check("agent_health", config=_load_config(), db_path=db)
        statuses = get_check_status(db_path=db)
        assert isinstance(statuses, list)
        assert len(statuses) >= 1
        entry = statuses[0]
        assert "check_type" in entry
        assert "status" in entry
        assert "last_run" in entry
