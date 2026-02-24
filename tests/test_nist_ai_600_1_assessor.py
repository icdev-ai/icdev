#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Tests for NIST AI 600-1 GenAI assessor (Phase 48).

Coverage: framework metadata, base class inheritance, catalog loading,
automated checks (confabulation, provenance, prompt injection, AI BOM,
fairness), gate evaluation, CLI entry point.
"""

import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Ensure project root is importable
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.compliance.nist_ai_600_1_assessor import NISTAI6001Assessor


# ============================================================
# Fixtures
# ============================================================

_MOCK_DB_SCHEMA = """
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
CREATE TABLE confabulation_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT,
    check_type TEXT,
    input_hash TEXT,
    output_hash TEXT,
    confidence_score REAL,
    grounding_sources TEXT,
    is_confabulated INTEGER DEFAULT 0,
    details TEXT,
    agent_id TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE prov_entities (
    id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    label TEXT,
    content_hash TEXT,
    content TEXT,
    attributes TEXT,
    trace_id TEXT,
    span_id TEXT,
    agent_id TEXT,
    project_id TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE prompt_injection_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT,
    source TEXT,
    input_text_hash TEXT,
    detection_category TEXT,
    confidence REAL,
    is_injection INTEGER DEFAULT 0,
    action_taken TEXT,
    agent_id TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE atlas_red_team_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT,
    technique_id TEXT,
    technique_name TEXT,
    status TEXT DEFAULT 'not_tested',
    result TEXT,
    evidence TEXT,
    risk_level TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE ai_bom (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT,
    component_name TEXT,
    component_type TEXT,
    version TEXT,
    model_family TEXT,
    provider TEXT,
    license TEXT,
    training_data_summary TEXT,
    risk_classification TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE fairness_assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT,
    model_id TEXT,
    assessment_type TEXT,
    protected_attributes TEXT,
    metrics TEXT,
    disparate_impact_ratio REAL,
    equalized_odds_diff REAL,
    status TEXT DEFAULT 'not_assessed',
    findings TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
INSERT INTO projects (id, name) VALUES ('proj-test', 'Test Project');
"""


@pytest.fixture
def mock_db_path(tmp_path):
    """Create a file-backed temp DB with required tables.

    Returns the db path so each _get_connection call can open a fresh
    connection (the source code closes connections in finally blocks).
    """
    db_path = tmp_path / "mock_nist_ai_600_1.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript(_MOCK_DB_SCHEMA)
    conn.commit()
    conn.close()
    return db_path


# ============================================================
# Import & Metadata
# ============================================================

class TestImportAndMetadata:
    def test_import(self):
        """NISTAI6001Assessor can be imported."""
        assert NISTAI6001Assessor is not None

    def test_framework_id(self):
        """Verify FRAMEWORK_ID constant."""
        assert NISTAI6001Assessor.FRAMEWORK_ID == "nist_ai_600_1"

    def test_framework_name(self):
        """Verify FRAMEWORK_NAME contains identifying text."""
        assert "NIST" in NISTAI6001Assessor.FRAMEWORK_NAME or "600-1" in NISTAI6001Assessor.FRAMEWORK_NAME

    def test_table_name(self):
        """Verify TABLE_NAME constant."""
        assert NISTAI6001Assessor.TABLE_NAME == "nist_ai_600_1_assessments"

    def test_catalog_filename(self):
        """Verify CATALOG_FILENAME constant."""
        assert NISTAI6001Assessor.CATALOG_FILENAME == "nist_ai_600_1_genai.json"

    def test_inherits_base_assessor(self):
        """NISTAI6001Assessor inherits from BaseAssessor (ABC).

        Verify via MRO class names to avoid dual-import identity mismatch.
        """
        base_names = [cls.__name__ for cls in NISTAI6001Assessor.__mro__]
        assert "BaseAssessor" in base_names


# ============================================================
# Catalog Loading
# ============================================================

class TestCatalogLoading:
    def test_catalog_file_exists(self):
        """Catalog JSON file exists in context/compliance/."""
        catalog_path = BASE_DIR / "context" / "compliance" / "nist_ai_600_1_genai.json"
        assert catalog_path.exists(), f"Catalog not found: {catalog_path}"

    def test_catalog_valid_json(self):
        """Catalog file is valid JSON with requirements/controls."""
        catalog_path = BASE_DIR / "context" / "compliance" / "nist_ai_600_1_genai.json"
        with open(catalog_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        has_items = data.get("requirements") or data.get("controls") or data.get("criteria")
        assert has_items, "Catalog must have 'requirements', 'controls', or 'criteria' key"

    def test_catalog_requirement_structure(self):
        """Each requirement has id, title, description."""
        catalog_path = BASE_DIR / "context" / "compliance" / "nist_ai_600_1_genai.json"
        with open(catalog_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        items = data.get("requirements") or data.get("controls") or data.get("criteria") or []
        assert len(items) > 0, "Catalog must contain at least one requirement"
        for item in items:
            assert "id" in item, f"Requirement missing 'id': {item}"
            assert "title" in item, f"Requirement missing 'title': {item}"

    def test_load_catalog_method(self, mock_db_path):
        """Assessor load_catalog returns a non-empty list."""
        assessor = NISTAI6001Assessor(db_path=mock_db_path)
        catalog = assessor.load_catalog()
        assert isinstance(catalog, list)
        assert len(catalog) > 0


# ============================================================
# Automated Checks — Assessment
# ============================================================

class TestAssessment:
    def test_automated_checks_returns_dict(self, mock_db_path):
        """get_automated_checks returns a dict with string values."""
        assessor = NISTAI6001Assessor(db_path=mock_db_path)
        project = {"id": "proj-test"}
        results = assessor.get_automated_checks(project, project_dir=None)

        assert isinstance(results, dict)
        for key, value in results.items():
            assert isinstance(key, str), f"Key {key} is not a string"
            assert isinstance(value, str), f"Value for {key} is not a string"

    def test_check_returns_valid_status(self, mock_db_path):
        """All check statuses are from the valid set."""
        assessor = NISTAI6001Assessor(db_path=mock_db_path)
        project = {"id": "proj-test"}
        results = assessor.get_automated_checks(project, project_dir=None)
        valid = {"satisfied", "partially_satisfied", "not_satisfied", "not_assessed", "not_applicable", "risk_accepted"}
        for check_id, status in results.items():
            assert status in valid, f"{check_id} has invalid status: {status}"

    def test_confabulation_check_not_satisfied_empty(self, mock_db_path):
        """Empty confabulation_checks -> confabulation check absent from results.

        When tables are empty the assessor does not add satisfied entries for
        most checks.  The confabulation key (GAI-1-1) should NOT appear as
        satisfied in the result dict.
        """
        assessor = NISTAI6001Assessor(db_path=mock_db_path)
        project = {"id": "proj-test"}
        results = assessor.get_automated_checks(project, project_dir=None)
        # Confabulation key must not be satisfied on an empty DB
        assert results.get("GAI-1-1") != "satisfied", \
            "GAI-1-1 should not be satisfied with empty confabulation_checks"
        assert results.get("GAI-10-1") != "satisfied", \
            "GAI-10-1 should not be satisfied with empty ai_bom"

    def test_confabulation_check_satisfied_with_data(self, mock_db_path):
        """Populated confabulation_checks -> confabulation check improves."""
        conn = sqlite3.connect(str(mock_db_path))
        conn.execute(
            "INSERT INTO confabulation_checks (project_id, check_type, confidence_score, is_confabulated) "
            "VALUES (?, ?, ?, ?)",
            ("proj-test", "factual_grounding", 0.95, 0),
        )
        conn.commit()
        conn.close()

        assessor = NISTAI6001Assessor(db_path=mock_db_path)
        project = {"id": "proj-test"}
        results = assessor.get_automated_checks(project, project_dir=None)
        satisfied_count = sum(1 for s in results.values() if s in ("satisfied", "partially_satisfied"))
        assert satisfied_count > 0, "Confabulation check data should satisfy at least one check"

    def test_provenance_check_satisfied_with_data(self, mock_db_path):
        """Populated prov_entities -> provenance check improves."""
        conn = sqlite3.connect(str(mock_db_path))
        conn.execute(
            "INSERT INTO prov_entities (id, entity_type, label, project_id) "
            "VALUES (?, ?, ?, ?)",
            ("ent-1", "prompt", "User query for doc classification", "proj-test"),
        )
        conn.commit()
        conn.close()

        assessor = NISTAI6001Assessor(db_path=mock_db_path)
        project = {"id": "proj-test"}
        results = assessor.get_automated_checks(project, project_dir=None)
        satisfied_count = sum(1 for s in results.values() if s in ("satisfied", "partially_satisfied"))
        assert satisfied_count > 0, "Provenance data should satisfy at least one check"

    def test_prompt_injection_check_satisfied_with_data(self, mock_db_path):
        """Populated prompt_injection_log -> injection check improves."""
        conn = sqlite3.connect(str(mock_db_path))
        conn.execute(
            "INSERT INTO prompt_injection_log (project_id, source, detection_category, "
            "confidence, is_injection, action_taken) VALUES (?, ?, ?, ?, ?, ?)",
            ("proj-test", "user_input", "role_hijacking", 0.92, 1, "blocked"),
        )
        conn.commit()
        conn.close()

        assessor = NISTAI6001Assessor(db_path=mock_db_path)
        project = {"id": "proj-test"}
        results = assessor.get_automated_checks(project, project_dir=None)
        satisfied_count = sum(1 for s in results.values() if s in ("satisfied", "partially_satisfied"))
        assert satisfied_count > 0, "Injection log data should satisfy at least one check"

    def test_ai_bom_check_satisfied_with_data(self, mock_db_path):
        """Populated ai_bom -> AI BOM check improves."""
        conn = sqlite3.connect(str(mock_db_path))
        conn.execute(
            "INSERT INTO ai_bom (project_id, component_name, component_type, version, provider) "
            "VALUES (?, ?, ?, ?, ?)",
            ("proj-test", "claude-opus-4", "foundation_model", "4.0", "anthropic"),
        )
        conn.commit()
        conn.close()

        assessor = NISTAI6001Assessor(db_path=mock_db_path)
        project = {"id": "proj-test"}
        results = assessor.get_automated_checks(project, project_dir=None)
        satisfied_count = sum(1 for s in results.values() if s in ("satisfied", "partially_satisfied"))
        assert satisfied_count > 0, "AI BOM data should satisfy at least one check"

    def test_assess_runs_without_error(self, mock_db_path):
        """Full assessment should run without raising exceptions."""
        assessor = NISTAI6001Assessor(db_path=mock_db_path)
        result = assessor.assess("proj-test")
        assert "framework_id" in result
        assert result["framework_id"] == "nist_ai_600_1"
        assert "total_requirements" in result
        assert "status_counts" in result
        assert "coverage_pct" in result


# ============================================================
# Gate Evaluation
# ============================================================

class TestGateEvaluation:
    def test_gate_not_assessed_empty(self, mock_db_path):
        """Gate returns not_assessed when no assessment has been run."""
        assessor = NISTAI6001Assessor(db_path=mock_db_path)
        gate = assessor.evaluate_gate("proj-test")
        assert gate["pass"] is False
        assert gate["gate_status"] == "not_assessed"
        assert "blocking_issues" in gate

    def test_gate_after_assessment(self, mock_db_path):
        """Gate returns structured result after running assessment."""
        assessor = NISTAI6001Assessor(db_path=mock_db_path)
        assessor.assess("proj-test")
        gate = assessor.evaluate_gate("proj-test")
        assert "pass" in gate
        assert isinstance(gate["pass"], bool)
        assert "total" in gate
        assert "satisfied" in gate
        assert "coverage_pct" in gate
        assert "blocking_issues" in gate
        assert isinstance(gate["blocking_issues"], list)

    def test_gate_returns_framework_info(self, mock_db_path):
        """Gate result includes framework identification."""
        assessor = NISTAI6001Assessor(db_path=mock_db_path)
        assessor.assess("proj-test")
        gate = assessor.evaluate_gate("proj-test")
        assert "framework" in gate or "framework_id" in gate

    def test_cli_entry_point_exists(self):
        """Assessor has run_cli method from BaseAssessor."""
        assessor = NISTAI6001Assessor()
        assert hasattr(assessor, "run_cli")
        assert callable(assessor.run_cli)
