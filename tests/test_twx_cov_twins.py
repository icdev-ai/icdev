# CUI // SP-CTI — QDC + AADC twin tests (twx-cov-01)
"""Tests for the two new minimal canvas twins and their twin_core adapters.

The twins use their canvas's own get_connection() (a separate canvas DB), so we
point them at a temp SQLite file wrapped in StorageConnection (matching how the
canvas get_connection() wraps its raw connection for %s->? translation). A fresh
wrapped connection is returned per call because the twins close after each op.
"""
from __future__ import annotations

import sqlite3

import pytest

from tools.agentic_ai_canvas import twin as aadc_twin
from tools.qdc_canvas import twin as qdc_twin
from tools.twin_core.registry import TwinRegistry

_QDC_DDL = [
    "CREATE TABLE qdc_designs (id TEXT PRIMARY KEY, name TEXT, graph_json TEXT)",
    "CREATE TABLE qdc_twin_snapshots (id TEXT PRIMARY KEY, design_id TEXT, label TEXT, graph_json TEXT, "
    "node_count INTEGER, edge_count INTEGER, created_by TEXT, created_at TEXT)",
    "CREATE TABLE qdc_simulations (id TEXT PRIMARY KEY, design_id TEXT, baseline_snap_id TEXT, "
    "delta_graph_json TEXT, verdict TEXT, findings_json TEXT, diff_json TEXT, created_by TEXT, created_at TEXT)",
    "CREATE TABLE qdc_gate_results (id TEXT PRIMARY KEY, design_id TEXT, gate_id TEXT, status TEXT, executed_at TEXT)",
    "CREATE TABLE qdc_uqs_history (id TEXT PRIMARY KEY, design_id TEXT, uqs_score REAL, computed_at TEXT)",
]
_AADC_DDL = [
    "CREATE TABLE aadc_designs (id TEXT PRIMARY KEY, name TEXT, graph_json TEXT, safety_impacting INTEGER, "
    "rights_impacting INTEGER, autonomy_max INTEGER, hitl_required INTEGER)",
    "CREATE TABLE aadc_twin_snapshots (id TEXT PRIMARY KEY, design_id TEXT, label TEXT, graph_json TEXT, "
    "node_count INTEGER, edge_count INTEGER, created_by TEXT, created_at TEXT)",
    "CREATE TABLE aadc_simulations (id TEXT PRIMARY KEY, design_id TEXT, baseline_snap_id TEXT, "
    "delta_graph_json TEXT, verdict TEXT, findings_json TEXT, diff_json TEXT, created_by TEXT, created_at TEXT)",
]


def _conn_factory(db_path):
    from tools.db.storage import StorageConnection

    def _make():
        raw = sqlite3.connect(db_path)
        raw.row_factory = sqlite3.Row
        return StorageConnection(raw, "sqlite")

    return _make


def _seed(db_path, ddl, inserts):
    raw = sqlite3.connect(db_path)
    for stmt in ddl:
        raw.execute(stmt)
    for sql, params in inserts:
        raw.execute(sql, params)
    raw.commit()
    raw.close()


@pytest.fixture
def qdc_db(tmp_path, monkeypatch):
    db = str(tmp_path / "qdc.db")
    graph = '{"nodes":[{"id":"g1","type":"quality-gate","label":"Coverage"},{"id":"deploy","type":"stage"}],' \
            '"edges":[{"source":"g1","target":"deploy"}]}'
    _seed(db, _QDC_DDL, [("INSERT INTO qdc_designs (id,name,graph_json) VALUES (?,?,?)", ("d1", "D", graph))])
    monkeypatch.setattr(qdc_twin, "_get_connection", _conn_factory(db))
    return db


@pytest.fixture
def aadc_db(tmp_path, monkeypatch):
    db = str(tmp_path / "aadc.db")
    graph = '{"nodes":[{"id":"a1","type":"orchestrator","label":"Orch"},' \
            '{"id":"a2","type":"autonomous-agent"},{"id":"a3","type":"autonomous-agent"}],' \
            '"edges":[{"source":"a1","target":"a2"},{"source":"a2","target":"a3"}]}'
    _seed(db, _AADC_DDL, [("INSERT INTO aadc_designs (id,name,graph_json,safety_impacting,rights_impacting,autonomy_max,hitl_required) "
                           "VALUES (?,?,?,?,?,?,?)", ("m1", "M", graph, 0, 0, 3, 0))])
    monkeypatch.setattr(aadc_twin, "_get_connection", _conn_factory(db))
    return db


# ── QDC ───────────────────────────────────────────────────────────────────────

def test_qdc_snapshot_and_dedup(qdc_db):
    s1 = qdc_twin.take_snapshot("d1", label="manual-1")
    assert s1["node_count"] == 2 and s1["edge_count"] == 1
    s2 = qdc_twin.take_snapshot("d1", label="manual-2")  # identical graph -> dedup
    assert s2.get("skipped") is True
    assert qdc_twin.list_snapshots("d1")  # at least one row


def test_qdc_missing_design_raises(qdc_db):
    with pytest.raises(ValueError):
        qdc_twin.take_snapshot("nope")


def test_qdc_simulate_failing_gate_and_removal(qdc_db):
    # Seed a failing gate result -> current posture unhealthy.
    raw = sqlite3.connect(qdc_db)
    raw.execute("INSERT INTO qdc_gate_results (id,design_id,gate_id,status,executed_at) VALUES (?,?,?,?,?)",
                ("gr1", "d1", "g1", "fail", "2026-07-25T00:00:00"))
    raw.commit(); raw.close()
    # Delta removes the quality-gate node g1 -> regression on a failing base -> fail.
    delta = {"nodes": [{"id": "deploy", "type": "stage"}], "edges": []}
    res = qdc_twin.simulate_delta("d1", delta)
    assert res["verdict"] == "fail"
    cats = {f["category"] for f in res["findings"]}
    assert "compliance" in cats
    assert res["diff"]["current_failing_gates"] == 1
    assert res["diff"]["removed_gate_nodes"] == 1


def test_qdc_simulate_clean_passes(qdc_db):
    # No gate results, delta keeps all gates -> pass.
    delta = {"nodes": [{"id": "g1", "type": "quality-gate"}, {"id": "deploy", "type": "stage"}], "edges": []}
    res = qdc_twin.simulate_delta("d1", delta)
    assert res["verdict"] == "pass"


# ── AADC ──────────────────────────────────────────────────────────────────────

def test_aadc_snapshot(aadc_db):
    s = aadc_twin.take_snapshot("m1", label="manual")
    assert s["node_count"] == 3 and s["edge_count"] == 2


def test_aadc_cascade_warn(aadc_db):
    # a1 fails -> cascades to a2, a3 (2 downstream). Not safety/rights impacting -> warn.
    res = aadc_twin.simulate_delta("m1", {"fail_nodes": ["a1"]})
    assert res["verdict"] == "warn"
    assert set(res["impacted_agents"]) == {"a2", "a3"}


def test_aadc_isolated_failure_passes(aadc_db):
    # a3 is a leaf -> no downstream cascade -> pass.
    res = aadc_twin.simulate_delta("m1", {"fail_nodes": ["a3"]})
    assert res["verdict"] == "pass"


def test_aadc_safety_impacting_escalates_to_fail(aadc_db):
    raw = sqlite3.connect(aadc_db)
    raw.execute("UPDATE aadc_designs SET safety_impacting=1 WHERE id='m1'")
    raw.commit(); raw.close()
    res = aadc_twin.simulate_delta("m1", {"fail_nodes": ["a1"]})
    assert res["verdict"] == "fail"          # any cascade on a safety-impacting design
    assert res["findings"][0]["severity"] == "critical"


def test_aadc_cascade_pure_function():
    graph = {"nodes": [{"id": "x"}, {"id": "y"}, {"id": "z"}],
             "edges": [{"source": "x", "target": "y"}, {"source": "y", "target": "z"}]}
    assert aadc_twin._cascade(graph, {"x"}) == ["y", "z"]
    assert aadc_twin._cascade(graph, {"z"}) == []


# ── adapters ──────────────────────────────────────────────────────────────────

def test_qdc_aadc_adapters_registered():
    keys = set(TwinRegistry.discover(force=True))
    assert {"qdc", "aadc"}.issubset(keys)
    assert TwinRegistry.get("qdc").method == "gate-analysis"
    assert TwinRegistry.get("aadc").method == "cascade-analysis"


def test_qdc_adapter_canonicalizes(qdc_db):
    delta = {"nodes": [{"id": "deploy", "type": "stage"}], "edges": []}
    out = TwinRegistry.get("qdc").simulate_delta("d1", delta)
    assert out["canvas"] == "qdc"
    assert out["verdict"] in ("pass", "warn", "fail")
    for v in out["violations"]:
        assert v["method"] == "gate-analysis"


def test_aadc_adapter_canonicalizes(aadc_db):
    out = TwinRegistry.get("aadc").simulate_delta("m1", {"fail_nodes": ["a1"]})
    assert out["canvas"] == "aadc"
    assert out["verdict"] == "warn"
    assert all(v["category"] == "security" for v in out["violations"])
