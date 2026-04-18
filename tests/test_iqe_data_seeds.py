# CUI // SP-CTI
"""Parse + execute 5 DDC data-lineage seed .iqe queries against a fixture lineage graph (dt-ddc-lineage-05)."""
from __future__ import annotations

import pathlib
import sqlite3

from tools.iqe.ast_nodes import AttrRef, ForeachNode
from tools.iqe.executor import Executor
from tools.iqe.parser import parse
from tools.iqe.adapters.data import classifications_adapter, lineage_edges_adapter

_QUERY_DIR = pathlib.Path(__file__).parent.parent / "context" / "iqe" / "queries" / "data"

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
    lineage_type    TEXT DEFAULT 'col-pass',
    column_name     TEXT DEFAULT '',
    transform_desc  TEXT DEFAULT '',
    classification  TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
"""

# 15 fixture rows — 5 groups of 3 (2 violations + 1 valid) per query
_ROWS = [
    # (id, design_id, src, tgt, lineage_type, column_name, transform_desc, classification, created_at)

    # q1 secret_to_il4_paths: classification=="SECRET" — 2 violations
    ("e01", "d1", "s1",  "t1",  "col-pass",     "dod_id", "",      "SECRET",       "2026-01-01"),
    ("e02", "d1", "s2",  "t2",  "col-pass",     "ctl_id", "",      "SECRET",       "2026-01-01"),
    ("e03", "d1", "s3",  "t3",  "col-pass",     "rec_id", "",      "CUI",          "2026-01-01"),

    # q2 cui_no_classification: classification==null — 2 violations
    ("e04", "d2", "s4",  "t4",  "col-deriv",    "email",  "",      None,           "2026-01-02"),
    ("e05", "d2", "s5",  "t5",  "col-deriv",    "name",   "",      None,           "2026-01-02"),
    ("e06", "d2", "s6",  "t6",  "col-pass",     "org_id", "",      "CUI",          "2026-01-02"),

    # q3 lineage_breaks: lineage_type=="" — 2 violations
    ("e07", "d3", "s7",  "t7",  "",             "amt",    "",      "CUI",          "2026-01-03"),
    ("e08", "d3", "s8",  "t8",  "",             "total",  "",      "CUI",          "2026-01-03"),
    ("e09", "d3", "s9",  "t9",  "col-trans",    "amount", "mask4", "CUI",          "2026-01-03"),

    # q4 untagged_pii: column_name contains "ssn" AND classification!="CUI" — 2 violations
    ("e10", "d4", "s10", "t10", "col-pass",     "ssn",    "",      "UNCLASSIFIED", "2026-01-04"),
    ("e11", "d4", "s11", "t11", "col-pass",     "ssn",    "",      "UNCLASSIFIED", "2026-01-04"),
    ("e12", "d4", "s12", "t12", "col-pass",     "ssn",    "",      "CUI",          "2026-01-04"),

    # q5 cross_region_pii: lineage_type=="cross-region" AND classification=="CUI" — 2 violations
    ("e13", "d5", "s13", "t13", "cross-region", "email",  "",      "CUI",          "2026-01-05"),
    ("e14", "d5", "s14", "t14", "cross-region", "phone",  "",      "CUI",          "2026-01-05"),
    ("e15", "d5", "s15", "t15", "col-pass",     "name",   "",      "CUI",          "2026-01-05"),
]


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(_SCHEMA)
    conn.executemany(
        "INSERT INTO dd_lineage "
        "(id, design_id, source_node_id, target_node_id, lineage_type, column_name, "
        "transform_desc, classification, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        _ROWS,
    )
    conn.commit()
    return conn


def _executor() -> Executor:
    ex = Executor()
    ex.register_collection("data.lineage.edges", lineage_edges_adapter)
    ex.register_collection("data.classifications", classifications_adapter)
    return ex


def _read(name: str) -> str:
    return (_QUERY_DIR / name).read_text(encoding="utf-8")


# 1 — secret_to_il4_paths -------------------------------------------------------

def test_secret_to_il4_paths() -> None:
    q = parse(_read("secret_to_il4_paths.iqe"))
    assert isinstance(q, ForeachNode)
    assert q.var == "e"
    assert q.collection == AttrRef(["data", "lineage", "edges"])
    assert len(q.where_clauses) == 1

    conn = _make_conn()
    rows = _executor().run(q, conn)
    conn.close()
    assert len(rows) == 2
    assert all(r["classification"] == "SECRET" for r in rows)


# 2 — cui_no_classification -----------------------------------------------------

def test_cui_no_classification() -> None:
    q = parse(_read("cui_no_classification.iqe"))
    assert isinstance(q, ForeachNode)
    assert q.var == "e"
    assert q.collection == AttrRef(["data", "lineage", "edges"])
    assert len(q.where_clauses) == 1

    conn = _make_conn()
    rows = _executor().run(q, conn)
    conn.close()
    assert len(rows) == 2
    assert all(r["classification"] is None for r in rows)


# 3 — lineage_breaks ------------------------------------------------------------

def test_lineage_breaks() -> None:
    q = parse(_read("lineage_breaks.iqe"))
    assert isinstance(q, ForeachNode)
    assert q.var == "e"
    assert q.collection == AttrRef(["data", "lineage", "edges"])
    assert len(q.where_clauses) == 1

    conn = _make_conn()
    rows = _executor().run(q, conn)
    conn.close()
    assert len(rows) == 2
    assert all(r["lineage_type"] == "" for r in rows)


# 4 — untagged_pii --------------------------------------------------------------

def test_untagged_pii() -> None:
    q = parse(_read("untagged_pii.iqe"))
    assert isinstance(q, ForeachNode)
    assert q.var == "e"
    assert q.collection == AttrRef(["data", "lineage", "edges"])
    assert len(q.where_clauses) == 2

    conn = _make_conn()
    rows = _executor().run(q, conn)
    conn.close()
    assert len(rows) == 2
    assert all("ssn" in r["column_name"] for r in rows)
    assert all(r["classification"] != "CUI" for r in rows)


# 5 — cross_region_pii ----------------------------------------------------------

def test_cross_region_pii() -> None:
    q = parse(_read("cross_region_pii.iqe"))
    assert isinstance(q, ForeachNode)
    assert q.var == "e"
    assert q.collection == AttrRef(["data", "lineage", "edges"])
    assert len(q.where_clauses) == 2

    conn = _make_conn()
    rows = _executor().run(q, conn)
    conn.close()
    assert len(rows) == 2
    assert all(r["lineage_type"] == "cross-region" for r in rows)
    assert all(r["classification"] == "CUI" for r in rows)
