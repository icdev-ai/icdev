#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for OWASP ASI01-ASI10 Assessor (Phase 53, D339).

Validates:
    - Assessor class attributes and BaseAssessor pattern
    - Catalog loading (10 requirements)
    - Automated check mapping for each ASI risk
    - Gate evaluation
    - CLI interface
"""

import json
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools" / "compliance"))
from owasp_asi_assessor import OWASPASIAssessor

BASE_DIR = Path(__file__).resolve().parent.parent


@pytest.fixture
def icdev_db(tmp_path):
    """Create a minimal ICDEV database with tables needed for ASI checks."""
    db_path = tmp_path / "icdev.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            name TEXT,
            status TEXT DEFAULT 'active'
        );
        CREATE TABLE IF NOT EXISTS prompt_injection_log (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            input_text TEXT,
            detection_result TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS tool_chain_events (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            agent_id TEXT,
            tool_name TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS agent_trust_scores (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            agent_id TEXT,
            trust_score REAL,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS ai_bom (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            component_name TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS ai_telemetry (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            event_type TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS memory_consolidation_log (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            action TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS audit_trail (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            event_type TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS atlas_red_team_results (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            technique_id TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS owasp_asi_assessments (
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
                 (project_id, "Test Project"))
    conn.commit()
    conn.close()
    return db_path, project_id


@pytest.fixture
def assessor(icdev_db):
    """Create an OWASPASIAssessor with test DB."""
    db_path, _ = icdev_db
    a = OWASPASIAssessor()
    a.db_path = db_path
    return a


class TestAssessorAttributes:
    """Test class-level attributes match BaseAssessor pattern."""

    def test_framework_id(self):
        assert OWASPASIAssessor.FRAMEWORK_ID == "owasp_asi"

    def test_framework_name(self):
        assert "OWASP" in OWASPASIAssessor.FRAMEWORK_NAME
        assert "ASI" in OWASPASIAssessor.FRAMEWORK_NAME

    def test_table_name(self):
        assert OWASPASIAssessor.TABLE_NAME == "owasp_asi_assessments"

    def test_catalog_filename(self):
        assert OWASPASIAssessor.CATALOG_FILENAME == "owasp_agentic_asi.json"


class TestCatalog:
    """Test catalog loading."""

    def test_catalog_exists(self):
        catalog_path = BASE_DIR / "context" / "compliance" / "owasp_agentic_asi.json"
        assert catalog_path.exists(), "ASI catalog JSON must exist"

    def test_catalog_has_10_requirements(self):
        catalog_path = BASE_DIR / "context" / "compliance" / "owasp_agentic_asi.json"
        data = json.loads(catalog_path.read_text(encoding="utf-8"))
        assert len(data["requirements"]) == 10

    def test_catalog_ids_sequential(self):
        catalog_path = BASE_DIR / "context" / "compliance" / "owasp_agentic_asi.json"
        data = json.loads(catalog_path.read_text(encoding="utf-8"))
        ids = [r["id"] for r in data["requirements"]]
        expected = [f"ASI-{str(i).zfill(2)}" for i in range(1, 11)]
        assert ids == expected

    def test_catalog_has_nist_crosswalk(self):
        catalog_path = BASE_DIR / "context" / "compliance" / "owasp_agentic_asi.json"
        data = json.loads(catalog_path.read_text(encoding="utf-8"))
        for req in data["requirements"]:
            assert "nist_800_53_crosswalk" in req
            assert len(req["nist_800_53_crosswalk"]) > 0


class TestAutomatedChecks:
    """Test automated check logic for each ASI risk."""

    def test_no_data_returns_empty(self, assessor, icdev_db):
        _, project_id = icdev_db
        project = {"id": project_id}
        results = assessor.get_automated_checks(project)
        # No data inserted, only file-based checks may pass
        db_checks = {"ASI-01", "ASI-02", "ASI-03", "ASI-04", "ASI-06", "ASI-09", "ASI-10"}
        for check_id in db_checks:
            assert check_id not in results

    def test_asi01_prompt_injection(self, assessor, icdev_db):
        db_path, project_id = icdev_db
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO prompt_injection_log (id, project_id, input_text, detection_result) VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), project_id, "test", "clean"),
        )
        conn.commit()
        conn.close()
        results = assessor.get_automated_checks({"id": project_id})
        assert results.get("ASI-01") == "satisfied"

    def test_asi02_tool_chain(self, assessor, icdev_db):
        db_path, project_id = icdev_db
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO tool_chain_events (id, project_id, agent_id, tool_name) VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), project_id, "builder", "scaffold"),
        )
        conn.commit()
        conn.close()
        results = assessor.get_automated_checks({"id": project_id})
        assert results.get("ASI-02") == "satisfied"

    def test_asi03_identity_access(self, assessor, icdev_db):
        db_path, project_id = icdev_db
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO agent_trust_scores (id, project_id, agent_id, trust_score) VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), project_id, "builder", 0.85),
        )
        conn.commit()
        conn.close()
        results = assessor.get_automated_checks({"id": project_id})
        assert results.get("ASI-03") == "satisfied"

    def test_asi04_supply_chain(self, assessor, icdev_db):
        db_path, project_id = icdev_db
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO ai_bom (id, project_id, component_name) VALUES (?, ?, ?)",
            (str(uuid.uuid4()), project_id, "claude-sonnet"),
        )
        conn.commit()
        conn.close()
        results = assessor.get_automated_checks({"id": project_id})
        assert results.get("ASI-04") == "satisfied"

    def test_asi05_code_execution_config(self, assessor):
        """ASI-05 checks for code_pattern_config.yaml existence."""
        config_path = BASE_DIR / "args" / "code_pattern_config.yaml"
        if config_path.exists():
            results = assessor.get_automated_checks({"id": "proj-test"})
            assert results.get("ASI-05") == "satisfied"

    def test_asi06_memory_poisoning(self, assessor, icdev_db):
        db_path, project_id = icdev_db
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO ai_telemetry (id, project_id, event_type) VALUES (?, ?, ?)",
            (str(uuid.uuid4()), project_id, "drift_check"),
        )
        conn.commit()
        conn.close()
        results = assessor.get_automated_checks({"id": project_id})
        assert results.get("ASI-06") == "satisfied"

    def test_asi08_resilience_config(self, assessor):
        """ASI-08 checks for resilience_config.yaml existence."""
        config_path = BASE_DIR / "args" / "resilience_config.yaml"
        if config_path.exists():
            results = assessor.get_automated_checks({"id": "proj-test"})
            assert results.get("ASI-08") == "satisfied"

    def test_asi09_human_oversight(self, assessor, icdev_db):
        db_path, project_id = icdev_db
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO audit_trail (id, project_id, event_type) VALUES (?, ?, ?)",
            (str(uuid.uuid4()), project_id, "code.commit"),
        )
        conn.commit()
        conn.close()
        results = assessor.get_automated_checks({"id": project_id})
        assert results.get("ASI-09") == "satisfied"

    def test_asi10_rogue_agents(self, assessor, icdev_db):
        db_path, project_id = icdev_db
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "INSERT INTO agent_trust_scores (id, project_id, agent_id, trust_score) VALUES (?, ?, ?, ?)",
            (str(uuid.uuid4()), project_id, "builder", 0.9),
        )
        conn.commit()
        conn.close()
        results = assessor.get_automated_checks({"id": project_id})
        assert results.get("ASI-10") == "satisfied"
