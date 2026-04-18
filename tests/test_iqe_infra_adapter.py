# CUI // SP-CTI
"""Unit tests for tools/iqe/adapters/infra.py — 5 cases."""
from __future__ import annotations

import sqlite3

from tools.iqe.ast_nodes import AttrRef, BinOp, ForeachNode, Literal, WhereNode
from tools.iqe.executor import Executor
from tools.iqe.parser import parse

# ---- Stub data -------------------------------------------------------

_RESOURCES = [
    {
        "id": 1, "csp": "aws", "region": "us-east-1",
        "resource_type": "ec2", "resource_name": "web-server-01",
        "classification": "CUI", "tags": '{"env":"prod"}',
        "cost_per_month": 450.0, "config": '{"instance_type":"t3.large"}',
        "created_at": "2025-01-15",
    },
    {
        "id": 2, "csp": "azure", "region": "usgovvirginia",
        "resource_type": "vm", "resource_name": "db-server-01",
        "classification": "UNCLASSIFIED", "tags": '{"env":"dev"}',
        "cost_per_month": 120.0, "config": '{"size":"Standard_D2s_v3"}',
        "created_at": "2025-02-01",
    },
    {
        "id": 3, "csp": "aws", "region": "us-gov-west-1",
        "resource_type": "rds", "resource_name": "pg-primary",
        "classification": "CUI", "tags": '{"env":"prod"}',
        "cost_per_month": 980.0, "config": '{"engine":"postgres14"}',
        "created_at": "2025-03-10",
    },
]

_SNAPSHOTS = [
    {
        "id": 1, "snapshot_id": "snap-20250401-001", "taken_at": "2025-04-01T00:00:00",
        "csp": "aws", "region": "us-east-1", "classification": "CUI",
        "resource_count": 42, "baseline_hash": "abc123", "notes": "Q2 baseline",
        "created_at": "2025-04-01",
    },
    {
        "id": 2, "snapshot_id": "snap-20250701-001", "taken_at": "2025-07-01T00:00:00",
        "csp": "azure", "region": "usgovvirginia", "classification": "UNCLASSIFIED",
        "resource_count": 17, "baseline_hash": "def456", "notes": "Q3 baseline",
        "created_at": "2025-07-01",
    },
]

_RESOURCES_SCHEMA = """
CREATE TABLE IF NOT EXISTS idc_infra_resources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    csp TEXT NOT NULL,
    region TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    resource_name TEXT,
    classification TEXT DEFAULT 'UNCLASSIFIED',
    tags TEXT,
    cost_per_month REAL DEFAULT 0.0,
    config TEXT,
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


# 1 — infra.resources returns all rows ------------------------------------

def test_resources_returns_all_rows() -> None:
    ex = _ex(("infra.resources", _RESOURCES))
    ast = ForeachNode(
        var="r",
        collection=AttrRef(["infra", "resources"]),
        where_clauses=[],
        select=None,
    )
    result = ex.run(ast, conn=None)
    assert len(result) == 3
    assert result[0]["resource_name"] == "web-server-01"


# 2 — infra.snapshots returns all rows ------------------------------------

def test_snapshots_returns_all_rows() -> None:
    ex = _ex(("infra.snapshots", _SNAPSHOTS))
    ast = ForeachNode(
        var="s",
        collection=AttrRef(["infra", "snapshots"]),
        where_clauses=[],
        select=None,
    )
    result = ex.run(ast, conn=None)
    assert len(result) == 2
    assert result[0]["snapshot_id"] == "snap-20250401-001"


# 3 — WHERE filter by csp -------------------------------------------------

def test_resources_where_filter_by_csp() -> None:
    ex = _ex(("infra.resources", _RESOURCES))
    ast = ForeachNode(
        var="r",
        collection=AttrRef(["infra", "resources"]),
        where_clauses=[
            WhereNode(BinOp("==", AttrRef(["r", "csp"]), Literal("aws")))
        ],
        select=None,
    )
    result = ex.run(ast, conn=None)
    assert len(result) == 2
    assert all(r["csp"] == "aws" for r in result)


# 4 — WHERE filter by region AND classification ---------------------------

def test_resources_where_filter_by_region_and_classification() -> None:
    ex = _ex(("infra.resources", _RESOURCES))
    ast = ForeachNode(
        var="r",
        collection=AttrRef(["infra", "resources"]),
        where_clauses=[
            WhereNode(BinOp(
                "and",
                BinOp("==", AttrRef(["r", "region"]), Literal("us-east-1")),
                BinOp("==", AttrRef(["r", "classification"]), Literal("CUI")),
            ))
        ],
        select=None,
    )
    result = ex.run(ast, conn=None)
    assert len(result) == 1
    assert result[0]["resource_name"] == "web-server-01"
    assert result[0]["classification"] == "CUI"


# 5 — parse() + execute against in-memory SQLite via resources_adapter ----

def test_parse_and_execute_resources_query() -> None:
    from tools.iqe.adapters.infra import resources_adapter  # triggers registration

    conn = sqlite3.connect(":memory:")
    conn.executescript(_RESOURCES_SCHEMA)
    conn.execute(
        "INSERT INTO idc_infra_resources "
        "(csp, region, resource_type, resource_name, classification, cost_per_month) "
        "VALUES ('gcp', 'us-central1', 'gke', 'k8s-cluster-01', 'CUI', 1200.0)"
    )
    conn.commit()

    ast = parse("foreach r in infra.resources select *")
    ex = Executor()
    ex.register_collection("infra.resources", resources_adapter)
    result = ex.run(ast, conn=conn)

    assert len(result) == 1
    assert result[0]["resource_name"] == "k8s-cluster-01"
    assert result[0]["csp"] == "gcp"
    assert result[0]["classification"] == "CUI"
    conn.close()
