#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for AI Accountability Audit (Phase 49).

Covers: run_accountability_audit, get_accountability_gaps,
ACCOUNTABILITY_CHECKS structure, inverted checks (ACC-7/ACC-9),
score calculation, gap severity, recommendation logic.
"""

import sqlite3
import sys
from pathlib import Path

import pytest

# Ensure project root is importable
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.compliance.ai_accountability_audit import (
    ACCOUNTABILITY_CHECKS,
    get_accountability_gaps,
    run_accountability_audit,
)


# ============================================================
# Fixtures
# ============================================================

@pytest.fixture
def db_path(tmp_path):
    """Create a temp DB with all tables referenced by ACCOUNTABILITY_CHECKS."""
    db = tmp_path / "test.db"
    conn = sqlite3.connect(str(db))
    conn.executescript("""
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
    """)
    conn.commit()
    conn.close()
    return db


def _populate_all(db_path, project_id="proj-123"):
    """Helper to populate all tables for a full-pass audit."""
    conn = sqlite3.connect(str(db_path))
    # ACC-1: oversight plan
    conn.execute(
        "INSERT INTO ai_oversight_plans (project_id, plan_name) VALUES (?, 'Plan')",
        (project_id,),
    )
    # ACC-2: approved plan
    conn.execute(
        "INSERT INTO ai_oversight_plans (project_id, plan_name, approval_status) VALUES (?, 'Approved Plan', 'approved')",
        (project_id,),
    )
    # ACC-3: appeal
    conn.execute(
        "INSERT INTO ai_accountability_appeals (project_id, appellant, ai_system) VALUES (?, 'Appellant', 'System')",
        (project_id,),
    )
    # ACC-4: CAIO
    conn.execute(
        "INSERT INTO ai_caio_registry (project_id, name) VALUES (?, 'Jane Smith')",
        (project_id,),
    )
    # ACC-5: inventory with responsible_official
    conn.execute(
        "INSERT INTO ai_use_case_inventory (project_id, name, responsible_official) VALUES (?, 'AI System', 'John Doe')",
        (project_id,),
    )
    # ACC-6: reassessment schedule (future date)
    conn.execute(
        "INSERT INTO ai_reassessment_schedule (project_id, ai_system, next_due) VALUES (?, 'System', '2099-01-01')",
        (project_id,),
    )
    # ACC-8: incident log
    conn.execute(
        "INSERT INTO ai_incident_log (project_id, incident_type, description) VALUES (?, 'other', 'Test incident')",
        (project_id,),
    )
    # ACC-10: ethics review
    conn.execute(
        "INSERT INTO ai_ethics_reviews (project_id, review_type) VALUES (?, 'ethics_framework')",
        (project_id,),
    )
    # ACC-11: legal compliance
    conn.execute(
        "INSERT INTO ai_ethics_reviews (project_id, review_type, legal_compliance_matrix) VALUES (?, 'legal_compliance', 1)",
        (project_id,),
    )
    # ACC-12: opt-out policy
    conn.execute(
        "INSERT INTO ai_ethics_reviews (project_id, review_type, opt_out_policy) VALUES (?, 'other', 1)",
        (project_id,),
    )
    # ACC-13: impact assessment
    conn.execute(
        "INSERT INTO ai_ethics_reviews (project_id, review_type) VALUES (?, 'impact_assessment')",
        (project_id,),
    )
    conn.commit()
    conn.close()


# ============================================================
# run_accountability_audit
# ============================================================

def test_audit_empty_project(db_path):
    """All checks fail on an empty DB, score is 0 (or near 0 if inverts pass)."""
    result = run_accountability_audit("proj-empty", db_path=db_path)
    # Inverted checks (ACC-7, ACC-9) pass when count=0
    # So 2 pass, 11 fail
    assert result["total_checks"] == 13
    assert result["failed"] >= 11
    assert result["accountability_score"] <= 20


def test_audit_all_checks_pass(db_path):
    """Populating all tables should pass most checks, yielding high score."""
    _populate_all(db_path, "proj-full")
    result = run_accountability_audit("proj-full", db_path=db_path)
    assert result["total_checks"] == 13
    assert result["passed"] == 13
    assert result["accountability_score"] == 100.0
    assert result["gap_count"] == 0


def test_audit_partial_pass(db_path):
    """Only some tables populated gives partial pass."""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO ai_oversight_plans (project_id, plan_name) VALUES ('proj-partial', 'Plan')"
    )
    conn.commit()
    conn.close()

    result = run_accountability_audit("proj-partial", db_path=db_path)
    assert 0 < result["passed"] < 13
    assert result["failed"] > 0


def test_audit_returns_gaps_for_failures(db_path):
    """Failed checks produce gap entries."""
    result = run_accountability_audit("proj-gaps", db_path=db_path)
    assert result["gap_count"] > 0
    assert len(result["gaps"]) == result["gap_count"]
    for gap in result["gaps"]:
        assert "check_id" in gap
        assert "title" in gap
        assert "severity" in gap


def test_audit_gap_severity_counts(db_path):
    """high_priority_gaps matches the number of high-severity gaps."""
    result = run_accountability_audit("proj-sev", db_path=db_path)
    high_count = sum(1 for g in result["gaps"] if g["severity"] == "high")
    assert result["high_priority_gaps"] == high_count


def test_audit_recommendation_pass(db_path):
    """Score >=70 and no high gaps yields PASS recommendation."""
    _populate_all(db_path, "proj-rec")
    result = run_accountability_audit("proj-rec", db_path=db_path)
    assert "PASS" in result["recommendation"]


def test_audit_recommendation_action_required(db_path):
    """Low score yields ACTION REQUIRED recommendation."""
    result = run_accountability_audit("proj-low", db_path=db_path)
    assert "ACTION REQUIRED" in result["recommendation"]


def test_audit_total_checks_is_13(db_path):
    """Audit always reports total_checks=13."""
    result = run_accountability_audit("proj-count", db_path=db_path)
    assert result["total_checks"] == 13


def test_audit_inverted_checks_fail(db_path):
    """ACC-7 (overdue) and ACC-9 (critical incidents) fail when count > 0."""
    conn = sqlite3.connect(str(db_path))
    # ACC-7: overdue reassessment (next_due in the past)
    conn.execute(
        "INSERT INTO ai_reassessment_schedule (project_id, ai_system, next_due) VALUES ('proj-inv', 'System', '2020-01-01')"
    )
    # ACC-9: unresolved critical incident
    conn.execute(
        "INSERT INTO ai_incident_log (project_id, incident_type, severity, status, description) "
        "VALUES ('proj-inv', 'safety', 'critical', 'open', 'Critical issue')"
    )
    conn.commit()
    conn.close()

    result = run_accountability_audit("proj-inv", db_path=db_path)
    # Find ACC-7 and ACC-9 in results
    acc7 = next(r for r in result["results"] if r["check_id"] == "ACC-7")
    acc9 = next(r for r in result["results"] if r["check_id"] == "ACC-9")
    assert acc7["status"] == "fail"
    assert acc9["status"] == "fail"


def test_audit_inverted_checks_pass(db_path):
    """ACC-7 and ACC-9 pass when no overdue and no unresolved critical incidents."""
    result = run_accountability_audit("proj-clean", db_path=db_path)
    acc7 = next(r for r in result["results"] if r["check_id"] == "ACC-7")
    acc9 = next(r for r in result["results"] if r["check_id"] == "ACC-9")
    assert acc7["status"] == "pass"
    assert acc9["status"] == "pass"


# ============================================================
# get_accountability_gaps
# ============================================================

def test_get_accountability_gaps_empty(db_path):
    """Gaps endpoint returns gaps for empty project."""
    result = get_accountability_gaps("proj-empty", db_path=db_path)
    assert result["gap_count"] > 0
    assert "gaps" in result
    assert "accountability_score" in result


def test_get_accountability_gaps_populated(db_path):
    """Fully populated project has zero gaps."""
    _populate_all(db_path, "proj-full")
    result = get_accountability_gaps("proj-full", db_path=db_path)
    assert result["gap_count"] == 0
    assert result["high_priority_gaps"] == 0


# ============================================================
# ACCOUNTABILITY_CHECKS structure
# ============================================================

def test_audit_frameworks_mapping():
    """Every check has at least one framework reference."""
    for check in ACCOUNTABILITY_CHECKS:
        assert len(check["frameworks"]) > 0, f"{check['id']} has no frameworks"


def test_audit_check_ids_unique():
    """All check IDs are unique."""
    ids = [c["id"] for c in ACCOUNTABILITY_CHECKS]
    assert len(ids) == len(set(ids))


def test_audit_actions_contain_project_id(db_path):
    """Gap action strings that use {pid} format correctly."""
    result = run_accountability_audit("proj-act", db_path=db_path)
    for gap in result["gaps"]:
        action = gap.get("action", "")
        # If the original template had {pid}, it should be replaced
        assert "{pid}" not in action


def test_audit_result_structure(db_path):
    """Audit result has all expected top-level keys."""
    result = run_accountability_audit("proj-struct", db_path=db_path)
    required_keys = {
        "audit_type", "classification", "project_id", "audit_date",
        "accountability_score", "total_checks", "passed", "failed",
        "results", "gaps", "gap_count", "high_priority_gaps", "recommendation",
    }
    assert required_keys.issubset(set(result.keys()))


def test_audit_score_calculation(db_path):
    """Score = passed / total * 100, rounded to 1 decimal."""
    _populate_all(db_path, "proj-score")
    result = run_accountability_audit("proj-score", db_path=db_path)
    expected_score = round(result["passed"] / result["total_checks"] * 100, 1)
    assert result["accountability_score"] == expected_score


def test_audit_with_only_oversight_plan(db_path):
    """Only ACC-1 (and inverted ACC-7/ACC-9) should pass with a single plan."""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO ai_oversight_plans (project_id, plan_name) VALUES ('proj-single', 'Minimal Plan')"
    )
    conn.commit()
    conn.close()

    result = run_accountability_audit("proj-single", db_path=db_path)
    acc1 = next(r for r in result["results"] if r["check_id"] == "ACC-1")
    assert acc1["status"] == "pass"
    # ACC-2 (approved) should still fail since approval_status defaults to 'draft'
    acc2 = next(r for r in result["results"] if r["check_id"] == "ACC-2")
    assert acc2["status"] == "fail"


def test_audit_classification_field(db_path):
    """Audit result includes CUI classification marking."""
    result = run_accountability_audit("proj-cls", db_path=db_path)
    assert result["classification"] == "CUI // SP-CTI"


def test_audit_date_present(db_path):
    """Audit result includes an ISO audit_date."""
    result = run_accountability_audit("proj-date", db_path=db_path)
    assert "audit_date" in result
    assert len(result["audit_date"]) > 10  # ISO timestamp
