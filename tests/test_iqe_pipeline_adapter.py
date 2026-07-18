# CUI // SP-CTI
"""Unit tests for tools/iqe/adapters/pipeline.py — 6 cases.

Adapters read the consolidated ``pdc_snapshots`` canvas store (pdx-data-01),
whose graph is a single ``graph_json`` blob of shape
``{"nodes": [...], "edges": [...]}``. A missing table yields ``[]`` rather
than raising (fresh-DB guard).
"""
from __future__ import annotations

import json
import sqlite3

from tools.iqe.ast_nodes import AttrRef, BinOp, ForeachNode, Literal, WhereNode
from tools.iqe.executor import Executor
from tools.iqe.parser import parse

# ---- Stub data -----------------------------------------------------------

_SNAPSHOTS = [
    {
        "id": "snap-001",
        "pipeline_id": "pipe-alpha",
        "label": "Q2 baseline",
        "graph_json": json.dumps({
            "nodes": [
                {"id": "n1", "type": "build-runner", "status": "active"},
                {"id": "n2", "type": "test-gate", "status": "active"},
            ],
            "edges": [{"id": "e1", "source": "n1", "target": "n2"}],
        }),
        "node_count": 2,
        "edge_count": 1,
        "created_by": "ci",
        "created_at": "2025-04-01T00:00:00",
    },
    {
        "id": "snap-002",
        "pipeline_id": "pipe-beta",
        "label": "proposed change",
        "graph_json": json.dumps({
            "nodes": [{"id": "n3", "type": "deploy-gate", "status": "pending"}],
            "edges": [],
        }),
        "node_count": 1,
        "edge_count": 0,
        "created_by": "ci",
        "created_at": "2025-05-01T00:00:00",
    },
]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pdc_snapshots (
    id             TEXT PRIMARY KEY,
    pipeline_id    TEXT,
    label          TEXT,
    graph_json     TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    node_count     INTEGER DEFAULT 0,
    edge_count     INTEGER DEFAULT 0,
    created_by     TEXT,
    created_at     TEXT
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


def _flat_nodes(snapshots: list[dict]) -> list[dict]:
    """Replicate nodes_adapter flattening logic for test stubs."""
    rows = []
    for snap in snapshots:
        graph = json.loads(snap["graph_json"] or "{}")
        for node in graph.get("nodes") or []:
            row = {
                "snapshot_id": snap["id"],
                "pipeline_id": snap["pipeline_id"],
                "created_at": snap["created_at"],
            }
            node_copy = dict(node)
            if "type" in node_copy:
                row["node_type"] = node_copy.pop("type")
            row.update(node_copy)
            rows.append(row)
    return rows


def _flat_edges(snapshots: list[dict]) -> list[dict]:
    """Replicate edges_adapter flattening logic for test stubs."""
    rows = []
    for snap in snapshots:
        graph = json.loads(snap["graph_json"] or "{}")
        for edge in graph.get("edges") or []:
            row = {
                "snapshot_id": snap["id"],
                "pipeline_id": snap["pipeline_id"],
                "created_at": snap["created_at"],
            }
            row.update(edge)
            rows.append(row)
    return rows


# 1 — pipeline.snapshots returns all rows ----------------------------------

def test_snapshots_returns_all_rows() -> None:
    ex = _ex(("pipeline.snapshots", _SNAPSHOTS))
    ast = ForeachNode(
        var="s",
        collection=AttrRef(["pipeline", "snapshots"]),
        where_clauses=[],
        select=None,
    )
    result = ex.run(ast, conn=None)
    assert len(result) == 2
    assert result[0]["pipeline_id"] == "pipe-alpha"
    assert result[1]["pipeline_id"] == "pipe-beta"


# 2 — pipeline.nodes returns flattened nodes --------------------------------

def test_nodes_returns_flattened_rows() -> None:
    stub_nodes = _flat_nodes(_SNAPSHOTS)
    ex = _ex(("pipeline.nodes", stub_nodes))
    ast = ForeachNode(
        var="n",
        collection=AttrRef(["pipeline", "nodes"]),
        where_clauses=[],
        select=None,
    )
    result = ex.run(ast, conn=None)
    assert len(result) == 3  # 2 nodes in snap-001 + 1 in snap-002
    assert result[0]["node_type"] == "build-runner"
    assert result[2]["node_type"] == "deploy-gate"


# 3 — WHERE filter by pipeline_id on snapshots ------------------------------

def test_snapshots_where_filter_by_pipeline_id() -> None:
    ex = _ex(("pipeline.snapshots", _SNAPSHOTS))
    ast = ForeachNode(
        var="s",
        collection=AttrRef(["pipeline", "snapshots"]),
        where_clauses=[
            WhereNode(BinOp("==", AttrRef(["s", "pipeline_id"]), Literal("pipe-alpha")))
        ],
        select=None,
    )
    result = ex.run(ast, conn=None)
    assert len(result) == 1
    assert result[0]["id"] == "snap-001"
    assert result[0]["label"] == "Q2 baseline"


# 4 — WHERE filter by node_type on nodes ------------------------------------

def test_nodes_where_filter_by_node_type() -> None:
    stub_nodes = _flat_nodes(_SNAPSHOTS)
    ex = _ex(("pipeline.nodes", stub_nodes))
    ast = ForeachNode(
        var="n",
        collection=AttrRef(["pipeline", "nodes"]),
        where_clauses=[
            WhereNode(BinOp("==", AttrRef(["n", "node_type"]), Literal("build-runner")))
        ],
        select=None,
    )
    result = ex.run(ast, conn=None)
    assert len(result) == 1
    assert result[0]["id"] == "n1"
    assert result[0]["pipeline_id"] == "pipe-alpha"


# 5 — parse() + execute against in-memory SQLite via snapshots_adapter -----

def test_parse_and_execute_snapshots_query() -> None:
    from tools.iqe.adapters.pipeline import snapshots_adapter  # triggers registration

    conn = sqlite3.connect(":memory:")
    conn.executescript(_SCHEMA)
    conn.execute(
        "INSERT INTO pdc_snapshots "
        "(id, pipeline_id, label, graph_json, node_count, edge_count, created_by, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "snap-live-01", "pipe-gamma", "live baseline",
            json.dumps({"nodes": [{"id": "n9", "type": "security-gate"}], "edges": []}),
            1, 0, "test", "2025-06-01T00:00:00",
        ),
    )
    conn.commit()

    ast = parse("foreach s in pipeline.snapshots select *")
    ex = Executor()
    ex.register_collection("pipeline.snapshots", snapshots_adapter)
    result = ex.run(ast, conn=conn)

    assert len(result) == 1
    assert result[0]["pipeline_id"] == "pipe-gamma"
    assert result[0]["label"] == "live baseline"
    conn.close()


# 6 — missing table yields [] (fresh-DB guard) -----------------------------

def test_adapters_tolerate_missing_table() -> None:
    from tools.iqe.adapters.pipeline import (
        edges_adapter,
        nodes_adapter,
        snapshots_adapter,
    )

    conn = sqlite3.connect(":memory:")  # no pdc_snapshots table
    assert snapshots_adapter(conn) == []
    assert nodes_adapter(conn) == []
    assert edges_adapter(conn) == []
    conn.close()
