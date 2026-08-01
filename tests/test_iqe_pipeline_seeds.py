# CUI // SP-CTI
"""Tests for context/iqe/queries/pipeline/*.iqe (5 seed queries).

Each query must:
  • parse without error and match expected AST shape
  • return a deterministic violation count against a seeded fixture DB
"""
from __future__ import annotations

import json
import pathlib
import sqlite3

from tools.iqe.adapters.pipeline import nodes_adapter
from tools.iqe.ast_nodes import AttrRef, Literal
from tools.iqe.executor import Executor
from tools.iqe.parser import parse

_QUERY_DIR = (
    pathlib.Path(__file__).parent.parent
    / "context" / "iqe" / "queries" / "pipeline"
)


def _read(name: str) -> str:
    return (_QUERY_DIR / name).read_text(encoding="utf-8")


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


def _pipeline_db() -> sqlite3.Connection:
    """In-memory fixture DB with two snapshots and six nodes total.

    snap-001 (pipe-001):
      build-01 — plaintext env, no signing, slsa=1, no conflict
      build-02 — vault env,     signing,    slsa=3, conflict=true
      publish-01 — artifact-publisher, no tag

    snap-002 (pipe-002):
      build-03 — plaintext env, no signing, slsa=2, no conflict
      publish-02 — artifact-publisher, tagged v1.2.3
      publish-03 — artifact-publisher, no tag

    Expected violation counts:
      secrets_in_env      → 2  (build-01, build-03)
      untagged_artifacts  → 2  (publish-01, publish-03)
      missing_signing_step→ 2  (build-01, build-03)
      parallel_overlap    → 1  (build-02)
      slsa_l3_violations  → 2  (build-01, build-03)
    """
    conn = sqlite3.connect(":memory:")
    conn.executescript(_SCHEMA)
    snap1_graph = json.dumps({
        "nodes": [
            {
                "id": "build-01", "type": "build-runner",
                "env_source": "plaintext", "signing_enabled": "false",
                "slsa_level": 1, "parallel_conflict": "false",
            },
            {
                "id": "build-02", "type": "build-runner",
                "env_source": "vault", "signing_enabled": "true",
                "slsa_level": 3, "parallel_conflict": "true",
            },
            {
                "id": "publish-01", "type": "artifact-publisher",
                "artifact_tag": None, "env_source": "vault",
            },
        ],
        "edges": [],
    })
    snap2_graph = json.dumps({
        "nodes": [
            {
                "id": "build-03", "type": "build-runner",
                "env_source": "plaintext", "signing_enabled": "false",
                "slsa_level": 2, "parallel_conflict": "false",
            },
            {
                "id": "publish-02", "type": "artifact-publisher",
                "artifact_tag": "v1.2.3", "env_source": "vault",
            },
            {
                "id": "publish-03", "type": "artifact-publisher",
                "artifact_tag": None, "env_source": "vault",
            },
        ],
        "edges": [],
    })
    conn.executemany(
        "INSERT INTO pdc_snapshots "
        "(id, pipeline_id, label, graph_json, node_count, edge_count, created_by, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        [
            ("snap-001", "pipe-001", "Q1 baseline",
             snap1_graph, 3, 0, "ci", "2025-01-01T00:00:00"),
            ("snap-002", "pipe-002", "Q2 baseline",
             snap2_graph, 3, 0, "ci", "2025-04-01T00:00:00"),
        ],
    )
    conn.commit()
    return conn


def _ex() -> tuple[Executor, sqlite3.Connection]:
    conn = _pipeline_db()
    ex = Executor()
    ex.register_collection("pipeline.nodes", nodes_adapter)
    return ex, conn


# ============================================================================
# Test 1 — secrets_in_env.iqe
# ============================================================================

def test_secrets_in_env_parses() -> None:
    q = parse(_read("secrets_in_env.iqe"))
    assert q.var == "n"
    assert q.collection == AttrRef(["pipeline", "nodes"])
    assert len(q.where_clauses) == 1
    assert q.where_clauses[0].predicate.op == "=="
    assert q.where_clauses[0].predicate.right == Literal("plaintext")
    fields = [f.parts[-1] for f in q.select.fields]
    assert "pipeline_id" in fields
    assert "id" in fields
    assert "env_source" in fields


def test_secrets_in_env_violation_count() -> None:
    ex, conn = _ex()
    ast = parse(_read("secrets_in_env.iqe"))
    result = ex.run(ast, conn=conn)
    conn.close()
    # build-01 (pipe-001) + build-03 (pipe-002): both env_source="plaintext"
    assert len(result) == 2
    ids = {r["id"] for r in result}
    assert "build-01" in ids
    assert "build-03" in ids


# ============================================================================
# Test 2 — untagged_artifacts.iqe
# ============================================================================

def test_untagged_artifacts_parses() -> None:
    q = parse(_read("untagged_artifacts.iqe"))
    assert q.var == "n"
    assert q.collection == AttrRef(["pipeline", "nodes"])
    assert len(q.where_clauses) == 2
    assert q.where_clauses[0].predicate.op == "=="
    assert q.where_clauses[0].predicate.right == Literal("artifact-publisher")
    assert q.where_clauses[1].predicate.op == "=="
    assert q.where_clauses[1].predicate.right == Literal(None)
    fields = [f.parts[-1] for f in q.select.fields]
    assert "pipeline_id" in fields
    assert "id" in fields
    assert "node_type" in fields


def test_untagged_artifacts_violation_count() -> None:
    ex, conn = _ex()
    ast = parse(_read("untagged_artifacts.iqe"))
    result = ex.run(ast, conn=conn)
    conn.close()
    # publish-01 + publish-03: artifact-publisher with artifact_tag=None
    # publish-02 excluded: has tag "v1.2.3"
    assert len(result) == 2
    ids = {r["id"] for r in result}
    assert "publish-01" in ids
    assert "publish-03" in ids


# ============================================================================
# Test 3 — missing_signing_step.iqe
# ============================================================================

def test_missing_signing_step_parses() -> None:
    q = parse(_read("missing_signing_step.iqe"))
    assert q.var == "n"
    assert q.collection == AttrRef(["pipeline", "nodes"])
    assert len(q.where_clauses) == 2
    assert q.where_clauses[0].predicate.op == "=="
    assert q.where_clauses[0].predicate.right == Literal("build-runner")
    assert q.where_clauses[1].predicate.op == "=="
    assert q.where_clauses[1].predicate.right == Literal("false")
    fields = [f.parts[-1] for f in q.select.fields]
    assert "pipeline_id" in fields
    assert "id" in fields
    assert "signing_enabled" in fields


def test_missing_signing_step_violation_count() -> None:
    ex, conn = _ex()
    ast = parse(_read("missing_signing_step.iqe"))
    result = ex.run(ast, conn=conn)
    conn.close()
    # build-01 + build-03: build-runner with signing_enabled="false"
    # build-02 excluded: signing_enabled="true"
    assert len(result) == 2
    ids = {r["id"] for r in result}
    assert "build-01" in ids
    assert "build-03" in ids


# ============================================================================
# Test 4 — parallel_overlap.iqe
# ============================================================================

def test_parallel_overlap_parses() -> None:
    q = parse(_read("parallel_overlap.iqe"))
    assert q.var == "n"
    assert q.collection == AttrRef(["pipeline", "nodes"])
    assert len(q.where_clauses) == 1
    assert q.where_clauses[0].predicate.op == "=="
    assert q.where_clauses[0].predicate.right == Literal("true")
    fields = [f.parts[-1] for f in q.select.fields]
    assert "pipeline_id" in fields
    assert "id" in fields
    assert "node_type" in fields


def test_parallel_overlap_violation_count() -> None:
    ex, conn = _ex()
    ast = parse(_read("parallel_overlap.iqe"))
    result = ex.run(ast, conn=conn)
    conn.close()
    # Only build-02 has parallel_conflict="true"
    assert len(result) == 1
    assert result[0]["id"] == "build-02"
    assert result[0]["pipeline_id"] == "pipe-001"


# ============================================================================
# Test 5 — slsa_l3_violations.iqe
# ============================================================================

def test_slsa_l3_violations_parses() -> None:
    q = parse(_read("slsa_l3_violations.iqe"))
    assert q.var == "n"
    assert q.collection == AttrRef(["pipeline", "nodes"])
    assert len(q.where_clauses) == 2
    assert q.where_clauses[0].predicate.op == "=="
    assert q.where_clauses[0].predicate.right == Literal("build-runner")
    pred = q.where_clauses[1].predicate
    assert pred.op == "<"
    assert isinstance(pred.left, AttrRef)
    assert pred.left.parts[-1] == "slsa_level"
    assert pred.right == Literal(3)
    fields = [f.parts[-1] for f in q.select.fields]
    assert "pipeline_id" in fields
    assert "id" in fields
    assert "slsa_level" in fields


def test_slsa_l3_violations_violation_count() -> None:
    ex, conn = _ex()
    ast = parse(_read("slsa_l3_violations.iqe"))
    result = ex.run(ast, conn=conn)
    conn.close()
    # build-01 (slsa=1 < 3) + build-03 (slsa=2 < 3)
    # build-02 excluded: slsa=3 is not < 3
    assert len(result) == 2
    ids = {r["id"] for r in result}
    assert "build-01" in ids
    assert "build-03" in ids
    assert all(r["slsa_level"] < 3 for r in result)
