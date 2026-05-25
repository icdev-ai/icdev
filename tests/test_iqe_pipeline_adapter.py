# CUI // SP-CTI
"""Unit tests for tools/iqe/adapters/pipeline.py — 5 cases."""
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
        "snapshot_type": "baseline",
        "label": "Q2 baseline",
        "nodes_json": json.dumps([
            {"id": "n1", "type": "build-runner", "status": "active"},
            {"id": "n2", "type": "test-gate", "status": "active"},
        ]),
        "edges_json": json.dumps([
            {"source": "n1", "target": "n2"},
        ]),
        "meta_json": "{}",
        "created_by": "ci",
        "created_at": "2025-04-01T00:00:00",
    },
    {
        "id": "snap-002",
        "pipeline_id": "pipe-beta",
        "snapshot_type": "delta",
        "label": "proposed change",
        "nodes_json": json.dumps([
            {"id": "n3", "type": "deploy-gate", "status": "pending"},
        ]),
        "edges_json": json.dumps([]),
        "meta_json": "{}",
        "created_by": "ci",
        "created_at": "2025-05-01T00:00:00",
    },
]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pipeline_snapshots (
    id             TEXT PRIMARY KEY,
    pipeline_id    TEXT NOT NULL,
    snapshot_type  TEXT NOT NULL DEFAULT 'baseline',
    label          TEXT,
    nodes_json     TEXT NOT NULL DEFAULT '[]',
    edges_json     TEXT NOT NULL DEFAULT '[]',
    meta_json      TEXT NOT NULL DEFAULT '{}',
    created_by     TEXT,
    created_at     TEXT NOT NULL
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
        nodes = json.loads(snap["nodes_json"] or "[]")
        for node in nodes:
            row = {
                "snapshot_id": snap["id"],
                "pipeline_id": snap["pipeline_id"],
                "snapshot_type": snap["snapshot_type"],
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
        edges = json.loads(snap["edges_json"] or "[]")
        for edge in edges:
            row = {
                "snapshot_id": snap["id"],
                "pipeline_id": snap["pipeline_id"],
                "snapshot_type": snap["snapshot_type"],
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
    assert result[1]["snapshot_type"] == "delta"


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
    assert result[0]["snapshot_type"] == "baseline"


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
        "INSERT INTO pipeline_snapshots "
        "(id, pipeline_id, snapshot_type, label, nodes_json, edges_json, meta_json, created_by, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "snap-live-01", "pipe-gamma", "baseline", "live baseline",
            json.dumps([{"id": "n9", "type": "security-gate"}]),
            json.dumps([]),
            "{}", "test", "2025-06-01T00:00:00",
        ),
    )
    conn.commit()

    ast = parse("foreach s in pipeline.snapshots select *")
    ex = Executor()
    ex.register_collection("pipeline.snapshots", snapshots_adapter)
    result = ex.run(ast, conn=conn)

    assert len(result) == 1
    assert result[0]["pipeline_id"] == "pipe-gamma"
    assert result[0]["snapshot_type"] == "baseline"
    conn.close()
