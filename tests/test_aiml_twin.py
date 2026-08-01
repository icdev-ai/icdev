# CUI // SP-CTI — AIML twin tests (twx-cov-02 wave-2)
"""Tests for the wave-2 AIML twin + its twin_core adapter, using a temp SQLite DB
wrapped in StorageConnection (the AIML canvas get_connection() pattern)."""
from __future__ import annotations

import sqlite3

import pytest

from tools.aiml_canvas import twin as aiml_twin
from tools.twin_core.registry import TwinRegistry

_DDL = [
    "CREATE TABLE aiml_designs (id TEXT PRIMARY KEY, name TEXT, graph_json TEXT)",
    "CREATE TABLE aiml_twin_snapshots (id TEXT PRIMARY KEY, design_id TEXT, label TEXT, graph_json TEXT, "
    "node_count INTEGER, edge_count INTEGER, created_by TEXT, created_at TEXT)",
    "CREATE TABLE aiml_simulations (id TEXT PRIMARY KEY, design_id TEXT, baseline_snap_id TEXT, "
    "delta_graph_json TEXT, verdict TEXT, findings_json TEXT, diff_json TEXT, created_by TEXT, created_at TEXT)",
    "CREATE TABLE aiml_assessments (id TEXT PRIMARY KEY, design_id TEXT, framework_id TEXT, framework_name TEXT, "
    "findings_json TEXT, score REAL, passed INTEGER, created_at TEXT)",
]


def _conn_factory(db_path):
    from tools.db.storage import StorageConnection

    def _make():
        raw = sqlite3.connect(db_path)
        raw.row_factory = sqlite3.Row
        return StorageConnection(raw, "sqlite")

    return _make


@pytest.fixture
def aiml_db(tmp_path, monkeypatch):
    db = str(tmp_path / "aiml.db")
    raw = sqlite3.connect(db)
    for stmt in _DDL:
        raw.execute(stmt)
    graph = '{"nodes":[{"id":"m1","type":"model","label":"LLM"},{"id":"g1","type":"guardrail"}],' \
            '"edges":[{"source":"m1","target":"g1"}]}'
    raw.execute("INSERT INTO aiml_designs (id,name,graph_json) VALUES (?,?,?)", ("d1", "D", graph))
    raw.commit(); raw.close()
    monkeypatch.setattr(aiml_twin, "_get_connection", _conn_factory(db))
    return db


def test_snapshot_and_dedup(aiml_db):
    s1 = aiml_twin.take_snapshot("d1", label="m1")
    assert s1["node_count"] == 2 and s1["edge_count"] == 1
    s2 = aiml_twin.take_snapshot("d1", label="m2")
    assert s2.get("skipped") is True


def test_missing_design_raises(aiml_db):
    with pytest.raises(ValueError):
        aiml_twin.take_snapshot("nope")


def test_simulate_failing_framework_and_node_removal(aiml_db):
    raw = sqlite3.connect(aiml_db)
    raw.execute("INSERT INTO aiml_assessments (id,design_id,framework_id,framework_name,score,passed,created_at) "
                "VALUES (?,?,?,?,?,?,?)", ("a1", "d1", "nist-ai-rmf", "NIST AI RMF", 0.4, 0, "2026-07-25T00:00:00"))
    raw.commit(); raw.close()
    # Delta removes guardrail node g1 on a failing-governance base -> fail.
    delta = {"nodes": [{"id": "m1", "type": "model"}], "edges": []}
    res = aiml_twin.simulate_delta("d1", delta)
    assert res["verdict"] == "fail"
    assert res["diff"]["current_failing_frameworks"] == 1
    assert res["diff"]["removed_nodes"] == 1
    assert any(f["category"] == "compliance" for f in res["findings"])


def test_simulate_clean_passes(aiml_db):
    delta = {"nodes": [{"id": "m1", "type": "model"}, {"id": "g1", "type": "guardrail"}], "edges": []}
    res = aiml_twin.simulate_delta("d1", delta)
    assert res["verdict"] == "pass"


def test_adapter_registered_and_canonicalizes(aiml_db):
    keys = set(TwinRegistry.discover(force=True))
    assert "aimc" in keys
    adapter = TwinRegistry.get("aimc")
    assert adapter.method == "assessment-analysis"
    out = adapter.simulate_delta("d1", {"nodes": [{"id": "m1"}], "edges": []})
    assert out["canvas"] == "aimc"
    assert out["verdict"] in ("pass", "warn", "fail")
    for v in out["violations"]:
        assert v["method"] == "assessment-analysis"
