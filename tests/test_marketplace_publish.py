# CUI // SP-CTI
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

"""Tests for tools.marketplace.publish_pipeline — marketplace asset publishing."""

import json
import os
import sqlite3
import tempfile
from unittest.mock import patch, MagicMock

import pytest

from tools.marketplace.publish_pipeline import (
    parse_skill_md,
    validate_asset_structure,
    publish_asset,
    ASSET_TYPE_FILES,
    ASSET_TYPE_ALTERNATIVES,
)


# ---------------------------------------------------------------------------
# Schema for marketplace tables required by publish pipeline
# ---------------------------------------------------------------------------
MARKETPLACE_SCHEMA = """
CREATE TABLE IF NOT EXISTS marketplace_assets (
    id TEXT PRIMARY KEY,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    display_name TEXT,
    asset_type TEXT NOT NULL,
    description TEXT NOT NULL,
    current_version TEXT NOT NULL,
    classification TEXT NOT NULL DEFAULT 'CUI // SP-CTI',
    impact_level TEXT NOT NULL DEFAULT 'IL4',
    publisher_tenant_id TEXT,
    publisher_org TEXT,
    publisher_user TEXT,
    catalog_tier TEXT NOT NULL DEFAULT 'tenant_local',
    status TEXT NOT NULL DEFAULT 'draft',
    license TEXT DEFAULT 'USG-INTERNAL',
    tags TEXT,
    compliance_controls TEXT,
    supported_languages TEXT,
    min_icdev_version TEXT,
    download_count INTEGER DEFAULT 0,
    average_rating REAL DEFAULT 0.0,
    review_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS marketplace_versions (
    id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL REFERENCES marketplace_assets(id),
    version TEXT NOT NULL,
    changelog TEXT,
    sha256_hash TEXT NOT NULL,
    signature TEXT,
    signed_by TEXT,
    sbom_id TEXT,
    file_path TEXT,
    file_size_bytes INTEGER DEFAULT 0,
    metadata TEXT,
    published_by TEXT,
    status TEXT NOT NULL DEFAULT 'draft',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(asset_id, version)
);

CREATE TABLE IF NOT EXISTS marketplace_scan_results (
    id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL REFERENCES marketplace_assets(id),
    version_id TEXT NOT NULL REFERENCES marketplace_versions(id),
    gate_name TEXT NOT NULL,
    status TEXT NOT NULL,
    findings_count INTEGER DEFAULT 0,
    critical_count INTEGER DEFAULT 0,
    high_count INTEGER DEFAULT 0,
    medium_count INTEGER DEFAULT 0,
    low_count INTEGER DEFAULT 0,
    details TEXT,
    scanned_by TEXT DEFAULT 'icdev-marketplace-scanner',
    scanned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS marketplace_reviews (
    id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL REFERENCES marketplace_assets(id),
    version_id TEXT NOT NULL REFERENCES marketplace_versions(id),
    reviewer_id TEXT,
    reviewer_role TEXT,
    decision TEXT,
    rationale TEXT,
    conditions TEXT,
    scan_results_reviewed INTEGER DEFAULT 0,
    code_reviewed INTEGER DEFAULT 0,
    compliance_reviewed INTEGER DEFAULT 0,
    submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reviewed_at TIMESTAMP
);
"""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture
def marketplace_db(tmp_path):
    """Temporary SQLite database with marketplace tables."""
    db_path = tmp_path / "icdev.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(MARKETPLACE_SCHEMA)
    conn.close()
    return db_path


@pytest.fixture
def skill_asset_dir(tmp_path):
    """A valid skill asset directory with SKILL.md containing YAML frontmatter."""
    asset_dir = tmp_path / "my-test-skill"
    asset_dir.mkdir()
    skill_md = asset_dir / "SKILL.md"
    skill_md.write_text(
        "---\n"
        "name: my-test-skill\n"
        "description: A test skill for unit testing\n"
        "version: 1.0.0\n"
        "impact_level: IL4\n"
        "classification: CUI // SP-CTI\n"
        "tags:\n"
        "  - testing\n"
        "  - ci\n"
        "---\n"
        "# My Test Skill\n\nThis is the body of the skill.\n",
        encoding="utf-8",
    )
    return asset_dir


@pytest.fixture
def goal_asset_dir(tmp_path):
    """A valid goal asset directory with goal.md."""
    asset_dir = tmp_path / "my-goal"
    asset_dir.mkdir()
    goal_md = asset_dir / "goal.md"
    goal_md.write_text(
        "---\n"
        "name: my-goal\n"
        "description: A test goal\n"
        "---\n"
        "# Goal\n\nSteps here.\n",
        encoding="utf-8",
    )
    return asset_dir


@pytest.fixture
def mock_scan_pass():
    """Mock asset_scanner.run_full_scan to return a passing result."""
    scan_result = {
        "passed": True,
        "overall_status": "pass",
        "blocking_gates_pass": True,
        "gates_scanned": 7,
        "gate_results": [],
    }
    with patch("tools.marketplace.publish_pipeline.run_full_scan", return_value=scan_result):
        yield scan_result


@pytest.fixture
def mock_scan_fail():
    """Mock asset_scanner.run_full_scan to return a failing result."""
    scan_result = {
        "passed": False,
        "overall_status": "fail",
        "blocking_gates_pass": False,
        "gates_scanned": 7,
        "gate_results": [{"gate": "sast_scan", "status": "fail"}],
    }
    with patch("tools.marketplace.publish_pipeline.run_full_scan", return_value=scan_result):
        yield scan_result


# ---------------------------------------------------------------------------
# TestParseSkillMd
# ---------------------------------------------------------------------------
class TestParseSkillMd:
    """parse_skill_md: YAML frontmatter extraction from SKILL.md files."""

    def test_parses_frontmatter_fields(self, skill_asset_dir):
        md_path = skill_asset_dir / "SKILL.md"
        metadata = parse_skill_md(str(md_path))
        assert metadata["name"] == "my-test-skill"
        assert metadata["description"] == "A test skill for unit testing"
        assert metadata["version"] == "1.0.0"

    def test_body_extracted(self, skill_asset_dir):
        md_path = skill_asset_dir / "SKILL.md"
        metadata = parse_skill_md(str(md_path))
        assert "_body" in metadata
        assert "My Test Skill" in metadata["_body"]

    def test_no_frontmatter_returns_body_only(self, tmp_path):
        md_file = tmp_path / "bare.md"
        md_file.write_text("# Just a heading\n\nParagraph text.", encoding="utf-8")
        metadata = parse_skill_md(str(md_file))
        assert metadata.get("name") is None
        assert "_body" in metadata
        assert "Just a heading" in metadata["_body"]


# ---------------------------------------------------------------------------
# TestValidateAssetStructure
# ---------------------------------------------------------------------------
class TestValidateAssetStructure:
    """validate_asset_structure: directory layout and metadata validation."""

    def test_valid_skill_passes(self, skill_asset_dir):
        is_valid, errors, metadata = validate_asset_structure(str(skill_asset_dir), "skill")
        assert is_valid is True
        assert errors == []
        assert metadata["name"] == "my-test-skill"

    def test_missing_main_file_fails(self, tmp_path):
        empty_dir = tmp_path / "empty-asset"
        empty_dir.mkdir()
        is_valid, errors, metadata = validate_asset_structure(str(empty_dir), "skill")
        assert is_valid is False
        assert any("Missing required file" in e for e in errors)

    def test_not_a_directory_fails(self, tmp_path):
        a_file = tmp_path / "not-a-dir.txt"
        a_file.write_text("hello", encoding="utf-8")
        is_valid, errors, _ = validate_asset_structure(str(a_file), "skill")
        assert is_valid is False
        assert "Asset path is not a directory" in errors

    def test_invalid_name_format_reports_error(self, tmp_path):
        asset_dir = tmp_path / "BAD_NAME"
        asset_dir.mkdir()
        skill_md = asset_dir / "SKILL.md"
        skill_md.write_text(
            "---\nname: BAD_NAME\ndescription: Bad name test\n---\n# Body\n",
            encoding="utf-8",
        )
        is_valid, errors, _ = validate_asset_structure(str(asset_dir), "skill")
        assert is_valid is False
        assert any("Invalid name format" in e for e in errors)

    def test_missing_description_reports_error(self, tmp_path):
        asset_dir = tmp_path / "no-desc"
        asset_dir.mkdir()
        skill_md = asset_dir / "SKILL.md"
        skill_md.write_text("---\nname: no-desc\n---\n# Body\n", encoding="utf-8")
        is_valid, errors, _ = validate_asset_structure(str(asset_dir), "skill")
        assert is_valid is False
        assert any("Missing required field: description" in e for e in errors)

    def test_goal_type_accepts_goal_md(self, goal_asset_dir):
        is_valid, errors, metadata = validate_asset_structure(str(goal_asset_dir), "goal")
        assert is_valid is True
        assert metadata["name"] == "my-goal"


# ---------------------------------------------------------------------------
# TestPublishAsset
# ---------------------------------------------------------------------------
class TestPublishAsset:
    """publish_asset: full pipeline orchestration."""

    def test_successful_publish_tenant_local(self, skill_asset_dir, marketplace_db, mock_scan_pass):
        result = publish_asset(
            asset_path=str(skill_asset_dir),
            asset_type="skill",
            tenant_id="tenant-abc-123",
            publisher_user="dev@mil",
            db_path=marketplace_db,
        )
        assert result["status"] == "published"
        assert result["asset_id"].startswith("asset-")
        assert result["version_id"].startswith("ver-")
        assert result["version"] == "1.0.0"
        assert result["target_tier"] == "tenant_local"
        # Verify steps completed
        step_names = [s["step"] for s in result["steps"]]
        assert "validate_structure" in step_names
        assert "parse_metadata" in step_names

    def test_publish_fails_on_invalid_structure(self, tmp_path, marketplace_db):
        empty_dir = tmp_path / "bad-asset"
        empty_dir.mkdir()
        result = publish_asset(
            asset_path=str(empty_dir),
            asset_type="skill",
            tenant_id="tenant-abc-123",
            publisher_user="dev@mil",
            db_path=marketplace_db,
        )
        assert result["status"] == "failed"
        assert result["failed_at"] == "validate_structure"
        assert len(result["errors"]) > 0

    def test_publish_fails_on_scan_failure(self, skill_asset_dir, marketplace_db, mock_scan_fail):
        result = publish_asset(
            asset_path=str(skill_asset_dir),
            asset_type="skill",
            tenant_id="tenant-abc-123",
            publisher_user="dev@mil",
            db_path=marketplace_db,
        )
        assert result["status"] == "failed"
        assert result["failed_at"] == "security_scan"

    def test_central_vetted_submits_for_review(self, skill_asset_dir, marketplace_db, mock_scan_pass):
        result = publish_asset(
            asset_path=str(skill_asset_dir),
            asset_type="skill",
            tenant_id="tenant-abc-123",
            publisher_user="dev@mil",
            target_tier="central_vetted",
            db_path=marketplace_db,
        )
        assert result["status"] == "pending_review"
        assert result["target_tier"] == "central_vetted"
        # Should have a submit_review step
        step_names = [s["step"] for s in result["steps"]]
        assert "submit_review" in step_names
        # Verify a review record was created in DB
        conn = sqlite3.connect(str(marketplace_db))
        row = conn.execute("SELECT COUNT(*) FROM marketplace_reviews").fetchone()
        conn.close()
        assert row[0] == 1


# ---------------------------------------------------------------------------
# TestGateStatus
# ---------------------------------------------------------------------------
class TestGateStatus:
    """Gate status: pipeline step recording and scan status propagation."""

    def test_scan_status_propagated_to_result(self, skill_asset_dir, marketplace_db, mock_scan_pass):
        result = publish_asset(
            asset_path=str(skill_asset_dir),
            asset_type="skill",
            tenant_id="tenant-abc-123",
            publisher_user="dev@mil",
            db_path=marketplace_db,
        )
        assert result["scan_status"] == "pass"

    def test_sign_step_skipped_without_key(self, skill_asset_dir, marketplace_db, mock_scan_pass):
        result = publish_asset(
            asset_path=str(skill_asset_dir),
            asset_type="skill",
            tenant_id="tenant-abc-123",
            publisher_user="dev@mil",
            db_path=marketplace_db,
        )
        sign_steps = [s for s in result["steps"] if s["step"] == "sign_artifact"]
        assert len(sign_steps) == 1
        assert sign_steps[0]["status"] == "skipped"

    def test_asset_status_set_to_draft_on_scan_failure(self, skill_asset_dir, marketplace_db, mock_scan_fail):
        result = publish_asset(
            asset_path=str(skill_asset_dir),
            asset_type="skill",
            tenant_id="tenant-abc-123",
            publisher_user="dev@mil",
            db_path=marketplace_db,
        )
        # Verify the DB was updated back to 'draft' on scan failure
        conn = sqlite3.connect(str(marketplace_db))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT status FROM marketplace_assets WHERE id = ?",
            (result["asset_id"],),
        ).fetchone()
        conn.close()
        assert row["status"] == "draft"


# ---------------------------------------------------------------------------
# TestILValidation
# ---------------------------------------------------------------------------
class TestILValidation:
    """IL (Impact Level) validation: metadata extraction and slug generation."""

    def test_default_impact_level_is_il4(self, tmp_path, marketplace_db):
        """When SKILL.md omits impact_level, default to IL4."""
        asset_dir = tmp_path / "no-il-skill"
        asset_dir.mkdir()
        (asset_dir / "SKILL.md").write_text(
            "---\nname: no-il-skill\ndescription: No IL specified\n---\n# Body\n",
            encoding="utf-8",
        )
        scan_result = {
            "passed": True, "overall_status": "pass",
            "blocking_gates_pass": True, "gates_scanned": 7, "gate_results": [],
        }
        with patch("tools.marketplace.publish_pipeline.run_full_scan", return_value=scan_result):
            result = publish_asset(
                asset_path=str(asset_dir),
                asset_type="skill",
                tenant_id="tenant-abc-123",
                publisher_user="dev@mil",
                db_path=marketplace_db,
            )
        assert result["status"] == "published"
        # Check metadata step recorded IL4
        meta_steps = [s for s in result["steps"] if s["step"] == "parse_metadata"]
        assert meta_steps[0]["metadata"]["impact_level"] == "IL4"

    def test_slug_includes_tenant_prefix(self, skill_asset_dir, marketplace_db, mock_scan_pass):
        result = publish_asset(
            asset_path=str(skill_asset_dir),
            asset_type="skill",
            tenant_id="tenant-abc-123456",
            publisher_user="dev@mil",
            db_path=marketplace_db,
        )
        assert result["slug"].startswith("tenant-abc-1")
        assert "/my-test-skill" in result["slug"]


# CUI // SP-CTI
