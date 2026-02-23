#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for production_remediate.py — auto-fix audit blockers.

Covers: data structures, registry validation, auto-fix logic,
suggestion/escalation, verification targeting, full pipeline.
"""

import dataclasses
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from unittest import mock

import pytest

# Ensure project root is on path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tools.testing.production_remediate import (
    REMEDIATION_REGISTRY,
    RemediationAction,
    RemediationReport,
    _extract_failed_checks,
    _run_auto_fix,
    _store_remediation,
    run_remediation,
)
from tools.testing.production_audit import AuditCheck, CHECK_REGISTRY


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_audit_report():
    """A minimal audit report dict with some failed checks."""
    return {
        "overall_pass": False,
        "timestamp": "2026-02-22T00:00:00+00:00",
        "categories": {
            "security": {
                "pass": 2,
                "fail": 2,
                "warn": 0,
                "skip": 0,
                "checks": [
                    {
                        "check_id": "SEC-001",
                        "check_name": "SAST (bandit)",
                        "category": "security",
                        "status": "fail",
                        "severity": "blocking",
                        "message": "bandit found 5 issues",
                        "details": {"findings_count": 5},
                        "duration_ms": 100,
                    },
                    {
                        "check_id": "SEC-002",
                        "check_name": "Dependency audit",
                        "category": "security",
                        "status": "fail",
                        "severity": "blocking",
                        "message": "3 vulnerable deps",
                        "details": {"vuln_count": 3},
                        "duration_ms": 200,
                    },
                    {
                        "check_id": "SEC-003",
                        "check_name": "Secret detection",
                        "category": "security",
                        "status": "fail",
                        "severity": "blocking",
                        "message": "2 secrets found",
                        "details": {"secrets_count": 2},
                        "duration_ms": 150,
                    },
                    {
                        "check_id": "SEC-004",
                        "check_name": "Prompt injection gate",
                        "category": "security",
                        "status": "pass",
                        "severity": "blocking",
                        "message": "OK",
                        "details": {},
                        "duration_ms": 50,
                    },
                ],
            },
            "integration": {
                "pass": 1,
                "fail": 1,
                "warn": 0,
                "skip": 0,
                "checks": [
                    {
                        "check_id": "INT-002",
                        "check_name": "DB schema",
                        "category": "integration",
                        "status": "fail",
                        "severity": "blocking",
                        "message": "3 tables missing",
                        "details": {"missing": ["table_a", "table_b", "table_c"]},
                        "duration_ms": 300,
                    },
                    {
                        "check_id": "INT-004",
                        "check_name": "Dashboard health",
                        "category": "integration",
                        "status": "pass",
                        "severity": "warning",
                        "message": "OK",
                        "details": {},
                        "duration_ms": 50,
                    },
                ],
            },
        },
        "total_checks": 6,
        "passed": 3,
        "failed": 3,
        "warned": 0,
        "skipped": 0,
        "blockers": ["SEC-001: bandit", "SEC-002: deps", "SEC-003: secrets"],
        "warnings": [],
        "duration_total_ms": 850,
    }


@pytest.fixture
def temp_db(tmp_path):
    """Create a temporary DB with the remediation_audit_log table."""
    db_path = tmp_path / "test_icdev.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS remediation_audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_audit_id INTEGER,
            check_id TEXT NOT NULL,
            check_name TEXT NOT NULL,
            category TEXT NOT NULL,
            confidence REAL NOT NULL,
            tier TEXT NOT NULL,
            status TEXT NOT NULL,
            fix_strategy TEXT NOT NULL,
            fix_command TEXT,
            message TEXT,
            details TEXT,
            duration_ms INTEGER DEFAULT 0,
            verification_check_id TEXT,
            verification_status TEXT,
            verification_message TEXT,
            dry_run INTEGER DEFAULT 0,
            report_json TEXT,
            classification TEXT DEFAULT 'CUI',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS production_audits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            overall_pass INTEGER NOT NULL DEFAULT 0,
            total_checks INTEGER NOT NULL DEFAULT 0,
            passed INTEGER DEFAULT 0,
            failed INTEGER DEFAULT 0,
            warned INTEGER DEFAULT 0,
            skipped INTEGER DEFAULT 0,
            blockers TEXT,
            warnings TEXT,
            categories_run TEXT,
            report_json TEXT,
            duration_ms INTEGER DEFAULT 0,
            classification TEXT DEFAULT 'CUI',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()
    return db_path


# ---------------------------------------------------------------------------
# Data structure tests
# ---------------------------------------------------------------------------

class TestRemediationAction:
    def test_create(self):
        action = RemediationAction(
            check_id="SEC-002",
            check_name="Dependency audit",
            category="security",
            confidence=0.80,
            tier="auto_fix",
            status="fixed",
            fix_strategy="dep_version_bumps",
            fix_command="pip-audit --fix",
            message="Fixed 3 vulnerabilities",
            details={"fixed_count": 3},
        )
        assert action.check_id == "SEC-002"
        assert action.confidence == 0.80
        assert action.tier == "auto_fix"
        assert action.status == "fixed"
        assert action.duration_ms == 0
        assert action.verification_result is None

    def test_to_dict(self):
        action = RemediationAction(
            check_id="INT-002",
            check_name="DB schema",
            category="integration",
            confidence=0.90,
            tier="auto_fix",
            status="dry_run",
            fix_strategy="rebuild_db_schema",
            fix_command=None,
            message="Would rebuild DB",
            details={},
        )
        d = action.to_dict()
        assert isinstance(d, dict)
        assert d["check_id"] == "INT-002"
        assert d["tier"] == "auto_fix"
        assert d["verification_result"] is None

    def test_with_verification(self):
        action = RemediationAction(
            check_id="SEC-002",
            check_name="Dep audit",
            category="security",
            confidence=0.80,
            tier="auto_fix",
            status="fixed",
            fix_strategy="dep_version_bumps",
            fix_command="cmd",
            message="OK",
            details={},
            verification_result={"status": "pass", "message": "Verification passed"},
        )
        d = action.to_dict()
        assert d["verification_result"]["status"] == "pass"


class TestRemediationReport:
    def test_create(self):
        report = RemediationReport(
            timestamp="2026-02-22T00:00:00+00:00",
            source_audit={"audit_id": 1, "overall_pass": False},
            dry_run=False,
            total_actions=5,
            auto_fixed=2,
            suggested=2,
            escalated=1,
            skipped=0,
            failed=0,
            verified_pass=2,
            verified_fail=0,
            actions=[],
            duration_total_ms=5000,
        )
        assert report.total_actions == 5
        assert report.auto_fixed == 2
        assert report.escalated == 1

    def test_to_dict(self):
        report = RemediationReport(
            timestamp="2026-02-22T00:00:00+00:00",
            source_audit=None,
            dry_run=True,
            total_actions=0,
            auto_fixed=0,
            suggested=0,
            escalated=0,
            skipped=0,
            failed=0,
            verified_pass=0,
            verified_fail=0,
            actions=[],
            duration_total_ms=100,
        )
        d = report.to_dict()
        assert isinstance(d, dict)
        assert d["dry_run"] is True
        assert d["actions"] == []


# ---------------------------------------------------------------------------
# Registry validation
# ---------------------------------------------------------------------------

class TestRemediationRegistry:
    def test_registry_has_entries(self):
        assert len(REMEDIATION_REGISTRY) >= 14

    def test_all_entries_have_required_fields(self):
        required = {"confidence", "tier", "strategy", "command", "suggestion"}
        for check_id, entry in REMEDIATION_REGISTRY.items():
            for field in required:
                assert field in entry, f"{check_id} missing field: {field}"

    def test_confidence_ranges(self):
        for check_id, entry in REMEDIATION_REGISTRY.items():
            c = entry["confidence"]
            assert 0.0 <= c <= 1.0, f"{check_id} confidence {c} out of range"

    def test_tier_matches_confidence(self):
        for check_id, entry in REMEDIATION_REGISTRY.items():
            c = entry["confidence"]
            tier = entry["tier"]
            if tier == "auto_fix":
                assert c >= 0.7, f"{check_id} auto_fix but confidence={c}"
            elif tier == "suggest":
                assert 0.3 <= c <= 0.7, f"{check_id} suggest but confidence={c}"
            elif tier == "escalate":
                assert c < 0.3, f"{check_id} escalate but confidence={c}"

    def test_sec003_always_escalated(self):
        """D297: SEC-003 (secrets) MUST NEVER be auto-fixed."""
        entry = REMEDIATION_REGISTRY.get("SEC-003")
        assert entry is not None
        assert entry["tier"] == "escalate"
        assert entry["confidence"] < 0.3
        assert entry["command"] is None

    def test_auto_fix_entries_have_commands(self):
        for check_id, entry in REMEDIATION_REGISTRY.items():
            if entry["tier"] == "auto_fix":
                assert entry["command"] is not None, f"{check_id} auto_fix but no command"
                assert isinstance(entry["command"], list), f"{check_id} command must be list"

    def test_suggest_entries_have_suggestions(self):
        for check_id, entry in REMEDIATION_REGISTRY.items():
            if entry["tier"] == "suggest":
                assert entry["suggestion"] is not None, f"{check_id} suggest but no suggestion"
                assert len(entry["suggestion"]) > 10, f"{check_id} suggestion too short"

    def test_escalate_entries_have_suggestions(self):
        for check_id, entry in REMEDIATION_REGISTRY.items():
            if entry["tier"] == "escalate":
                assert entry["suggestion"] is not None, f"{check_id} escalate but no suggestion"

    def test_valid_tiers(self):
        valid_tiers = {"auto_fix", "suggest", "escalate"}
        for check_id, entry in REMEDIATION_REGISTRY.items():
            assert entry["tier"] in valid_tiers, f"{check_id} invalid tier: {entry['tier']}"

    def test_all_registered_check_ids_exist_in_audit(self):
        """Every remediation registry entry should map to a real audit check."""
        for check_id in REMEDIATION_REGISTRY:
            assert check_id in CHECK_REGISTRY, f"Remediation {check_id} not in CHECK_REGISTRY"


# ---------------------------------------------------------------------------
# Extract failed checks
# ---------------------------------------------------------------------------

class TestExtractFailedChecks:
    def test_extract_from_report(self, sample_audit_report):
        failed = _extract_failed_checks(sample_audit_report)
        # SEC-001, SEC-002, SEC-003, INT-002 are failed
        failed_ids = {c["check_id"] for c in failed}
        assert "SEC-001" in failed_ids
        assert "SEC-002" in failed_ids
        assert "SEC-003" in failed_ids
        assert "INT-002" in failed_ids
        # SEC-004 passed, should not be in failed
        assert "SEC-004" not in failed_ids

    def test_empty_report(self):
        failed = _extract_failed_checks({"categories": {}})
        assert failed == []

    def test_all_passing(self):
        report = {
            "categories": {
                "security": {
                    "checks": [
                        {"check_id": "SEC-001", "status": "pass", "category": "security"},
                    ]
                }
            }
        }
        failed = _extract_failed_checks(report)
        assert failed == []

    def test_warn_included(self):
        report = {
            "categories": {
                "compliance": {
                    "checks": [
                        {"check_id": "CMP-001", "status": "warn", "category": "compliance"},
                    ]
                }
            }
        }
        failed = _extract_failed_checks(report)
        assert len(failed) == 1
        assert failed[0]["check_id"] == "CMP-001"


# ---------------------------------------------------------------------------
# Auto-fix logic
# ---------------------------------------------------------------------------

class TestRunAutoFix:
    def test_dry_run(self):
        entry = {
            "command": [sys.executable, "-c", "print('hello')"],
            "confidence": 0.80,
            "tier": "auto_fix",
            "strategy": "test",
            "suggestion": None,
        }
        status, message, details = _run_auto_fix("TST-001", entry, dry_run=True)
        assert status == "dry_run"
        assert "Would run" in message

    def test_success(self):
        entry = {
            "command": [sys.executable, "-c", "print('fixed')"],
            "confidence": 0.80,
            "tier": "auto_fix",
            "strategy": "test",
            "suggestion": None,
        }
        status, message, details = _run_auto_fix("TST-001", entry, dry_run=False)
        assert status == "fixed"
        assert "succeeded" in message

    def test_failure(self):
        entry = {
            "command": [sys.executable, "-c", "import sys; sys.exit(1)"],
            "confidence": 0.80,
            "tier": "auto_fix",
            "strategy": "test",
            "suggestion": None,
        }
        status, message, details = _run_auto_fix("TST-001", entry, dry_run=False)
        assert status == "failed"
        assert "failed" in message.lower()

    def test_no_command(self):
        entry = {
            "command": None,
            "confidence": 0.80,
            "tier": "auto_fix",
            "strategy": "test",
            "suggestion": None,
        }
        status, message, details = _run_auto_fix("TST-001", entry, dry_run=False)
        assert status == "failed"
        assert "No command" in message

    def test_command_not_found(self):
        entry = {
            "command": ["nonexistent_command_12345"],
            "confidence": 0.80,
            "tier": "auto_fix",
            "strategy": "test",
            "suggestion": None,
        }
        status, message, details = _run_auto_fix("TST-001", entry, dry_run=False)
        assert status == "failed"


# ---------------------------------------------------------------------------
# DB storage
# ---------------------------------------------------------------------------

class TestStoreRemediation:
    def test_store_action(self, temp_db):
        action = RemediationAction(
            check_id="SEC-002",
            check_name="Dep audit",
            category="security",
            confidence=0.80,
            tier="auto_fix",
            status="fixed",
            fix_strategy="dep_version_bumps",
            fix_command="pip-audit --fix",
            message="Fixed",
            details={"count": 3},
            duration_ms=500,
        )
        with mock.patch("tools.testing.production_remediate._get_db") as mock_db:
            conn = sqlite3.connect(str(temp_db))
            conn.row_factory = sqlite3.Row
            mock_db.return_value = conn
            _store_remediation(action, source_audit_id=1, dry_run=False)

        # Verify stored
        conn2 = sqlite3.connect(str(temp_db))
        row = conn2.execute("SELECT * FROM remediation_audit_log ORDER BY id DESC LIMIT 1").fetchone()
        assert row is not None
        assert row[2] == "SEC-002"  # check_id
        assert row[7] == "fixed"    # status
        assert row[16] == 0         # dry_run
        conn2.close()

    def test_store_dry_run(self, temp_db):
        action = RemediationAction(
            check_id="INT-002",
            check_name="DB schema",
            category="integration",
            confidence=0.90,
            tier="auto_fix",
            status="dry_run",
            fix_strategy="rebuild_db_schema",
            fix_command=None,
            message="Would rebuild",
            details={},
        )
        with mock.patch("tools.testing.production_remediate._get_db") as mock_db:
            conn = sqlite3.connect(str(temp_db))
            conn.row_factory = sqlite3.Row
            mock_db.return_value = conn
            _store_remediation(action, source_audit_id=None, dry_run=True)

        conn2 = sqlite3.connect(str(temp_db))
        row = conn2.execute("SELECT * FROM remediation_audit_log ORDER BY id DESC LIMIT 1").fetchone()
        assert row is not None
        assert row[16] == 1  # dry_run = True
        conn2.close()


# ---------------------------------------------------------------------------
# Full remediation pipeline
# ---------------------------------------------------------------------------

class TestRunRemediation:
    def test_with_mocked_audit(self, sample_audit_report, temp_db):
        """Test full pipeline with mocked audit and mocked subprocess."""
        with mock.patch("tools.testing.production_remediate.run_audit") as mock_audit, \
             mock.patch("tools.testing.production_remediate._get_latest_audit") as mock_latest, \
             mock.patch("tools.testing.production_remediate._run_subprocess") as mock_sub, \
             mock.patch("tools.testing.production_remediate._get_db") as mock_db, \
             mock.patch("tools.testing.production_remediate._verify_fix") as mock_verify:

            # Setup mocks
            from tools.testing.production_audit import AuditReport
            mock_audit.return_value = AuditReport(
                overall_pass=False,
                timestamp="2026-02-22T00:00:00+00:00",
                categories=sample_audit_report["categories"],
                total_checks=6, passed=3, failed=3, warned=0, skipped=0,
                blockers=["SEC-001", "SEC-002", "SEC-003"],
                warnings=[],
                duration_total_ms=850,
            )
            mock_latest.return_value = {**sample_audit_report, "_db_id": 42}
            mock_sub.return_value = (0, "success", "")
            mock_db.return_value = sqlite3.connect(str(temp_db))
            mock_verify.return_value = AuditCheck(
                check_id="SEC-002", check_name="Dep audit",
                category="security", status="pass", severity="blocking",
                message="OK", details={},
            )

            report = run_remediation(auto=True, stream=False)

            assert isinstance(report, RemediationReport)
            assert report.total_actions >= 4  # SEC-001, SEC-002, SEC-003, INT-002
            # SEC-001 = suggest, SEC-002 = auto_fix, SEC-003 = escalate, INT-002 = auto_fix
            assert report.suggested >= 1
            assert report.escalated >= 1
            assert report.auto_fixed >= 1

    def test_dry_run_pipeline(self, sample_audit_report, temp_db):
        """Dry run should preview without executing."""
        with mock.patch("tools.testing.production_remediate.run_audit") as mock_audit, \
             mock.patch("tools.testing.production_remediate._get_latest_audit") as mock_latest, \
             mock.patch("tools.testing.production_remediate._get_db") as mock_db:

            from tools.testing.production_audit import AuditReport
            mock_audit.return_value = AuditReport(
                overall_pass=False,
                timestamp="2026-02-22T00:00:00+00:00",
                categories=sample_audit_report["categories"],
                total_checks=6, passed=3, failed=3, warned=0, skipped=0,
                blockers=["SEC-002"],
                warnings=[],
                duration_total_ms=500,
            )
            mock_latest.return_value = {**sample_audit_report, "_db_id": 10}
            mock_db.return_value = sqlite3.connect(str(temp_db))

            report = run_remediation(dry_run=True, stream=False)

            assert report.dry_run is True
            # Auto-fix entries should have dry_run status
            for a in report.actions:
                if a.get("tier") == "auto_fix":
                    assert a["status"] == "dry_run"

    def test_skip_audit(self, sample_audit_report, temp_db):
        """Test using stored audit instead of re-running."""
        with mock.patch("tools.testing.production_remediate._get_latest_audit") as mock_latest, \
             mock.patch("tools.testing.production_remediate._get_db") as mock_db, \
             mock.patch("tools.testing.production_remediate._run_subprocess") as mock_sub, \
             mock.patch("tools.testing.production_remediate._verify_fix") as mock_verify:

            mock_latest.return_value = {**sample_audit_report, "_db_id": 5}
            mock_db.return_value = sqlite3.connect(str(temp_db))
            mock_sub.return_value = (0, "ok", "")
            mock_verify.return_value = None

            report = run_remediation(skip_audit=True, auto=True, stream=False)

            assert report.total_actions >= 1

    def test_single_check_filter(self, sample_audit_report, temp_db):
        """Test targeting a specific check ID."""
        with mock.patch("tools.testing.production_remediate.run_audit") as mock_audit, \
             mock.patch("tools.testing.production_remediate._get_latest_audit") as mock_latest, \
             mock.patch("tools.testing.production_remediate._get_db") as mock_db, \
             mock.patch("tools.testing.production_remediate._run_subprocess") as mock_sub, \
             mock.patch("tools.testing.production_remediate._verify_fix") as mock_verify:

            from tools.testing.production_audit import AuditReport
            mock_audit.return_value = AuditReport(
                overall_pass=False,
                timestamp="2026-02-22T00:00:00+00:00",
                categories=sample_audit_report["categories"],
                total_checks=6, passed=3, failed=3, warned=0, skipped=0,
                blockers=["SEC-002"],
                warnings=[],
                duration_total_ms=500,
            )
            mock_latest.return_value = {**sample_audit_report, "_db_id": 7}
            mock_db.return_value = sqlite3.connect(str(temp_db))
            mock_sub.return_value = (0, "ok", "")
            mock_verify.return_value = AuditCheck(
                check_id="SEC-002", check_name="Dep audit",
                category="security", status="pass", severity="blocking",
                message="OK", details={},
            )

            report = run_remediation(check_id="SEC-002", auto=True, stream=False)

            assert report.total_actions == 1
            assert report.actions[0]["check_id"] == "SEC-002"

    def test_no_auto_flag_skips_auto_fix(self, sample_audit_report, temp_db):
        """Without --auto, auto-fix items should be skipped."""
        with mock.patch("tools.testing.production_remediate.run_audit") as mock_audit, \
             mock.patch("tools.testing.production_remediate._get_latest_audit") as mock_latest, \
             mock.patch("tools.testing.production_remediate._get_db") as mock_db:

            from tools.testing.production_audit import AuditReport
            mock_audit.return_value = AuditReport(
                overall_pass=False,
                timestamp="2026-02-22T00:00:00+00:00",
                categories=sample_audit_report["categories"],
                total_checks=6, passed=3, failed=3, warned=0, skipped=0,
                blockers=[],
                warnings=[],
                duration_total_ms=500,
            )
            mock_latest.return_value = {**sample_audit_report, "_db_id": 8}
            mock_db.return_value = sqlite3.connect(str(temp_db))

            report = run_remediation(auto=False, stream=False)

            # Auto-fix items should be skipped (not executed)
            for a in report.actions:
                if a.get("tier") == "auto_fix":
                    assert a["status"] == "skipped"

    def test_category_filter(self, sample_audit_report, temp_db):
        """Test filtering by category."""
        with mock.patch("tools.testing.production_remediate.run_audit") as mock_audit, \
             mock.patch("tools.testing.production_remediate._get_latest_audit") as mock_latest, \
             mock.patch("tools.testing.production_remediate._get_db") as mock_db, \
             mock.patch("tools.testing.production_remediate._run_subprocess") as mock_sub, \
             mock.patch("tools.testing.production_remediate._verify_fix") as mock_verify:

            from tools.testing.production_audit import AuditReport
            mock_audit.return_value = AuditReport(
                overall_pass=False,
                timestamp="2026-02-22T00:00:00+00:00",
                categories=sample_audit_report["categories"],
                total_checks=6, passed=3, failed=3, warned=0, skipped=0,
                blockers=[],
                warnings=[],
                duration_total_ms=500,
            )
            mock_latest.return_value = {**sample_audit_report, "_db_id": 9}
            mock_db.return_value = sqlite3.connect(str(temp_db))
            mock_sub.return_value = (0, "ok", "")
            mock_verify.return_value = None

            report = run_remediation(category="integration", auto=True, stream=False)

            # Should only have integration checks
            for a in report.actions:
                assert a["category"] == "integration"


# ---------------------------------------------------------------------------
# Verification targeting (D298)
# ---------------------------------------------------------------------------

class TestVerificationTargeting:
    def test_all_registry_checks_in_audit(self):
        """Every remediation check_id should exist in the audit CHECK_REGISTRY."""
        for check_id in REMEDIATION_REGISTRY:
            assert check_id in CHECK_REGISTRY, f"{check_id} not in CHECK_REGISTRY"

    def test_auto_fix_checks_are_verifiable(self):
        """All auto-fix entries should have corresponding audit checks for verification."""
        for check_id, entry in REMEDIATION_REGISTRY.items():
            if entry["tier"] == "auto_fix":
                assert check_id in CHECK_REGISTRY, f"Auto-fix {check_id} not verifiable"


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

class TestFormatHuman:
    def test_format_basic(self):
        from tools.testing.production_remediate import _format_human
        report = RemediationReport(
            timestamp="2026-02-22T00:00:00+00:00",
            source_audit=None,
            dry_run=False,
            total_actions=3,
            auto_fixed=1,
            suggested=1,
            escalated=1,
            skipped=0,
            failed=0,
            verified_pass=1,
            verified_fail=0,
            actions=[
                {
                    "check_id": "SEC-002", "check_name": "Dep audit",
                    "tier": "auto_fix", "status": "fixed", "message": "Fixed 3 deps",
                    "verification_result": {"status": "pass", "message": "OK"},
                },
                {
                    "check_id": "SEC-001", "check_name": "SAST",
                    "tier": "suggest", "status": "suggested", "message": "Run bandit",
                    "verification_result": None,
                },
                {
                    "check_id": "SEC-003", "check_name": "Secrets",
                    "tier": "escalate", "status": "escalated", "message": "Rotate secrets",
                    "verification_result": None,
                },
            ],
            duration_total_ms=5000,
        )
        output = _format_human(report)
        assert "Production Remediation Report" in output
        assert "Auto-fixed:     1" in output
        assert "Suggested:      1" in output
        assert "Escalated:      1" in output
        assert "BLOCKERS REMAIN" in output

    def test_format_dry_run(self):
        from tools.testing.production_remediate import _format_human
        report = RemediationReport(
            timestamp="2026-02-22T00:00:00+00:00",
            source_audit=None,
            dry_run=True,
            total_actions=0,
            auto_fixed=0,
            suggested=0,
            escalated=0,
            skipped=0,
            failed=0,
            verified_pass=0,
            verified_fail=0,
            actions=[],
            duration_total_ms=100,
        )
        output = _format_human(report)
        assert "DRY RUN" in output

    def test_format_all_clear(self):
        from tools.testing.production_remediate import _format_human
        report = RemediationReport(
            timestamp="2026-02-22T00:00:00+00:00",
            source_audit=None,
            dry_run=False,
            total_actions=1,
            auto_fixed=1,
            suggested=0,
            escalated=0,
            skipped=0,
            failed=0,
            verified_pass=1,
            verified_fail=0,
            actions=[
                {
                    "check_id": "INT-002", "check_name": "DB schema",
                    "tier": "auto_fix", "status": "fixed", "message": "Rebuilt",
                    "verification_result": {"status": "pass", "message": "OK"},
                },
            ],
            duration_total_ms=2000,
        )
        output = _format_human(report)
        assert "ALL CLEAR" in output
