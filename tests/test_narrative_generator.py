# CUI // SP-CTI
"""Tests for the ICDEV Narrative Generator (tools/compliance/narrative_generator.py).

Validates template rendering with evidence data, evidence gathering from DB,
DB storage of narratives, and generate_for_project batch generation.
"""

import sqlite3
import sys
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:
    from tools.compliance.narrative_generator import (
        ControlEvidence,
        NarrativeGenerator,
        NarrativeResult,
        DEFAULT_TEMPLATE,
    )
except ImportError:
    pytestmark = pytest.mark.skip("tools.compliance.narrative_generator not available")

try:
    from jinja2 import Template as Jinja2Template
    HAS_JINJA2 = True
except ImportError:
    HAS_JINJA2 = False


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

NARRATIVE_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'webapp',
    classification TEXT DEFAULT 'CUI',
    status TEXT DEFAULT 'active',
    directory_path TEXT DEFAULT '/tmp',
    impact_level TEXT DEFAULT 'IL5'
);

CREATE TABLE IF NOT EXISTS compliance_controls (
    id TEXT PRIMARY KEY,
    title TEXT,
    description TEXT
);

CREATE TABLE IF NOT EXISTS project_controls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    control_id TEXT NOT NULL,
    implementation_status TEXT DEFAULT 'planned',
    implementation_description TEXT,
    responsible_role TEXT,
    evidence_path TEXT,
    last_assessed TEXT,
    UNIQUE(project_id, control_id)
);

CREATE TABLE IF NOT EXISTS fedramp_assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT,
    control_id TEXT,
    baseline TEXT,
    status TEXT,
    evidence_description TEXT,
    notes TEXT,
    assessment_date TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS cmmc_assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT,
    nist_171_id TEXT,
    level INTEGER,
    practice_id TEXT,
    domain TEXT,
    status TEXT,
    evidence_description TEXT,
    assessment_date TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS audit_trail (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT,
    project_id TEXT,
    details TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS control_narratives (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    control_id TEXT NOT NULL,
    narrative_text TEXT NOT NULL,
    generation_method TEXT DEFAULT 'template',
    generated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(project_id, control_id)
);
"""


@pytest.fixture
def narrative_db(tmp_path):
    """Create a temporary database with narrative-related tables and seed data."""
    db_path = tmp_path / "icdev_narrative.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(NARRATIVE_SCHEMA)

    # Seed project
    conn.execute(
        "INSERT INTO projects (id, name, type, status, directory_path) "
        "VALUES (?, ?, ?, ?, ?)",
        ("proj-narr-1", "Narrative Test Project", "webapp", "active", "/tmp/narr"),
    )

    # Seed compliance controls
    conn.execute(
        "INSERT INTO compliance_controls (id, title, description) VALUES (?, ?, ?)",
        ("AC-2", "Account Management", "Manage system accounts."),
    )
    conn.execute(
        "INSERT INTO compliance_controls (id, title, description) VALUES (?, ?, ?)",
        ("AU-3", "Content of Audit Records", "Ensure audit records contain..."),
    )

    # Seed project_controls
    conn.execute(
        "INSERT INTO project_controls "
        "(project_id, control_id, implementation_status, "
        "implementation_description, responsible_role, evidence_path, last_assessed) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "proj-narr-1", "AC-2", "implemented",
            "IdP integration for account provisioning",
            "System Administrator",
            "/evidence/ac-2/audit_logs.pdf",
            "2026-01-15",
        ),
    )
    conn.execute(
        "INSERT INTO project_controls "
        "(project_id, control_id, implementation_status) "
        "VALUES (?, ?, ?)",
        ("proj-narr-1", "AU-3", "planned"),
    )

    # Seed audit trail
    conn.execute(
        "INSERT INTO audit_trail (event_type, actor, details, project_id) "
        "VALUES (?, ?, ?, ?)",
        ("compliance_check", "icdev-compliance", "AC-2 assessed", "proj-narr-1"),
    )

    conn.commit()
    conn.close()
    return db_path


# ---------------------------------------------------------------------------
# Template Rendering Tests
# ---------------------------------------------------------------------------

class TestTemplateRendering:
    """Verify Jinja2 template rendering with evidence data."""

    @pytest.mark.skipif(not HAS_JINJA2, reason="jinja2 not installed")
    def test_render_produces_markdown(self, narrative_db):
        gen = NarrativeGenerator(db_path=str(narrative_db))
        result = gen.render_narrative("proj-narr-1", "AC-2")
        assert "AC-2" in result
        assert "Account Management" in result

    @pytest.mark.skipif(not HAS_JINJA2, reason="jinja2 not installed")
    def test_render_includes_status(self, narrative_db):
        gen = NarrativeGenerator(db_path=str(narrative_db))
        result = gen.render_narrative("proj-narr-1", "AC-2")
        assert "implemented" in result.lower() or "Implementation Status" in result

    @pytest.mark.skipif(not HAS_JINJA2, reason="jinja2 not installed")
    def test_render_includes_responsible_role(self, narrative_db):
        gen = NarrativeGenerator(db_path=str(narrative_db))
        result = gen.render_narrative("proj-narr-1", "AC-2")
        assert "System Administrator" in result

    @pytest.mark.skipif(not HAS_JINJA2, reason="jinja2 not installed")
    def test_render_includes_evidence_path(self, narrative_db):
        gen = NarrativeGenerator(db_path=str(narrative_db))
        result = gen.render_narrative("proj-narr-1", "AC-2")
        assert "/evidence/ac-2/" in result


# ---------------------------------------------------------------------------
# Evidence Gathering Tests
# ---------------------------------------------------------------------------

class TestEvidenceGathering:
    """Verify gather_evidence queries multiple tables."""

    def test_gather_returns_control_evidence(self, narrative_db):
        gen = NarrativeGenerator(db_path=str(narrative_db))
        ev = gen.gather_evidence("proj-narr-1", "AC-2")
        assert isinstance(ev, ControlEvidence)
        assert ev.control_id == "AC-2"

    def test_gather_populates_title(self, narrative_db):
        gen = NarrativeGenerator(db_path=str(narrative_db))
        ev = gen.gather_evidence("proj-narr-1", "AC-2")
        assert ev.control_title == "Account Management"

    def test_gather_populates_implementation_status(self, narrative_db):
        gen = NarrativeGenerator(db_path=str(narrative_db))
        ev = gen.gather_evidence("proj-narr-1", "AC-2")
        assert ev.implementation_status == "implemented"

    def test_gather_populates_audit_events(self, narrative_db):
        gen = NarrativeGenerator(db_path=str(narrative_db))
        ev = gen.gather_evidence("proj-narr-1", "AC-2")
        assert isinstance(ev.audit_events, list)
        # Should have at least the seeded audit event
        assert len(ev.audit_events) >= 1

    def test_gather_raises_for_unknown_project(self, narrative_db):
        gen = NarrativeGenerator(db_path=str(narrative_db))
        with pytest.raises(ValueError, match="not found"):
            gen.gather_evidence("nonexistent-project", "AC-2")


# ---------------------------------------------------------------------------
# Storage Tests
# ---------------------------------------------------------------------------

class TestNarrativeStorage:
    """Verify narratives are stored in the control_narratives table."""

    def test_store_narrative(self, narrative_db):
        gen = NarrativeGenerator(db_path=str(narrative_db))
        gen.store_narrative("proj-narr-1", "AC-2", "Test narrative text", method="template")
        conn = sqlite3.connect(str(narrative_db))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM control_narratives WHERE project_id=? AND control_id=?",
            ("proj-narr-1", "AC-2"),
        ).fetchone()
        conn.close()
        assert row is not None
        assert row["narrative_text"] == "Test narrative text"
        assert row["generation_method"] == "template"

    def test_store_narrative_upsert(self, narrative_db):
        gen = NarrativeGenerator(db_path=str(narrative_db))
        gen.store_narrative("proj-narr-1", "AC-2", "Version 1")
        gen.store_narrative("proj-narr-1", "AC-2", "Version 2")
        conn = sqlite3.connect(str(narrative_db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM control_narratives WHERE project_id=? AND control_id=?",
            ("proj-narr-1", "AC-2"),
        ).fetchall()
        conn.close()
        assert len(rows) == 1
        assert rows[0]["narrative_text"] == "Version 2"


# ---------------------------------------------------------------------------
# Batch Generation Tests
# ---------------------------------------------------------------------------

class TestGenerateForProject:
    """Verify generate_for_project processes all controls."""

    @pytest.mark.skipif(not HAS_JINJA2, reason="jinja2 not installed")
    def test_generate_all_controls(self, narrative_db):
        gen = NarrativeGenerator(db_path=str(narrative_db))
        results = gen.generate_for_project("proj-narr-1")
        assert len(results) == 2  # AC-2 and AU-3
        assert all(isinstance(r, NarrativeResult) for r in results)

    @pytest.mark.skipif(not HAS_JINJA2, reason="jinja2 not installed")
    def test_generate_specific_control(self, narrative_db):
        gen = NarrativeGenerator(db_path=str(narrative_db))
        results = gen.generate_for_project("proj-narr-1", control_ids=["AC-2"])
        assert len(results) == 1
        assert results[0].control_id == "AC-2"

    @pytest.mark.skipif(not HAS_JINJA2, reason="jinja2 not installed")
    def test_generate_marks_stored(self, narrative_db):
        gen = NarrativeGenerator(db_path=str(narrative_db))
        results = gen.generate_for_project("proj-narr-1", control_ids=["AC-2"])
        assert results[0].stored is True

    @pytest.mark.skipif(not HAS_JINJA2, reason="jinja2 not installed")
    def test_generate_returns_empty_for_no_controls(self, narrative_db):
        """Project with no controls should return empty list."""
        conn = sqlite3.connect(str(narrative_db))
        conn.execute(
            "INSERT INTO projects (id, name, type, status, directory_path) "
            "VALUES (?, ?, ?, ?, ?)",
            ("proj-empty", "Empty Project", "webapp", "active", "/tmp/empty"),
        )
        conn.commit()
        conn.close()
        gen = NarrativeGenerator(db_path=str(narrative_db))
        results = gen.generate_for_project("proj-empty")
        assert results == []

    def test_constructor_raises_for_missing_db(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            NarrativeGenerator(db_path=str(tmp_path / "nonexistent.db"))
