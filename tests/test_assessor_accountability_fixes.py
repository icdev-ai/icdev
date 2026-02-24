#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for Phase 49 assessor accountability check fixes.

Verifies that the 14 fixed checks across 4 assessors
(omb_m25_21_assessor, omb_m26_04_assessor, gao_ai_assessor,
fairness_assessor) correctly query the Phase 49 tables:
ai_oversight_plans, ai_accountability_appeals, ai_caio_registry,
ai_use_case_inventory, ai_reassessment_schedule, ai_incident_log,
ai_ethics_reviews.
"""

import sqlite3
import sys
from pathlib import Path

import pytest

# Ensure project root is importable
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.compliance.omb_m25_21_assessor import OMBM2521Assessor
from tools.compliance.omb_m26_04_assessor import OMBM2604Assessor
from tools.compliance.gao_ai_assessor import GAOAIAssessor
from tools.compliance.fairness_assessor import assess_fairness, evaluate_gate


# ============================================================
# Fixtures
# ============================================================

_SCHEMA = """
    CREATE TABLE IF NOT EXISTS ai_oversight_plans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id TEXT NOT NULL, plan_name TEXT NOT NULL,
        description TEXT DEFAULT '', approval_status TEXT DEFAULT 'draft',
        created_by TEXT DEFAULT '', approved_by TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now')), updated_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS ai_accountability_appeals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id TEXT NOT NULL, appellant TEXT NOT NULL, ai_system TEXT NOT NULL,
        grievance TEXT DEFAULT '', status TEXT DEFAULT 'submitted',
        resolution TEXT DEFAULT '', filed_at TEXT DEFAULT (datetime('now')), resolved_at TEXT
    );
    CREATE TABLE IF NOT EXISTS ai_caio_registry (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id TEXT NOT NULL, name TEXT NOT NULL, role TEXT DEFAULT 'CAIO',
        organization TEXT DEFAULT '', appointment_date TEXT DEFAULT (datetime('now')),
        status TEXT DEFAULT 'active', created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS ai_use_case_inventory (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id TEXT NOT NULL, name TEXT NOT NULL, purpose TEXT DEFAULT '',
        risk_level TEXT DEFAULT 'minimal_risk', responsible_official TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS ai_reassessment_schedule (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id TEXT NOT NULL, ai_system TEXT NOT NULL,
        frequency TEXT NOT NULL DEFAULT 'annual', next_due TEXT,
        last_completed TEXT, created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS ai_incident_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id TEXT NOT NULL, incident_type TEXT NOT NULL, ai_system TEXT,
        severity TEXT DEFAULT 'medium', description TEXT NOT NULL,
        corrective_action TEXT, status TEXT DEFAULT 'open',
        reported_by TEXT, created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS ai_ethics_reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id TEXT NOT NULL, review_type TEXT NOT NULL,
        ai_system TEXT, findings TEXT,
        opt_out_policy INTEGER DEFAULT 0, legal_compliance_matrix INTEGER DEFAULT 0,
        pre_deployment_review INTEGER DEFAULT 0,
        reviewer TEXT, created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS audit_trail (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id TEXT NOT NULL, event_type TEXT NOT NULL,
        actor TEXT DEFAULT '', action TEXT DEFAULT '',
        details TEXT DEFAULT '', affected_files TEXT DEFAULT '[]',
        classification TEXT DEFAULT 'CUI', session_id TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS fairness_assessments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id TEXT NOT NULL, assessment_data TEXT NOT NULL,
        overall_score REAL DEFAULT 0.0,
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS ai_telemetry (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id TEXT NOT NULL, event_type TEXT DEFAULT '',
        prompt_hash TEXT DEFAULT '', response_hash TEXT DEFAULT '',
        model_id TEXT DEFAULT '', tokens_in INTEGER DEFAULT 0,
        tokens_out INTEGER DEFAULT 0, created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS xai_assessments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id TEXT NOT NULL, assessment_data TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS shap_attributions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id TEXT NOT NULL, trace_id TEXT DEFAULT '',
        attribution_data TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS model_cards (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id TEXT NOT NULL, model_name TEXT DEFAULT '',
        card_data TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS system_cards (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id TEXT NOT NULL, system_name TEXT DEFAULT '',
        card_data TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS nist_ai_rmf_assessments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id TEXT NOT NULL, assessment_date TEXT DEFAULT (datetime('now')),
        requirement_id TEXT NOT NULL, status TEXT DEFAULT 'not_assessed',
        UNIQUE(project_id, requirement_id)
    );
    CREATE TABLE IF NOT EXISTS atlas_assessments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id TEXT NOT NULL, assessment_date TEXT DEFAULT (datetime('now')),
        requirement_id TEXT NOT NULL, status TEXT DEFAULT 'not_assessed',
        UNIQUE(project_id, requirement_id)
    );
    CREATE TABLE IF NOT EXISTS prov_entities (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id TEXT NOT NULL, entity_id TEXT DEFAULT '',
        entity_type TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS ai_bom (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id TEXT NOT NULL, component_name TEXT DEFAULT '',
        component_type TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS agent_vetoes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id TEXT DEFAULT '', agent_id TEXT DEFAULT '',
        reason TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS prompt_injection_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id TEXT DEFAULT '', input_text TEXT DEFAULT '',
        result TEXT DEFAULT '',
        created_at TEXT DEFAULT (datetime('now'))
    );
"""


@pytest.fixture
def db_path(tmp_path):
    """Create a temp DB with all tables needed by all 4 assessors."""
    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db))
    conn.executescript(_SCHEMA)
    conn.commit()
    conn.close()
    return db


def _insert(db_path, sql, params=()):
    """Helper to insert a row."""
    conn = sqlite3.connect(str(db_path))
    conn.execute(sql, params)
    conn.commit()
    conn.close()


PID = "proj-test"


# ============================================================
# OMB M-25-21 Assessor Tests
# ============================================================

class TestOMBM2521:
    """Tests for the 5 Phase 49 fixes in OMBM2521Assessor."""

    def test_m25_ovr1_oversight_plan_check(self, db_path):
        """M25-OVR-1 satisfied when ai_oversight_plans has a record."""
        _insert(db_path, "INSERT INTO ai_oversight_plans (project_id, plan_name, approval_status) VALUES (?, ?, ?)",
                (PID, "Plan A", "approved"))
        assessor = OMBM2521Assessor(db_path=db_path)
        results = assessor.get_automated_checks({"id": PID})
        assert results.get("M25-OVR-1") == "satisfied"

    def test_m25_ovr1_no_plan(self, db_path):
        """M25-OVR-1 absent when no oversight plan exists."""
        assessor = OMBM2521Assessor(db_path=db_path)
        results = assessor.get_automated_checks({"id": PID})
        assert "M25-OVR-1" not in results

    def test_m25_ovr3_appeal_process(self, db_path):
        """M25-OVR-3 satisfied when ai_accountability_appeals has a record."""
        _insert(db_path, "INSERT INTO ai_accountability_appeals (project_id, appellant, ai_system) VALUES (?, ?, ?)",
                (PID, "User", "Classifier"))
        assessor = OMBM2521Assessor(db_path=db_path)
        results = assessor.get_automated_checks({"id": PID})
        assert results.get("M25-OVR-3") == "satisfied"

    def test_m25_ovr4_caio_designated(self, db_path):
        """M25-OVR-4 satisfied when ai_caio_registry has a record."""
        _insert(db_path, "INSERT INTO ai_caio_registry (project_id, name) VALUES (?, ?)",
                (PID, "Jane Smith"))
        assessor = OMBM2521Assessor(db_path=db_path)
        results = assessor.get_automated_checks({"id": PID})
        assert results.get("M25-OVR-4") == "satisfied"

    def test_m25_inv2_responsible_official(self, db_path):
        """M25-INV-2 satisfied when ai_use_case_inventory has responsible_official."""
        _insert(db_path, "INSERT INTO ai_use_case_inventory (project_id, name, responsible_official) VALUES (?, ?, ?)",
                (PID, "System X", "John Doe"))
        assessor = OMBM2521Assessor(db_path=db_path)
        results = assessor.get_automated_checks({"id": PID})
        assert results.get("M25-INV-2") == "satisfied"

    def test_m25_inv2_missing_official(self, db_path):
        """M25-INV-2 absent when responsible_official is NULL."""
        _insert(db_path, "INSERT INTO ai_use_case_inventory (project_id, name) VALUES (?, ?)",
                (PID, "System X"))
        assessor = OMBM2521Assessor(db_path=db_path)
        results = assessor.get_automated_checks({"id": PID})
        assert "M25-INV-2" not in results

    def test_m25_inv3_reassessment(self, db_path):
        """M25-INV-3 satisfied when ai_reassessment_schedule has a record."""
        _insert(db_path, "INSERT INTO ai_reassessment_schedule (project_id, ai_system, next_due) VALUES (?, ?, ?)",
                (PID, "Classifier", "2099-01-01"))
        assessor = OMBM2521Assessor(db_path=db_path)
        results = assessor.get_automated_checks({"id": PID})
        assert results.get("M25-INV-3") == "satisfied"

    def test_m25_risk4_incident_response(self, db_path):
        """M25-RISK-4 satisfied when ai_incident_log has a record."""
        _insert(db_path, "INSERT INTO ai_incident_log (project_id, incident_type, description) VALUES (?, ?, ?)",
                (PID, "other", "Test incident"))
        assessor = OMBM2521Assessor(db_path=db_path)
        results = assessor.get_automated_checks({"id": PID})
        assert results.get("M25-RISK-4") == "satisfied"


# ============================================================
# OMB M-26-04 Assessor Tests
# ============================================================

class TestOMBM2604:
    """Tests for the 3 Phase 49 fixes in OMBM2604Assessor."""

    def test_m26_rev2_appeal_registered(self, db_path):
        """M26-REV-2 satisfied when ai_accountability_appeals has a record."""
        _insert(db_path, "INSERT INTO ai_accountability_appeals (project_id, appellant, ai_system) VALUES (?, ?, ?)",
                (PID, "User", "Detector"))
        assessor = OMBM2604Assessor(db_path=db_path)
        results = assessor.get_automated_checks({"id": PID})
        assert results.get("M26-REV-2") == "satisfied"

    def test_m26_rev3_opt_out_policy(self, db_path):
        """M26-REV-3 satisfied when ai_ethics_reviews has opt_out_policy=1."""
        _insert(db_path, "INSERT INTO ai_ethics_reviews (project_id, review_type, opt_out_policy) VALUES (?, ?, ?)",
                (PID, "other", 1))
        assessor = OMBM2604Assessor(db_path=db_path)
        results = assessor.get_automated_checks({"id": PID})
        assert results.get("M26-REV-3") == "satisfied"

    def test_m26_imp1_impact_assessment(self, db_path):
        """M26-IMP-1 satisfied when ai_ethics_reviews has review_type='impact_assessment'."""
        _insert(db_path, "INSERT INTO ai_ethics_reviews (project_id, review_type) VALUES (?, ?)",
                (PID, "impact_assessment"))
        assessor = OMBM2604Assessor(db_path=db_path)
        results = assessor.get_automated_checks({"id": PID})
        assert results.get("M26-IMP-1") == "satisfied"


# ============================================================
# GAO AI Assessor Tests
# ============================================================

class TestGAOAI:
    """Tests for the 5 Phase 49 fixes in GAOAIAssessor."""

    def test_gao_mon3_incident_detection(self, db_path):
        """GAO-MON-3 satisfied when ai_incident_log has a record."""
        _insert(db_path, "INSERT INTO ai_incident_log (project_id, incident_type, description) VALUES (?, ?, ?)",
                (PID, "safety", "Safety event"))
        assessor = GAOAIAssessor(db_path=db_path)
        results = assessor.get_automated_checks({"id": PID})
        assert results.get("GAO-MON-3") == "satisfied"

    def test_gao_mon2_feedback_collection(self, db_path):
        """GAO-MON-2 satisfied when audit_trail has event_type like '%feedback%'."""
        _insert(db_path, "INSERT INTO audit_trail (project_id, event_type, action) VALUES (?, ?, ?)",
                (PID, "ai_feedback_submitted", "User submitted feedback"))
        assessor = GAOAIAssessor(db_path=db_path)
        results = assessor.get_automated_checks({"id": PID})
        assert results.get("GAO-MON-2") == "satisfied"

    def test_gao_mon4_reassessment(self, db_path):
        """GAO-MON-4 satisfied when ai_reassessment_schedule has a record."""
        _insert(db_path, "INSERT INTO ai_reassessment_schedule (project_id, ai_system, next_due) VALUES (?, ?, ?)",
                (PID, "Model", "2099-01-01"))
        assessor = GAOAIAssessor(db_path=db_path)
        results = assessor.get_automated_checks({"id": PID})
        assert results.get("GAO-MON-4") == "satisfied"

    def test_gao_gov2_legal_compliance(self, db_path):
        """GAO-GOV-2 satisfied when ai_ethics_reviews has legal_compliance_matrix=1."""
        _insert(db_path, "INSERT INTO ai_ethics_reviews (project_id, review_type, legal_compliance_matrix) VALUES (?, ?, ?)",
                (PID, "legal_compliance", 1))
        assessor = GAOAIAssessor(db_path=db_path)
        results = assessor.get_automated_checks({"id": PID})
        assert results.get("GAO-GOV-2") == "satisfied"

    def test_gao_gov3_ethics_framework(self, db_path):
        """GAO-GOV-3 satisfied when ai_ethics_reviews has any record."""
        _insert(db_path, "INSERT INTO ai_ethics_reviews (project_id, review_type) VALUES (?, ?)",
                (PID, "ethics_framework"))
        assessor = GAOAIAssessor(db_path=db_path)
        results = assessor.get_automated_checks({"id": PID})
        assert results.get("GAO-GOV-3") == "satisfied"


# ============================================================
# Fairness Assessor Tests
# ============================================================

class TestFairness:
    """Tests for the 4 Phase 49 fixes in fairness_assessor + gate."""

    def test_fair1_bias_testing_policy(self, db_path):
        """FAIR-1 satisfied when ai_ethics_reviews has review_type='bias_testing_policy'."""
        _insert(db_path, "INSERT INTO ai_ethics_reviews (project_id, review_type) VALUES (?, ?)",
                (PID, "bias_testing_policy"))
        result = assess_fairness(PID, db_path=db_path)
        fair1 = next(d for d in result["dimensions"] if d["id"] == "FAIR-1")
        assert fair1["status"] == "satisfied"

    def test_fair3_disparity_analysis(self, db_path):
        """FAIR-3 satisfied when ai_ethics_reviews has pre_deployment_review=1."""
        _insert(db_path, "INSERT INTO ai_ethics_reviews (project_id, review_type, pre_deployment_review) VALUES (?, ?, ?)",
                (PID, "review", 1))
        result = assess_fairness(PID, db_path=db_path)
        fair3 = next(d for d in result["dimensions"] if d["id"] == "FAIR-3")
        assert fair3["status"] == "satisfied"

    def test_fair6_human_review(self, db_path):
        """FAIR-6 satisfied when ai_oversight_plans has a record."""
        _insert(db_path, "INSERT INTO ai_oversight_plans (project_id, plan_name) VALUES (?, ?)",
                (PID, "Oversight Plan"))
        result = assess_fairness(PID, db_path=db_path)
        fair6 = next(d for d in result["dimensions"] if d["id"] == "FAIR-6")
        assert fair6["status"] == "satisfied"

    def test_fair7_appeal_process(self, db_path):
        """FAIR-7 satisfied when ai_accountability_appeals has a record."""
        _insert(db_path, "INSERT INTO ai_accountability_appeals (project_id, appellant, ai_system) VALUES (?, ?, ?)",
                (PID, "Appellant", "System"))
        result = assess_fairness(PID, db_path=db_path)
        fair7 = next(d for d in result["dimensions"] if d["id"] == "FAIR-7")
        assert fair7["status"] == "satisfied"

    def test_fairness_gate_threshold_25(self, db_path):
        """Gate passes when score >= 25%."""
        # Satisfy 2/8 dimensions (25%) — FAIR-6 and FAIR-7
        _insert(db_path, "INSERT INTO ai_oversight_plans (project_id, plan_name) VALUES (?, ?)",
                (PID, "Plan"))
        _insert(db_path, "INSERT INTO ai_accountability_appeals (project_id, appellant, ai_system) VALUES (?, ?, ?)",
                (PID, "User", "System"))
        # Run the assessment first to store results
        assess_fairness(PID, db_path=db_path)
        gate = evaluate_gate(PID, db_path=db_path)
        assert gate["pass"] is True

    def test_fairness_gate_below_threshold(self, db_path):
        """Gate fails when score < 25%."""
        # Only FAIR-7 satisfied (1/8 = 12.5%)
        _insert(db_path, "INSERT INTO ai_accountability_appeals (project_id, appellant, ai_system) VALUES (?, ?, ?)",
                (PID, "User", "System"))
        assess_fairness(PID, db_path=db_path)
        gate = evaluate_gate(PID, db_path=db_path)
        assert gate["pass"] is False


# ============================================================
# Cross-Assessor Tests
# ============================================================

class TestCrossAssessor:
    """Tests verifying behavior across all 4 assessors."""

    def test_empty_db_all_checks_missing(self, db_path):
        """All 4 assessors should have Phase 49 accountability checks absent on empty DB."""
        # M-25-21
        m25 = OMBM2521Assessor(db_path=db_path)
        m25_results = m25.get_automated_checks({"id": PID})
        for check_id in ["M25-OVR-1", "M25-OVR-3", "M25-OVR-4", "M25-INV-2", "M25-INV-3", "M25-RISK-4"]:
            assert check_id not in m25_results, f"{check_id} should not be satisfied on empty DB"

        # M-26-04
        m26 = OMBM2604Assessor(db_path=db_path)
        m26_results = m26.get_automated_checks({"id": PID})
        for check_id in ["M26-REV-2", "M26-REV-3", "M26-IMP-1"]:
            assert check_id not in m26_results, f"{check_id} should not be satisfied on empty DB"

        # GAO
        gao = GAOAIAssessor(db_path=db_path)
        gao_results = gao.get_automated_checks({"id": PID})
        for check_id in ["GAO-MON-3", "GAO-MON-2", "GAO-MON-4", "GAO-GOV-2", "GAO-GOV-3"]:
            assert check_id not in gao_results, f"{check_id} should not be satisfied on empty DB"

        # Fairness
        fair_result = assess_fairness(PID, db_path=db_path)
        for dim in fair_result["dimensions"]:
            if dim["id"] in ("FAIR-1", "FAIR-3", "FAIR-6", "FAIR-7"):
                assert dim["status"] != "satisfied", f"{dim['id']} should not be satisfied on empty DB"

    def test_all_checks_present(self, db_path):
        """Populating all tables makes all Phase 49 checks satisfied across assessors."""
        conn = sqlite3.connect(str(db_path))
        # Oversight plan
        conn.execute("INSERT INTO ai_oversight_plans (project_id, plan_name, approval_status) VALUES (?, ?, ?)",
                      (PID, "Plan", "approved"))
        # Appeal
        conn.execute("INSERT INTO ai_accountability_appeals (project_id, appellant, ai_system) VALUES (?, ?, ?)",
                      (PID, "User", "System"))
        # CAIO
        conn.execute("INSERT INTO ai_caio_registry (project_id, name) VALUES (?, ?)",
                      (PID, "CAIO Officer"))
        # Inventory with official
        conn.execute("INSERT INTO ai_use_case_inventory (project_id, name, responsible_official) VALUES (?, ?, ?)",
                      (PID, "AI System", "Official"))
        # Reassessment schedule
        conn.execute("INSERT INTO ai_reassessment_schedule (project_id, ai_system, next_due) VALUES (?, ?, ?)",
                      (PID, "System", "2099-01-01"))
        # Incident log
        conn.execute("INSERT INTO ai_incident_log (project_id, incident_type, description) VALUES (?, ?, ?)",
                      (PID, "other", "Test"))
        # Ethics reviews (multiple)
        conn.execute("INSERT INTO ai_ethics_reviews (project_id, review_type, opt_out_policy) VALUES (?, ?, ?)",
                      (PID, "other", 1))
        conn.execute("INSERT INTO ai_ethics_reviews (project_id, review_type, legal_compliance_matrix) VALUES (?, ?, ?)",
                      (PID, "legal_compliance", 1))
        conn.execute("INSERT INTO ai_ethics_reviews (project_id, review_type) VALUES (?, ?)",
                      (PID, "impact_assessment"))
        conn.execute("INSERT INTO ai_ethics_reviews (project_id, review_type) VALUES (?, ?)",
                      (PID, "bias_testing_policy"))
        conn.execute("INSERT INTO ai_ethics_reviews (project_id, review_type, pre_deployment_review) VALUES (?, ?, ?)",
                      (PID, "review", 1))
        # Feedback in audit trail
        conn.execute("INSERT INTO audit_trail (project_id, event_type, action) VALUES (?, ?, ?)",
                      (PID, "ai_feedback_submitted", "Feedback"))
        conn.commit()
        conn.close()

        # M-25-21
        m25 = OMBM2521Assessor(db_path=db_path)
        m25_results = m25.get_automated_checks({"id": PID})
        for check_id in ["M25-OVR-1", "M25-OVR-3", "M25-OVR-4", "M25-INV-2", "M25-INV-3", "M25-RISK-4"]:
            assert m25_results.get(check_id) == "satisfied", f"{check_id} should be satisfied"

        # M-26-04
        m26 = OMBM2604Assessor(db_path=db_path)
        m26_results = m26.get_automated_checks({"id": PID})
        for check_id in ["M26-REV-2", "M26-REV-3", "M26-IMP-1"]:
            assert m26_results.get(check_id) == "satisfied", f"{check_id} should be satisfied"

        # GAO
        gao = GAOAIAssessor(db_path=db_path)
        gao_results = gao.get_automated_checks({"id": PID})
        for check_id in ["GAO-MON-3", "GAO-MON-2", "GAO-MON-4", "GAO-GOV-2", "GAO-GOV-3"]:
            assert gao_results.get(check_id) == "satisfied", f"{check_id} should be satisfied"

        # Fairness
        fair_result = assess_fairness(PID, db_path=db_path)
        for dim in fair_result["dimensions"]:
            if dim["id"] in ("FAIR-1", "FAIR-3", "FAIR-6", "FAIR-7"):
                assert dim["status"] == "satisfied", f"{dim['id']} should be satisfied"
