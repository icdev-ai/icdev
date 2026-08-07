#!/usr/bin/env python3
# CUI // SP-CTI
"""obs-cov-05: kanban init must not index a column it has not backfilled yet.

``CREATE TABLE IF NOT EXISTS`` never alters an existing table, so a
``kanban_tasks`` created by an older DDL reaches ``init_kanban_tables()``
without ``idempotency_key`` / ``last_heartbeat_at``. The index loop ran before
the ALTER loop, so indexing the absent column raised and aborted init *before*
any backfill could run — leaving the table permanently short of every column in
``_KANBAN_TASKS_EXTRA_COLUMNS``, not just the one being indexed.
"""

import pytest

from tools.db.storage import get_connection
from tools.kanban.init_db import _KANBAN_INDEXES, init_kanban_tables

# Columns that _KANBAN_INDEXES references but the legacy CREATE TABLE lacked.
INDEXED_LATE_COLUMNS = ["idempotency_key", "last_heartbeat_at"]


@pytest.fixture()
def legacy_db(tmp_path, monkeypatch):
    """A kanban_tasks predating the extra columns, as found in the wild."""
    db = tmp_path / "legacy.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db))

    conn = get_connection(str(db))
    conn.execute(
        "CREATE TABLE kanban_tasks ("
        "id TEXT PRIMARY KEY, title TEXT, status TEXT, updated_at TEXT)"
    )
    conn.commit()
    conn.close()

    import tools.db.storage as storage_mod

    real = storage_mod.get_connection
    monkeypatch.setattr(storage_mod, "get_connection", lambda *a, **kw: real(str(db)))
    return db


def _columns(db):
    conn = get_connection(str(db))
    rows = conn.execute("PRAGMA table_info(kanban_tasks)").fetchall()
    conn.close()
    return {dict(r)["name"] for r in rows}


def test_indexed_columns_are_referenced_by_the_index_list():
    """Guard the premise: these columns really are indexed by init."""
    joined = " ".join(_KANBAN_INDEXES)
    for col in INDEXED_LATE_COLUMNS:
        assert col in joined


def test_init_backfills_columns_on_a_legacy_table(legacy_db):
    """The whole ALTER loop must survive an index on a not-yet-added column."""
    assert init_kanban_tables()["status"] == "ok"

    cols = _columns(legacy_db)
    for col in INDEXED_LATE_COLUMNS:
        assert col in cols, f"{col} was never backfilled — init aborted early"
    # Not just the indexed ones: an early abort took every later ALTER with it.
    for col in ("failure_count", "last_failure_reason", "execution_seconds"):
        assert col in cols, f"{col} was never backfilled — init aborted early"


def test_init_is_idempotent_on_a_legacy_table(legacy_db):
    """Second run must be a clean no-op, not a re-raise."""
    init_kanban_tables()
    assert init_kanban_tables()["status"] == "ok"
    assert INDEXED_LATE_COLUMNS[0] in _columns(legacy_db)
