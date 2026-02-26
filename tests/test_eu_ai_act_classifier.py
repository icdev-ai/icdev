#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for EU AI Act Risk Classifier (Phase 57, D349).

Validates:
    - Assessor class attributes and BaseAssessor pattern
    - Catalog loading (12 requirements + 8 Annex III categories)
    - Automated check mapping for each EUAI requirement
    - Gate evaluation
    - CLI interface
"""

import json
import sqlite3
import sys
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools" / "compliance"))
from eu_ai_act_classifier import EUAIActClassifier

BASE_DIR = Path(__file__).resolve().parent.parent


@pytest.fixture
def icdev_db(tmp_path):
    """Create a minimal ICDEV database with tables needed for EU AI Act checks."""
    db_path = tmp_path / "icdev.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            name TEXT,
            status TEXT DEFAULT 'active'
        );
        CREATE TABLE IF NOT EXISTS ai_use_case_inventory (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            name TEXT,
            risk_level TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS model_cards (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            model_name TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS system_cards (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            system_name TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS audit_trail (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            event_type TEXT,
            action TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS ai_telemetry (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            event_type TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS omb_m25_21_assessments (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            requirement_id TEXT,
            status TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS gao_ai_assessments (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            requirement_id TEXT,
            status TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS ai_oversight_plans (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            plan_name TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS ai_caio_registry (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            name TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS stig_results (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            rule_id TEXT,
            status TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS nist_ai_rmf_assessments (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            requirement_id TEXT,
            status TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS production_audits (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS cato_evidence (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS ai_incident_log (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS ai_ethics_reviews (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            review_type TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS eu_ai_act_assessments (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            assessment_date TEXT DEFAULT (datetime('now')),
            results_json TEXT,
            total_controls INTEGER DEFAULT 0,
            satisfied_count INTEGER DEFAULT 0,
            not_satisfied_count INTEGER DEFAULT 0,
            coverage_pct REAL DEFAULT 0.0,
            assessor_version TEXT DEFAULT '1.0',
            created_at TEXT DEFAULT (datetime('now'))
        );
    """)
    project_id = f"proj-{uuid.uuid4().hex[:8]}"
    conn.execute("INSERT INTO projects (id, name) VALUES (?, ?)",
                 (project_id, "Test EU Project"))
    conn.commit()
    conn.close()
    return db_path, project_id


@pytest.fixture
def assessor(icdev_db):
    """Create an EUAIActClassifier with test DB."""
    db_path, _ = icdev_db
    a = EUAIActClassifier()
    a.db_path = db_path
    return a


class TestAssessorAttributes:
    """Test class-level attributes match BaseAssessor pattern."""

    def test_framework_id(self):
        assert EUAIActClassifier.FRAMEWORK_ID == "eu_ai_act"

    def test_framework_name(self):
        assert "EU" in EUAIActClassifier.FRAMEWORK_NAME
        assert "Artificial Intelligence Act" in EUAIActClassifier.FRAMEWORK_NAME

    def test_table_name(self):
        assert EUAIActClassifier.TABLE_NAME == "eu_ai_act_assessments"

    def test_catalog_filename(self):
        assert EUAIActClassifier.CATALOG_FILENAME == "eu_ai_act_annex_iii.json"


class TestCatalog:
    """Test catalog loading."""

    def test_catalog_exists(self):
        catalog_path = BASE_DIR / "context" / "compliance" / "eu_ai_act_annex_iii.json"
        assert catalog_path.exists(), "EU AI Act catalog JSON must exist"

    def test_catalog_has_12_requirements(self):
        catalog_path = BASE_DIR / "context" / "compliance" / "eu_ai_act_annex_iii.json"
        data = json.loads(catalog_path.read_text(encoding="utf-8"))
        assert len(data["requirements"]) == 12

    def test_catalog_ids_sequential(self):
        catalog_path = BASE_DIR / "context" / "compliance" / "eu_ai_act_annex_iii.json"
        data = json.loads(catalog_path.read_text(encoding="utf-8"))
        ids = [r["id"] for r in data["requirements"]]
        expected = [f"EUAI-{str(i).zfill(2)}" for i in range(1, 13)]
        assert ids == expected

    def test_catalog_has_nist_crosswalk(self):
        catalog_path = BASE_DIR / "context" / "compliance" / "eu_ai_act_annex_iii.json"
        data = json.loads(catalog_path.read_text(encoding="utf-8"))
        for req in data["requirements"]:
            assert "nist_800_53_crosswalk" in req
            assert len(req["nist_800_53_crosswalk"]) > 0

    def test_catalog_has_annex_iii_categories(self):
        catalog_path = BASE_DIR / "context" / "compliance" / "eu_ai_act_annex_iii.json"
        data = json.loads(catalog_path.read_text(encoding="utf-8"))
        assert "annex_iii_categories" in data
        assert len(data["annex_iii_categories"]) == 8

    def test_catalog_has_risk_levels(self):
        catalog_path = BASE_DIR / "context" / "compliance" / "eu_ai_act_annex_iii.json"
        data = json.loads(catalog_path.read_text(encoding="utf-8"))
        assert "risk_levels" in data
        risk_levels = [r["level"] for r in data["risk_levels"]]
        assert "unacceptable" in risk_levels
        assert "high_risk" in risk_levels


class TestAutomatedChecks:
    """Test automated check logic for each EU AI Act requirement."""

    def test_no_data_returns_empty(self, assessor, icdev_db):
        _, project_id = icdev_db
        results = assessor.get_automated_checks({"id": project_id})
        assert len(results) == 0

    def test_euai01_risk_classification(self, assessor, icdev_db):
        """EUAI-01: AI use case inventory exists."""
        db_path, project_id = icdev_db
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO ai_use_case_inventory (id, project_id, name, risk_level) VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), project_id, "Test AI", "high_risk"),
        )
        conn.commit()
        conn.close()
        results = assessor.get_automated_checks({"id": project_id})
        assert results.get("EUAI-01") == "satisfied"

    def test_euai02_data_governance(self, assessor, icdev_db):
        """EUAI-02: Model cards exist with training data docs."""
        db_path, project_id = icdev_db
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO model_cards (id, project_id, model_name) VALUES (?, ?, ?)",
            (str(uuid.uuid4()), project_id, "test-model"),
        )
        conn.commit()
        conn.close()
        results = assessor.get_automated_checks({"id": project_id})
        assert results.get("EUAI-02") == "satisfied"

    def test_euai03_technical_docs_full(self, assessor, icdev_db):
        """EUAI-03: Both model cards AND system cards → satisfied."""
        db_path, project_id = icdev_db
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO model_cards (id, project_id, model_name) VALUES (?, ?, ?)",
            (str(uuid.uuid4()), project_id, "test-model"),
        )
        conn.execute(
            "INSERT INTO system_cards (id, project_id, system_name) VALUES (?, ?, ?)",
            (str(uuid.uuid4()), project_id, "test-system"),
        )
        conn.commit()
        conn.close()
        results = assessor.get_automated_checks({"id": project_id})
        assert results.get("EUAI-03") == "satisfied"

    def test_euai03_technical_docs_partial(self, assessor, icdev_db):
        """EUAI-03: Only model cards → partially_satisfied."""
        db_path, project_id = icdev_db
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO model_cards (id, project_id, model_name) VALUES (?, ?, ?)",
            (str(uuid.uuid4()), project_id, "test-model"),
        )
        conn.commit()
        conn.close()
        results = assessor.get_automated_checks({"id": project_id})
        assert results.get("EUAI-03") == "partially_satisfied"

    def test_euai04_record_keeping_full(self, assessor, icdev_db):
        """EUAI-04: Both audit_trail AND ai_telemetry → satisfied."""
        db_path, project_id = icdev_db
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO audit_trail (id, project_id, event_type) VALUES (?, ?, ?)",
            (str(uuid.uuid4()), project_id, "compliance_check"),
        )
        conn.execute(
            "INSERT INTO ai_telemetry (id, project_id, event_type) VALUES (?, ?, ?)",
            (str(uuid.uuid4()), project_id, "inference"),
        )
        conn.commit()
        conn.close()
        results = assessor.get_automated_checks({"id": project_id})
        assert results.get("EUAI-04") == "satisfied"

    def test_euai04_record_keeping_partial(self, assessor, icdev_db):
        """EUAI-04: Only audit_trail → partially_satisfied."""
        db_path, project_id = icdev_db
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO audit_trail (id, project_id, event_type) VALUES (?, ?, ?)",
            (str(uuid.uuid4()), project_id, "compliance_check"),
        )
        conn.commit()
        conn.close()
        results = assessor.get_automated_checks({"id": project_id})
        assert results.get("EUAI-04") == "partially_satisfied"

    def test_euai05_transparency(self, assessor, icdev_db):
        """EUAI-05: Transparency assessment from OMB/GAO/NIST."""
        db_path, project_id = icdev_db
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO omb_m25_21_assessments (id, project_id, requirement_id, status) VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), project_id, "M25-01", "satisfied"),
        )
        conn.commit()
        conn.close()
        results = assessor.get_automated_checks({"id": project_id})
        assert results.get("EUAI-05") == "satisfied"

    def test_euai06_human_oversight_full(self, assessor, icdev_db):
        """EUAI-06: Both oversight plans AND CAIO → satisfied."""
        db_path, project_id = icdev_db
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO ai_oversight_plans (id, project_id, plan_name) VALUES (?, ?, ?)",
            (str(uuid.uuid4()), project_id, "Human Oversight Plan"),
        )
        conn.execute(
            "INSERT INTO ai_caio_registry (id, project_id, name) VALUES (?, ?, ?)",
            (str(uuid.uuid4()), project_id, "Jane Smith"),
        )
        conn.commit()
        conn.close()
        results = assessor.get_automated_checks({"id": project_id})
        assert results.get("EUAI-06") == "satisfied"

    def test_euai06_human_oversight_partial(self, assessor, icdev_db):
        """EUAI-06: Only CAIO designated → partially_satisfied."""
        db_path, project_id = icdev_db
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO ai_caio_registry (id, project_id, name) VALUES (?, ?, ?)",
            (str(uuid.uuid4()), project_id, "Jane Smith"),
        )
        conn.commit()
        conn.close()
        results = assessor.get_automated_checks({"id": project_id})
        assert results.get("EUAI-06") == "partially_satisfied"
