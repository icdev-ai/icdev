# CUI // SP-CTI
"""dcpr-fix-04 — real lineage-backed twin.simulate_delta / quality_gate.

Seeds ``data_designs`` / ``data_nodes`` / ``dd_lineage`` through the storage
layer (``get_canvas_connection``, never raw sqlite3) and asserts the twin
computes downstream impact, orphan counts, and referential-integrity /
classification-boundary violations from real state rather than the input list.
"""
from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.data_canvas import twin
from tools.db.storage import get_canvas_connection


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    """Point the storage layer at an isolated SQLite file so the twin queries
    never contend with the shared data/icdev.db (which can lock under a live
    dashboard/kanban process)."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(tmp_path / "dcpr_twin.db"))
    yield

_DDL = [
    """CREATE TABLE IF NOT EXISTS data_designs (
        id TEXT PRIMARY KEY, name TEXT, description TEXT,
        graph_json TEXT DEFAULT '{}', template_id TEXT,
        classification TEXT DEFAULT 'CUI',
        created_at TEXT, updated_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS data_nodes (
        id TEXT PRIMARY KEY, design_id TEXT, node_type TEXT DEFAULT 'table',
        label TEXT, x REAL DEFAULT 0, y REAL DEFAULT 0,
        classification TEXT DEFAULT 'CUI', properties_json TEXT DEFAULT '{}',
        created_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS dd_lineage (
        id TEXT PRIMARY KEY, design_id TEXT,
        source_node_id TEXT, target_node_id TEXT,
        lineage_type TEXT DEFAULT 'col-derive', column_name TEXT DEFAULT '',
        transform_desc TEXT DEFAULT '', classification TEXT DEFAULT 'CUI',
        created_at TEXT)""",
]


def _ensure_schema(conn):
    for ddl in _DDL:
        conn.execute(ddl)
    conn.commit()


def _seed_design(design_id, classification="CUI"):
    conn = get_canvas_connection()
    _ensure_schema(conn)
    conn.execute(
        "INSERT INTO data_designs (id, name, classification) VALUES (%s,%s,%s)",
        (design_id, "dcpr twin test", classification),
    )
    conn.commit()


def _seed_node(design_id, node_id, classification="CUI"):
    conn = get_canvas_connection()
    conn.execute(
        "INSERT INTO data_nodes (id, design_id, node_type, label, classification) "
        "VALUES (%s,%s,'table',%s,%s)",
        (node_id, design_id, node_id, classification),
    )
    conn.commit()


def _seed_edge(design_id, src, tgt, column_name):
    conn = get_canvas_connection()
    conn.execute(
        "INSERT INTO dd_lineage (id, design_id, source_node_id, target_node_id, "
        "lineage_type, column_name) VALUES (%s,%s,%s,%s,'col-derive',%s)",
        (str(uuid.uuid4()), design_id, src, tgt, column_name),
    )
    conn.commit()


@pytest.fixture()
def chain_design():
    """A→B→C column-level chain on column 'amount'."""
    did = f"dcpr-{uuid.uuid4().hex[:10]}"
    _seed_design(did, classification="CUI")
    for n in ("tbl_a", "tbl_b", "tbl_c"):
        _seed_node(did, n)
    _seed_edge(did, "tbl_a", "tbl_b", "amount")
    _seed_edge(did, "tbl_b", "tbl_c", "amount")
    return did


# ── simulate_delta ───────────────────────────────────────────────────────────

def test_simulate_delta_reports_real_downstream_impact(chain_design):
    result = twin.simulate_delta(
        chain_design,
        [{"change": "remove_column", "table": "tbl_a", "column": "amount"}],
    )
    # Downstream consumers reached via real lineage edges: tbl_b, tbl_c.
    assert result["impacted_table_count"] == 2
    # tbl_b loses its only upstream (tbl_a) → orphaned; tbl_c still fed by tbl_b.
    assert result["orphan_count"] >= 1
    assert "tbl_b::amount" in result["orphaned_nodes"]
    assert "tbl_c::amount" not in result["orphaned_nodes"]
    assert result["verdict"] == "warn"
    # coverage reflects real state: 2 impacted, 1 orphan → 0.5.
    assert result["coverage_score"] == pytest.approx(0.5)
    di = result["downstream_impacts"][0]
    assert di["consumer_count"] == 2
    assert "tbl_b::amount" in di["consumers"]


def test_simulate_delta_no_lineage_is_honest_zero():
    did = f"dcpr-{uuid.uuid4().hex[:10]}"
    _seed_design(did, classification="CUI")
    result = twin.simulate_delta(
        did, [{"change": "remove_column", "table": "ghost", "column": "x"}]
    )
    assert result["orphan_count"] == 0
    assert result["impacted_table_count"] == 0
    assert result["coverage_score"] == 0.0
    assert "note" in result  # honest disclosure, not fabricated impact


def test_simulate_delta_additive_change_passes(chain_design):
    result = twin.simulate_delta(
        chain_design, [{"change": "add_column", "table": "tbl_a", "column": "new_col"}]
    )
    assert result["verdict"] == "pass"
    assert result["orphan_count"] == 0
    assert result["coverage_score"] == 1.0


# ── quality_gate ─────────────────────────────────────────────────────────────

def test_quality_gate_referential_integrity_is_lineage_backed(chain_design):
    """A removal WITH real downstream consumers is flagged."""
    result = twin.quality_gate(
        chain_design,
        [{"change": "remove_column", "table": "tbl_a", "column": "amount"}],
    )
    assert result["gate"] == "fail"
    ri = [v for v in result["violations"] if v["type"] == "referential_integrity"]
    assert len(ri) == 1
    assert "tbl_b::amount" in ri[0]["consumers"]
    assert set(result["checks"]) == {
        "null_safety", "referential_integrity", "classification_boundary"
    }


def test_quality_gate_removal_without_consumers_not_flagged(chain_design):
    """tbl_c.amount is a lineage sink — removing it has no downstream consumers,
    so the referential-integrity check (real DB query) must NOT fire."""
    result = twin.quality_gate(
        chain_design,
        [{"change": "remove_column", "table": "tbl_c", "column": "amount"}],
    )
    ri = [v for v in result["violations"] if v["type"] == "referential_integrity"]
    assert ri == []


def test_quality_gate_classification_boundary(chain_design):
    """A change touching a SECRET table under a CUI design is a boundary violation."""
    _seed_node(chain_design, "tbl_secret", classification="SECRET")
    result = twin.quality_gate(
        chain_design,
        [{"change": "add_column", "table": "tbl_secret", "column": "y", "nullable": True}],
    )
    cb = [v for v in result["violations"] if v["type"] == "classification_boundary"]
    assert len(cb) == 1
    assert cb[0]["id"] == "tbl_secret"
    assert result["gate"] == "fail"


def test_quality_gate_null_safety(chain_design):
    result = twin.quality_gate(
        chain_design,
        [{"change": "add_column", "table": "tbl_a", "column": "n", "nullable": False}],
    )
    ns = [v for v in result["violations"] if v["type"] == "null_safety"]
    assert len(ns) == 1
