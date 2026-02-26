#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Tests for GAO AI Accountability assessor (Phase 48).

Coverage: framework metadata, base class inheritance, catalog loading,
automated checks (audit trail, agents, telemetry, XAI, provenance),
gate evaluation, CLI entry point.
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

from tools.compliance.gao_ai_assessor import GAOAIAssessor


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
CREATE TABLE agents (
    id TEXT PRIMARY KEY,
    name TEXT,
    agent_type TEXT,
    port INTEGER,
    status TEXT DEFAULT 'inactive',
    capabilities TEXT,
    last_heartbeat TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE ai_telemetry (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT,
    agent_id TEXT,
    event_type TEXT,
    model_id TEXT,
    prompt_hash TEXT,
    response_hash TEXT,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    classification TEXT DEFAULT 'CUI',
    logged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE xai_assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    assessment_date TEXT DEFAULT (datetime('now')),
    assessor TEXT DEFAULT 'icdev-compliance-engine',
    requirement_id TEXT NOT NULL,
    requirement_title TEXT,
    family TEXT,
    status TEXT DEFAULT 'not_assessed',
    evidence_description TEXT,
    evidence_path TEXT,
    automation_result TEXT,
    notes TEXT,
    nist_800_53_crosswalk TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(project_id, requirement_id)
);
CREATE TABLE shap_attributions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    shapley_value REAL NOT NULL,
    coalition_size INTEGER,
    confidence_low REAL,
    confidence_high REAL,
    outcome_metric TEXT DEFAULT 'success',
    outcome_value REAL,
    analysis_params TEXT,
    agent_id TEXT,
    project_id TEXT,
    classification TEXT DEFAULT 'CUI',
    analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
INSERT INTO projects (id, name) VALUES ('proj-test', 'Test Project');
"""


@pytest.fixture
def mock_db_path(tmp_path):
    """Create a file-backed temp DB with required tables.

    Returns the db path so each _get_connection call can open a fresh
    connection (the source code closes connections in finally blocks).
    """
    db_path = tmp_path / "mock_gao_ai.db"
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
        """GAOAIAssessor can be imported."""
        assert GAOAIAssessor is not None

    def test_framework_id(self):
        """Verify FRAMEWORK_ID constant."""
        assert GAOAIAssessor.FRAMEWORK_ID == "gao_ai"

    def test_framework_name(self):
        """Verify FRAMEWORK_NAME contains identifying text."""
        assert "GAO" in GAOAIAssessor.FRAMEWORK_NAME or "Accountability" in GAOAIAssessor.FRAMEWORK_NAME

    def test_table_name(self):
        """Verify TABLE_NAME constant."""
        assert GAOAIAssessor.TABLE_NAME == "gao_ai_assessments"

    def test_catalog_filename(self):
        """Verify CATALOG_FILENAME constant."""
        assert GAOAIAssessor.CATALOG_FILENAME == "gao_ai_accountability.json"

    def test_inherits_base_assessor(self):
        """GAOAIAssessor inherits from BaseAssessor (ABC).

        Verify via MRO class names to avoid dual-import identity mismatch.
        """
        base_names = [cls.__name__ for cls in GAOAIAssessor.__mro__]
        assert "BaseAssessor" in base_names


# ============================================================
# Catalog Loading
# ============================================================

class TestCatalogLoading:
    def test_catalog_file_exists(self):
        """Catalog JSON file exists in context/compliance/."""
        catalog_path = BASE_DIR / "context" / "compliance" / "gao_ai_accountability.json"
        assert catalog_path.exists(), f"Catalog not found: {catalog_path}"

    def test_catalog_valid_json(self):
        """Catalog file is valid JSON with requirements/controls."""
        catalog_path = BASE_DIR / "context" / "compliance" / "gao_ai_accountability.json"
        with open(catalog_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        has_items = data.get("requirements") or data.get("controls") or data.get("criteria")
        assert has_items, "Catalog must have 'requirements', 'controls', or 'criteria' key"

    def test_catalog_requirement_structure(self):
        """Each requirement has id, title, description."""
        catalog_path = BASE_DIR / "context" / "compliance" / "gao_ai_accountability.json"
        with open(catalog_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        items = data.get("requirements") or data.get("controls") or data.get("criteria") or []
        assert len(items) > 0, "Catalog must contain at least one requirement"
        for item in items:
            assert "id" in item, f"Requirement missing 'id': {item}"
            assert "title" in item, f"Requirement missing 'title': {item}"

    def test_load_catalog_method(self, mock_db_path):
        """Assessor load_catalog returns a non-empty list."""
        assessor = GAOAIAssessor(db_path=mock_db_path)
        catalog = assessor.load_catalog()
        assert isinstance(catalog, list)
        assert len(catalog) > 0


# ============================================================
# Automated Checks — Assessment
# ============================================================

class TestAssessment:
    def test_automated_checks_returns_dict(self, mock_db_path):
        """get_automated_checks returns a dict with string values."""
        assessor = GAOAIAssessor(db_path=mock_db_path)
        project = {"id": "proj-test"}
        results = assessor.get_automated_checks(project, project_dir=None)

        assert isinstance(results, dict)
        for key, value in results.items():
            assert isinstance(key, str), f"Key {key} is not a string"
            assert isinstance(value, str), f"Value for {key} is not a string"

    def test_check_returns_valid_status(self, mock_db_path):
        """All check statuses are from the valid set."""
        assessor = GAOAIAssessor(db_path=mock_db_path)
        project = {"id": "proj-test"}
        results = assessor.get_automated_checks(project, project_dir=None)
        valid = {"satisfied", "partially_satisfied", "not_satisfied", "not_assessed", "not_applicable", "risk_accepted"}
        for check_id, status in results.items():
            assert status in valid, f"{check_id} has invalid status: {status}"

    def test_empty_db_mostly_not_satisfied(self, mock_db_path):
        """Empty DB should not satisfy data-dependent checks.

        When tables are empty the assessor does not add satisfied entries for
        most checks.  The data-dependent keys (GAO-PERF-4, GAO-PERF-1,
        GAO-DATA-2) should NOT appear as satisfied in the result dict.
        """
        assessor = GAOAIAssessor(db_path=mock_db_path)
        project = {"id": "proj-test"}
        results = assessor.get_automated_checks(project, project_dir=None)
        # Data-dependent keys must not be satisfied on an empty DB
        assert results.get("GAO-PERF-4") != "satisfied", \
            "GAO-PERF-4 should not be satisfied with empty audit_trail"
        assert results.get("GAO-PERF-1") != "satisfied", \
            "GAO-PERF-1 should not be satisfied with empty ai_telemetry"
        assert results.get("GAO-DATA-2") != "satisfied", \
            "GAO-DATA-2 should not be satisfied with empty provenance data"

    def test_audit_trail_check_satisfied_with_data(self, mock_db_path):
        """Populated audit_trail -> audit trail check improves."""
        conn = sqlite3.connect(str(mock_db_path))
        conn.execute(
            "INSERT INTO audit_trail (project_id, event_type, actor, action, details) "
            "VALUES (?, ?, ?, ?, ?)",
            ("proj-test", "code.commit", "builder-agent", "Committed module X", '{"files": 3}'),
        )
        conn.commit()
        conn.close()

        assessor = GAOAIAssessor(db_path=mock_db_path)
        project = {"id": "proj-test"}
        results = assessor.get_automated_checks(project, project_dir=None)
        satisfied_count = sum(1 for s in results.values() if s in ("satisfied", "partially_satisfied"))
        assert satisfied_count > 0, "Audit trail data should satisfy at least one check"

    def test_agents_check_satisfied_with_data(self, mock_db_path, tmp_path):
        """Agent governance file -> GAO-GOV-1 check satisfied."""
        # GAO-GOV-1 checks for agent/authority keywords in YAML files
        gov_yaml = tmp_path / "agent_authority.yaml"
        gov_yaml.write_text("agent:\n  authority:\n    security: hard_veto\n")

        assessor = GAOAIAssessor(db_path=mock_db_path)
        project = {"id": "proj-test"}
        results = assessor.get_automated_checks(project, project_dir=str(tmp_path))
        assert results.get("GAO-GOV-1") == "satisfied", \
            "Agent governance YAML should satisfy GAO-GOV-1"

    def test_telemetry_check_satisfied_with_data(self, mock_db_path):
        """Populated ai_telemetry -> telemetry check improves."""
        conn = sqlite3.connect(str(mock_db_path))
        conn.execute(
            "INSERT INTO ai_telemetry (project_id, agent_id, event_type, model_id) "
            "VALUES (?, ?, ?, ?)",
            ("proj-test", "builder-agent", "llm_call", "claude-opus-4"),
        )
        conn.commit()
        conn.close()

        assessor = GAOAIAssessor(db_path=mock_db_path)
        project = {"id": "proj-test"}
        results = assessor.get_automated_checks(project, project_dir=None)
        satisfied_count = sum(1 for s in results.values() if s in ("satisfied", "partially_satisfied"))
        assert satisfied_count > 0, "Telemetry data should satisfy at least one check"

    def test_provenance_check_satisfied_with_data(self, mock_db_path):
        """Populated prov_entities -> provenance check improves."""
        conn = sqlite3.connect(str(mock_db_path))
        conn.execute(
            "INSERT INTO prov_entities (id, entity_type, label, project_id) "
            "VALUES (?, ?, ?, ?)",
            ("ent-1", "prompt", "User query for analysis", "proj-test"),
        )
        conn.commit()
        conn.close()

        assessor = GAOAIAssessor(db_path=mock_db_path)
        project = {"id": "proj-test"}
        results = assessor.get_automated_checks(project, project_dir=None)
        satisfied_count = sum(1 for s in results.values() if s in ("satisfied", "partially_satisfied"))
        assert satisfied_count > 0, "Provenance data should satisfy at least one check"

    def test_assess_runs_without_error(self, mock_db_path):
        """Full assessment should run without raising exceptions."""
        assessor = GAOAIAssessor(db_path=mock_db_path)
        result = assessor.assess("proj-test")
        assert "framework_id" in result
        assert result["framework_id"] == "gao_ai"
        assert "total_requirements" in result
        assert "status_counts" in result
        assert "coverage_pct" in result


# ============================================================
# Gate Evaluation
# ============================================================

class TestGateEvaluation:
    def test_gate_not_assessed_empty(self, mock_db_path):
        """Gate returns not_assessed when no assessment has been run."""
        assessor = GAOAIAssessor(db_path=mock_db_path)
        gate = assessor.evaluate_gate("proj-test")
        assert gate["pass"] is False
        assert gate["gate_status"] == "not_assessed"
        assert "blocking_issues" in gate

    def test_gate_after_assessment(self, mock_db_path):
        """Gate returns structured result after running assessment."""
        assessor = GAOAIAssessor(db_path=mock_db_path)
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
        assessor = GAOAIAssessor(db_path=mock_db_path)
        assessor.assess("proj-test")
        gate = assessor.evaluate_gate("proj-test")
        assert "framework" in gate or "framework_id" in gate

    def test_cli_entry_point_exists(self):
        """Assessor has run_cli method from BaseAssessor."""
        assessor = GAOAIAssessor()
        assert hasattr(assessor, "run_cli")
        assert callable(assessor.run_cli)
