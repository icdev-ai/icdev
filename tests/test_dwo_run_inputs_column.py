"""dwo-evt-04-d1 — inputs_json on studio_workflow_runs.

A triggered run computes its inputs from the event (migration 304's
input_mapping_json) but had nowhere to record them. This adds the column.

Acceptance:
  - Migration 305 exists and adds a nullable inputs_json to studio_workflow_runs.
  - tools/studio/init_db.py::STUDIO_TABLES creates the column, defaulting NULL.
  - The icdev/ package mirror and the conftest schema carry it too.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from tools.db.migration_runner import MigrationRunner
from tools.studio.init_db import STUDIO_TABLES

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATION = REPO_ROOT / "tools" / "db" / "migrations" / "305_studio_workflow_run_inputs.sql"


def _columns(conn: sqlite3.Connection, table: str) -> dict[str, tuple]:
    return {row[1]: row for row in conn.execute(f"PRAGMA table_info({table})")}


def _statements(sql: str) -> list[str]:
    """Executable lines only — the filter keeps comments, which we don't test."""
    return [
        line.strip()
        for line in sql.split("\n")
        if line.strip() and not line.strip().startswith("--")
    ]


def test_migration_file_exists():
    assert MIGRATION.exists(), f"missing migration: {MIGRATION}"


def test_migration_adds_inputs_json_to_workflow_runs():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "studio_workflow_runs" in sql
    assert "ADD COLUMN IF NOT EXISTS inputs_json" in sql
    # Nullable with no default — NULL means "no inputs recorded", which is
    # distinct from '{}' meaning "started with an empty input set".
    assert "inputs_json TEXT;" in sql
    assert "NOT NULL" not in sql


def test_alter_is_pg_only_so_sqlite_init_still_applies():
    # ALTER TABLE IF EXISTS ... ADD COLUMN IF NOT EXISTS is PG-native; on the
    # SQLite fallback the statement must be filtered out, not executed.
    runner = MigrationRunner.__new__(MigrationRunner)
    sql = MIGRATION.read_text(encoding="utf-8")

    runner.engine = "sqlite"
    assert _statements(runner._filter_sql(sql)) == []

    runner.engine = "postgresql"
    pg = _statements(runner._filter_sql(sql))
    assert any("inputs_json" in stmt for stmt in pg), pg


def test_init_db_schema_creates_nullable_inputs_json():
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(STUDIO_TABLES["studio_workflow_runs"])
        col = _columns(conn, "studio_workflow_runs").get("inputs_json")
        assert col is not None, "inputs_json missing from STUDIO_TABLES schema"
        assert col[3] == 0, "inputs_json must be nullable"
        assert col[4] is None, "inputs_json must have no default"

        conn.execute(
            "INSERT INTO studio_workflow_runs (run_id, workflow_id) VALUES ('r1', 'wf1')"
        )
        row = conn.execute(
            "SELECT inputs_json FROM studio_workflow_runs WHERE run_id='r1'"
        ).fetchone()
        assert row[0] is None, "inputs_json must default to NULL"
    finally:
        conn.close()


def test_icdev_mirror_carries_the_column():
    from icdev.tools.studio.init_db import STUDIO_TABLES as MIRRORED

    assert "inputs_json" in MIRRORED["studio_workflow_runs"]


def test_conftest_schema_carries_the_column(icdev_db):
    conn = sqlite3.connect(str(icdev_db))
    try:
        assert "inputs_json" in _columns(conn, "studio_workflow_runs")
    finally:
        conn.close()
