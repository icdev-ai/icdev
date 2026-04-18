# CUI // SP-CTI
"""Unit tests for tools/iqe/adapters/observability.py — 5 cases."""
from __future__ import annotations

import json
import sqlite3

from tools.iqe.ast_nodes import AttrRef, BinOp, ForeachNode, Literal, WhereNode
from tools.iqe.executor import Executor
from tools.iqe.parser import parse

# ── Stub technique rows ────────────────────────────────────────────────────────

_TECHNIQUES = [
    {
        "id": "T1059",
        "name": "Command and Scripting Interpreter",
        "description": "Adversaries may abuse command interpreters.",
        "tactic_id": "TA0002",
        "is_sub_technique": False,
        "parent_id": None,
    },
    {
        "id": "T1059.001",
        "name": "PowerShell",
        "description": "Adversaries may abuse PowerShell.",
        "tactic_id": "TA0002",
        "is_sub_technique": True,
        "parent_id": "T1059",
    },
    {
        "id": "T1078",
        "name": "Valid Accounts",
        "description": "Adversaries may use valid accounts.",
        "tactic_id": "TA0001",
        "is_sub_technique": False,
        "parent_id": None,
    },
]

# ── Stub coverage rows ─────────────────────────────────────────────────────────

_COVERAGE = [
    {
        "design_id": "d1",
        "design_name": "SOC Baseline",
        "technique_id": "T1059",
        "technique_name": "Command and Scripting Interpreter",
        "covered": True,
        "coverage_state": "full",
        "tactic_id": "TA0002",
    },
    {
        "design_id": "d1",
        "design_name": "SOC Baseline",
        "technique_id": "T1053",
        "technique_name": "Scheduled Task/Job",
        "covered": False,
        "coverage_state": "none",
        "tactic_id": "TA0003",
    },
    {
        "design_id": "d1",
        "design_name": "SOC Baseline",
        "technique_id": "T1021",
        "technique_name": "Remote Services",
        "covered": False,
        "coverage_state": "none",
        "tactic_id": "TA0008",
    },
]

# ── SQLite schema for coverage / gaps tests ────────────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS observability_designs (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    graph_json  TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}'
);
"""

_GRAPH_JSON = json.dumps(
    {
        "nodes": [
            {
                "id": "baseline",
                "type": "cmp-baseline",
                "label": "MITRE Baseline",
                "config_json": json.dumps(
                    {
                        "techniques": [
                            {"id": "T1059", "name": "Command and Scripting Interpreter", "covered": True, "tactic_id": "TA0002"},
                            {"id": "T1053", "name": "Scheduled Task/Job", "covered": False, "tactic_id": "TA0003"},
                            {"id": "T1078", "name": "Valid Accounts", "covered": True, "tactic_id": "TA0001"},
                            {"id": "T1021", "name": "Remote Services", "covered": False, "tactic_id": "TA0008"},
                        ]
                    }
                ),
            }
        ],
        "edges": [],
    }
)


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(_SCHEMA)
    conn.execute(
        "INSERT INTO observability_designs (id, name, graph_json) VALUES (?, ?, ?)",
        ("d1", "SOC Baseline", _GRAPH_JSON),
    )
    conn.commit()
    return conn


def _stub(rows: list[dict]):
    def _adapter(_conn):
        return list(rows)

    return _adapter


def _ex(*pairs: tuple[str, list[dict]]) -> Executor:
    ex = Executor()
    for name, rows in pairs:
        ex.register_collection(name, _stub(rows))
    return ex


# 1 — mitre.techniques returns all technique rows ───────────────────────────────

def test_techniques_returns_all_rows() -> None:
    ex = _ex(("mitre.techniques", _TECHNIQUES))
    ast = ForeachNode(
        var="t",
        collection=AttrRef(["mitre", "techniques"]),
        where_clauses=[],
        select=None,
    )
    result = ex.run(ast, conn=None)
    assert len(result) == 3
    ids = {r["id"] for r in result}
    assert ids == {"T1059", "T1059.001", "T1078"}


# 2 — mitre.techniques WHERE tactic_id filter ──────────────────────────────────

def test_techniques_filter_by_tactic() -> None:
    ex = _ex(("mitre.techniques", _TECHNIQUES))
    ast = ForeachNode(
        var="t",
        collection=AttrRef(["mitre", "techniques"]),
        where_clauses=[
            WhereNode(BinOp("==", AttrRef(["t", "tactic_id"]), Literal("TA0002")))
        ],
        select=None,
    )
    result = ex.run(ast, conn=None)
    assert len(result) == 2
    assert all(r["tactic_id"] == "TA0002" for r in result)


# 3 — mitre.coverage returns all flattened rows from SQLite ────────────────────

def test_coverage_returns_all_rows() -> None:
    from tools.iqe.adapters.observability import coverage_adapter

    conn = _make_conn()
    rows = coverage_adapter(conn)
    conn.close()

    assert len(rows) == 4
    states = {r["coverage_state"] for r in rows}
    assert states == {"full", "none"}
    design_ids = {r["design_id"] for r in rows}
    assert design_ids == {"d1"}


# 4 — mitre.coverage WHERE coverage_state == 'full' ────────────────────────────

def test_coverage_filter_by_state() -> None:
    ex = _ex(("mitre.coverage", _COVERAGE))
    ast = ForeachNode(
        var="c",
        collection=AttrRef(["mitre", "coverage"]),
        where_clauses=[
            WhereNode(BinOp("==", AttrRef(["c", "coverage_state"]), Literal("full")))
        ],
        select=None,
    )
    result = ex.run(ast, conn=None)
    assert len(result) == 1
    assert result[0]["technique_id"] == "T1059"
    assert result[0]["coverage_state"] == "full"


# 5 — mitre.gaps returns only uncovered; parse + execute smoke test ─────────────

def test_gaps_returns_only_uncovered_via_parse_execute() -> None:
    from tools.iqe.adapters.observability import gaps_adapter

    conn = _make_conn()
    ast = parse("foreach g in mitre.gaps select *")
    ex = Executor()
    ex.register_collection("mitre.gaps", gaps_adapter)
    result = ex.run(ast, conn=conn)
    conn.close()

    assert len(result) == 2
    assert all(r["coverage_state"] == "none" for r in result)
    ids = {r["technique_id"] for r in result}
    assert ids == {"T1053", "T1021"}
    assert all("recommended_signal" in r for r in result)
