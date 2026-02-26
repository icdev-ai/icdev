#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for SLSA attestation generator and SWFT evidence bundler (Phase 54)."""

import json
import sqlite3
import uuid
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db(tmp_path):
    """Create a temporary ICDEV database with minimal schema."""
    db_file = tmp_path / "icdev.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute("""CREATE TABLE IF NOT EXISTS devsecops_pipeline_audit (
        id TEXT PRIMARY KEY, project_id TEXT, stage TEXT, status TEXT,
        created_at TEXT DEFAULT (datetime('now')))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS sbom_records (
        id TEXT PRIMARY KEY, project_id TEXT, version TEXT,
        format TEXT, file_path TEXT, component_count INTEGER DEFAULT 0,
        vulnerability_count INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now')))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS devsecops_profiles (
        project_id TEXT PRIMARY KEY, maturity_level TEXT,
        active_stages TEXT DEFAULT '[]', stage_configs TEXT DEFAULT '{}',
        created_at TEXT DEFAULT (datetime('now')))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS audit_trail (
        id TEXT PRIMARY KEY, project_id TEXT, event_type TEXT,
        actor TEXT, action TEXT, created_at TEXT DEFAULT (datetime('now')))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS production_audits (
        id TEXT PRIMARY KEY, project_id TEXT, results_json TEXT,
        total_checks INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now')))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS vulnerability_records (
        id TEXT PRIMARY KEY, project_id TEXT, severity TEXT,
        status TEXT, justification TEXT,
        created_at TEXT DEFAULT (datetime('now')))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS cve_triage (
        id TEXT PRIMARY KEY, project_id TEXT, cve_id TEXT,
        cvss_score REAL, triage_decision TEXT,
        created_at TEXT DEFAULT (datetime('now')))""")
    conn.commit()
    conn.close()
    return db_file


@pytest.fixture
def populated_db(tmp_db):
    """Populate the temporary database with test data."""
    conn = sqlite3.connect(str(tmp_db))
    pid = "proj-test"

    # Pipeline audit
    conn.execute(
        "INSERT INTO devsecops_pipeline_audit (id, project_id, stage, status) VALUES (?, ?, ?, ?)",
        (str(uuid.uuid4()), pid, "image_signing", "passed"),
    )
    # SBOM
    conn.execute(
        "INSERT INTO sbom_records (id, project_id, version, format, file_path, component_count) VALUES (?, ?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), pid, "1", "cyclonedx", "/tmp/sbom.json", 42),
    )
    # DevSecOps profile
    conn.execute(
        "INSERT INTO devsecops_profiles (project_id, maturity_level, active_stages) VALUES (?, ?, ?)",
        (pid, "level_3_defined", json.dumps(["image_signing", "sbom_attestation"])),
    )
    # Audit trail deploy event
    conn.execute(
        "INSERT INTO audit_trail (id, project_id, event_type, actor, action) VALUES (?, ?, ?, ?, ?)",
        (str(uuid.uuid4()), pid, "deploy.staging", "infra-agent", "Deployed to staging"),
    )
    # Production audit
    conn.execute(
        "INSERT INTO production_audits (id, project_id, results_json, total_checks) VALUES (?, ?, ?, ?)",
        (str(uuid.uuid4()), pid, '{"checks": []}', 30),
    )
    conn.commit()
    conn.close()
    return tmp_db


# ---------------------------------------------------------------------------
# SLSA Attestation Generator Tests
# ---------------------------------------------------------------------------

class TestSLSAProvenance:
    """Tests for SLSA provenance generation."""

    def test_generate_provenance_empty_db(self, tmp_db):
        from tools.compliance.slsa_attestation_generator import generate_slsa_provenance
        result = generate_slsa_provenance("proj-test", db_path=tmp_db)
        assert result["project_id"] == "proj-test"
        assert result["slsa_level"] == 0
        assert "provenance" in result
        assert result["provenance"]["_type"] == "https://in-toto.io/Statement/v1"
        assert result["provenance"]["predicateType"] == "https://slsa.dev/provenance/v1"

    def test_generate_provenance_with_evidence(self, populated_db):
        from tools.compliance.slsa_attestation_generator import generate_slsa_provenance
        result = generate_slsa_provenance("proj-test", db_path=populated_db)
        assert result["slsa_level"] >= 2
        assert result["evidence_met"] > 0
        assert result["evidence"]["build_process_documented"] is True
        assert result["evidence"]["version_controlled_source"] is True

    def test_provenance_has_subjects(self, populated_db):
        from tools.compliance.slsa_attestation_generator import generate_slsa_provenance
        result = generate_slsa_provenance("proj-test", db_path=populated_db)
        subjects = result["provenance"]["subject"]
        assert len(subjects) > 0
        assert "digest" in subjects[0]
        assert "sha256" in subjects[0]["digest"]

    def test_provenance_build_info(self, populated_db):
        from tools.compliance.slsa_attestation_generator import generate_slsa_provenance
        build_info = {
            "repository": "https://gitlab.mil/test-project",
            "commit": "abc123",
            "branch": "feature-branch",
            "pipeline_id": "pipe-456",
        }
        result = generate_slsa_provenance("proj-test", build_info=build_info, db_path=populated_db)
        ext_params = result["provenance"]["predicate"]["buildDefinition"]["externalParameters"]
        assert ext_params["repository"] == "https://gitlab.mil/test-project"
        assert ext_params["ref"] == "abc123"


class TestSLSALevel:
    """Tests for SLSA level determination."""

    def test_level_0_no_evidence(self, tmp_db):
        from tools.compliance.slsa_attestation_generator import verify_slsa_level
        result = verify_slsa_level("proj-empty", db_path=tmp_db)
        assert result["current_level"] == 0
        assert result["meets_target"] is False

    def test_level_verification_gaps(self, tmp_db):
        from tools.compliance.slsa_attestation_generator import verify_slsa_level
        result = verify_slsa_level("proj-empty", target_level=3, db_path=tmp_db)
        assert len(result["gaps"]) > 0
        assert len(result["recommendations"]) > 0

    def test_level_requirements_structure(self):
        from tools.compliance.slsa_attestation_generator import SLSA_LEVEL_REQUIREMENTS
        assert 0 in SLSA_LEVEL_REQUIREMENTS
        assert 4 in SLSA_LEVEL_REQUIREMENTS
        for level in range(5):
            assert "description" in SLSA_LEVEL_REQUIREMENTS[level]
            assert "requirements" in SLSA_LEVEL_REQUIREMENTS[level]
        # Higher levels have more requirements
        assert len(SLSA_LEVEL_REQUIREMENTS[4]["requirements"]) > len(SLSA_LEVEL_REQUIREMENTS[1]["requirements"])


class TestVEXDocument:
    """Tests for VEX document generation."""

    def test_generate_vex_empty(self, tmp_db):
        from tools.compliance.slsa_attestation_generator import generate_vex_document
        result = generate_vex_document("proj-test", db_path=tmp_db)
        assert result["project_id"] == "proj-test"
        assert "vex_document" in result
        assert result["vex_document"]["bomFormat"] == "CycloneDX"
        assert result["vulnerability_summary"]["total"] == 0

    def test_vex_with_vulnerabilities(self, tmp_db):
        from tools.compliance.slsa_attestation_generator import generate_vex_document
        conn = sqlite3.connect(str(tmp_db))
        conn.execute(
            "INSERT INTO vulnerability_records (id, project_id, severity, status) VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), "proj-test", "high", "affected"),
        )
        conn.commit()
        conn.close()

        result = generate_vex_document("proj-test", db_path=tmp_db)
        assert result["vulnerability_summary"]["total"] >= 1
        assert len(result["vex_document"]["vulnerabilities"]) >= 1


# ---------------------------------------------------------------------------
# SWFT Evidence Bundler Tests
# ---------------------------------------------------------------------------

class TestSWFTBundle:
    """Tests for SWFT evidence bundling."""

    def test_bundle_empty_db(self, tmp_db):
        from tools.compliance.swft_evidence_bundler import bundle_swft_evidence
        result = bundle_swft_evidence("proj-test", db_path=tmp_db)
        assert result["project_id"] == "proj-test"
        assert result["bundle_type"] == "swft_evidence"
        assert "artifacts" in result
        assert "summary" in result
        assert result["summary"]["readiness_pct"] == 0.0

    def test_bundle_with_evidence(self, populated_db):
        from tools.compliance.swft_evidence_bundler import bundle_swft_evidence
        result = bundle_swft_evidence("proj-test", db_path=populated_db)
        assert result["summary"]["available"] > 0
        assert result["summary"]["readiness_pct"] > 0

    def test_bundle_artifact_categories(self, tmp_db):
        from tools.compliance.swft_evidence_bundler import bundle_swft_evidence, SWFT_ARTIFACT_CATEGORIES
        result = bundle_swft_evidence("proj-test", db_path=tmp_db)
        for category in SWFT_ARTIFACT_CATEGORIES:
            assert category in result["artifacts"]

    def test_bundle_integrity_hash(self, tmp_db):
        from tools.compliance.swft_evidence_bundler import bundle_swft_evidence
        result = bundle_swft_evidence("proj-test", db_path=tmp_db)
        assert "integrity" in result
        assert result["integrity"]["digest_algorithm"] == "sha256"
        assert len(result["integrity"]["bundle_hash"]) == 64

    def test_bundle_output_dir(self, populated_db, tmp_path):
        from tools.compliance.swft_evidence_bundler import bundle_swft_evidence
        out_dir = tmp_path / "swft_output"
        result = bundle_swft_evidence("proj-test", output_dir=str(out_dir), db_path=populated_db)
        assert "output_file" in result
        assert Path(result["output_file"]).exists()


class TestSWFTValidation:
    """Tests for SWFT evidence validation."""

    def test_validate_empty_db(self, tmp_db):
        from tools.compliance.swft_evidence_bundler import validate_swft_bundle
        result = validate_swft_bundle("proj-test", db_path=tmp_db)
        assert result["valid"] is False
        assert result["blocking_gaps"] > 0
        assert len(result["recommendations"]) > 0

    def test_validate_with_evidence(self, populated_db):
        from tools.compliance.swft_evidence_bundler import validate_swft_bundle
        result = validate_swft_bundle("proj-test", db_path=populated_db)
        # Some evidence should reduce blocking gaps
        assert result["gap_count"] < len(
            [c for c, v in __import__("tools.compliance.swft_evidence_bundler", fromlist=["SWFT_ARTIFACT_CATEGORIES"]).SWFT_ARTIFACT_CATEGORIES.items()]
        )
