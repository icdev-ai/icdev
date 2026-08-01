# CUI // SP-CTI
"""The asset reconcile must PERSIST, not just execute.

_reconcile_step_assets issues UPDATEs and does not commit. While it was reached
only from _seed_steps, a later commit in the seeding flow happened to cover it. Once
reconcile_all_step_assets started running before the seeder's already-seeded
fast-path return (#1015), nothing committed afterwards and every UPDATE was
discarded on return.

Verified against the live database: the log said "attached code assets to
m01-llm-fundamentals step 1 (starter_code_path, step_type, test_code_path)" for
26 steps, and the rows were unchanged — still 10 steps with a stored test, still 16
coding steps.

The existing reconcile tests could not catch this: they write and read back on ONE
`:memory:` connection, and uncommitted writes are visible inside their own
transaction. These use a FILE-backed database and a SECOND connection, which is the
only arrangement where a missing commit actually fails.
"""
from __future__ import annotations

import sqlite3

from _academy_conn import academy_conn

import pytest

from apps.forge_academy.content_loader import reconcile_all_step_assets

SCHEMA = """
CREATE TABLE fa_missions (id INTEGER PRIMARY KEY AUTOINCREMENT, slug TEXT UNIQUE,
  title TEXT, is_active INTEGER DEFAULT 1);
CREATE TABLE fa_mission_steps (id INTEGER PRIMARY KEY AUTOINCREMENT,
  mission_id INTEGER, step_num INTEGER, title TEXT,
  step_type TEXT DEFAULT 'watch', content_path TEXT,
  starter_code_path TEXT DEFAULT '', test_code_path TEXT DEFAULT '');
INSERT INTO fa_missions (id, slug, title) VALUES (1, 'm01-x', 'X');
INSERT INTO fa_mission_steps (mission_id, step_num, title) VALUES (1, 1, 'A');
"""

DISCOVERED = {
    "m01-x": [{
        "step_num": 1,
        "starter_code_path": "tier1/m01-x/steps/step1_starter.py",
        "test_code_path": "tier1/m01-x/steps/step1_test.py",
    }],
}


@pytest.fixture
def db_path(tmp_path):
    p = tmp_path / "academy.db"
    setup = academy_conn(str(p))
    setup.executescript(SCHEMA)
    setup.commit()
    setup.close()
    return str(p)


def test_reconcile_persists_across_connections(db_path):
    """The assertion the in-memory tests structurally could not make."""
    writer = academy_conn(db_path)
    assert reconcile_all_step_assets(writer, DISCOVERED) == 1
    writer.close()  # a missing commit is discarded here

    reader = academy_conn(db_path)
    row = reader.execute(
        "SELECT step_type, starter_code_path, test_code_path FROM fa_mission_steps "
        "WHERE mission_id=1 AND step_num=1"
    ).fetchone()
    reader.close()

    assert row["step_type"] == "coding", "the promotion did not persist"
    assert row["test_code_path"] == "tier1/m01-x/steps/step1_test.py"
    assert row["starter_code_path"] == "tier1/m01-x/steps/step1_starter.py"


def test_a_second_pass_over_persisted_rows_is_a_no_op(db_path):
    """Idempotency has to hold across the commit boundary too."""
    first = academy_conn(db_path)
    reconcile_all_step_assets(first, DISCOVERED)
    first.close()

    second = academy_conn(db_path)
    reconcile_all_step_assets(second, DISCOVERED)
    second.close()

    reader = academy_conn(db_path)
    rows = reader.execute(
        "SELECT step_type, test_code_path FROM fa_mission_steps WHERE mission_id=1"
    ).fetchall()
    reader.close()
    assert len(rows) == 1, "the second pass duplicated a row"
    assert rows[0]["step_type"] == "coding"


def test_nothing_to_do_does_not_require_a_commit(db_path):
    """An unknown slug touches nothing; the pass must still return cleanly."""
    conn = academy_conn(db_path)
    assert reconcile_all_step_assets(conn, {"no-such": [{"step_num": 1}]}) == 0
    conn.close()


def test_a_failing_commit_is_logged_not_raised(db_path, caplog):
    """Start-up must survive a database that cannot commit."""
    conn = academy_conn(db_path)

    class NoCommit:
        def __init__(self, inner):
            self._inner = inner

        def execute(self, *a, **k):
            return self._inner.execute(*a, **k)

        def commit(self):
            raise sqlite3.OperationalError("disk I/O error")

    with caplog.at_level("WARNING"):
        reconcile_all_step_assets(NoCommit(conn), DISCOVERED)  # must not raise
    conn.close()
    assert any("commit failed" in r.message or "commit failed" in r.getMessage()
               for r in caplog.records)
