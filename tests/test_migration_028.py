# CUI // SP-CTI
"""Tests for the odc_mitre_coverage DDL migration.

Plan step: dt-odc-twin-01 (ODC coverage schema).
DB migration number: 336 (renumbered from 028 by PR #1199 to resolve a duplicate
version). This file kept pointing at the old path and had been failing with
FileNotFoundError since. See tests/test_migration_027.py for why loading a
migration by path is what leaves a stale directory behind (mvs-guard-02).
"""

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_MIGRATION_PATH = (
    Path(__file__).resolve().parent.parent
    / "tools" / "db" / "migrations" / "336_odc_mitre_coverage" / "up.py"
)


def _load_up():
    spec = importlib.util.spec_from_file_location("migration_336_up", _MIGRATION_PATH)
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
    assert "mitre_coverage" in tables


def test_up_idempotent(conn):
    up = _load_up()
    up(conn)
    result = up(conn)
    assert result["status"] == "applied"
    assert "table_exists" in result["actions"]


def test_table_has_required_columns(conn):
    _load_up()(conn)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(mitre_coverage)")}
    assert {"id", "technique_id", "signal_source", "state", "last_observed_at", "project_id", "created_at"} <= cols


def test_indexes_created(conn):
    _load_up()(conn)
    indexes = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='mitre_coverage'"
        )
    }
    assert "idx_mc_project" in indexes
    assert "idx_mc_technique" in indexes
    assert "idx_mc_state" in indexes
    assert "idx_mc_observed" in indexes


def test_state_check_constraint(conn):
    _load_up()(conn)
    import uuid
    now = "2026-04-18T00:00:00Z"
    for valid_state in ("none", "partial", "full", "false_positive"):
        conn.execute(
            "INSERT INTO mitre_coverage (id, technique_id, signal_source, state, last_observed_at, project_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (uuid.uuid4().hex, "T1059", "caldera", valid_state, now, "proj-1"),
        )
    conn.commit()
    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO mitre_coverage (id, technique_id, signal_source, state, last_observed_at, project_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (uuid.uuid4().hex, "T1059", "caldera", "unknown_state", now, "proj-1"),
        )
        conn.commit()


def test_insert_and_select(conn):
    _load_up()(conn)
    conn.execute(
        "INSERT INTO mitre_coverage (id, technique_id, signal_source, state, last_observed_at, project_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("row-001", "T1059.001", "caldera", "partial", "2026-04-18T10:00:00Z", "proj-alpha"),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM mitre_coverage WHERE id='row-001'").fetchone()
    assert row["technique_id"] == "T1059.001"
    assert row["signal_source"] == "caldera"
    assert row["state"] == "partial"
    assert row["project_id"] == "proj-alpha"
