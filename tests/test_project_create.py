# CUI // SP-CTI
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

"""Tests for tools.project.project_create — project creation end-to-end."""

import json
import sqlite3
from unittest.mock import patch

import pytest

from tests.conftest import MINIMAL_ICDEV_SCHEMA, SEED_PROJECT_ID


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db(tmp_path):
    """Create a minimal ICDEV database and return its path."""
    db_path = tmp_path / "data" / "icdev.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.executescript(MINIMAL_ICDEV_SCHEMA)
    conn.close()
    return db_path


def _create_project(tmp_path, db_path, **kwargs):
    """Call create_project with DB_PATH and PROJECTS_DIR patched to tmp_path."""
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir(parents=True, exist_ok=True)
    with patch("tools.project.project_create.DB_PATH", db_path), \
         patch("tools.project.project_create.PROJECTS_DIR", projects_dir):
        from tools.project.project_create import create_project
        return create_project(**kwargs)


def _query_project(db_path, project_id):
    """Fetch a project row by ID."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def _query_audit(db_path, project_id):
    """Fetch audit trail entries for a project."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM audit_trail WHERE project_id = ?", (project_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# TestProjectCreate
# ---------------------------------------------------------------------------

class TestProjectCreate:
    """create_project: successful project creation scenarios."""

    def test_create_project_returns_dict(self, tmp_path):
        db_path = _make_db(tmp_path)
        result = _create_project(tmp_path, db_path, name="Alpha", skip_scaffold=True)
        assert isinstance(result, dict)
        assert "project_id" in result

    def test_create_project_assigns_uuid(self, tmp_path):
        db_path = _make_db(tmp_path)
        result = _create_project(tmp_path, db_path, name="Beta", skip_scaffold=True)
        # UUID v4 is 36 chars with hyphens
        assert len(result["project_id"]) == 36
        assert result["project_id"].count("-") == 4

    def test_create_project_default_fields(self, tmp_path):
        db_path = _make_db(tmp_path)
        result = _create_project(tmp_path, db_path, name="Gamma", skip_scaffold=True)
        assert result["type"] == "webapp"
        assert result["classification"] == "CUI"
        assert result["impact_level"] == "IL5"
        assert result["status"] == "active"
        assert result["ato_status"] == "none"
        assert result["cloud_environment"] == "aws-govcloud"

    def test_create_project_with_all_options(self, tmp_path):
        db_path = _make_db(tmp_path)
        result = _create_project(
            tmp_path, db_path,
            name="Full Opts",
            project_type="microservice",
            classification="FOUO",
            description="A test project",
            tech_backend="Python/FastAPI",
            tech_frontend="React",
            tech_database="PostgreSQL",
            impact_level="IL4",
            target_frameworks="fedramp-moderate",
            cloud_environment="azure-gov",
            accrediting_authority="ISSM Jones",
            skip_scaffold=True,
        )
        assert result["type"] == "microservice"
        assert result["classification"] == "FOUO"
        assert result["impact_level"] == "IL4"
        assert result["tech_stack"]["backend"] == "Python/FastAPI"
        assert result["tech_stack"]["frontend"] == "React"
        assert result["tech_stack"]["database"] == "PostgreSQL"
        assert result["cloud_environment"] == "azure-gov"
        assert result["target_frameworks"] == "fedramp-moderate"

    def test_create_project_persists_to_db(self, tmp_path):
        db_path = _make_db(tmp_path)
        result = _create_project(tmp_path, db_path, name="Persist Test", skip_scaffold=True)
        row = _query_project(db_path, result["project_id"])
        assert row is not None
        assert row["name"] == "Persist Test"
        assert row["type"] == "webapp"
        assert row["status"] == "active"

    def test_create_project_name_slug(self, tmp_path):
        db_path = _make_db(tmp_path)
        result = _create_project(
            tmp_path, db_path, name="My Cool App (v2)", skip_scaffold=True,
        )
        directory = Path(result["directory"])
        # Slug should be lowercased with special chars replaced by dashes
        assert directory.name == "my-cool-app-v2"

    def test_create_project_name_collision_appends_suffix(self, tmp_path):
        db_path = _make_db(tmp_path)
        projects_dir = tmp_path / "projects"
        projects_dir.mkdir(parents=True, exist_ok=True)
        # Pre-create the directory to force a collision
        (projects_dir / "collision-test").mkdir()
        result = _create_project(tmp_path, db_path, name="Collision Test", skip_scaffold=True)
        directory = Path(result["directory"])
        # Should have a UUID suffix appended
        assert directory.name.startswith("collision-test-")
        assert len(directory.name) > len("collision-test-")


# ---------------------------------------------------------------------------
# TestProjectValidation
# ---------------------------------------------------------------------------

class TestProjectValidation:
    """create_project: input validation errors."""

    def test_empty_name_raises(self, tmp_path):
        db_path = _make_db(tmp_path)
        with pytest.raises(ValueError, match="Project name is required"):
            _create_project(tmp_path, db_path, name="", skip_scaffold=True)

    def test_whitespace_only_name_raises(self, tmp_path):
        db_path = _make_db(tmp_path)
        with pytest.raises(ValueError, match="Project name is required"):
            _create_project(tmp_path, db_path, name="   ", skip_scaffold=True)

    def test_invalid_type_raises(self, tmp_path):
        db_path = _make_db(tmp_path)
        with pytest.raises(ValueError, match="Invalid type"):
            _create_project(tmp_path, db_path, name="Bad Type", project_type="nosuchtype", skip_scaffold=True)

    def test_invalid_classification_raises(self, tmp_path):
        db_path = _make_db(tmp_path)
        with pytest.raises(ValueError, match="Invalid classification"):
            _create_project(tmp_path, db_path, name="Bad Class", classification="UNCLASSIFIED", skip_scaffold=True)

    def test_invalid_impact_level_raises(self, tmp_path):
        db_path = _make_db(tmp_path)
        with pytest.raises(ValueError, match="Invalid impact_level"):
            _create_project(tmp_path, db_path, name="Bad IL", impact_level="IL9", skip_scaffold=True)


# ---------------------------------------------------------------------------
# TestProjectClassification
# ---------------------------------------------------------------------------

class TestProjectClassification:
    """create_project: classification and impact level interaction."""

    def test_il6_auto_sets_secret(self, tmp_path):
        db_path = _make_db(tmp_path)
        result = _create_project(
            tmp_path, db_path, name="Secret App", impact_level="IL6", skip_scaffold=True,
        )
        assert result["classification"] == "SECRET"

    def test_il6_explicit_secret_unchanged(self, tmp_path):
        db_path = _make_db(tmp_path)
        result = _create_project(
            tmp_path, db_path, name="Explicit Secret", classification="SECRET",
            impact_level="IL6", skip_scaffold=True,
        )
        assert result["classification"] == "SECRET"

    def test_il6_fouo_stays_fouo(self, tmp_path):
        """IL6 auto-set only triggers when classification is CUI (default)."""
        db_path = _make_db(tmp_path)
        result = _create_project(
            tmp_path, db_path, name="FOUO at IL6", classification="FOUO",
            impact_level="IL6", skip_scaffold=True,
        )
        # The auto-set only fires if classification == "CUI"
        assert result["classification"] == "FOUO"

    def test_il5_keeps_cui(self, tmp_path):
        db_path = _make_db(tmp_path)
        result = _create_project(
            tmp_path, db_path, name="IL5 CUI", impact_level="IL5", skip_scaffold=True,
        )
        assert result["classification"] == "CUI"


# ---------------------------------------------------------------------------
# TestProjectAudit
# ---------------------------------------------------------------------------

class TestProjectAudit:
    """create_project: audit trail entries."""

    def test_audit_trail_created(self, tmp_path):
        db_path = _make_db(tmp_path)
        result = _create_project(tmp_path, db_path, name="Audit Me", skip_scaffold=True)
        rows = _query_audit(db_path, result["project_id"])
        assert len(rows) >= 1

    def test_audit_event_type(self, tmp_path):
        db_path = _make_db(tmp_path)
        result = _create_project(tmp_path, db_path, name="Audit Event", skip_scaffold=True)
        rows = _query_audit(db_path, result["project_id"])
        event_types = [r["event_type"] for r in rows]
        assert "project_created" in event_types

    def test_audit_contains_project_details(self, tmp_path):
        db_path = _make_db(tmp_path)
        result = _create_project(
            tmp_path, db_path, name="Audit Detail", project_type="cli", skip_scaffold=True,
        )
        rows = _query_audit(db_path, result["project_id"])
        audit_row = [r for r in rows if r["event_type"] == "project_created"][0]
        details = json.loads(audit_row["details"])
        assert details["name"] == "Audit Detail"
        assert details["type"] == "cli"


# ---------------------------------------------------------------------------
# TestProjectFilesystem
# ---------------------------------------------------------------------------

class TestProjectFilesystem:
    """create_project: filesystem side effects."""

    def test_project_directory_created(self, tmp_path):
        db_path = _make_db(tmp_path)
        result = _create_project(tmp_path, db_path, name="Dir Test", skip_scaffold=True)
        assert Path(result["directory"]).is_dir()

    def test_scaffold_creates_files(self, tmp_path):
        db_path = _make_db(tmp_path)
        result = _create_project(tmp_path, db_path, name="Scaffold Test", project_type="webapp")
        assert "scaffold" in result
        assert result["scaffold"]["files_created"] > 0
        project_dir = Path(result["directory"])
        assert (project_dir / "README.md").exists()
        assert (project_dir / ".gitignore").exists()

    def test_skip_scaffold_no_scaffold_key(self, tmp_path):
        db_path = _make_db(tmp_path)
        result = _create_project(tmp_path, db_path, name="No Scaffold", skip_scaffold=True)
        assert "scaffold" not in result

    def test_directory_path_stored_in_db(self, tmp_path):
        db_path = _make_db(tmp_path)
        result = _create_project(tmp_path, db_path, name="DB Dir", skip_scaffold=True)
        row = _query_project(db_path, result["project_id"])
        assert row["directory_path"] == result["directory"]
        assert Path(row["directory_path"]).is_dir()


# CUI // SP-CTI
