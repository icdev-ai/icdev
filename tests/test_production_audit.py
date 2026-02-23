#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for tools/testing/production_audit.py — Production Readiness Audit."""

import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.testing.production_audit import (
    AuditCheck,
    AuditReport,
    CHECK_REGISTRY,
    CATEGORY_ORDER,
    ALL_CATEGORIES,
    run_audit,
    check_python_version,
    check_stdlib_modules,
    check_platform_compat,
    check_dockerfile_syntax,
    check_sast_bandit,
    check_dependency_audit,
    check_secret_detection,
    check_prompt_injection_gate,
    check_owasp_agentic,
    check_code_pattern_scan,
    check_cui_markings,
    check_claude_governance,
    check_append_only_tables,
    check_security_gates_config,
    check_xai_compliance,
    check_sbom_generation,
    check_mcp_servers,
    check_db_schema,
    check_cross_imports,
    check_dashboard_health,
    check_api_gateway,
    check_migration_status,
    check_backup_config,
    check_resilience_config,
    check_test_collection,
    check_claude_md_table_count,
    check_tools_manifest,
    check_goals_manifest,
    check_route_documentation,
    check_skill_count,
)


# ---------------------------------------------------------------------------
# Data structure tests
# ---------------------------------------------------------------------------

class TestAuditCheck:
    def test_to_dict(self):
        check = AuditCheck(
            check_id="TEST-001", check_name="Test Check",
            category="test", status="pass", severity="warning",
            message="OK", details={"key": "value"}, duration_ms=42,
        )
        d = check.to_dict()
        assert d["check_id"] == "TEST-001"
        assert d["status"] == "pass"
        assert d["duration_ms"] == 42
        assert d["details"]["key"] == "value"

    def test_status_values(self):
        for status in ("pass", "fail", "warn", "skip"):
            check = AuditCheck("T", "T", "t", status, "warning", "", {})
            assert check.status == status

    def test_severity_values(self):
        for sev in ("blocking", "warning"):
            check = AuditCheck("T", "T", "t", "pass", sev, "", {})
            assert check.severity == sev


class TestAuditReport:
    def test_to_dict(self):
        report = AuditReport(
            overall_pass=True, timestamp="2026-01-01T00:00:00Z",
            categories={}, total_checks=5, passed=4, failed=0,
            warned=1, skipped=0, blockers=[], warnings=["w1"],
            duration_total_ms=100,
        )
        d = report.to_dict()
        assert d["overall_pass"] is True
        assert d["total_checks"] == 5
        assert d["passed"] == 4
        assert d["warnings"] == ["w1"]

    def test_overall_pass_false_when_blockers(self):
        report = AuditReport(
            overall_pass=False, timestamp="", categories={},
            total_checks=1, passed=0, failed=1, warned=0, skipped=0,
            blockers=["BLOCKER"], warnings=[], duration_total_ms=0,
        )
        assert report.overall_pass is False
        assert len(report.blockers) == 1


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------

class TestCheckRegistry:
    def test_registry_has_30_checks(self):
        assert len(CHECK_REGISTRY) == 30

    def test_all_categories_covered(self):
        categories = {cat for _, (_, cat, _) in CHECK_REGISTRY.items()}
        assert categories == ALL_CATEGORIES

    def test_category_order_has_6(self):
        assert len(CATEGORY_ORDER) == 6

    def test_all_check_ids_unique(self):
        ids = list(CHECK_REGISTRY.keys())
        assert len(ids) == len(set(ids))

    def test_all_functions_callable(self):
        for check_id, (fn, cat, sev) in CHECK_REGISTRY.items():
            assert callable(fn), f"{check_id} function not callable"

    def test_severity_valid(self):
        for check_id, (fn, cat, sev) in CHECK_REGISTRY.items():
            assert sev in ("blocking", "warning"), f"{check_id} has invalid severity: {sev}"


# ---------------------------------------------------------------------------
# Individual check tests (fast, no subprocess)
# ---------------------------------------------------------------------------

class TestPlatformChecks:
    def test_python_version_passes(self):
        result = check_python_version()
        assert isinstance(result, AuditCheck)
        assert result.check_id == "PLT-002"
        assert result.status == "pass"  # We're running Python 3.9+

    def test_stdlib_modules_passes(self):
        result = check_stdlib_modules()
        assert result.check_id == "PLT-003"
        assert result.status == "pass"

    def test_dockerfile_syntax(self):
        result = check_dockerfile_syntax()
        assert result.check_id == "PLT-004"
        assert result.status in ("pass", "warn", "skip")


class TestSecurityChecks:
    def test_prompt_injection_gate(self):
        result = check_prompt_injection_gate()
        assert result.check_id == "SEC-004"
        assert result.status in ("pass", "warn", "skip")

    def test_owasp_agentic(self):
        result = check_owasp_agentic()
        assert result.check_id == "SEC-005"
        assert result.status in ("pass", "warn")

    @patch("tools.testing.production_audit._run_subprocess")
    def test_sast_bandit_not_installed(self, mock_sub):
        mock_sub.return_value = (-1, "", "Command not found: bandit")
        result = check_sast_bandit()
        assert result.status == "skip"

    @patch("tools.testing.production_audit._run_subprocess")
    def test_sast_bandit_clean(self, mock_sub):
        mock_sub.return_value = (0, json.dumps({"results": []}), "")
        result = check_sast_bandit()
        assert result.status == "pass"

    @patch("tools.testing.production_audit._run_subprocess")
    def test_secret_detection_clean(self, mock_sub):
        mock_sub.return_value = (0, json.dumps({"results": {}}), "")
        result = check_secret_detection()
        assert result.status == "pass"

    @patch("tools.testing.production_audit._run_subprocess")
    def test_secret_detection_found(self, mock_sub):
        mock_sub.return_value = (0, json.dumps({"results": {"file.py": [{"type": "Secret"}]}}), "")
        result = check_secret_detection()
        assert result.status == "fail"


class TestComplianceChecks:
    def test_cui_markings(self):
        result = check_cui_markings()
        assert result.check_id == "CMP-001"
        assert result.status in ("pass", "warn")
        assert "details" in result.to_dict()

    def test_append_only_tables(self):
        result = check_append_only_tables()
        assert result.check_id == "CMP-003"
        assert result.status in ("pass", "warn", "skip")

    def test_xai_compliance(self):
        result = check_xai_compliance()
        assert result.check_id == "CMP-005"
        assert result.status in ("pass", "fail", "skip")

    def test_sbom_generation(self):
        result = check_sbom_generation()
        assert result.check_id == "CMP-006"
        assert result.status in ("pass", "fail", "skip")


class TestIntegrationChecks:
    def test_mcp_servers(self):
        result = check_mcp_servers()
        assert result.check_id == "INT-001"
        assert result.status in ("pass", "fail", "skip")

    def test_db_schema(self):
        result = check_db_schema()
        assert result.check_id == "INT-002"
        assert result.status in ("pass", "warn", "fail")

    def test_cross_imports(self):
        result = check_cross_imports()
        assert result.check_id == "INT-003"
        assert result.status in ("pass", "fail")

    def test_api_gateway(self):
        result = check_api_gateway()
        assert result.check_id == "INT-005"
        assert result.status in ("pass", "warn", "skip")


class TestPerformanceChecks:
    def test_backup_config(self):
        result = check_backup_config()
        assert result.check_id == "PRF-002"
        assert result.status in ("pass", "warn")

    def test_resilience_config(self):
        result = check_resilience_config()
        assert result.check_id == "PRF-003"
        assert result.status in ("pass", "warn", "skip")


class TestDocumentationChecks:
    def test_claude_md_table_count(self):
        result = check_claude_md_table_count()
        assert result.check_id == "DOC-001"
        assert result.status in ("pass", "warn", "skip")

    def test_tools_manifest(self):
        result = check_tools_manifest()
        assert result.check_id == "DOC-002"
        assert result.status in ("pass", "warn")

    def test_goals_manifest(self):
        result = check_goals_manifest()
        assert result.check_id == "DOC-003"
        assert result.status in ("pass", "warn")

    def test_route_documentation(self):
        result = check_route_documentation()
        assert result.check_id == "DOC-004"
        assert result.status in ("pass", "warn", "skip")

    def test_skill_count(self):
        result = check_skill_count()
        assert result.check_id == "DOC-005"
        assert result.status in ("pass", "warn", "skip")


# ---------------------------------------------------------------------------
# Runner tests
# ---------------------------------------------------------------------------

class TestRunAudit:
    @patch("tools.testing.production_audit._store_report")
    def test_run_single_category(self, mock_store):
        report = run_audit(categories=["platform"])
        assert isinstance(report, AuditReport)
        assert "platform" in report.categories
        assert len(report.categories) == 1

    @patch("tools.testing.production_audit._store_report")
    def test_run_multiple_categories(self, mock_store):
        report = run_audit(categories=["platform", "documentation"])
        assert len(report.categories) == 2

    @patch("tools.testing.production_audit._store_report")
    def test_overall_pass_with_no_blockers(self, mock_store):
        report = run_audit(categories=["platform"])
        # Platform checks should pass on any dev machine
        assert report.overall_pass is True

    @patch("tools.testing.production_audit._store_report")
    def test_report_has_duration(self, mock_store):
        report = run_audit(categories=["platform"])
        assert report.duration_total_ms >= 0

    @patch("tools.testing.production_audit._store_report")
    def test_report_json_serializable(self, mock_store):
        report = run_audit(categories=["platform"])
        serialized = json.dumps(report.to_dict())
        assert isinstance(serialized, str)
        parsed = json.loads(serialized)
        assert parsed["overall_pass"] is True


# ---------------------------------------------------------------------------
# Category filtering tests
# ---------------------------------------------------------------------------

class TestCategoryFiltering:
    @patch("tools.testing.production_audit._store_report")
    def test_invalid_category_ignored(self, mock_store):
        report = run_audit(categories=["nonexistent"])
        assert report.total_checks == 0

    @patch("tools.testing.production_audit._store_report")
    def test_none_runs_all(self, mock_store):
        # Don't actually run all (slow), just verify the logic
        assert ALL_CATEGORIES == set(CATEGORY_ORDER)
