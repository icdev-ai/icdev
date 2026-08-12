# CUI // SP-CTI
"""A rolled-back migration must be able to become applied again.

``get_pending_migrations`` counts a rolled-back version as pending, because
``get_applied_migrations`` filters on ``rolled_back_at IS NULL``. But
``apply_migration`` recorded with ``INSERT OR IGNORE``, and a rolled-back version
still owns its row — so the insert was a silent no-op, the marker was never
cleared, and the migration could never leave the pending list no matter how many
times it succeeded. It re-ran its DDL on every pass, forever, reporting success
each time.

Observed on the live board: ``20260808161736_sag_standing_goals`` was applied and
rolled back 106ms apart on 2026-08-08 and had been stuck pending since.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.db.migration_runner import MigrationRunner  # noqa: E402

_SCHEMA = """
CREATE TABLE schema_migrations (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    version           TEXT UNIQUE NOT NULL,
    name              TEXT,
    applied_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    checksum          TEXT,
    execution_time_ms INTEGER,
    applied_by        TEXT,
    rolled_back_at    TIMESTAMP,
    classification    TEXT
);
"""


@pytest.fixture()
def runner(tmp_path):
    """A runner over a throwaway SQLite DB with one migration on disk."""
    db = tmp_path / "m.db"
    raw = sqlite3.connect(str(db))
    raw.executescript(_SCHEMA)
    raw.commit()
    raw.close()

    migs = tmp_path / "migrations"
    (migs / "20260808161736_thing").mkdir(parents=True)
    with (migs / "20260808161736_thing" / "up.sql").open(
        "w", encoding="utf-8", newline=""
    ) as fh:
        fh.write("CREATE TABLE IF NOT EXISTS thing (id TEXT PRIMARY KEY);\n")

    r = MigrationRunner(db_path=db, migrations_dir=migs, engine="sqlite")
    return r, db


def _rows(db):
    raw = sqlite3.connect(str(db))
    raw.row_factory = sqlite3.Row
    try:
        return [dict(x) for x in raw.execute("SELECT * FROM schema_migrations")]
    finally:
        raw.close()


def _pending(r):
    return [str(m["version"]) for m in r.get_pending_migrations()]


def test_a_fresh_migration_applies_and_leaves_pending(runner):
    r, db = runner
    assert _pending(r) == ["20260808161736"]

    assert r.apply_migration(r.get_pending_migrations()[0])["success"] is True

    assert _pending(r) == [], "a freshly applied migration must not stay pending"
    assert len(_rows(db)) == 1


def test_a_rolled_back_migration_can_be_applied_again(runner):
    """The regression: it used to stay pending forever."""
    r, db = runner
    r.apply_migration(r.get_pending_migrations()[0])

    # Simulate the rollback that put the live row in this state.
    raw = sqlite3.connect(str(db))
    raw.execute(
        "UPDATE schema_migrations SET rolled_back_at = '2026-08-08T16:22:26' "
        "WHERE version = '20260808161736'"
    )
    raw.commit()
    raw.close()

    assert _pending(r) == ["20260808161736"], "a rolled-back version is pending again"

    res = r.apply_migration(r.get_pending_migrations()[0])
    assert res["success"] is True

    assert _pending(r) == [], (
        "re-applying a rolled-back migration must clear the marker — otherwise it "
        "re-runs its DDL on every pass forever while reporting success"
    )
    rows = _rows(db)
    assert len(rows) == 1, "version is UNIQUE: one row per version, updated in place"
    assert rows[0]["rolled_back_at"] is None


def test_reapply_does_not_rewrite_a_normal_migrations_applied_at(runner):
    """The UPDATE is scoped to rolled-back rows only."""
    r, db = runner
    r.apply_migration(r.get_pending_migrations()[0])
    first = _rows(db)[0]["applied_at"]

    # Applying again with no rollback in between must not touch the row.
    migration = r.discover_migrations()[0]
    r.apply_migration(migration)

    assert _rows(db)[0]["applied_at"] == first, (
        "a normally-applied migration's applied_at is history and must not move"
    )
