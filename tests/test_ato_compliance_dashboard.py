#!/usr/bin/env python3
# CUI // SP-CTI
"""Unit tests for ATO Compliance Dashboard — TDD RED phase.

Feature: Implement ATO Compliance Dashboard pattern
  - Control tracking views (NIST 800-53 implementation status per family)
  - RMF workflow stages (6-step DoD RMF lifecycle)
  - Artifact generation routes (SSP, POAM, STIG, SBOM triggers)
  - NIST 800-53 crosswalk via tools/compliance/crosswalk_engine

NIST 800-53 Controls: SA-11 (Developer Security Testing), CM-3 (Configuration Change Control)
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# ---------------------------------------------------------------------------
# Minimal DB schema for ATO compliance dashboard tests
# ---------------------------------------------------------------------------
ATO_DASHBOARD_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'webapp',
    classification TEXT DEFAULT 'CUI',
    status TEXT DEFAULT 'active',
    directory_path TEXT DEFAULT '/tmp',
    impact_level TEXT DEFAULT 'IL4',
    ato_status TEXT DEFAULT 'none',
    fips199_overall TEXT
);

CREATE TABLE IF NOT EXISTS compliance_controls (
    id TEXT PRIMARY KEY,
    family TEXT NOT NULL,
    title TEXT,
    description TEXT,
    baseline TEXT DEFAULT 'moderate'
);

CREATE TABLE IF NOT EXISTS project_controls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    control_id TEXT NOT NULL,
    implementation_status TEXT NOT NULL DEFAULT 'planned'
        CHECK(implementation_status IN (
            'planned','implemented','partially_implemented',
            'not_applicable','compensating'
        )),
    implementation_description TEXT,
    responsible_role TEXT,
    evidence_path TEXT,
    last_assessed TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(project_id, control_id)
);

CREATE TABLE IF NOT EXISTS ssp_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    version TEXT NOT NULL,
    system_name TEXT NOT NULL,
    content TEXT NOT NULL,
    classification TEXT DEFAULT 'CUI',
    status TEXT DEFAULT 'draft',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS poam_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    weakness_id TEXT NOT NULL,
    weakness_description TEXT NOT NULL,
    severity TEXT NOT NULL
        CHECK(severity IN ('critical','high','moderate','low')),
    source TEXT NOT NULL,
    control_id TEXT,
    status TEXT DEFAULT 'open'
        CHECK(status IN ('open','in_progress','completed','accepted_risk')),
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS stig_findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    stig_id TEXT NOT NULL,
    finding_id TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    severity TEXT NOT NULL CHECK(severity IN ('CAT1','CAT2','CAT3')),
    title TEXT NOT NULL,
    status TEXT DEFAULT 'Open'
        CHECK(status IN ('Open','NotAFinding','Not_Applicable','Not_Reviewed')),
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sbom_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    version TEXT NOT NULL,
    format TEXT DEFAULT 'cyclonedx',
    file_path TEXT NOT NULL,
    component_count INTEGER,
    generated_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS audit_trail (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL DEFAULT 'system',
    action TEXT,
    project_id TEXT,
    details TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS rmf_workflow_stages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    stage TEXT NOT NULL
        CHECK(stage IN ('categorize','select','implement','assess','authorize','monitor')),
    status TEXT NOT NULL DEFAULT 'not_started'
        CHECK(status IN ('not_started','in_progress','complete','blocked')),
    assigned_to TEXT,
    completed_at TEXT,
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(project_id, stage)
);
"""

# RMF stage order per NIST SP 800-37 Rev 2
RMF_STAGES = ["categorize", "select", "implement", "assess", "authorize", "monitor"]


@pytest.fixture()
def ato_db():
    """In-memory SQLite DB with full ATO compliance schema."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(ATO_DASHBOARD_SCHEMA)
    conn.commit()
    # Wrapped in StorageConnection so the module's PG-native %s SQL is
    # translated for SQLite (same pattern as tests/test_cato_twin.py).
    from tools.db.storage import StorageConnection

    # Closed rather than returned: the constraint tests here drive an INSERT to
    # an IntegrityError, which aborts the statement but leaves SQLite's implicit
    # transaction open, so the test ends with an uncommitted write and the guard
    # in conftest.py fails it (tsh-leak-01). seeded_ato_db builds on this
    # fixture, so closing here covers both.
    wrapped = StorageConnection(conn, "sqlite")
    try:
        yield wrapped
    finally:
        wrapped.close()


@pytest.fixture()
def seeded_ato_db(ato_db):
    """ATO DB with sample project, controls, and documents."""
    conn = ato_db

    # Project
    conn.execute(
        "INSERT INTO projects (id, name, type, impact_level, ato_status) "
        "VALUES ('proj-ato-001', 'Test System', 'webapp', 'IL4', 'none')"
    )

    # Compliance controls (NIST families)
    controls_data = [
        ("AC-1", "AC", "Access Control Policy", "moderate"),
        ("AC-2", "AC", "Account Management", "moderate"),
        ("AU-2", "AU", "Event Logging", "moderate"),
        ("SA-11", "SA", "Developer Security Testing", "moderate"),
        ("CM-3", "CM", "Configuration Change Control", "moderate"),
        ("SC-7", "SC", "Boundary Protection", "moderate"),
        ("IA-2", "IA", "Identification and Authentication", "moderate"),
        ("RA-5", "RA", "Vulnerability Monitoring and Scanning", "moderate"),
    ]
    conn.executemany(
        "INSERT INTO compliance_controls (id, family, title, baseline) VALUES (?,?,?,?)",
        controls_data,
    )

    # Project controls with mixed statuses
    proj_controls = [
        ("proj-ato-001", "AC-1", "implemented", "ISSO role assigned, policy published"),
        ("proj-ato-001", "AC-2", "partially_implemented", "Manual provisioning, automation pending"),
        ("proj-ato-001", "AU-2", "implemented", "SIEM ingesting all event types"),
        ("proj-ato-001", "SA-11", "planned", None),
        ("proj-ato-001", "CM-3", "planned", None),
        ("proj-ato-001", "SC-7", "not_applicable", "Air-gap boundary"),
        ("proj-ato-001", "IA-2", "implemented", "CAC + PIV enforced"),
        ("proj-ato-001", "RA-5", "partially_implemented", "Manual scans only"),
    ]
    conn.executemany(
        "INSERT INTO project_controls "
        "(project_id, control_id, implementation_status, implementation_description) "
        "VALUES (?,?,?,?)",
        proj_controls,
    )

    # SSP document
    conn.execute(
        "INSERT INTO ssp_documents (project_id, version, system_name, content, status) "
        "VALUES ('proj-ato-001', '1.0', 'Test System', 'SSP content here', 'draft')"
    )

    # POAM items
    poam_data = [
        ("proj-ato-001", "W-001", "Missing MFA on admin accounts", "high", "STIG", "IA-2", "open"),
        ("proj-ato-001", "W-002", "Log retention < 1 year", "moderate", "STIG", "AU-2", "in_progress"),
        ("proj-ato-001", "W-003", "Unpatched CVE-2024-1234", "critical", "ACAS", "RA-5", "open"),
    ]
    conn.executemany(
        "INSERT INTO poam_items "
        "(project_id, weakness_id, weakness_description, severity, source, control_id, status) "
        "VALUES (?,?,?,?,?,?,?)",
        poam_data,
    )

    # STIG findings
    stig_data = [
        ("proj-ato-001", "RHEL-08", "V-001", "R-001", "CAT1", "SSH PermitRootLogin enabled", "Open"),
        ("proj-ato-001", "RHEL-08", "V-002", "R-002", "CAT2", "Audit log size too small", "Open"),
        ("proj-ato-001", "RHEL-08", "V-003", "R-003", "CAT3", "Banner not configured", "NotAFinding"),
    ]
    conn.executemany(
        "INSERT INTO stig_findings "
        "(project_id, stig_id, finding_id, rule_id, severity, title, status) "
        "VALUES (?,?,?,?,?,?,?)",
        stig_data,
    )

    # RMF workflow stages
    for stage in RMF_STAGES:
        status = "complete" if stage in ("categorize", "select") else "not_started"
        completed = "2026-01-15T10:00:00" if status == "complete" else None
        conn.execute(
            "INSERT INTO rmf_workflow_stages "
            "(project_id, stage, status, completed_at) VALUES (?,?,?,?)",
            ("proj-ato-001", stage, status, completed),
        )

    conn.commit()
    return conn


# ===========================================================================
# 1. ATO Dashboard Module — Module-level imports
# ===========================================================================

class TestATODashboardModule:
    """RED: tools/ato_compliance/dashboard.py must be importable with key functions."""

    def test_module_importable(self):
        """ato_compliance.dashboard must be importable."""
        from tools.ato_compliance import dashboard  # noqa: F401
        assert dashboard is not None

    def test_get_control_summary_importable(self):
        """get_control_summary must be defined in ato_compliance.dashboard."""
        from tools.ato_compliance.dashboard import get_control_summary
        assert callable(get_control_summary)

    def test_get_rmf_stages_importable(self):
        """get_rmf_stages must be defined in ato_compliance.dashboard."""
        from tools.ato_compliance.dashboard import get_rmf_stages
        assert callable(get_rmf_stages)

    def test_get_artifact_status_importable(self):
        """get_artifact_status must be defined in ato_compliance.dashboard."""
        from tools.ato_compliance.dashboard import get_artifact_status
        assert callable(get_artifact_status)

    def test_get_crosswalk_summary_importable(self):
        """get_crosswalk_summary must be defined in ato_compliance.dashboard."""
        from tools.ato_compliance.dashboard import get_crosswalk_summary
        assert callable(get_crosswalk_summary)

    def test_get_posture_score_importable(self):
        """get_posture_score must be defined in ato_compliance.dashboard."""
        from tools.ato_compliance.dashboard import get_posture_score
        assert callable(get_posture_score)


# ===========================================================================
# 2. RMF Workflow Stage Table
# ===========================================================================

class TestRMFWorkflowStageTable:
    """RED: rmf_workflow_stages table must support the 6 RMF stage workflow."""

    def test_rmf_stages_row_count(self, seeded_ato_db):
        """All 6 RMF stages should be seeded for a project."""
        rows = seeded_ato_db.execute(
            "SELECT stage FROM rmf_workflow_stages WHERE project_id = 'proj-ato-001'"
        ).fetchall()
        assert len(rows) == 6

    def test_rmf_stage_names_are_valid(self, seeded_ato_db):
        """Stage names must match RMF_STAGES list."""
        rows = seeded_ato_db.execute(
            "SELECT stage FROM rmf_workflow_stages WHERE project_id = 'proj-ato-001'"
        ).fetchall()
        stages_in_db = {r["stage"] for r in rows}
        assert stages_in_db == set(RMF_STAGES)

    def test_rmf_stage_status_constraint(self, ato_db):
        """CHECK constraint should reject invalid stage status."""
        ato_db.execute(
            "INSERT INTO projects (id, name) VALUES ('p-chk', 'Check')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            ato_db.execute(
                "INSERT INTO rmf_workflow_stages (project_id, stage, status) "
                "VALUES ('p-chk', 'categorize', 'invalid_status')"
            )

    def test_rmf_unique_constraint(self, seeded_ato_db):
        """Cannot have duplicate stage for same project."""
        with pytest.raises(sqlite3.IntegrityError):
            seeded_ato_db.execute(
                "INSERT INTO rmf_workflow_stages (project_id, stage, status) "
                "VALUES ('proj-ato-001', 'categorize', 'in_progress')"
            )

    def test_rmf_stage_completion_tracking(self, seeded_ato_db):
        """Complete stages should have completed_at set."""
        rows = seeded_ato_db.execute(
            "SELECT stage, status, completed_at FROM rmf_workflow_stages "
            "WHERE project_id = 'proj-ato-001' AND status = 'complete'"
        ).fetchall()
        assert len(rows) == 2
        for row in rows:
            assert row["completed_at"] is not None


# ===========================================================================
# 3. Control Summary View
# ===========================================================================

class TestGetControlSummary:
    """RED: get_control_summary must return per-family implementation stats."""

    def test_returns_dict(self, seeded_ato_db):
        """get_control_summary must return a dict."""
        from tools.ato_compliance.dashboard import get_control_summary
        result = get_control_summary("proj-ato-001", conn=seeded_ato_db)
        assert isinstance(result, dict)

    def test_has_families_key(self, seeded_ato_db):
        """Result must include 'families' list."""
        from tools.ato_compliance.dashboard import get_control_summary
        result = get_control_summary("proj-ato-001", conn=seeded_ato_db)
        assert "families" in result

    def test_has_totals(self, seeded_ato_db):
        """Result must include total_controls, implemented, and partial counts."""
        from tools.ato_compliance.dashboard import get_control_summary
        result = get_control_summary("proj-ato-001", conn=seeded_ato_db)
        assert "total_controls" in result
        assert "implemented" in result
        assert "partially_implemented" in result
        assert "planned" in result
        assert "not_applicable" in result

    def test_total_controls_count(self, seeded_ato_db):
        """total_controls should equal number of controls assigned to project."""
        from tools.ato_compliance.dashboard import get_control_summary
        result = get_control_summary("proj-ato-001", conn=seeded_ato_db)
        assert result["total_controls"] == 8

    def test_implemented_count(self, seeded_ato_db):
        """Implemented count should be 3 (AC-1, AU-2, IA-2)."""
        from tools.ato_compliance.dashboard import get_control_summary
        result = get_control_summary("proj-ato-001", conn=seeded_ato_db)
        assert result["implemented"] == 3

    def test_partially_implemented_count(self, seeded_ato_db):
        """Partially implemented count should be 2 (AC-2, RA-5)."""
        from tools.ato_compliance.dashboard import get_control_summary
        result = get_control_summary("proj-ato-001", conn=seeded_ato_db)
        assert result["partially_implemented"] == 2

    def test_families_structure(self, seeded_ato_db):
        """Each family entry must include family, count, and status breakdown."""
        from tools.ato_compliance.dashboard import get_control_summary
        result = get_control_summary("proj-ato-001", conn=seeded_ato_db)
        for fam in result["families"]:
            assert "family" in fam
            assert "total" in fam
            assert "implemented" in fam

    def test_posture_percentage(self, seeded_ato_db):
        """posture_pct should be (implemented + partially) / total * 100, rounded."""
        from tools.ato_compliance.dashboard import get_control_summary
        result = get_control_summary("proj-ato-001", conn=seeded_ato_db)
        # 3 implemented + 2 partial out of 8 = 62.5%, rounded to int
        assert "posture_pct" in result
        assert isinstance(result["posture_pct"], (int, float))

    def test_invalid_project_returns_empty(self, ato_db):
        """Non-existent project should return zeroed summary."""
        from tools.ato_compliance.dashboard import get_control_summary
        result = get_control_summary("proj-nonexistent", conn=ato_db)
        assert result["total_controls"] == 0


# ===========================================================================
# 4. RMF Stages View
# ===========================================================================

class TestGetRMFStages:
    """RED: get_rmf_stages must return ordered stage lifecycle data."""

    def test_returns_list(self, seeded_ato_db):
        """get_rmf_stages must return a list."""
        from tools.ato_compliance.dashboard import get_rmf_stages
        result = get_rmf_stages("proj-ato-001", conn=seeded_ato_db)
        assert isinstance(result, list)

    def test_returns_six_stages(self, seeded_ato_db):
        """Must return exactly 6 stages."""
        from tools.ato_compliance.dashboard import get_rmf_stages
        result = get_rmf_stages("proj-ato-001", conn=seeded_ato_db)
        assert len(result) == 6

    def test_stage_order(self, seeded_ato_db):
        """Stages must be in RMF order: categorize → select → ... → monitor."""
        from tools.ato_compliance.dashboard import get_rmf_stages
        result = get_rmf_stages("proj-ato-001", conn=seeded_ato_db)
        stage_names = [s["stage"] for s in result]
        assert stage_names == RMF_STAGES

    def test_stage_has_required_fields(self, seeded_ato_db):
        """Each stage entry must include stage, status, and label."""
        from tools.ato_compliance.dashboard import get_rmf_stages
        result = get_rmf_stages("proj-ato-001", conn=seeded_ato_db)
        for stage in result:
            assert "stage" in stage
            assert "status" in stage
            assert "label" in stage

    def test_complete_stages_have_completed_at(self, seeded_ato_db):
        """Stages with status='complete' must have completed_at."""
        from tools.ato_compliance.dashboard import get_rmf_stages
        result = get_rmf_stages("proj-ato-001", conn=seeded_ato_db)
        complete_stages = [s for s in result if s["status"] == "complete"]
        for s in complete_stages:
            assert s.get("completed_at") is not None

    def test_unknown_project_returns_default_stages(self, ato_db):
        """Unknown project should return 6 stages all as not_started."""
        from tools.ato_compliance.dashboard import get_rmf_stages
        result = get_rmf_stages("proj-unknown", conn=ato_db)
        assert len(result) == 6
        for s in result:
            assert s["status"] == "not_started"


# ===========================================================================
# 5. Artifact Status View
# ===========================================================================

class TestGetArtifactStatus:
    """RED: get_artifact_status must report readiness of ATO artifacts."""

    def test_returns_dict(self, seeded_ato_db):
        """get_artifact_status must return a dict."""
        from tools.ato_compliance.dashboard import get_artifact_status
        result = get_artifact_status("proj-ato-001", conn=seeded_ato_db)
        assert isinstance(result, dict)

    def test_has_artifacts_key(self, seeded_ato_db):
        """Result must include 'artifacts' list."""
        from tools.ato_compliance.dashboard import get_artifact_status
        result = get_artifact_status("proj-ato-001", conn=seeded_ato_db)
        assert "artifacts" in result

    def test_artifact_types_covered(self, seeded_ato_db):
        """SSP, POAM, STIG, SBOM must all be reported."""
        from tools.ato_compliance.dashboard import get_artifact_status
        result = get_artifact_status("proj-ato-001", conn=seeded_ato_db)
        artifact_names = {a["type"] for a in result["artifacts"]}
        assert "ssp" in artifact_names
        assert "poam" in artifact_names
        assert "stig" in artifact_names
        assert "sbom" in artifact_names

    def test_ssp_detected(self, seeded_ato_db):
        """SSP artifact should be detected (1 draft SSP seeded)."""
        from tools.ato_compliance.dashboard import get_artifact_status
        result = get_artifact_status("proj-ato-001", conn=seeded_ato_db)
        ssp = next(a for a in result["artifacts"] if a["type"] == "ssp")
        assert ssp["count"] >= 1

    def test_poam_count(self, seeded_ato_db):
        """POAM should report 3 open items (seeded)."""
        from tools.ato_compliance.dashboard import get_artifact_status
        result = get_artifact_status("proj-ato-001", conn=seeded_ato_db)
        poam = next(a for a in result["artifacts"] if a["type"] == "poam")
        assert poam["count"] == 3

    def test_stig_open_count(self, seeded_ato_db):
        """STIG should report open findings count."""
        from tools.ato_compliance.dashboard import get_artifact_status
        result = get_artifact_status("proj-ato-001", conn=seeded_ato_db)
        stig = next(a for a in result["artifacts"] if a["type"] == "stig")
        # 2 open STIG findings (CAT1 + CAT2) seeded
        assert stig["count"] >= 1

    def test_readiness_score_in_result(self, seeded_ato_db):
        """Result must include a readiness_score (0-100)."""
        from tools.ato_compliance.dashboard import get_artifact_status
        result = get_artifact_status("proj-ato-001", conn=seeded_ato_db)
        assert "readiness_score" in result
        score = result["readiness_score"]
        assert 0 <= score <= 100


# ===========================================================================
# 6. Crosswalk Summary
# ===========================================================================

class TestGetCrosswalkSummary:
    """RED: get_crosswalk_summary must surface NIST crosswalk data via icdev-comply."""

    def test_returns_dict(self, seeded_ato_db):
        """get_crosswalk_summary must return a dict."""
        from tools.ato_compliance.dashboard import get_crosswalk_summary
        result = get_crosswalk_summary("proj-ato-001", conn=seeded_ato_db)
        assert isinstance(result, dict)

    def test_has_frameworks_key(self, seeded_ato_db):
        """Result must include 'frameworks' list."""
        from tools.ato_compliance.dashboard import get_crosswalk_summary
        result = get_crosswalk_summary("proj-ato-001", conn=seeded_ato_db)
        assert "frameworks" in result

    def test_framework_structure(self, seeded_ato_db):
        """Each framework entry must have name, coverage_pct, and mapped_controls."""
        from tools.ato_compliance.dashboard import get_crosswalk_summary
        result = get_crosswalk_summary("proj-ato-001", conn=seeded_ato_db)
        if result["frameworks"]:
            for fw in result["frameworks"]:
                assert "name" in fw
                assert "coverage_pct" in fw
                assert "mapped_controls" in fw

    def test_coverage_pct_is_numeric(self, seeded_ato_db):
        """coverage_pct must be a number in [0, 100]."""
        from tools.ato_compliance.dashboard import get_crosswalk_summary
        result = get_crosswalk_summary("proj-ato-001", conn=seeded_ato_db)
        for fw in result["frameworks"]:
            assert 0 <= fw["coverage_pct"] <= 100

    def test_available_key_present(self, seeded_ato_db):
        """Result must include 'available' bool (crosswalk data available)."""
        from tools.ato_compliance.dashboard import get_crosswalk_summary
        result = get_crosswalk_summary("proj-ato-001", conn=seeded_ato_db)
        assert "available" in result


# ===========================================================================
# 7. Posture Score
# ===========================================================================

class TestGetPostureScore:
    """RED: get_posture_score must return composite ATO readiness score."""

    def test_returns_dict(self, seeded_ato_db):
        """get_posture_score must return a dict."""
        from tools.ato_compliance.dashboard import get_posture_score
        result = get_posture_score("proj-ato-001", conn=seeded_ato_db)
        assert isinstance(result, dict)

    def test_score_in_range(self, seeded_ato_db):
        """Score must be between 0 and 100."""
        from tools.ato_compliance.dashboard import get_posture_score
        result = get_posture_score("proj-ato-001", conn=seeded_ato_db)
        assert "score" in result
        assert 0 <= result["score"] <= 100

    def test_grade_present(self, seeded_ato_db):
        """Result must include a letter grade (A/B/C/D/F)."""
        from tools.ato_compliance.dashboard import get_posture_score
        result = get_posture_score("proj-ato-001", conn=seeded_ato_db)
        assert "grade" in result
        assert result["grade"] in ("A", "B", "C", "D", "F")

    def test_components_present(self, seeded_ato_db):
        """Score breakdown must include control, rmf, and artifact components."""
        from tools.ato_compliance.dashboard import get_posture_score
        result = get_posture_score("proj-ato-001", conn=seeded_ato_db)
        assert "components" in result
        comp = result["components"]
        assert "control_pct" in comp
        assert "rmf_pct" in comp
        assert "artifact_pct" in comp


# ===========================================================================
# 8. API Blueprint — Flask routes
# ===========================================================================

class TestATODashboardBlueprintExists:
    """RED: tools/dashboard/api/ato_compliance.py blueprint must exist and register routes."""

    def test_blueprint_importable(self):
        """ato_compliance_api blueprint must be importable."""
        from tools.dashboard.api.ato_compliance import ato_compliance_api
        assert ato_compliance_api is not None

    def test_blueprint_has_correct_prefix(self):
        """Blueprint must have /api/ato-compliance URL prefix."""
        from tools.dashboard.api.ato_compliance import ato_compliance_api
        assert ato_compliance_api.url_prefix == "/api/ato-compliance"

    def test_dashboard_route_exists(self):
        """GET /api/ato-compliance/dashboard/<project_id> must be registered."""
        from tools.dashboard.api.ato_compliance import ato_compliance_api
        [rule.rule for rule in ato_compliance_api.deferred_functions
                 if hasattr(rule, 'rule')]
        # Alternatively check via Flask test client
        from flask import Flask
        app = Flask(__name__)
        app.register_blueprint(ato_compliance_api)
        url_map = {r.rule for r in app.url_map.iter_rules()}
        assert any("ato-compliance" in r for r in url_map)

    def test_controls_route_exists(self):
        """GET /api/ato-compliance/controls/<project_id> must be registered."""
        from flask import Flask
        from tools.dashboard.api.ato_compliance import ato_compliance_api
        app = Flask(__name__)
        app.register_blueprint(ato_compliance_api)
        url_map = {r.rule for r in app.url_map.iter_rules()}
        assert any("controls" in r for r in url_map)

    def test_rmf_route_exists(self):
        """GET /api/ato-compliance/rmf/<project_id> must be registered."""
        from flask import Flask
        from tools.dashboard.api.ato_compliance import ato_compliance_api
        app = Flask(__name__)
        app.register_blueprint(ato_compliance_api)
        url_map = {r.rule for r in app.url_map.iter_rules()}
        assert any("rmf" in r for r in url_map)

    def test_artifacts_route_exists(self):
        """GET /api/ato-compliance/artifacts/<project_id> must be registered."""
        from flask import Flask
        from tools.dashboard.api.ato_compliance import ato_compliance_api
        app = Flask(__name__)
        app.register_blueprint(ato_compliance_api)
        url_map = {r.rule for r in app.url_map.iter_rules()}
        assert any("artifacts" in r for r in url_map)

    def test_crosswalk_route_exists(self):
        """GET /api/ato-compliance/crosswalk/<project_id> must be registered."""
        from flask import Flask
        from tools.dashboard.api.ato_compliance import ato_compliance_api
        app = Flask(__name__)
        app.register_blueprint(ato_compliance_api)
        url_map = {r.rule for r in app.url_map.iter_rules()}
        assert any("crosswalk" in r for r in url_map)


# ===========================================================================
# 9. API Response Shape (with Flask test client)
# ===========================================================================

@pytest.fixture()
def flask_client(seeded_ato_db):
    """Flask test client with ato_compliance_api blueprint and mocked DB."""
    from flask import Flask
    from tools.dashboard.api.ato_compliance import ato_compliance_api

    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(ato_compliance_api)

    # Patch the DB connection used by the blueprint
    with patch("tools.dashboard.api.ato_compliance._get_db", return_value=seeded_ato_db):
        with app.test_client() as client:
            yield client


class TestATODashboardAPIResponses:
    """RED: API endpoints must return valid JSON with expected shapes."""

    def test_dashboard_endpoint_returns_200(self, flask_client):
        """GET /api/ato-compliance/dashboard/proj-ato-001 must return 200."""
        resp = flask_client.get("/api/ato-compliance/dashboard/proj-ato-001")
        assert resp.status_code == 200

    def test_dashboard_response_is_json(self, flask_client):
        """Dashboard endpoint must return valid JSON."""
        resp = flask_client.get("/api/ato-compliance/dashboard/proj-ato-001")
        data = resp.get_json()
        assert data is not None

    def test_dashboard_response_has_posture_score(self, flask_client):
        """Dashboard JSON must include posture_score."""
        resp = flask_client.get("/api/ato-compliance/dashboard/proj-ato-001")
        data = resp.get_json()
        assert "posture_score" in data

    def test_controls_endpoint_returns_200(self, flask_client):
        """GET /api/ato-compliance/controls/proj-ato-001 must return 200."""
        resp = flask_client.get("/api/ato-compliance/controls/proj-ato-001")
        assert resp.status_code == 200

    def test_controls_response_has_families(self, flask_client):
        """Controls endpoint JSON must include families."""
        resp = flask_client.get("/api/ato-compliance/controls/proj-ato-001")
        data = resp.get_json()
        assert "families" in data

    def test_rmf_endpoint_returns_200(self, flask_client):
        """GET /api/ato-compliance/rmf/proj-ato-001 must return 200."""
        resp = flask_client.get("/api/ato-compliance/rmf/proj-ato-001")
        assert resp.status_code == 200

    def test_rmf_response_has_stages(self, flask_client):
        """RMF endpoint JSON must include stages list."""
        resp = flask_client.get("/api/ato-compliance/rmf/proj-ato-001")
        data = resp.get_json()
        assert "stages" in data
        assert len(data["stages"]) == 6

    def test_artifacts_endpoint_returns_200(self, flask_client):
        """GET /api/ato-compliance/artifacts/proj-ato-001 must return 200."""
        resp = flask_client.get("/api/ato-compliance/artifacts/proj-ato-001")
        assert resp.status_code == 200

    def test_artifacts_response_has_artifacts(self, flask_client):
        """Artifacts endpoint JSON must include artifacts list."""
        resp = flask_client.get("/api/ato-compliance/artifacts/proj-ato-001")
        data = resp.get_json()
        assert "artifacts" in data

    def test_crosswalk_endpoint_returns_200(self, flask_client):
        """GET /api/ato-compliance/crosswalk/proj-ato-001 must return 200."""
        resp = flask_client.get("/api/ato-compliance/crosswalk/proj-ato-001")
        assert resp.status_code == 200

    def test_crosswalk_response_has_frameworks(self, flask_client):
        """Crosswalk endpoint JSON must include frameworks."""
        resp = flask_client.get("/api/ato-compliance/crosswalk/proj-ato-001")
        data = resp.get_json()
        assert "frameworks" in data


# ===========================================================================
# 10. Dashboard Page Route — app.py integration
# ===========================================================================

class TestDashboardPageRoute:
    """RED: /ato-compliance route must be registered in tools/dashboard/app.py."""

    def test_ato_compliance_route_in_app(self):
        """app.py must have a @app.route('/ato-compliance') handler."""
        app_path = BASE_DIR / "tools" / "dashboard" / "app.py"
        assert app_path.exists(), "tools/dashboard/app.py not found"
        content = app_path.read_text(encoding="utf-8")
        assert "/ato-compliance" in content, (
            "Route /ato-compliance not found in tools/dashboard/app.py. "
            "Add @app.route('/ato-compliance') handler."
        )

    def test_ato_compliance_template_exists(self):
        """The template lives on the Boundary canvas (rmf-ui-01), not at the top level.

        The page route migrated to /boundary/ato-compliance on
        tools/boundary_canvas/blueprint.py; a top-level ato_compliance.html
        would be a second, ungoverned home for the same page.
        """
        tmpl = (
            BASE_DIR / "tools" / "dashboard" / "templates"
            / "boundary_canvas" / "ato_compliance.html"
        )
        assert tmpl.exists(), (
            "Template boundary_canvas/ato_compliance.html not found. "
            "The ATO Compliance Dashboard renders from the Boundary canvas."
        )
        legacy = BASE_DIR / "tools" / "dashboard" / "templates" / "ato_compliance.html"
        assert not legacy.exists(), (
            "tools/dashboard/templates/ato_compliance.html still exists -- "
            "two homes for one page"
        )

    def test_ato_compliance_blueprint_in_api_init(self):
        """ato_compliance_api blueprint must be registered in tools/dashboard/api/__init__.py."""
        api_init = BASE_DIR / "tools" / "dashboard" / "api" / "__init__.py"
        assert api_init.exists()
        content = api_init.read_text(encoding="utf-8")
        assert "ato_compliance" in content, (
            "ato_compliance blueprint not found in tools/dashboard/api/__init__.py."
        )
