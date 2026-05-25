# CUI // SP-CTI
"""Tests for context/iqe/queries/compliance/fedramp-moderate/*.iqe (5 queries)
and context/iqe/queries/compliance/fedramp-high/*.iqe (5 queries).

Each query must:
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

_QUERY_DIR_HIGH = (
    pathlib.Path(__file__).parent.parent
    / "context" / "iqe" / "queries" / "compliance" / "fedramp-high"
)


def _read(name: str) -> str:
    return (_QUERY_DIR / name).read_text(encoding="utf-8")


def _read_high(name: str) -> str:
    return (_QUERY_DIR_HIGH / name).read_text(encoding="utf-8")


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


# ---- FedRAMP High fixture builders -----------------------------------------

def _high_controls_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(_CONTROLS_SCHEMA)
    # Impact levels chosen so:
    #   CP-7 (HIGH) + CP-2 (MODERATE) → cp7 catches both (neither implemented)
    #   SC-7 (HIGH) + SC-12 (MODERATE) → sc7 catches both (both not_implemented)
    #   CP-7 (HIGH, not_impl) + SC-7 (HIGH, not_impl) → ac2 returns exactly 2 HIGH gaps
    for cid, family, title, impact in [
        ("CP-7",  "CP", "Alternate Processing Site",   "HIGH"),
        ("CP-2",  "CP", "Contingency Plan",             "MODERATE"),
        ("CP-9",  "CP", "System Backup",                "MODERATE"),
        ("SC-7",  "SC", "Boundary Protection",          "HIGH"),
        ("SC-12", "SC", "Cryptographic Key Establish",  "MODERATE"),
        ("SC-8",  "SC", "Transmission Confidentiality", "MODERATE"),
        ("SC-28", "SC", "Protection of Data at Rest",   "MODERATE"),
        ("AC-2",  "AC", "Account Management",           "MODERATE"),
        ("AC-3",  "AC", "Access Enforcement",           "MODERATE"),
    ]:
        conn.execute(
            "INSERT INTO compliance_controls (id, family, title, impact_level)"
            " VALUES (?,?,?,?)",
            (cid, family, title, impact),
        )
    for project_id, control_id, status, desc, role in [
        ("p2", "CP-7",  "not_implemented",       "",                    "ISSO"),
        ("p2", "CP-2",  "partially_implemented", "Draft plan exists",   "ISSO"),
        ("p2", "CP-9",  "implemented",           "Backup policy active","DevOps"),
        ("p2", "SC-7",  "not_implemented",       "",                    "NetEng"),
        ("p2", "SC-12", "not_implemented",       "",                    "ISSO"),
        ("p2", "SC-8",  "implemented",           "TLS 1.3 enforced",   "NetEng"),
        ("p2", "SC-28", "implemented",           "AES-256 at rest",    "DevOps"),
        ("p2", "AC-2",  "implemented",           "IAM policy active",  "ISSO"),
        ("p2", "AC-3",  "implemented",           "RBAC enforced",      "ISSO"),
    ]:
        conn.execute(
            "INSERT INTO project_controls"
            " (project_id, control_id, implementation_status, implementation_description, responsible_role)"
            " VALUES (?,?,?,?,?)",
            (project_id, control_id, status, desc, role),
        )
    conn.commit()
    return conn


def _high_violations_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(_POAM_SCHEMA)
    # IR-001, IR-002: critical + open  → violates ir4_dynamic_reconfig (2)
    # IR-003: high (not critical) + open → no violation
    # IR-004: critical + closed        → no violation
    rows = [
        ("p2", "IR-001", "Perimeter firewall misconfiguration", "critical", "PENTEST", "SC-7",  "open",        "Patch firewall ruleset",          "2025-08-01", None),
        ("p2", "IR-002", "Privileged account without MFA",      "critical", "AUDIT",   "IA-2",  "open",        "Enforce MFA on priv accounts",    "2025-07-15", None),
        ("p2", "IR-003", "Stale service account active",        "high",     "SAST",    "AC-2",  "open",        "Deactivate dormant svc account",  "2025-09-01", None),
        ("p2", "IR-004", "Unencrypted backup media",            "critical", "AUDIT",   "CP-9",  "closed",      "Backup encryption enabled",       "2025-06-01", "2025-05-20"),
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


def _high_snapshots_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(_SNAPSHOTS_SCHEMA)
    # p2/PI-3: end=0.82 → below 0.85 High threshold → violation
    # p2/PI-4: end=0.75 → below 0.85 High threshold → violation
    # p2/PI-5: end=0.90 → above threshold → no violation
    # p3/PI-1: end=0.87 → above threshold → no violation
    rows = [
        ("p2", "PI-3", 0.80, 0.82),
        ("p2", "PI-4", 0.90, 0.75),
        ("p2", "PI-5", 0.88, 0.90),
        ("p3", "PI-1", 0.85, 0.87),
    ]
    conn.executemany(
        "INSERT INTO pi_compliance_tracking"
        " (project_id, pi_number, compliance_score_start, compliance_score_end)"
        " VALUES (?,?,?,?)",
        rows,
    )
    conn.commit()
    return conn


def _ex_high(collection: str, adapter_fn) -> tuple[Executor, sqlite3.Connection]:
    db_builders = {
        "compliance.controls":   _high_controls_db,
        "compliance.violations": _high_violations_db,
        "compliance.snapshots":  _high_snapshots_db,
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


# ============================================================================
# Test 6 — cp7_alternate_site.iqe  (FedRAMP High)
# ============================================================================

def test_cp7_alternate_site_parses() -> None:
    q = parse(_read_high("cp7_alternate_site.iqe"))
    assert q.var == "c"
    assert q.collection == AttrRef(["compliance", "controls"])
    assert len(q.where_clauses) == 2
    assert q.where_clauses[0].predicate.op == "=="
    assert q.where_clauses[0].predicate.right == Literal("CP")
    assert q.where_clauses[1].predicate.op == "!="
    assert q.where_clauses[1].predicate.right == Literal("implemented")
    fields = [f.parts[-1] for f in q.select.fields]
    assert "project_id" in fields
    assert "control_id" in fields
    assert "implementation_status" in fields


def test_cp7_alternate_site_violation_count() -> None:
    ex, conn = _ex_high("compliance.controls", controls_adapter)
    ast = parse(_read_high("cp7_alternate_site.iqe"))
    result = ex.run(ast, conn=conn)
    conn.close()
    # CP-7 (not_implemented) + CP-2 (partially_implemented) → 2 CP gaps
    assert len(result) == 2
    cids = {r["control_id"] for r in result}
    assert "CP-7" in cids
    assert "CP-2" in cids


# ============================================================================
# Test 7 — ir4_dynamic_reconfig.iqe  (FedRAMP High)
# ============================================================================

def test_ir4_dynamic_reconfig_parses() -> None:
    q = parse(_read_high("ir4_dynamic_reconfig.iqe"))
    assert q.var == "v"
    assert q.collection == AttrRef(["compliance", "violations"])
    assert len(q.where_clauses) == 2
    assert q.where_clauses[0].predicate.op == "=="
    assert q.where_clauses[0].predicate.right == Literal("critical")
    assert q.where_clauses[1].predicate.op == "=="
    assert q.where_clauses[1].predicate.right == Literal("open")
    fields = [f.parts[-1] for f in q.select.fields]
    assert "weakness_id" in fields
    assert "severity" in fields
    assert "milestone_date" in fields


def test_ir4_dynamic_reconfig_violation_count() -> None:
    ex, conn = _ex_high("compliance.violations", violations_adapter)
    ast = parse(_read_high("ir4_dynamic_reconfig.iqe"))
    result = ex.run(ast, conn=conn)
    conn.close()
    # IR-001 (critical, open) + IR-002 (critical, open) → 2
    assert len(result) == 2
    wids = {r["weakness_id"] for r in result}
    assert "IR-001" in wids
    assert "IR-002" in wids


# ============================================================================
# Test 8 — sc7_boundary_gaps.iqe  (FedRAMP High)
# ============================================================================

def test_sc7_boundary_gaps_parses() -> None:
    q = parse(_read_high("sc7_boundary_gaps.iqe"))
    assert q.var == "c"
    assert q.collection == AttrRef(["compliance", "controls"])
    assert len(q.where_clauses) == 2
    assert q.where_clauses[0].predicate.op == "=="
    assert q.where_clauses[0].predicate.right == Literal("SC")
    assert q.where_clauses[1].predicate.op == "=="
    assert q.where_clauses[1].predicate.right == Literal("not_implemented")
    fields = [f.parts[-1] for f in q.select.fields]
    assert "control_id" in fields
    assert "title" in fields
    assert "implementation_status" in fields


def test_sc7_boundary_gaps_violation_count() -> None:
    ex, conn = _ex_high("compliance.controls", controls_adapter)
    ast = parse(_read_high("sc7_boundary_gaps.iqe"))
    result = ex.run(ast, conn=conn)
    conn.close()
    # SC-7 (not_implemented) + SC-12 (not_implemented) → 2 boundary gaps
    assert len(result) == 2
    cids = {r["control_id"] for r in result}
    assert "SC-7" in cids
    assert "SC-12" in cids


# ============================================================================
# Test 9 — ac2_usage_conditions.iqe  (FedRAMP High)
# ============================================================================

def test_ac2_usage_conditions_parses() -> None:
    q = parse(_read_high("ac2_usage_conditions.iqe"))
    assert q.var == "c"
    assert q.collection == AttrRef(["compliance", "controls"])
    assert len(q.where_clauses) == 2
    assert q.where_clauses[0].predicate.op == "=="
    assert q.where_clauses[0].predicate.right == Literal("HIGH")
    assert q.where_clauses[1].predicate.op == "=="
    assert q.where_clauses[1].predicate.right == Literal("not_implemented")
    fields = [f.parts[-1] for f in q.select.fields]
    assert "control_id" in fields
    assert "family" in fields
    assert "title" in fields


def test_ac2_usage_conditions_violation_count() -> None:
    ex, conn = _ex_high("compliance.controls", controls_adapter)
    ast = parse(_read_high("ac2_usage_conditions.iqe"))
    result = ex.run(ast, conn=conn)
    conn.close()
    # CP-7 (HIGH, not_implemented) + SC-7 (HIGH, not_implemented) → 2 HIGH-baseline gaps
    assert len(result) == 2
    cids = {r["control_id"] for r in result}
    assert "CP-7" in cids
    assert "SC-7" in cids


# ============================================================================
# Test 10 — cm3_crypto_mgmt.iqe  (FedRAMP High)
# ============================================================================

def test_cm3_crypto_mgmt_parses() -> None:
    q = parse(_read_high("cm3_crypto_mgmt.iqe"))
    assert q.var == "s"
    assert q.collection == AttrRef(["compliance", "snapshots"])
    assert len(q.where_clauses) == 1
    pred = q.where_clauses[0].predicate
    assert pred.op == "<"
    assert isinstance(pred.left, AttrRef)
    assert pred.left.parts[-1] == "compliance_score_end"
    assert pred.right == Literal(0.85)
    fields = [f.parts[-1] for f in q.select.fields]
    assert "project_id" in fields
    assert "pi_number" in fields
    assert "compliance_score_end" in fields


def test_cm3_crypto_mgmt_violation_count() -> None:
    ex, conn = _ex_high("compliance.snapshots", snapshots_adapter)
    ast = parse(_read_high("cm3_crypto_mgmt.iqe"))
    result = ex.run(ast, conn=conn)
    conn.close()
    # p2/PI-3 (end=0.82 < 0.85) + p2/PI-4 (end=0.75 < 0.85) → 2 High-threshold breaches
    assert len(result) == 2
    pis = {(r["project_id"], r["pi_number"]) for r in result}
    assert ("p2", "PI-3") in pis
    assert ("p2", "PI-4") in pis
