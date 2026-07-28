"""dwo-evt-01-d1 — studio event tables migration + init_db registration.

Acceptance:
  - Migration 304 applies cleanly.
  - tools/studio/init_db.py::STUDIO_TABLES names all three tables.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tools.studio.init_db import APPEND_ONLY_TABLES, STUDIO_TABLES

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATION = REPO_ROOT / "tools" / "db" / "migrations" / "304_studio_event_tables.sql"
#: dwo-evt-02 adds the dispatch columns (classification ceilings, the UNIQUE
#: replay key, the audit outcome) on top of 304. A fresh install gets both
#: from STUDIO_TABLES, so the parity check has to apply both to the
#: reference database or it compares a fresh install against a stale schema.
MIGRATION_DISPATCH = (
    REPO_ROOT / "tools" / "db" / "migrations" / "308_studio_trigger_dispatch.sql"
)

EVENT_TABLES = [
    "studio_event_sources",
    "studio_workflow_triggers",
    "studio_trigger_events",
]


def _apply(conn: sqlite3.Connection, sql: str) -> None:
    conn.executescript(sql)
    conn.commit()


@pytest.fixture()
def migrated() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    # studio_workflows is the FK target for studio_workflow_triggers.
    _apply(conn, STUDIO_TABLES["studio_workflows"])
    _apply(conn, MIGRATION.read_text(encoding="utf-8"))
    yield conn
    conn.close()


def test_migration_file_exists():
    assert MIGRATION.exists(), f"missing migration: {MIGRATION}"


@pytest.mark.parametrize("table", EVENT_TABLES)
def test_migration_creates_table(migrated, table):
    row = migrated.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    assert row is not None, f"{table} not created by migration"


def test_migration_is_idempotent(migrated):
    # Re-applying must not raise — every statement is IF NOT EXISTS.
    _apply(migrated, MIGRATION.read_text(encoding="utf-8"))


@pytest.mark.parametrize("table", EVENT_TABLES)
def test_registered_in_studio_tables(table):
    assert table in STUDIO_TABLES, f"{table} missing from STUDIO_TABLES"


@pytest.mark.parametrize("table", EVENT_TABLES)
def test_init_db_ddl_matches_migration(table):
    """init_db must create the same table the migration does."""
    conn = sqlite3.connect(":memory:")
    try:
        _apply(conn, STUDIO_TABLES["studio_workflows"])
        _apply(conn, STUDIO_TABLES["studio_event_sources"])
        _apply(conn, STUDIO_TABLES[table])
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}

        ref = sqlite3.connect(":memory:")
        try:
            _apply(ref, STUDIO_TABLES["studio_workflows"])
            _apply(ref, MIGRATION.read_text(encoding="utf-8"))
            _apply(ref, MIGRATION_DISPATCH.read_text(encoding="utf-8"))
            ref_cols = {r[1] for r in ref.execute(f"PRAGMA table_info({table})")}
        finally:
            ref.close()
    finally:
        conn.close()
    assert cols == ref_cols


def test_source_kind_is_constrained(migrated):
    migrated.execute(
        "INSERT INTO studio_event_sources (source_id, name, kind) VALUES (?,?,?)",
        ("src-1", "GitHub webhook", "gateway_channel"),
    )
    with pytest.raises(sqlite3.IntegrityError):
        migrated.execute(
            "INSERT INTO studio_event_sources (source_id, name, kind) VALUES (?,?,?)",
            ("src-2", "bogus", "carrier_pigeon"),
        )


def test_unmatched_event_is_recordable(migrated):
    """An event that matched no trigger still lands in the audit table."""
    migrated.execute(
        "INSERT INTO studio_trigger_events "
        "(event_id, source_id, event_type, payload_json, matched, reason) "
        "VALUES (?,?,?,?,?,?)",
        ("evt-1", "src-1", "push", "{}", 0, "no trigger matched"),
    )
    row = migrated.execute(
        "SELECT matched, run_id FROM studio_trigger_events WHERE event_id=?", ("evt-1",)
    ).fetchone()
    assert row == (0, None)


def test_trigger_events_is_append_only():
    assert "studio_trigger_events" in APPEND_ONLY_TABLES
    hook = (REPO_ROOT / ".claude" / "hooks" / "pre_tool_use.py").read_text(encoding="utf-8")
    assert '"studio_trigger_events"' in hook


def test_icdev_mirror_is_in_sync():
    root = (REPO_ROOT / "tools" / "studio" / "init_db.py").read_text(encoding="utf-8")
    mirror = (REPO_ROOT / "icdev" / "tools" / "studio" / "init_db.py").read_text(encoding="utf-8")
    assert root == mirror
