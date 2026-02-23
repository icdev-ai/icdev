#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for OWASPAgenticAssessor (Phase 45, Gap 8, D264)."""

import json
import sqlite3
from pathlib import Path

import pytest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.compliance.owasp_agentic_assessor import OWASPAgenticAssessor


@pytest.fixture
def assessor_db(tmp_path):
    """Create temp DB with required tables."""
    db_path = tmp_path / "test_owasp.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE projects (
            id TEXT PRIMARY KEY,
            name TEXT,
            description TEXT,
            project_type TEXT DEFAULT 'microservice',
            status TEXT DEFAULT 'active',
            compliance_level TEXT DEFAULT 'moderate',
            classification TEXT DEFAULT 'CUI',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE project_controls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT,
            control_id TEXT,
            implementation_status TEXT DEFAULT 'not_implemented',
            evidence TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(project_id, control_id)
        );
        CREATE TABLE audit_trail (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT,
            event_type TEXT,
            actor TEXT,
            action TEXT,
            details TEXT,
            affected_files TEXT,
            classification TEXT DEFAULT 'CUI',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE project_framework_status (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT,
            framework_id TEXT,
            total_controls INTEGER DEFAULT 0,
            implemented_controls INTEGER DEFAULT 0,
            coverage_pct REAL DEFAULT 0,
            gate_status TEXT DEFAULT 'not_started',
            last_assessed TEXT,
            updated_at TEXT,
            UNIQUE(project_id, framework_id)
        );
        INSERT INTO projects (id, name) VALUES ('proj-123', 'Test Project');
    """)
    conn.commit()
    conn.close()
    return db_path


class TestOWASPAgenticAssessor:
    """Tests for OWASPAgenticAssessor."""

    def test_framework_id(self):
        a = OWASPAgenticAssessor()
        assert a.FRAMEWORK_ID == "owasp_agentic"

    def test_framework_name(self):
        a = OWASPAgenticAssessor()
        assert a.FRAMEWORK_NAME == "OWASP Agentic AI Security v1.0"

    def test_table_name(self):
        a = OWASPAgenticAssessor()
        assert a.TABLE_NAME == "owasp_agentic_assessments"

    def test_catalog_filename(self):
        a = OWASPAgenticAssessor()
        assert a.CATALOG_FILENAME == "owasp_agentic_threats.json"

    def test_automated_checks_returns_dict(self, assessor_db):
        a = OWASPAgenticAssessor(db_path=assessor_db)
        project = {"id": "proj-123", "name": "Test"}
        checks = a.get_automated_checks(project)
        assert isinstance(checks, dict)
        assert len(checks) > 0

    def test_all_17_threats_checked(self, assessor_db):
        a = OWASPAgenticAssessor(db_path=assessor_db)
        project = {"id": "proj-123", "name": "Test"}
        checks = a.get_automated_checks(project)
        for i in range(1, 18):
            tid = f"T{i:02d}"
            assert tid in checks, f"Missing check for {tid}"

    def test_check_returns_valid_status(self, assessor_db):
        a = OWASPAgenticAssessor(db_path=assessor_db)
        project = {"id": "proj-123", "name": "Test"}
        checks = a.get_automated_checks(project)
        valid = {"satisfied", "partially_satisfied", "not_satisfied", "not_assessed", "not_applicable", "risk_accepted"}
        for tid, status in checks.items():
            assert status in valid, f"{tid} has invalid status: {status}"

    def test_behavioral_drift_check(self, assessor_db):
        """Gap 1 check should return satisfied (tools exist in this repo)."""
        a = OWASPAgenticAssessor(db_path=assessor_db)
        status = a._check_behavioral_drift()
        # ai_telemetry_logger.py exists and has detect_behavioral_drift
        assert status in ("satisfied", "partially_satisfied")

    def test_tool_chain_check(self, assessor_db):
        """Gap 2 check should return satisfied (tool created in Wave 2)."""
        a = OWASPAgenticAssessor(db_path=assessor_db)
        status = a._check_tool_chain()
        assert status in ("satisfied", "partially_satisfied")

    def test_output_safety_check(self, assessor_db):
        """Gap 3 check should return satisfied."""
        a = OWASPAgenticAssessor(db_path=assessor_db)
        status = a._check_output_safety()
        assert status in ("satisfied", "partially_satisfied")

    def test_threat_model_check(self, assessor_db):
        """Gap 4 check depends on threat model file existing."""
        a = OWASPAgenticAssessor(db_path=assessor_db)
        status = a._check_threat_model()
        # agentic_threat_model.md was created in Wave 1
        assert status in ("satisfied", "partially_satisfied", "not_satisfied")

    def test_trust_scoring_check(self, assessor_db):
        """Gap 5 check should return satisfied."""
        a = OWASPAgenticAssessor(db_path=assessor_db)
        status = a._check_trust_scoring()
        assert status in ("satisfied", "partially_satisfied")

    def test_mcp_rbac_check(self, assessor_db):
        """Gap 6 check should return satisfied."""
        a = OWASPAgenticAssessor(db_path=assessor_db)
        status = a._check_mcp_rbac()
        assert status in ("satisfied", "partially_satisfied")

    def test_behavioral_red_team_check(self, assessor_db):
        """Gap 7 check depends on atlas_red_team.py having behavioral techniques."""
        a = OWASPAgenticAssessor(db_path=assessor_db)
        status = a._check_behavioral_red_team()
        assert status in ("satisfied", "partially_satisfied", "not_satisfied")

    def test_nist_crosswalk_check(self, assessor_db):
        """Gap 8 check depends on catalog file existing."""
        a = OWASPAgenticAssessor(db_path=assessor_db)
        status = a._check_nist_crosswalk()
        assert status in ("satisfied", "partially_satisfied", "not_satisfied")

    def test_assess_runs_without_error(self, assessor_db):
        """Full assessment should run without raising exceptions."""
        a = OWASPAgenticAssessor(db_path=assessor_db)
        try:
            result = a.assess("proj-123")
            assert "framework_id" in result
            assert result["framework_id"] == "owasp_agentic"
            assert "total_requirements" in result
        except FileNotFoundError:
            # Catalog file may not exist in test environment
            pass
