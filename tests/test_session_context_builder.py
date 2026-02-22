#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for tools/project/session_context_builder.py."""

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tools.project.session_context_builder import (
    build_session_context,
    init_from_manifest,
    _detect_project,
    _format_markdown,
    _get_compliance_summary,
    _get_dev_profile_summary,
    _get_recent_activity,
    _get_active_intake_sessions,
    _suggest_workflows,
)


# ── Helpers ──────────────────────────────────────────────────────────────

def _write_yaml(tmp_dir: str, content: dict) -> str:
    """Write a dict as YAML (or JSON fallback) to tmp_dir/icdev.yaml."""
    path = Path(tmp_dir) / "icdev.yaml"
    try:
        import yaml
        path.write_text(yaml.dump(content), encoding="utf-8")
    except ImportError:
        path.write_text(json.dumps(content), encoding="utf-8")
    return str(path)


def _create_test_db(db_path: str, project_id: str = None, directory: str = None):
    """Create a minimal test database with required tables."""
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            name TEXT,
            description TEXT DEFAULT '',
            type TEXT DEFAULT 'webapp',
            classification TEXT DEFAULT 'CUI',
            status TEXT DEFAULT 'active',
            tech_stack_backend TEXT DEFAULT '',
            tech_stack_frontend TEXT DEFAULT '',
            tech_stack_database TEXT DEFAULT '',
            directory_path TEXT,
            created_by TEXT DEFAULT 'test',
            impact_level TEXT DEFAULT 'IL4',
            cloud_environment TEXT DEFAULT 'aws_govcloud',
            target_frameworks TEXT DEFAULT '',
            ato_status TEXT DEFAULT 'none',
            accrediting_authority TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS ssp_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT,
            version TEXT,
            status TEXT,
            system_name TEXT,
            authorization_type TEXT,
            approved_by TEXT,
            approved_at TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS poam_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT,
            status TEXT DEFAULT 'open',
            severity TEXT DEFAULT 'medium'
        );
        CREATE TABLE IF NOT EXISTS stig_findings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT,
            severity TEXT,
            status TEXT DEFAULT 'Open'
        );
        CREATE TABLE IF NOT EXISTS project_controls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT,
            control_id TEXT,
            implementation_status TEXT DEFAULT 'planned'
        );
        CREATE TABLE IF NOT EXISTS audit_trail (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT,
            event_type TEXT,
            actor TEXT,
            action TEXT,
            details TEXT,
            classification TEXT DEFAULT 'CUI',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS intake_sessions (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            customer_name TEXT,
            status TEXT DEFAULT 'active',
            readiness_score REAL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS dev_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scope TEXT,
            scope_id TEXT,
            version INTEGER DEFAULT 1,
            template TEXT,
            dimensions TEXT DEFAULT '{}',
            created_by TEXT DEFAULT 'test',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS framework_applicability (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT,
            framework_id TEXT,
            status TEXT DEFAULT 'confirmed'
        );
        CREATE TABLE IF NOT EXISTS cato_evidence (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id TEXT,
            readiness_score REAL,
            assessed_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
    """)

    if project_id and directory:
        conn.execute(
            "INSERT INTO projects (id, name, type, classification, directory_path, impact_level) VALUES (?, 'Test App', 'api', 'CUI', ?, 'IL4')",
            (project_id, directory),
        )
        conn.commit()

    conn.close()
    return db_path


# ── Test detect_project ─────────────────────────────────────────────────

class TestDetectProject:
    def test_detects_yaml(self, tmp_path):
        content = {
            "version": 1,
            "project": {"name": "my-app", "type": "api", "language": "python"},
            "impact_level": "IL4",
        }
        _write_yaml(str(tmp_path), content)
        db_path = str(tmp_path / "test.db")
        _create_test_db(db_path)

        result = _detect_project(str(tmp_path), db_path)
        assert result["source"] == "yaml"
        assert result["config"]["project"]["name"] == "my-app"

    def test_detects_db(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        project_dir = str(tmp_path / "project")
        Path(project_dir).mkdir()
        _create_test_db(db_path, project_id="proj-123", directory=project_dir)

        result = _detect_project(project_dir, db_path)
        assert result["source"] == "db"
        assert result["db_record"]["id"] == "proj-123"

    def test_detects_nothing(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        _create_test_db(db_path)

        result = _detect_project(str(tmp_path), db_path)
        assert result["source"] == "none"

    def test_yaml_plus_db(self, tmp_path):
        """When both yaml and DB exist, source is 'yaml' and db_record is set."""
        content = {
            "version": 1,
            "project": {"name": "my-app", "type": "api"},
            "impact_level": "IL4",
        }
        _write_yaml(str(tmp_path), content)
        db_path = str(tmp_path / "test.db")
        _create_test_db(db_path, project_id="proj-456", directory=str(tmp_path))

        result = _detect_project(str(tmp_path), db_path)
        assert result["source"] == "yaml"
        assert result["db_record"]["id"] == "proj-456"


# ── Test build_session_context ──────────────────────────────────────────

class TestBuildSessionContext:
    def test_context_with_yaml(self, tmp_path):
        content = {
            "version": 1,
            "project": {"name": "my-app", "type": "api", "language": "python"},
            "impact_level": "IL4",
        }
        _write_yaml(str(tmp_path), content)
        db_path = str(tmp_path / "test.db")
        _create_test_db(db_path)

        ctx = build_session_context(directory=str(tmp_path), db_path=db_path)
        assert ctx["source"] == "yaml"
        assert ctx["project"]["name"] == "my-app"
        assert ctx["project"]["impact_level"] == "IL4"
        assert ctx["setup_needed"] is False

    def test_context_db_only(self, tmp_path):
        project_dir = str(tmp_path / "project")
        Path(project_dir).mkdir()
        db_path = str(tmp_path / "test.db")
        _create_test_db(db_path, project_id="proj-789", directory=project_dir)

        ctx = build_session_context(directory=project_dir, db_path=db_path)
        assert ctx["source"] == "db"
        assert ctx["project"]["name"] == "Test App"
        assert ctx["project"]["db_project_id"] == "proj-789"

    def test_context_nothing_setup_needed(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        _create_test_db(db_path)

        ctx = build_session_context(directory=str(tmp_path), db_path=db_path)
        assert ctx["setup_needed"] is True
        assert ctx["source"] == "none"
        assert any("No icdev.yaml" in w for w in ctx["warnings"])

    def test_yaml_without_db_warns_init(self, tmp_path):
        content = {
            "version": 1,
            "project": {"name": "test", "type": "cli"},
            "impact_level": "IL2",
        }
        _write_yaml(str(tmp_path), content)
        db_path = str(tmp_path / "test.db")
        _create_test_db(db_path)

        ctx = build_session_context(directory=str(tmp_path), db_path=db_path)
        assert ctx["source"] == "yaml"
        assert any("/icdev-init" in w for w in ctx["warnings"])


# ── Test compliance summary ─────────────────────────────────────────────

class TestComplianceSummary:
    def test_compliance_from_db(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        _create_test_db(db_path, project_id="proj-c1", directory=str(tmp_path))

        conn = sqlite3.connect(db_path)
        conn.execute("INSERT INTO ssp_documents (project_id, version, status) VALUES ('proj-c1', '1.0', 'current')")
        conn.execute("INSERT INTO poam_items (project_id, status) VALUES ('proj-c1', 'open')")
        conn.execute("INSERT INTO poam_items (project_id, status) VALUES ('proj-c1', 'open')")
        conn.execute("INSERT INTO poam_items (project_id, status) VALUES ('proj-c1', 'closed')")
        conn.execute("INSERT INTO stig_findings (project_id, severity, status) VALUES ('proj-c1', 'CAT1', 'Open')")
        conn.execute("INSERT INTO project_controls (project_id, control_id, implementation_status) VALUES ('proj-c1', 'AC-2', 'implemented')")
        conn.execute("INSERT INTO project_controls (project_id, control_id, implementation_status) VALUES ('proj-c1', 'AC-3', 'planned')")
        conn.execute("INSERT INTO framework_applicability (project_id, framework_id, status) VALUES ('proj-c1', 'fedramp_moderate', 'confirmed')")
        conn.execute("INSERT INTO cato_evidence (project_id, readiness_score) VALUES ('proj-c1', 0.72)")
        conn.commit()
        conn.close()

        summary = _get_compliance_summary("proj-c1", db_path)
        assert summary["ssp_version"] == "1.0"
        assert summary["ssp_status"] == "current"
        assert summary["open_poams"] == 2
        assert summary["stig_cat1"] == 1
        assert summary["controls_implemented"] == 1
        assert summary["controls_total"] == 2
        assert summary["frameworks"] == ["fedramp_moderate"]
        assert summary["cato_readiness"] == 0.72

    def test_compliance_empty(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        _create_test_db(db_path)

        summary = _get_compliance_summary("nonexistent", db_path)
        assert summary["ssp_status"] == "not_generated"
        assert summary["open_poams"] == 0


# ── Test dev profile summary ────────────────────────────────────────────

class TestDevProfileSummary:
    def test_profile_from_db(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        _create_test_db(db_path)

        conn = sqlite3.connect(db_path)
        dims = json.dumps({
            "language": {"primary": "python", "min_version": "3.11"},
            "style": {"line_length": 100, "naming_convention": "snake_case"},
            "testing": {"framework": "pytest", "min_coverage": 80},
            "security": {"crypto_standard": "FIPS 140-2"},
        })
        conn.execute(
            "INSERT INTO dev_profiles (scope, scope_id, version, dimensions) VALUES ('project', 'proj-dp1', 1, ?)",
            (dims,),
        )
        conn.commit()
        conn.close()

        summary = _get_dev_profile_summary("proj-dp1", db_path)
        assert summary["language"] == "python"
        assert summary["min_version"] == "3.11"
        assert summary["line_length"] == 100
        assert summary["test_framework"] == "pytest"
        assert summary["crypto_standard"] == "FIPS 140-2"

    def test_profile_missing(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        _create_test_db(db_path)

        summary = _get_dev_profile_summary("nonexistent", db_path)
        assert summary == {}


# ── Test recent activity ────────────────────────────────────────────────

class TestRecentActivity:
    def test_activity_from_db(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        _create_test_db(db_path)

        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO audit_trail (project_id, event_type, actor, action) VALUES ('proj-a1', 'code.commit', 'builder', 'Built auth module')"
        )
        conn.execute(
            "INSERT INTO audit_trail (project_id, event_type, actor, action) VALUES ('proj-a1', 'test_passed', 'builder', 'All tests pass')"
        )
        conn.commit()
        conn.close()

        activity = _get_recent_activity("proj-a1", limit=5, db_path=db_path)
        assert len(activity) == 2
        assert activity[0]["event_type"] in ("code.commit", "test_passed")

    def test_activity_empty(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        _create_test_db(db_path)

        activity = _get_recent_activity("nonexistent", db_path=db_path)
        assert activity == []


# ── Test intake sessions ────────────────────────────────────────────────

class TestIntakeSessions:
    def test_active_sessions(self, tmp_path):
        db_path = str(tmp_path / "test.db")
        _create_test_db(db_path)

        conn = sqlite3.connect(db_path)
        conn.execute(
            "INSERT INTO intake_sessions (id, project_id, customer_name, status, readiness_score) VALUES ('sess-1', 'proj-i1', 'Jane Smith', 'active', 0.45)"
        )
        conn.execute(
            "INSERT INTO intake_sessions (id, project_id, customer_name, status) VALUES ('sess-2', 'proj-i1', 'Bob Jones', 'completed')"
        )
        conn.commit()
        conn.close()

        sessions = _get_active_intake_sessions("proj-i1", db_path)
        assert len(sessions) == 1
        assert sessions[0]["customer_name"] == "Jane Smith"
        assert sessions[0]["readiness_score"] == 0.45


# ── Test suggest workflows ──────────────────────────────────────────────

class TestSuggestWorkflows:
    def test_setup_needed(self):
        ctx = {"setup_needed": True, "project": {}, "compliance": {}, "intake_sessions": [], "recent_activity": []}
        suggestions = _suggest_workflows(ctx)
        assert len(suggestions) == 1
        assert suggestions[0]["command"] == "/icdev-init"

    def test_no_ssp(self):
        ctx = {
            "setup_needed": False,
            "source": "db",
            "project": {"db_project_id": "proj-1"},
            "compliance": {"ssp_status": "not_generated", "open_poams": 0, "stig_cat1": 0},
            "intake_sessions": [],
            "recent_activity": [],
        }
        suggestions = _suggest_workflows(ctx)
        commands = [s["command"] for s in suggestions]
        assert "/icdev-comply" in commands

    def test_open_poams(self):
        ctx = {
            "setup_needed": False,
            "source": "db",
            "project": {"db_project_id": "proj-1"},
            "compliance": {"ssp_status": "current", "open_poams": 3, "stig_cat1": 0},
            "intake_sessions": [],
            "recent_activity": [],
        }
        suggestions = _suggest_workflows(ctx)
        assert any("3 open POAM" in s["reason"] for s in suggestions)

    def test_cat1_stig(self):
        ctx = {
            "setup_needed": False,
            "source": "db",
            "project": {"db_project_id": "proj-1"},
            "compliance": {"ssp_status": "current", "open_poams": 0, "stig_cat1": 2},
            "intake_sessions": [],
            "recent_activity": [],
        }
        suggestions = _suggest_workflows(ctx)
        assert any("/icdev-secure" in s["command"] for s in suggestions)

    def test_active_intake(self):
        ctx = {
            "setup_needed": False,
            "source": "db",
            "project": {"db_project_id": "proj-1"},
            "compliance": {"ssp_status": "current", "open_poams": 0, "stig_cat1": 0},
            "intake_sessions": [{"customer_name": "Jane", "status": "active"}],
            "recent_activity": [],
        }
        suggestions = _suggest_workflows(ctx)
        assert any("/icdev-intake" in s["command"] for s in suggestions)


# ── Test format_markdown ────────────────────────────────────────────────

class TestFormatMarkdown:
    def test_setup_needed_format(self):
        ctx = {
            "setup_needed": True,
            "project": {},
            "compliance": {},
            "dev_profile": {},
            "recent_activity": [],
            "intake_sessions": [],
            "recommended_workflows": [],
            "warnings": ["No icdev.yaml found"],
        }
        md = _format_markdown(ctx)
        assert "No ICDEV project detected" in md
        assert "icdev.yaml" in md

    def test_full_context_format(self):
        ctx = {
            "setup_needed": False,
            "source": "yaml",
            "project": {
                "id": "proj-test",
                "name": "Test App",
                "type": "api",
                "language": "python",
                "impact_level": "IL4",
                "classification": "CUI",
                "ato_status": "active",
            },
            "compliance": {
                "frameworks": ["fedramp_moderate"],
                "ssp_version": "1.2",
                "ssp_status": "current",
                "open_poams": 3,
                "stig_cat1": 0,
                "stig_cat2": 2,
                "controls_implemented": 150,
                "controls_total": 200,
                "cato_readiness": 0.72,
            },
            "dev_profile": {
                "language": "python",
                "min_version": "3.11",
                "line_length": 100,
                "naming_convention": "snake_case",
                "test_framework": "pytest",
                "min_coverage": 80,
                "crypto_standard": "FIPS 140-2",
            },
            "recent_activity": [
                {"event_type": "code.commit", "actor": "builder", "action": "Built auth module", "timestamp": "2026-02-21T10:00:00"},
            ],
            "intake_sessions": [],
            "recommended_workflows": [
                {"command": "/icdev-comply", "reason": "3 open POAM items"},
            ],
            "warnings": [],
        }
        md = _format_markdown(ctx)
        assert "### Project: Test App" in md
        assert "IL4" in md
        assert "CUI" in md
        assert "fedramp_moderate" in md
        assert "150/200" in md
        assert "python" in md
        assert "100-char lines" in md
        assert "/icdev-comply" in md
        assert "code.commit" in md


# ── Test init_from_manifest ─────────────────────────────────────────────

class TestInitFromManifest:
    def test_init_creates_project(self, tmp_path):
        content = {
            "version": 1,
            "project": {"name": "init-test", "type": "api", "language": "go"},
            "impact_level": "IL5",
            "profile": {"template": "dod_baseline"},
        }
        _write_yaml(str(tmp_path), content)
        db_path = str(tmp_path / "test.db")
        _create_test_db(db_path)

        result = init_from_manifest(directory=str(tmp_path), db_path=db_path)
        assert result["created"] is True
        assert result["project_id"] is not None
        assert result["dev_profile_created"] is True
        assert result["errors"] == []

        # Verify DB record
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM projects WHERE id = ?", (result["project_id"],)).fetchone()
        assert row is not None
        assert row["name"] == "init-test"
        assert row["impact_level"] == "IL5"
        conn.close()

    def test_init_already_registered(self, tmp_path):
        content = {
            "version": 1,
            "project": {"name": "existing", "type": "api"},
            "impact_level": "IL4",
        }
        _write_yaml(str(tmp_path), content)
        db_path = str(tmp_path / "test.db")
        _create_test_db(db_path, project_id="proj-existing", directory=str(tmp_path))

        result = init_from_manifest(directory=str(tmp_path), db_path=db_path)
        assert result["created"] is False
        assert result["project_id"] == "proj-existing"
        assert any("already registered" in e for e in result["errors"])

    def test_init_invalid_manifest(self, tmp_path):
        # No icdev.yaml
        db_path = str(tmp_path / "test.db")
        _create_test_db(db_path)

        result = init_from_manifest(directory=str(tmp_path), db_path=db_path)
        assert result["created"] is False
        assert len(result["errors"]) > 0

    def test_init_no_profile_template(self, tmp_path):
        content = {
            "version": 1,
            "project": {"name": "no-profile", "type": "cli"},
            "impact_level": "IL2",
        }
        _write_yaml(str(tmp_path), content)
        db_path = str(tmp_path / "test.db")
        _create_test_db(db_path)

        result = init_from_manifest(directory=str(tmp_path), db_path=db_path)
        assert result["created"] is True
        assert result["dev_profile_created"] is False
