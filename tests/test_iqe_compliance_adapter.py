# CUI // SP-CTI
"""Unit tests for tools/iqe/adapters/compliance.py — 5 cases."""
from __future__ import annotations

import sqlite3

from tools.iqe.ast_nodes import AttrRef, BinOp, ForeachNode, Literal, WhereNode
from tools.iqe.executor import Executor
from tools.iqe.parser import parse

# ---- Stub data -------------------------------------------------------

_SNAPSHOTS = [
    {
        "id": 1, "project_id": "p1", "pi_number": "PI 22.1",
        "pi_start_date": "2025-01-01", "pi_end_date": "2025-03-31",
        "compliance_score_start": 0.72, "compliance_score_end": 0.85,
        "controls_implemented": 10, "controls_remaining": 3,
        "poam_items_closed": 2, "poam_items_opened": 1,
        "findings_remediated": 4, "notes": "On track", "created_at": "2025-01-01",
    },
    {
        "id": 2, "project_id": "p1", "pi_number": "PI 22.2",
        "pi_start_date": "2025-04-01", "pi_end_date": "2025-06-30",
        "compliance_score_start": 0.85, "compliance_score_end": 0.91,
        "controls_implemented": 13, "controls_remaining": 0,
        "poam_items_closed": 1, "poam_items_opened": 0,
        "findings_remediated": 2, "notes": "Closed PI", "created_at": "2025-04-01",
    },
]

_CONTROLS = [
    {
        "id": 1, "project_id": "p1", "control_id": "AC-2",
        "family": "AC", "title": "Account Management", "impact_level": "HIGH",
        "implementation_status": "implemented",
        "implementation_description": "LDAP managed", "responsible_role": "ISSO",
    },
    {
        "id": 2, "project_id": "p1", "control_id": "AU-12",
        "family": "AU", "title": "Audit Record Generation", "impact_level": "HIGH",
        "implementation_status": "partially_implemented",
        "implementation_description": "Logs captured, SIEM pending",
        "responsible_role": "ISSO",
    },
]

_VIOLATIONS = [
    {
        "id": 1, "project_id": "p1", "weakness_id": "WK-001",
        "weakness_description": "Unpatched CVE-2024-1234",
        "severity": "high", "source": "SAST", "control_id": "SI-2",
        "status": "open", "corrective_action": "Apply patch 1.2.3",
        "milestone_date": "2025-06-01", "completion_date": None,
        "responsible_party": "DevOps", "created_at": "2025-01-10",
    },
    {
        "id": 2, "project_id": "p1", "weakness_id": "WK-002",
        "weakness_description": "MFA not enforced on admin portal",
        "severity": "critical", "source": "PENTEST", "control_id": "IA-2",
        "status": "in_progress", "corrective_action": "Enable TOTP for all admin accounts",
        "milestone_date": "2025-05-15", "completion_date": None,
        "responsible_party": "IAM Team", "created_at": "2025-02-01",
    },
]

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


def _stub(rows: list[dict]):
    def _adapter(_conn):
        return list(rows)
    return _adapter


def _ex(*pairs: tuple[str, list[dict]]) -> Executor:
    ex = Executor()
    for name, rows in pairs:
        ex.register_collection(name, _stub(rows))
    return ex


# 1 — compliance.snapshots returns all rows --------------------------------

def test_snapshots_returns_all_rows() -> None:
    ex = _ex(("compliance.snapshots", _SNAPSHOTS))
    ast = ForeachNode(
        var="c",
        collection=AttrRef(["compliance", "snapshots"]),
        where_clauses=[],
        select=None,
    )
    result = ex.run(ast, conn=None)
    assert len(result) == 2
    assert result[0]["pi_number"] == "PI 22.1"


# 2 — compliance.controls returns all rows ---------------------------------

def test_controls_returns_all_rows() -> None:
    ex = _ex(("compliance.controls", _CONTROLS))
    ast = ForeachNode(
        var="c",
        collection=AttrRef(["compliance", "controls"]),
        where_clauses=[],
        select=None,
    )
    result = ex.run(ast, conn=None)
    assert len(result) == 2
    assert result[0]["control_id"] == "AC-2"


# 3 — compliance.violations returns all rows --------------------------------

def test_violations_returns_all_rows() -> None:
    ex = _ex(("compliance.violations", _VIOLATIONS))
    ast = ForeachNode(
        var="c",
        collection=AttrRef(["compliance", "violations"]),
        where_clauses=[],
        select=None,
    )
    result = ex.run(ast, conn=None)
    assert len(result) == 2
    assert result[0]["weakness_id"] == "WK-001"


# 4 — WHERE filter on compliance.snapshots ----------------------------------

def test_snapshots_where_filter_by_pi_number() -> None:
    ex = _ex(("compliance.snapshots", _SNAPSHOTS))
    ast = ForeachNode(
        var="c",
        collection=AttrRef(["compliance", "snapshots"]),
        where_clauses=[
            WhereNode(BinOp("==", AttrRef(["c", "pi_number"]), Literal("PI 22.1")))
        ],
        select=None,
    )
    result = ex.run(ast, conn=None)
    assert len(result) == 1
    assert result[0]["pi_number"] == "PI 22.1"
    assert result[0]["compliance_score_end"] == 0.85


# 5 — parse() + execute against in-memory SQLite via snapshots_adapter ------

def test_parse_and_execute_snapshots_query() -> None:
    from tools.iqe.adapters.compliance import snapshots_adapter  # triggers registration

    conn = sqlite3.connect(":memory:")
    conn.executescript(_SNAPSHOTS_SCHEMA)
    conn.execute(
        "INSERT INTO pi_compliance_tracking "
        "(project_id, pi_number, compliance_score_start, compliance_score_end) "
        "VALUES ('p1', 'PI 22.3', 0.80, 0.93)"
    )
    conn.commit()

    ast = parse("foreach c in compliance.snapshots select *")
    ex = Executor()
    ex.register_collection("compliance.snapshots", snapshots_adapter)
    result = ex.run(ast, conn=conn)

    assert len(result) == 1
    assert result[0]["pi_number"] == "PI 22.3"
    assert result[0]["compliance_score_end"] == 0.93
    conn.close()
