# CUI // SP-CTI
"""Unit tests for tools/iqe/adapters/security.py — 5 cases."""
from __future__ import annotations

import json
import sqlite3

from tools.iqe.executor import Executor
from tools.iqe.parser import parse

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sdc_attack_snapshots (
    id           TEXT PRIMARY KEY,
    component_id TEXT NOT NULL,
    nodes_json   TEXT NOT NULL DEFAULT '[]',
    edges_json   TEXT NOT NULL DEFAULT '[]',
    created_at   TEXT NOT NULL
);
"""

_NODES_JSON = json.dumps([
    {"id": "internet", "label": "Internet", "node_type": "external"},
    {"id": "web_app",  "label": "Web App",  "node_type": "service"},
    {"id": "db_admin", "label": "DB Admin", "node_type": "target"},
])

_EDGES_JSON = json.dumps([
    {"source": "internet", "target": "web_app",  "risk_score": 7},
    {"source": "web_app",  "target": "db_admin", "risk_score": 9},
])


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(_SCHEMA)
    conn.execute(
        "INSERT INTO sdc_attack_snapshots (id, component_id, nodes_json, edges_json, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        ("snap-01", "comp-web", _NODES_JSON, _EDGES_JSON, "2026-01-01T00:00:00"),
    )
    conn.commit()
    return conn


# 1 — attack.nodes returns flattened node rows --------------------------------

def test_nodes_returns_flattened_rows() -> None:
    from tools.iqe.adapters.security import nodes_adapter

    conn = _make_conn()
    rows = nodes_adapter(conn)
    conn.close()

    assert len(rows) == 3
    ids = {r["id"] for r in rows}
    assert ids == {"internet", "web_app", "db_admin"}
    assert all("snapshot_id" in r for r in rows)
    assert all(r["component_id"] == "comp-web" for r in rows)


# 2 — attack.edges returns flattened edge rows --------------------------------

def test_edges_returns_flattened_rows() -> None:
    from tools.iqe.adapters.security import edges_adapter

    conn = _make_conn()
    rows = edges_adapter(conn)
    conn.close()

    assert len(rows) == 2
    assert rows[0]["source"] == "internet"
    assert rows[0]["target"] == "web_app"
    assert rows[1]["risk_score"] == 9
    assert all("snapshot_id" in r for r in rows)


# 3 — attack.paths unparameterized returns all edges as 1-hop paths -----------

def test_paths_unparameterized_returns_all_edges() -> None:
    from tools.iqe.adapters.security import paths_adapter

    conn = _make_conn()
    rows = paths_adapter(conn)
    conn.close()

    assert len(rows) == 2
    srcs = {r["src"] for r in rows}
    goals = {r["goal"] for r in rows}
    assert "internet" in srcs
    assert "db_admin" in goals
    assert all(r["hops"] == 1 for r in rows)


# 4 — attack.paths(src, goal) BFS returns multi-hop path ----------------------

def test_paths_parameterized_bfs_finds_path() -> None:
    from tools.iqe.adapters.security import paths_adapter

    conn = _make_conn()
    rows = paths_adapter(conn, src="internet", goal="db_admin")
    conn.close()

    assert len(rows) >= 1
    row = rows[0]
    assert row["src"] == "internet"
    assert row["goal"] == "db_admin"
    assert row["hops"] == 2
    assert row["path"] == ["internet", "web_app", "db_admin"]


# 5 — smoke: parse attack.paths("src", "goal") + execute returns rows ---------

def test_smoke_parse_and_execute_parameterized_paths() -> None:
    from tools.iqe.adapters.security import paths_adapter

    conn = _make_conn()
    ast = parse('foreach p in attack.paths("internet", "db_admin") select *')
    ex = Executor()
    ex.register_collection("attack.paths", paths_adapter)
    result = ex.run(ast, conn=conn)
    conn.close()

    assert len(result) >= 1
    assert result[0]["src"] == "internet"
    assert result[0]["goal"] == "db_admin"
    assert result[0]["hops"] == 2
