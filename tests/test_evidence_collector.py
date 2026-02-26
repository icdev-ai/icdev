#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for Universal Compliance Evidence Auto-Collector (Phase 56, D347)."""

import json
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db(tmp_path):
    """Create a temporary icdev DB with representative tables."""
    db_path = tmp_path / "icdev.db"
    conn = sqlite3.connect(str(db_path))
    # Create tables that evidence_collector checks
    conn.execute("""
        CREATE TABLE control_implementations (
            id INTEGER PRIMARY KEY,
            project_id TEXT,
            control_id TEXT,
            status TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE audit_trail (
            id INTEGER PRIMARY KEY,
            project_id TEXT,
            event_type TEXT,
            action TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE stig_results (
            id INTEGER PRIMARY KEY,
            project_id TEXT,
            rule_id TEXT,
            status TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE sbom_records (
            id INTEGER PRIMARY KEY,
            project_id TEXT,
            component_name TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE fedramp_assessments (
            id INTEGER PRIMARY KEY,
            project_id TEXT,
            control_id TEXT,
            status TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE hipaa_assessments (
            id INTEGER PRIMARY KEY,
            project_id TEXT,
            requirement_id TEXT,
            status TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def populated_db(tmp_db):
    """DB with test evidence data."""
    conn = sqlite3.connect(str(tmp_db))
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    old = (datetime.now(timezone.utc) - timedelta(hours=72)).strftime("%Y-%m-%d %H:%M:%S")

    # Fresh evidence
    conn.execute(
        "INSERT INTO control_implementations (project_id, control_id, status, created_at) VALUES (?, ?, ?, ?)",
        ("proj-test", "AC-2", "implemented", now),
    )
    conn.execute(
        "INSERT INTO audit_trail (project_id, event_type, action, created_at) VALUES (?, ?, ?, ?)",
        ("proj-test", "compliance_check", "ssp_generate", now),
    )
    conn.execute(
        "INSERT INTO sbom_records (project_id, component_name, created_at) VALUES (?, ?, ?)",
        ("proj-test", "flask", now),
    )
    # Stale evidence
    conn.execute(
        "INSERT INTO fedramp_assessments (project_id, control_id, status, created_at) VALUES (?, ?, ?, ?)",
        ("proj-test", "AC-2", "satisfied", old),
    )
    # Different project
    conn.execute(
        "INSERT INTO hipaa_assessments (project_id, requirement_id, status, created_at) VALUES (?, ?, ?, ?)",
        ("proj-other", "164.312(a)", "met", now),
    )
    conn.commit()
    conn.close()
    return tmp_db


# ---------------------------------------------------------------------------
# Evidence Collection Tests
# ---------------------------------------------------------------------------

class TestEvidenceCollection:
    """Test evidence collection functionality."""

    def test_collect_empty_db(self, tmp_db):
        from tools.compliance.evidence_collector import collect_evidence
        result = collect_evidence("proj-test", db_path=tmp_db)
        assert result["project_id"] == "proj-test"
        assert "summary" in result
        assert "frameworks" in result
        assert result["summary"]["total_frameworks"] >= 10

    def test_collect_with_data(self, populated_db):
        from tools.compliance.evidence_collector import collect_evidence
        result = collect_evidence("proj-test", db_path=populated_db)
        assert result["summary"]["frameworks_with_evidence"] > 0
        assert result["summary"]["tables_with_data"] > 0
        # NIST 800-53 should have evidence (control_implementations, audit_trail)
        nist = result["frameworks"]["nist_800_53"]
        assert nist["status"] in ("evidence_found", "partial")

    def test_collect_specific_framework(self, populated_db):
        from tools.compliance.evidence_collector import collect_evidence
        result = collect_evidence("proj-test", framework="fedramp", db_path=populated_db)
        assert len(result["frameworks"]) == 1
        assert "fedramp" in result["frameworks"]

    def test_collect_unknown_framework(self, populated_db):
        from tools.compliance.evidence_collector import collect_evidence
        result = collect_evidence("proj-test", framework="nonexistent", db_path=populated_db)
        assert "error" in result

    def test_collect_with_file_scanning(self, populated_db, tmp_path):
        from tools.compliance.evidence_collector import collect_evidence
        # Create some artifact files
        (tmp_path / "ssp_report.json").write_text('{"type": "ssp"}')
        (tmp_path / "sbom_components.json").write_text('{"type": "sbom"}')
        result = collect_evidence("proj-test", project_dir=tmp_path, db_path=populated_db)
        assert result["summary"]["total_file_artifacts"] > 0

    def test_coverage_percentage(self, populated_db):
        from tools.compliance.evidence_collector import collect_evidence
        result = collect_evidence("proj-test", db_path=populated_db)
        assert 0 <= result["summary"]["coverage_pct"] <= 100

    def test_different_project_isolation(self, populated_db):
        from tools.compliance.evidence_collector import collect_evidence
        result = collect_evidence("proj-nonexistent", db_path=populated_db)
        # Should have no evidence for non-existent project
        assert result["summary"]["frameworks_with_evidence"] == 0


# ---------------------------------------------------------------------------
# Freshness Tests
# ---------------------------------------------------------------------------

class TestFreshness:
    """Test evidence freshness checking."""

    def test_freshness_empty_db(self, tmp_db):
        from tools.compliance.evidence_collector import check_freshness
        result = check_freshness("proj-test", db_path=tmp_db)
        assert result["overall_status"] == "unhealthy"
        assert result["summary"]["missing"] > 0

    def test_freshness_with_data(self, populated_db):
        from tools.compliance.evidence_collector import check_freshness
        result = check_freshness("proj-test", max_age_hours=48.0, db_path=populated_db)
        assert result["overall_status"] in ("healthy", "degraded", "unhealthy")
        assert result["summary"]["total"] == len(result["frameworks"])
        # Check that fresh records are detected
        assert result["summary"]["fresh"] > 0

    def test_freshness_stale_detection(self, populated_db):
        from tools.compliance.evidence_collector import check_freshness
        # proj-other only has old hipaa data — use tight max_age to catch it
        # First insert a stale record for proj-stale in a framework with only one table
        conn = sqlite3.connect(str(populated_db))
        old = (datetime.now(timezone.utc) - timedelta(hours=100)).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "INSERT INTO hipaa_assessments (project_id, requirement_id, status, created_at) VALUES (?, ?, ?, ?)",
            ("proj-stale", "164.312(a)", "met", old),
        )
        conn.commit()
        conn.close()
        result = check_freshness("proj-stale", max_age_hours=48.0, db_path=populated_db)
        stale_count = result["summary"]["stale"]
        assert stale_count > 0

    def test_freshness_required_tracking(self, populated_db):
        from tools.compliance.evidence_collector import check_freshness
        result = check_freshness("proj-test", db_path=populated_db)
        # required_stale and required_missing should be tracked
        assert "required_stale" in result["summary"]
        assert "required_missing" in result["summary"]


# ---------------------------------------------------------------------------
# Framework Listing Tests
# ---------------------------------------------------------------------------

class TestFrameworkListing:
    """Test framework listing."""

    def test_list_frameworks(self):
        from tools.compliance.evidence_collector import list_frameworks
        frameworks = list_frameworks()
        assert len(frameworks) >= 10
        names = [f["id"] for f in frameworks]
        assert "nist_800_53" in names
        assert "fedramp" in names
        assert "sbom" in names

    def test_framework_structure(self):
        from tools.compliance.evidence_collector import list_frameworks
        frameworks = list_frameworks()
        for fw in frameworks:
            assert "id" in fw
            assert "description" in fw
            assert "required" in fw
            assert "tables" in fw
            assert isinstance(fw["tables"], list)
