# CUI // SP-CTI
"""Tests for migration 027 — pipeline_snapshots DDL."""

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "tools" / "db" / "migrations" / "027_pipeline_snapshots" / "up.py"
)


def _load_up():
    spec = importlib.util.spec_from_file_location("migration_027_up", _MIGRATION_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.up


@pytest.fixture()
def conn():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    yield db
    db.close()


def test_up_creates_table(conn):
    result = _load_up()(conn)
    assert result["status"] == "applied"
    assert "created_table" in result["actions"]
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "pipeline_snapshots" in tables


def test_up_idempotent(conn):
    up = _load_up()
    up(conn)
    result = up(conn)
    assert result["status"] == "applied"
    assert "table_exists" in result["actions"]


def test_table_schema_has_required_columns(conn):
    _load_up()(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(pipeline_snapshots)")}
    assert {"id", "pipeline_id", "nodes_json", "edges_json", "meta_json", "created_at"} <= cols


def test_indexes_created(conn):
    _load_up()(conn)
    indexes = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='pipeline_snapshots'"
        )
    }
    assert "idx_ps_pipeline" in indexes
    assert "idx_ps_type" in indexes


def test_insert_and_select(conn):
    _load_up()(conn)
    conn.execute(
        "INSERT INTO pipeline_snapshots (id, pipeline_id, nodes_json, edges_json, meta_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            "snap-1", "pipe-abc",
            '[{"id":"n1"}]',
            '[{"source":"n1","target":"n2"}]',
            '{"slsa":"L2"}',
            "2026-04-18T00:00:00Z",
        ),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM pipeline_snapshots WHERE id='snap-1'").fetchone()
    assert row["pipeline_id"] == "pipe-abc"
    assert row["nodes_json"] == '[{"id":"n1"}]'
    assert row["meta_json"] == '{"slsa":"L2"}'
