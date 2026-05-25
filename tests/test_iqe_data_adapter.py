# CUI // SP-CTI
"""Unit tests for tools/iqe/adapters/data.py — 5 cases."""
from __future__ import annotations

import sqlite3

from tools.iqe.executor import Executor
from tools.iqe.parser import parse

_SCHEMA = """
CREATE TABLE IF NOT EXISTS data_designs (
    id             TEXT PRIMARY KEY,
    name           TEXT NOT NULL,
    classification TEXT DEFAULT 'CUI',
    created_at     TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS dd_lineage (
    id              TEXT PRIMARY KEY,
    design_id       TEXT NOT NULL,
    source_node_id  TEXT NOT NULL,
    target_node_id  TEXT NOT NULL,
    lineage_type    TEXT DEFAULT 'flow',
    column_name     TEXT DEFAULT '',
    transform_desc  TEXT DEFAULT '',
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(_SCHEMA)
    conn.executemany(
        "INSERT INTO data_designs (id, name, classification, created_at) VALUES (?, ?, ?, ?)",
        [
            ("des-01", "Customer PII Dataset", "CUI", "2026-01-01"),
            ("des-02", "Internal Analytics", "SECRET", "2026-01-02"),
        ],
    )
    conn.executemany(
        "INSERT INTO dd_lineage "
        "(id, design_id, source_node_id, target_node_id, lineage_type, column_name, classification, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("edge-01", "des-01", "src-a", "tgt-b", "col-derive",      "ssn",  "CUI",    "2026-01-01"),
            ("edge-02", "des-01", "src-a", "tgt-c", "col-passthrough", "name", "CUI",    "2026-01-01"),
            ("edge-03", "des-02", "src-d", "tgt-e", "col-join",        "id",   "SECRET", "2026-01-02"),
        ],
    )
    conn.commit()
    return conn


# 1 — lineage_edges_adapter returns all edge rows ------------------------------

def test_lineage_edges_returns_all_rows() -> None:
    from tools.iqe.adapters.data import lineage_edges_adapter

    conn = _make_conn()
    rows = lineage_edges_adapter(conn)
    conn.close()

    assert len(rows) == 3
    assert {r["id"] for r in rows} == {"edge-01", "edge-02", "edge-03"}
    assert all("source_node_id" in r for r in rows)
    assert all("classification" in r for r in rows)


# 2 — filter lineage edges by classification via IQE WHERE -------------------

def test_lineage_edges_filter_by_classification() -> None:
    from tools.iqe.adapters.data import lineage_edges_adapter

    conn = _make_conn()
    ast = parse("foreach e in data.lineage.edges where e.classification == 'SECRET' select *")
    ex = Executor()
    ex.register_collection("data.lineage.edges", lineage_edges_adapter)
    result = ex.run(ast, conn=conn)
    conn.close()

    assert len(result) == 1
    assert result[0]["id"] == "edge-03"
    assert result[0]["classification"] == "SECRET"


# 3 — filter lineage edges by source_node_id via IQE WHERE -------------------

def test_lineage_edges_filter_by_source() -> None:
    from tools.iqe.adapters.data import lineage_edges_adapter

    conn = _make_conn()
    ast = parse("foreach e in data.lineage.edges where e.source_node_id == 'src-a' select *")
    ex = Executor()
    ex.register_collection("data.lineage.edges", lineage_edges_adapter)
    result = ex.run(ast, conn=conn)
    conn.close()

    assert len(result) == 2
    assert all(r["source_node_id"] == "src-a" for r in result)


# 4 — classifications_adapter returns design rows with classification field ---

def test_classifications_returns_design_rows() -> None:
    from tools.iqe.adapters.data import classifications_adapter

    conn = _make_conn()
    rows = classifications_adapter(conn)
    conn.close()

    assert len(rows) == 2
    assert {r["name"] for r in rows} == {"Customer PII Dataset", "Internal Analytics"}
    assert all("classification" in r for r in rows)


# 5 — smoke: parse + execute data.classifications filter by level -------------

def test_smoke_classifications_filter_by_level() -> None:
    from tools.iqe.adapters.data import classifications_adapter

    conn = _make_conn()
    ast = parse("foreach c in data.classifications where c.classification == 'CUI' select *")
    ex = Executor()
    ex.register_collection("data.classifications", classifications_adapter)
    result = ex.run(ast, conn=conn)
    conn.close()

    assert len(result) == 1
    assert result[0]["name"] == "Customer PII Dataset"
    assert result[0]["classification"] == "CUI"
