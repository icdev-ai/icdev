# CUI // SP-CTI
"""Parse + execute 5 SDC security seed .iqe queries against a fixture attack graph (dt-sdc-twin-05)."""
from __future__ import annotations

import json
import pathlib
import sqlite3

from tools.iqe.ast_nodes import AttrRef, CollectionCall, ForeachNode
from tools.iqe.executor import Executor
from tools.iqe.parser import parse
from tools.iqe.adapters.security import edges_adapter, nodes_adapter, paths_adapter

_QUERY_DIR = pathlib.Path(__file__).parent.parent / "context" / "iqe" / "queries" / "security"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sdc_attack_snapshots (
    id           TEXT PRIMARY KEY,
    component_id TEXT NOT NULL,
    nodes_json   TEXT NOT NULL DEFAULT '[]',
    edges_json   TEXT NOT NULL DEFAULT '[]',
    created_at   TEXT NOT NULL
);
"""

_NODES = json.dumps([
    {"id": "internet",  "label": "Internet",     "node_type": "external",       "privilege": "none"},
    {"id": "web_app",   "label": "Web App",      "node_type": "service",        "privilege": "user"},
    {"id": "db_server", "label": "DB Server",    "node_type": "asset-database", "privilege": "user"},
    {"id": "il5_store", "label": "IL5 Storage",  "node_type": "asset-storage",  "privilege": "user"},
    {"id": "root_svc",  "label": "Root Service", "node_type": "service",        "privilege": "root"},
    {"id": "jump_host", "label": "Jump Host",    "node_type": "service",        "privilege": "user"},
])

_EDGES = json.dumps([
    {"source": "internet",  "target": "web_app",   "risk_score": 7, "encrypted": False, "authenticated": False, "target_il_level": 4},
    {"source": "web_app",   "target": "db_server", "risk_score": 9, "encrypted": False, "authenticated": False, "target_il_level": 4},
    {"source": "web_app",   "target": "il5_store", "risk_score": 8, "encrypted": False, "authenticated": False, "target_il_level": 5},
    {"source": "web_app",   "target": "root_svc",  "risk_score": 6, "encrypted": True,  "authenticated": False, "target_il_level": 4},
    {"source": "jump_host", "target": "il5_store", "risk_score": 8, "encrypted": False, "authenticated": False, "target_il_level": 5},
    {"source": "internet",  "target": "jump_host", "risk_score": 5, "encrypted": False, "authenticated": False, "target_il_level": 4},
])


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(_SCHEMA)
    conn.execute(
        "INSERT INTO sdc_attack_snapshots (id, component_id, nodes_json, edges_json, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("snap-sec-01", "comp-sdc", _NODES, _EDGES, "2026-04-17T00:00:00"),
    )
    conn.commit()
    return conn


def _executor() -> Executor:
    ex = Executor()
    ex.register_collection("attack.nodes", nodes_adapter)
    ex.register_collection("attack.edges", edges_adapter)
    ex.register_collection("attack.paths", paths_adapter)
    return ex


def _read(name: str) -> str:
    return (_QUERY_DIR / name).read_text(encoding="utf-8")


# 1 — data_exfil_paths --------------------------------------------------------

def test_data_exfil_paths() -> None:
    q = parse(_read("data_exfil_paths.iqe"))
    assert isinstance(q, ForeachNode)
    assert q.var == "e"
    assert q.collection == AttrRef(["attack", "edges"])
    assert len(q.where_clauses) == 2

    conn = _make_conn()
    rows = _executor().run(q, conn)
    conn.close()
    # web_app→db_server (risk=9), web_app→il5_store (risk=8), jump_host→il5_store (risk=8)
    assert len(rows) == 3
    sources = {r["source"] for r in rows}
    assert "web_app" in sources
    assert "jump_host" in sources


# 2 — lateral_to_il5 ----------------------------------------------------------

def test_lateral_to_il5() -> None:
    q = parse(_read("lateral_to_il5.iqe"))
    assert isinstance(q, ForeachNode)
    assert q.var == "e"
    assert q.collection == AttrRef(["attack", "edges"])
    assert len(q.where_clauses) == 1

    conn = _make_conn()
    rows = _executor().run(q, conn)
    conn.close()
    # web_app→il5_store, jump_host→il5_store
    assert len(rows) == 2
    targets = {r["target"] for r in rows}
    assert targets == {"il5_store"}


# 3 — priv_escal_paths --------------------------------------------------------

def test_priv_escal_paths() -> None:
    q = parse(_read("priv_escal_paths.iqe"))
    assert isinstance(q, ForeachNode)
    assert q.var == "n"
    assert q.collection == AttrRef(["attack", "nodes"])
    assert len(q.where_clauses) == 2

    conn = _make_conn()
    rows = _executor().run(q, conn)
    conn.close()
    # root_svc only
    assert len(rows) == 1
    assert rows[0]["id"] == "root_svc"
    assert rows[0]["privilege"] == "root"


# 4 — cross_boundary_paths ----------------------------------------------------

def test_cross_boundary_paths() -> None:
    q = parse(_read("cross_boundary_paths.iqe"))
    assert isinstance(q, ForeachNode)
    assert q.var == "e"
    assert q.collection == AttrRef(["attack", "edges"])
    assert len(q.where_clauses) == 2

    conn = _make_conn()
    rows = _executor().run(q, conn)
    conn.close()
    # all 6 edges except web_app→root_svc (encrypted=True) → 5
    assert len(rows) == 5
    targets = {r["target"] for r in rows}
    assert "root_svc" not in targets


# 5 — mttr_critical_paths -----------------------------------------------------

def test_mttr_critical_paths() -> None:
    q = parse(_read("mttr_critical_paths.iqe"))
    assert isinstance(q, ForeachNode)
    assert q.var == "p"
    assert isinstance(q.collection, CollectionCall)
    assert str(q.collection) == "attack.paths"
    assert q.collection.args[0].value == "internet"
    assert q.collection.args[1].value == "db_server"
    assert len(q.where_clauses) == 1

    conn = _make_conn()
    rows = _executor().run(q, conn)
    conn.close()
    # internet → web_app → db_server: hops=2
    assert len(rows) == 1
    assert rows[0]["hops"] == 2
