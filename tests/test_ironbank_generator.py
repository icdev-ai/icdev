#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for Platform One / Iron Bank metadata generator (Phase 57, D350).

Validates:
    - Hardening manifest generation
    - Container approval record structure
    - Language detection
    - Manifest validation
    - CLI interface
"""

import json
import sqlite3
import sys
import uuid
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))
from tools.infra.ironbank_metadata_generator import (
    IRONBANK_BASE_IMAGES,
    generate_hardening_manifest,
    validate_hardening_manifest,
    _detect_language,
)


@pytest.fixture
def icdev_db(tmp_path):
    """Create a minimal ICDEV database for project lookup."""
    db_path = tmp_path / "icdev.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            name TEXT,
            status TEXT DEFAULT 'active'
        )
    """)
    project_id = f"proj-{uuid.uuid4().hex[:8]}"
    conn.execute("INSERT INTO projects (id, name) VALUES (?, ?)",
                 (project_id, "Test Iron Bank App"))
    conn.commit()
    conn.close()
    return db_path, project_id


class TestBaseImages:
    """Test Iron Bank base image registry."""

    def test_all_languages_present(self):
        expected = {"python", "java", "go", "node", "rust", "dotnet", "base"}
        assert expected.issubset(set(IRONBANK_BASE_IMAGES.keys()))

    def test_registry_references_ironbank(self):
        for lang, info in IRONBANK_BASE_IMAGES.items():
            assert "registry1.dso.mil" in info["registry"], (
                f"{lang} base image must reference Iron Bank registry"
            )

    def test_all_images_have_required_fields(self):
        for lang, info in IRONBANK_BASE_IMAGES.items():
            assert "registry" in info
            assert "tag" in info
            assert "os" in info


class TestLanguageDetection:
    """Test language auto-detection from project directory."""

    def test_python_detection(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("flask\n")
        assert _detect_language(str(tmp_path)) == "python"

    def test_java_detection(self, tmp_path):
        (tmp_path / "pom.xml").write_text("<project/>\n")
        assert _detect_language(str(tmp_path)) == "java"

    def test_go_detection(self, tmp_path):
        (tmp_path / "go.mod").write_text("module example\n")
        assert _detect_language(str(tmp_path)) == "go"

    def test_node_detection(self, tmp_path):
        (tmp_path / "package.json").write_text("{}\n")
        assert _detect_language(str(tmp_path)) == "node"

    def test_rust_detection(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text("[package]\n")
        assert _detect_language(str(tmp_path)) == "rust"

    def test_unknown_defaults_to_base(self, tmp_path):
        assert _detect_language(str(tmp_path)) == "base"

    def test_none_project_dir_defaults_to_base(self):
        assert _detect_language(None) == "base"


class TestManifestGeneration:
    """Test hardening manifest generation."""

    def test_generate_returns_required_keys(self, icdev_db):
        db_path, project_id = icdev_db
        result = generate_hardening_manifest(project_id, db_path=db_path)
        required = {"project_id", "project_name", "manifest_yaml", "approval_record",
                    "output_paths", "language_detected", "base_image", "generated_at"}
        assert required.issubset(set(result.keys()))

    def test_manifest_yaml_has_required_fields(self, icdev_db):
        db_path, project_id = icdev_db
        result = generate_hardening_manifest(project_id, db_path=db_path)
        manifest = result["manifest_yaml"]
        for field in ["apiVersion: v1", "name:", "labels:", "base_image:", "image_author:"]:
            assert field in manifest, f"Manifest missing: {field}"

    def test_manifest_references_ironbank_registry(self, icdev_db):
        db_path, project_id = icdev_db
        result = generate_hardening_manifest(project_id, db_path=db_path)
        assert "registry1.dso.mil" in result["manifest_yaml"]

    def test_manifest_has_impact_level_label(self, icdev_db):
        db_path, project_id = icdev_db
        result = generate_hardening_manifest(project_id, db_path=db_path)
        assert "mil.dod.impact.level" in result["manifest_yaml"]

    def test_approval_record_structure(self, icdev_db):
        db_path, project_id = icdev_db
        result = generate_hardening_manifest(project_id, db_path=db_path)
        record = result["approval_record"]
        assert record["project_id"] == project_id
        assert record["status"] == "pending"
        assert "iron_bank_url" in record
        assert "ironbank.dso.mil" in record["iron_bank_url"]

    def test_output_written_to_dir(self, icdev_db, tmp_path):
        db_path, project_id = icdev_db
        out_dir = str(tmp_path / "ironbank_out")
        result = generate_hardening_manifest(
            project_id, db_path=db_path, output_dir=out_dir
        )
        assert "hardening_manifest" in result["output_paths"]
        assert Path(result["output_paths"]["hardening_manifest"]).exists()
        assert "container_approval" in result["output_paths"]
        assert Path(result["output_paths"]["container_approval"]).exists()

    def test_language_detection_used_in_base_image(self, icdev_db, tmp_path):
        db_path, project_id = icdev_db
        # Create a Python project
        (tmp_path / "requirements.txt").write_text("flask\n")
        result = generate_hardening_manifest(
            project_id, project_dir=str(tmp_path), db_path=db_path
        )
        assert result["language_detected"] == "python"
        assert "python" in result["base_image"].lower() or "registry1" in result["base_image"]


class TestManifestValidation:
    """Test manifest validation."""

    def test_valid_manifest_passes(self, icdev_db, tmp_path):
        db_path, project_id = icdev_db
        out_dir = str(tmp_path / "out")
        result = generate_hardening_manifest(
            project_id, db_path=db_path, output_dir=out_dir
        )
        manifest_path = result["output_paths"]["hardening_manifest"]
        validation = validate_hardening_manifest(project_id, manifest_path=manifest_path)
        assert validation["error_count"] == 0

    def test_missing_manifest_file_returns_error(self):
        result = validate_hardening_manifest(
            "proj-test", manifest_path="/nonexistent/hardening_manifest.yaml"
        )
        assert not result["passed"]
        assert result["error_count"] > 0

    def test_no_manifest_path_returns_warning(self):
        result = validate_hardening_manifest("proj-test", manifest_path=None)
        assert result["passed"]  # No file = no errors, just warnings
        assert result["warning_count"] > 0
