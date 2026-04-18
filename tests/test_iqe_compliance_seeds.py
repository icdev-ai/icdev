# CUI // SP-CTI
"""Tests for context/iqe/queries/compliance/fedramp-moderate/*.iqe

5 seed queries — each must:
  • parse without error and match expected AST shape
  • return a deterministic violation count against a seeded fixture DB
"""
from __future__ import annotations

import pathlib
import sqlite3

from tools.iqe.adapters.compliance import (
    controls_adapter,
    snapshots_adapter,
    violations_adapter,
)
from tools.iqe.ast_nodes import AttrRef, Literal
from tools.iqe.executor import Executor
from tools.iqe.parser import parse

_QUERY_DIR = (
    pathlib.Path(__file__).parent.parent
    / "context" / "iqe" / "queries" / "compliance" / "fedramp-moderate"
)


def _read(name: str) -> str:
    return (_QUERY_DIR / name).read_text(encoding="utf-8")


# ---- Schemas ----------------------------------------------------------------

_CONTROLS_SCHEMA = """
CREATE TABLE IF NOT EXISTS compliance_controls (
    id TEXT PRIMARY KEY,
    family TEXT,
    title TEXT,
    impact_level TEXT
);
CREATE TABLE IF NOT EXISTS project_controls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    control_id TEXT NOT NULL,
    implementation_status TEXT,
    implementation_description TEXT,
    responsible_role TEXT
);
"""

_POAM_SCHEMA = """
CREATE TABLE IF NOT EXISTS poam_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    weakness_id TEXT,
    weakness_description TEXT,
    severity TEXT,
    source TEXT,
    control_id TEXT,
    status TEXT DEFAULT 'open',
    corrective_action TEXT,
    milestone_date TEXT,
    completion_date TEXT,
    responsible_party TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
"""

_SNAPSHOTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS pi_compliance_tracking (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    pi_number TEXT NOT NULL,
    pi_start_date TEXT,
    pi_end_date TEXT,
    compliance_score_start REAL,
    compliance_score_end REAL,
    controls_implemented INTEGER DEFAULT 0,
    controls_remaining INTEGER DEFAULT 0,
    poam_items_closed INTEGER DEFAULT 0,
    poam_items_opened INTEGER DEFAULT 0,
    findings_remediated INTEGER DEFAULT 0,
    artifacts_generated TEXT,
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
"""


# ---- Fixture builders -------------------------------------------------------

def _controls_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(_CONTROLS_SCHEMA)
    for cid, family, title, impact in [
        ("AC-2",  "AC", "Account Management",      "HIGH"),
        ("AC-3",  "AC", "Access Enforcement",       "HIGH"),
        ("AC-17", "AC", "Remote Access",            "MODERATE"),
        ("AU-12", "AU", "Audit Record Generation",  "HIGH"),
        ("SI-2",  "SI", "Flaw Remediation",         "MODERATE"),
    ]:
        conn.execute(
            "INSERT INTO compliance_controls (id, family, title, impact_level)"
            " VALUES (?,?,?,?)",
            (cid, family, title, impact),
        )
    # AC-2: not_implemented, empty description   → violates ac2_status, controls_failed_30d, evidence_missing
    # AC-3: partially_implemented, has desc      → violates ac2_status only
    # AC-17: implemented, has desc               → no violations
    # AU-12: not_implemented, empty description  → violates controls_failed_30d, evidence_missing
    # SI-2: implemented, has desc                → no violations
    for project_id, control_id, status, desc, role in [
        ("p1", "AC-2",  "not_implemented",       "",                    "ISSO"),
        ("p1", "AC-3",  "partially_implemented", "Partial RBAC in place", "ISSO"),
        ("p1", "AC-17", "implemented",           "VPN enforced",         "NetEng"),
        ("p1", "AU-12", "not_implemented",       "",                    "ISSO"),
        ("p1", "SI-2",  "implemented",           "Patch management active", "DevOps"),
    ]:
        conn.execute(
            "INSERT INTO project_controls"
            " (project_id, control_id, implementation_status, implementation_description, responsible_role)"
            " VALUES (?,?,?,?,?)",
            (project_id, control_id, status, desc, role),
        )
    conn.commit()
    return conn


def _violations_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(_POAM_SCHEMA)
    # WK-001, WK-002: open + no completion_date  → overdue (2 violations)
    # WK-003: in_progress + no completion_date   → not overdue (status != open)
    # WK-004: closed + completion_date set       → not overdue
    rows = [
        ("p1", "WK-001", "Unpatched CVE-2024-1234",   "high",     "SAST",   "SI-2", "open",        "Apply patch 1.2.3",            "2025-06-01", None),
        ("p1", "WK-002", "MFA not enforced on admin",  "critical", "PENTEST","IA-2", "open",        "Enable TOTP for all admins",   "2025-05-15", None),
        ("p1", "WK-003", "Stale admin account active", "medium",   "AUDIT",  "AC-2", "in_progress", "Deactivate dormant account",   "2025-07-01", None),
        ("p1", "WK-004", "Log rotation disabled",      "low",      "SAST",   "AU-12","closed",      "Rotation policy applied",      "2025-04-01", "2025-03-15"),
    ]
    conn.executemany(
        "INSERT INTO poam_items"
        " (project_id, weakness_id, weakness_description, severity, source, control_id,"
        "  status, corrective_action, milestone_date, completion_date)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    return conn


def _snapshots_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(_SNAPSHOTS_SCHEMA)
    # p1/PI-1: score dropped 0.90→0.75  → drift violation
    # p1/PI-2: score improved 0.80→0.88 → no violation
    # p2/PI-1: score improved 0.70→0.82 → no violation
    rows = [
        ("p1", "PI-1", 0.90, 0.75),
        ("p1", "PI-2", 0.80, 0.88),
        ("p2", "PI-1", 0.70, 0.82),
    ]
    conn.executemany(
        "INSERT INTO pi_compliance_tracking"
        " (project_id, pi_number, compliance_score_start, compliance_score_end)"
        " VALUES (?,?,?,?)",
        rows,
    )
    conn.commit()
    return conn


def _ex(collection: str, adapter_fn) -> tuple[Executor, sqlite3.Connection]:
    db_builders = {
        "compliance.controls":   _controls_db,
        "compliance.violations": _violations_db,
        "compliance.snapshots":  _snapshots_db,
    }
    conn = db_builders[collection]()
    ex = Executor()
    ex.register_collection(collection, adapter_fn)
    return ex, conn


# ============================================================================
# Test 1 — ac2_status.iqe
# ============================================================================

def test_ac2_status_parses() -> None:
    q = parse(_read("ac2_status.iqe"))
    assert q.var == "c"
    assert q.collection == AttrRef(["compliance", "controls"])
    assert len(q.where_clauses) == 2
    assert q.where_clauses[0].predicate.op == "=="
    assert q.where_clauses[0].predicate.right == Literal("AC")
    assert q.where_clauses[1].predicate.op == "!="
    assert q.where_clauses[1].predicate.right == Literal("implemented")
    fields = [f.parts[-1] for f in q.select.fields]
    assert "project_id" in fields
    assert "control_id" in fields
    assert "implementation_status" in fields


def test_ac2_status_violation_count() -> None:
    ex, conn = _ex("compliance.controls", controls_adapter)
    ast = parse(_read("ac2_status.iqe"))
    result = ex.run(ast, conn=conn)
    conn.close()
    # AC-2 (not_implemented) + AC-3 (partially_implemented) → 2 AC violations
    assert len(result) == 2
    cids = {r["control_id"] for r in result}
    assert "AC-2" in cids
    assert "AC-3" in cids


# ============================================================================
# Test 2 — controls_failed_30d.iqe
# ============================================================================

def test_controls_failed_30d_parses() -> None:
    q = parse(_read("controls_failed_30d.iqe"))
    assert q.var == "c"
    assert q.collection == AttrRef(["compliance", "controls"])
    assert len(q.where_clauses) == 1
    assert q.where_clauses[0].predicate.op == "=="
    assert q.where_clauses[0].predicate.right == Literal("not_implemented")
    fields = [f.parts[-1] for f in q.select.fields]
    assert "control_id" in fields
    assert "family" in fields


def test_controls_failed_30d_violation_count() -> None:
    ex, conn = _ex("compliance.controls", controls_adapter)
    ast = parse(_read("controls_failed_30d.iqe"))
    result = ex.run(ast, conn=conn)
    conn.close()
    # AC-2 (not_implemented) + AU-12 (not_implemented) → 2
    assert len(result) == 2
    cids = {r["control_id"] for r in result}
    assert "AC-2" in cids
    assert "AU-12" in cids


# ============================================================================
# Test 3 — projects_overdue.iqe
# ============================================================================

def test_projects_overdue_parses() -> None:
    q = parse(_read("projects_overdue.iqe"))
    assert q.var == "v"
    assert q.collection == AttrRef(["compliance", "violations"])
    assert len(q.where_clauses) == 2
    assert q.where_clauses[0].predicate.op == "=="
    assert q.where_clauses[0].predicate.right == Literal("open")
    assert q.where_clauses[1].predicate.op == "=="
    assert q.where_clauses[1].predicate.right == Literal(None)
    fields = [f.parts[-1] for f in q.select.fields]
    assert "weakness_id" in fields
    assert "severity" in fields
    assert "milestone_date" in fields


def test_projects_overdue_violation_count() -> None:
    ex, conn = _ex("compliance.violations", violations_adapter)
    ast = parse(_read("projects_overdue.iqe"))
    result = ex.run(ast, conn=conn)
    conn.close()
    # WK-001 (open, no completion) + WK-002 (open, no completion) → 2
    assert len(result) == 2
    wids = {r["weakness_id"] for r in result}
    assert "WK-001" in wids
    assert "WK-002" in wids


# ============================================================================
# Test 4 — evidence_missing.iqe
# ============================================================================

def test_evidence_missing_parses() -> None:
    q = parse(_read("evidence_missing.iqe"))
    assert q.var == "c"
    assert q.collection == AttrRef(["compliance", "controls"])
    assert len(q.where_clauses) == 1
    assert q.where_clauses[0].predicate.op == "=="
    assert q.where_clauses[0].predicate.right == Literal("")
    fields = [f.parts[-1] for f in q.select.fields]
    assert "control_id" in fields
    assert "implementation_status" in fields


def test_evidence_missing_violation_count() -> None:
    ex, conn = _ex("compliance.controls", controls_adapter)
    ast = parse(_read("evidence_missing.iqe"))
    result = ex.run(ast, conn=conn)
    conn.close()
    # AC-2 (empty desc) + AU-12 (empty desc) → 2
    assert len(result) == 2
    cids = {r["control_id"] for r in result}
    assert "AC-2" in cids
    assert "AU-12" in cids


# ============================================================================
# Test 5 — drift_since_last_audit.iqe
# ============================================================================

def test_drift_since_last_audit_parses() -> None:
    q = parse(_read("drift_since_last_audit.iqe"))
    assert q.var == "s"
    assert q.collection == AttrRef(["compliance", "snapshots"])
    assert len(q.where_clauses) == 1
    pred = q.where_clauses[0].predicate
    assert pred.op == "<"
    assert isinstance(pred.left, AttrRef)
    assert pred.left.parts[-1] == "compliance_score_end"
    assert isinstance(pred.right, AttrRef)
    assert pred.right.parts[-1] == "compliance_score_start"
    fields = [f.parts[-1] for f in q.select.fields]
    assert "pi_number" in fields
    assert "compliance_score_start" in fields
    assert "compliance_score_end" in fields


def test_drift_since_last_audit_violation_count() -> None:
    ex, conn = _ex("compliance.snapshots", snapshots_adapter)
    ast = parse(_read("drift_since_last_audit.iqe"))
    result = ex.run(ast, conn=conn)
    conn.close()
    # Only p1/PI-1 dropped (0.90→0.75) → 1 violation
    assert len(result) == 1
    assert result[0]["pi_number"] == "PI-1"
    assert result[0]["project_id"] == "p1"
    assert result[0]["compliance_score_end"] < result[0]["compliance_score_start"]
