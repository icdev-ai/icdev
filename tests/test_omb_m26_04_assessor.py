#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Tests for OMB M-26-04 Unbiased AI assessor (Phase 48).

Coverage: framework metadata, base class inheritance, catalog loading,
automated checks (model card, system card, fairness, XAI, human review
documentation), gate evaluation, CLI entry point.
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

from tools.compliance.omb_m26_04_assessor import OMBM2604Assessor


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
CREATE TABLE model_cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT,
    model_name TEXT,
    model_version TEXT,
    model_type TEXT,
    intended_use TEXT,
    limitations TEXT,
    training_data_description TEXT,
    performance_metrics TEXT,
    fairness_analysis TEXT,
    status TEXT DEFAULT 'draft',
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE system_cards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT,
    system_name TEXT,
    system_description TEXT,
    ai_components TEXT,
    human_oversight_description TEXT,
    risk_assessment TEXT,
    deployment_context TEXT,
    status TEXT DEFAULT 'draft',
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
CREATE TABLE nist_ai_rmf_assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    assessment_date TEXT DEFAULT (datetime('now')),
    assessor TEXT DEFAULT 'icdev-compliance-engine',
    requirement_id TEXT NOT NULL,
    requirement_title TEXT,
    family TEXT,
    status TEXT DEFAULT 'not_assessed',
    evidence_description TEXT,
    automation_result TEXT,
    notes TEXT,
    nist_800_53_crosswalk TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(project_id, requirement_id)
);
INSERT INTO projects (id, name) VALUES ('proj-test', 'Test Project');
"""


@pytest.fixture
def mock_db_path(tmp_path):
    """Create a file-backed temp DB with required tables.

    Returns the db path so each _get_connection call can open a fresh
    connection (the source code closes connections in finally blocks).
    """
    db_path = tmp_path / "mock_omb_m26_04.db"
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
        """OMBM2604Assessor can be imported."""
        assert OMBM2604Assessor is not None

    def test_framework_id(self):
        """Verify FRAMEWORK_ID constant."""
        assert OMBM2604Assessor.FRAMEWORK_ID == "omb_m26_04"

    def test_framework_name(self):
        """Verify FRAMEWORK_NAME contains identifying text."""
        assert "OMB" in OMBM2604Assessor.FRAMEWORK_NAME or "M-26-04" in OMBM2604Assessor.FRAMEWORK_NAME

    def test_table_name(self):
        """Verify TABLE_NAME constant."""
        assert OMBM2604Assessor.TABLE_NAME == "omb_m26_04_assessments"

    def test_catalog_filename(self):
        """Verify CATALOG_FILENAME constant."""
        assert OMBM2604Assessor.CATALOG_FILENAME == "omb_m26_04_unbiased_ai.json"

    def test_inherits_base_assessor(self):
        """OMBM2604Assessor inherits from BaseAssessor (ABC).

        Verify via MRO class names to avoid dual-import identity mismatch.
        """
        base_names = [cls.__name__ for cls in OMBM2604Assessor.__mro__]
        assert "BaseAssessor" in base_names


# ============================================================
# Catalog Loading
# ============================================================

class TestCatalogLoading:
    def test_catalog_file_exists(self):
        """Catalog JSON file exists in context/compliance/."""
        catalog_path = BASE_DIR / "context" / "compliance" / "omb_m26_04_unbiased_ai.json"
        assert catalog_path.exists(), f"Catalog not found: {catalog_path}"

    def test_catalog_valid_json(self):
        """Catalog file is valid JSON with requirements/controls."""
        catalog_path = BASE_DIR / "context" / "compliance" / "omb_m26_04_unbiased_ai.json"
        with open(catalog_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        has_items = data.get("requirements") or data.get("controls") or data.get("criteria")
        assert has_items, "Catalog must have 'requirements', 'controls', or 'criteria' key"

    def test_catalog_requirement_structure(self):
        """Each requirement has id, title, description."""
        catalog_path = BASE_DIR / "context" / "compliance" / "omb_m26_04_unbiased_ai.json"
        with open(catalog_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        items = data.get("requirements") or data.get("controls") or data.get("criteria") or []
        assert len(items) > 0, "Catalog must contain at least one requirement"
        for item in items:
            assert "id" in item, f"Requirement missing 'id': {item}"
            assert "title" in item, f"Requirement missing 'title': {item}"

    def test_load_catalog_method(self, mock_db_path):
        """Assessor load_catalog returns a non-empty list."""
        assessor = OMBM2604Assessor(db_path=mock_db_path)
        catalog = assessor.load_catalog()
        assert isinstance(catalog, list)
        assert len(catalog) > 0


# ============================================================
# Automated Checks — Assessment
# ============================================================

class TestAssessment:
    def test_automated_checks_returns_dict(self, mock_db_path):
        """get_automated_checks returns a dict with string values."""
        assessor = OMBM2604Assessor(db_path=mock_db_path)
        project = {"id": "proj-test"}
        results = assessor.get_automated_checks(project, project_dir=None)

        assert isinstance(results, dict)
        for key, value in results.items():
            assert isinstance(key, str), f"Key {key} is not a string"
            assert isinstance(value, str), f"Value for {key} is not a string"

    def test_check_returns_valid_status(self, mock_db_path):
        """All check statuses are from the valid set."""
        assessor = OMBM2604Assessor(db_path=mock_db_path)
        project = {"id": "proj-test"}
        results = assessor.get_automated_checks(project, project_dir=None)
        valid = {"satisfied", "partially_satisfied", "not_satisfied", "not_assessed", "not_applicable", "risk_accepted"}
        for check_id, status in results.items():
            assert status in valid, f"{check_id} has invalid status: {status}"

    def test_model_card_check_not_satisfied_empty(self, mock_db_path):
        """Empty model_cards table -> model card check absent from results.

        When tables are empty the assessor does not add satisfied entries for
        most checks.  The model-card key (M26-DOC-1) should NOT appear as
        satisfied in the result dict.
        """
        assessor = OMBM2604Assessor(db_path=mock_db_path)
        project = {"id": "proj-test"}
        results = assessor.get_automated_checks(project, project_dir=None)
        # Model card key must not be satisfied on an empty DB
        assert results.get("M26-DOC-1") != "satisfied", \
            "M26-DOC-1 should not be satisfied with empty model_cards"
        assert results.get("M26-DOC-2") != "satisfied", \
            "M26-DOC-2 should not be satisfied with empty system_cards"

    def test_model_card_check_satisfied_with_data(self, mock_db_path):
        """Populated model_cards -> model card check improves."""
        conn = sqlite3.connect(str(mock_db_path))
        conn.execute(
            "INSERT INTO model_cards (project_id, model_name, model_version, model_type, intended_use, limitations) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("proj-test", "DocumentClassifier", "1.0", "transformer", "CUI document classification", "English only"),
        )
        conn.commit()
        conn.close()

        assessor = OMBM2604Assessor(db_path=mock_db_path)
        project = {"id": "proj-test"}
        results = assessor.get_automated_checks(project, project_dir=None)
        satisfied_count = sum(1 for s in results.values() if s in ("satisfied", "partially_satisfied"))
        assert satisfied_count > 0, "Model card data should satisfy at least one check"

    def test_fairness_check_satisfied_with_data(self, mock_db_path):
        """Populated fairness_assessments -> fairness check improves."""
        conn = sqlite3.connect(str(mock_db_path))
        conn.execute(
            "INSERT INTO fairness_assessments (project_id, model_id, assessment_type, "
            "protected_attributes, metrics, status) VALUES (?, ?, ?, ?, ?, ?)",
            ("proj-test", "model-1", "pre_deployment", "race,gender", '{"disparate_impact": 0.85}', "completed"),
        )
        conn.commit()
        conn.close()

        assessor = OMBM2604Assessor(db_path=mock_db_path)
        project = {"id": "proj-test"}
        results = assessor.get_automated_checks(project, project_dir=None)
        satisfied_count = sum(1 for s in results.values() if s in ("satisfied", "partially_satisfied"))
        assert satisfied_count > 0, "Fairness data should satisfy at least one check"

    def test_xai_check_satisfied_with_shap(self, mock_db_path):
        """Populated shap_attributions -> XAI check improves."""
        conn = sqlite3.connect(str(mock_db_path))
        conn.execute(
            "INSERT INTO shap_attributions (trace_id, tool_name, shapley_value, project_id) "
            "VALUES (?, ?, ?, ?)",
            ("trace-1", "scaffold", 0.45, "proj-test"),
        )
        conn.commit()
        conn.close()

        assessor = OMBM2604Assessor(db_path=mock_db_path)
        project = {"id": "proj-test"}
        results = assessor.get_automated_checks(project, project_dir=None)
        satisfied_count = sum(1 for s in results.values() if s in ("satisfied", "partially_satisfied"))
        assert satisfied_count > 0, "SHAP data should satisfy at least one check"

    def test_assess_runs_without_error(self, mock_db_path):
        """Full assessment should run without raising exceptions."""
        assessor = OMBM2604Assessor(db_path=mock_db_path)
        result = assessor.assess("proj-test")
        assert "framework_id" in result
        assert result["framework_id"] == "omb_m26_04"
        assert "total_requirements" in result
        assert "status_counts" in result
        assert "coverage_pct" in result


# ============================================================
# Gate Evaluation
# ============================================================

class TestGateEvaluation:
    def test_gate_not_assessed_empty(self, mock_db_path):
        """Gate returns not_assessed when no assessment has been run."""
        assessor = OMBM2604Assessor(db_path=mock_db_path)
        gate = assessor.evaluate_gate("proj-test")
        assert gate["pass"] is False
        assert gate["gate_status"] == "not_assessed"
        assert "blocking_issues" in gate

    def test_gate_after_assessment(self, mock_db_path):
        """Gate returns structured result after running assessment."""
        assessor = OMBM2604Assessor(db_path=mock_db_path)
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
        assessor = OMBM2604Assessor(db_path=mock_db_path)
        assessor.assess("proj-test")
        gate = assessor.evaluate_gate("proj-test")
        assert "framework" in gate or "framework_id" in gate

    def test_cli_entry_point_exists(self):
        """Assessor has run_cli method from BaseAssessor."""
        assessor = OMBM2604Assessor()
        assert hasattr(assessor, "run_cli")
        assert callable(assessor.run_cli)
