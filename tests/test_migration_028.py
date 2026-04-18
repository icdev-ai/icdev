# CUI // SP-CTI
"""Tests for migration 028 — attack_graph_nodes + attack_graph_edges DDL."""

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "tools" / "db" / "migrations" / "028_attack_graph" / "up.py"
)


def _load_up():
    spec = importlib.util.spec_from_file_location("migration_028_up", _MIGRATION_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.up


@pytest.fixture()
def conn():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    yield db
    db.close()


def test_up_creates_nodes_table(conn):
    result = _load_up()(conn)
    assert result["status"] == "applied"
    assert "created_nodes_table" in result["actions"]
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "attack_graph_nodes" in tables


def test_up_creates_edges_table(conn):
    result = _load_up()(conn)
    assert "created_edges_table" in result["actions"]
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "attack_graph_edges" in tables


def test_up_idempotent(conn):
    up = _load_up()
    up(conn)
    result = up(conn)
    assert result["status"] == "applied"
    assert "nodes_table_exists" in result["actions"]
    assert "edges_table_exists" in result["actions"]


def test_nodes_schema_columns(conn):
    _load_up()(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(attack_graph_nodes)")}
    assert {"id", "asset_id", "classification", "value", "created_at"} <= cols


def test_edges_schema_columns(conn):
    _load_up()(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(attack_graph_edges)")}
    assert {"id", "src_node_id", "dst_node_id", "ttp_id", "cost", "prereqs_json", "created_at"} <= cols


def test_indexes_created(conn):
    _load_up()(conn)
    indexes = {
        r[0]
        for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")
    }
    assert "idx_ag_nodes_asset" in indexes
    assert "idx_ag_nodes_classification" in indexes
    assert "idx_ag_edges_src" in indexes
    assert "idx_ag_edges_dst" in indexes
    assert "idx_ag_edges_ttp" in indexes


def test_insert_node_and_select(conn):
    _load_up()(conn)
    conn.execute(
        "INSERT INTO attack_graph_nodes (id, asset_id, classification, value, meta_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("n-1", "asset-db-01", "IL5", 5.0, "{}", "2026-04-18T00:00:00Z"),
    )
    row = dict(conn.execute(
        "SELECT * FROM attack_graph_nodes WHERE id = 'n-1'"
    ).fetchone())
    assert row["asset_id"] == "asset-db-01"
    assert row["classification"] == "IL5"
    assert row["value"] == 5.0


def test_insert_edge_and_select(conn):
    _load_up()(conn)
    conn.execute(
        "INSERT INTO attack_graph_edges "
        "(id, src_node_id, dst_node_id, ttp_id, cost, prereqs_json, meta_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("e-1", "n-1", "n-2", "T1078", 2.5, '["no_mfa"]', "{}", "2026-04-18T00:00:00Z"),
    )
    row = dict(conn.execute(
        "SELECT * FROM attack_graph_edges WHERE id = 'e-1'"
    ).fetchone())
    assert row["ttp_id"] == "T1078"
    assert row["cost"] == 2.5
    assert row["prereqs_json"] == '["no_mfa"]'
