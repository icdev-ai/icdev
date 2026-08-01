# CUI // SP-CTI
"""studio run tables must satisfy the global RLS predicate.

Migration 305 backfilled classification/tenant_id onto studio_workflows and
stopped there. The two RUN tables never got them, so `get_connection()` — which
attaches the tenant/classification predicate inside a request context — produced
SQL they cannot satisfy:

    sqlite3.OperationalError: no such column: classification
      tools/studio/workflow_runner.py::get_run

The same read succeeds OUTSIDE a request context. That asymmetry is why the
pytest layer never caught it and only the browser and the DWO E2E specs 500 —
and it is why these tests assert on the SCHEMA rather than on a read: a test
that just calls get_run() passes with or without the fix.
"""
from __future__ import annotations

import sqlite3

import pytest

from tools.studio.init_db import STUDIO_TABLES

RUN_TABLES = ("studio_workflow_runs", "studio_workflow_run_steps")
RLS_COLUMNS = ("classification", "tenant_id")


def _columns(table: str) -> set[str]:
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(STUDIO_TABLES[table])
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    finally:
        conn.close()


@pytest.mark.parametrize("table", RUN_TABLES)
@pytest.mark.parametrize("column", RLS_COLUMNS)
def test_fresh_install_has_rls_column(table, column):
    """A database built from STUDIO_TABLES alone — no migration — must have them.

    CI and the dashboard the restart spec spawns are both fresh installs; the
    dev database had these columns added by hand, which masked the defect.
    """
    assert column in _columns(table), (
        f"{table} is missing {column}; get_connection()'s RLS predicate will "
        f"raise 'no such column' inside a request context"
    )


@pytest.mark.parametrize("table", RUN_TABLES)
def test_classification_defaults_so_existing_rows_stay_readable(table):
    """NOT NULL DEFAULT 'CUI', mirroring studio_workflows.

    A nullable classification would make pre-existing rows invisible to an
    exact-match predicate — the backfill has to leave them readable.
    """
    ddl = STUDIO_TABLES[table]
    assert "classification TEXT NOT NULL DEFAULT 'CUI'" in ddl, (
        f"{table}.classification must default to 'CUI' like studio_workflows"
    )


@pytest.mark.parametrize("table", RUN_TABLES)
def test_tenant_id_is_nullable(table):
    """Single-tenant installs must not be forced to populate it."""
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(STUDIO_TABLES[table])
        notnull = {r[1]: r[3] for r in conn.execute(f"PRAGMA table_info({table})")}
        assert notnull.get("tenant_id") == 0, f"{table}.tenant_id must be nullable"
    finally:
        conn.close()


def test_migration_309_is_idempotent_on_an_already_patched_db():
    """The dev database already had these columns; re-running must not raise.

    SQLite has no IF NOT EXISTS on ADD COLUMN, so the migration tolerates
    duplicate-column errors — the same shape as migrations 162 and 305.
    """
    from pathlib import Path
    up = (Path(__file__).resolve().parents[1]
          / "tools/db/migrations/309_studio_run_tables_rls_columns/up.py")
    assert up.exists(), "migration 309 is missing"
    text = up.read_text(encoding="utf-8")
    assert "duplicate column" in text, (
        "migration 309 must tolerate duplicate-column errors on SQLite"
    )
    assert "IF NOT EXISTS" in text, "migration 309 must use IF NOT EXISTS on PostgreSQL"


def test_run_tables_match_workflows_rls_shape():
    """Whatever RLS columns studio_workflows carries, the run tables carry too.

    They are read through the same predicate; a table that lags is a 500 waiting
    to happen, which is exactly how this defect arose.
    """
    wf = _columns("studio_workflows")
    for table in RUN_TABLES:
        missing = (wf & set(RLS_COLUMNS)) - _columns(table)
        assert not missing, f"{table} lags studio_workflows on RLS columns: {missing}"
