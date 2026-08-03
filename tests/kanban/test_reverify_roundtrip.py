"""Real-schema round-trip for reverify (kpr-rvfy-01 follow-up).

Why this file exists separately from test_reverify.py: that suite drives a
FakeConn, which accepts any SQL. It passed while the INSERT omitted
`kanban_verifications.id` — a TEXT PRIMARY KEY that is NOT NULL with no default
on PostgreSQL — so every live write raised NotNullViolation, was swallowed by the
watcher's best-effort except, and the self-heal silently did nothing.

A mock cannot catch a schema mismatch. This one inserts against the real DDL and
reads the row back, which is the only way to prove the write LANDED.
"""
from __future__ import annotations

import sqlite3

import pytest

from tools.kanban import reverify as rv


@pytest.fixture
def real_schema_conn(tmp_path):
    """A sqlite connection carrying the genuine kanban_verifications DDL.

    Uses the shipped DDL rather than a hand-written table, so a future column
    change is reflected here automatically.
    """
    from tools.kanban import init_db as kanban_init

    db = tmp_path / "kanban.db"
    raw = sqlite3.connect(str(db))
    raw.row_factory = sqlite3.Row
    ddl = getattr(kanban_init, "_KANBAN_VERIFICATIONS_DDL", None)
    if ddl is None:  # DDL constant renamed — find it rather than silently skip
        ddl = next(
            (v for k, v in vars(kanban_init).items()
             if isinstance(v, str) and "CREATE TABLE IF NOT EXISTS kanban_verifications" in v),
            None,
        )
    assert ddl, "kanban_verifications DDL not found in tools/kanban/init_db.py"
    raw.executescript(ddl)
    raw.executescript(
        "CREATE TABLE IF NOT EXISTS kanban_tasks "
        "(id TEXT PRIMARY KEY, branch_name TEXT);"
        "INSERT INTO kanban_tasks (id, branch_name) VALUES ('t1', NULL);"
    )
    raw.commit()

    class _Wrap:
        """Mimics StorageConnection's %s placeholders over raw sqlite3."""

        def __init__(self, c):
            self._c = c

        def execute(self, sql, params=None):
            return self._c.execute(sql.replace("%s", "?"), params or ())

        def commit(self):
            self._c.commit()

        def close(self):
            pass

    yield lambda: _Wrap(raw), raw
    raw.close()


def _runner(files="a.py\nb.py\n", count="1"):
    from types import SimpleNamespace

    def run(args, **kwargs):
        sub = args[1]
        if sub == "fetch":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if sub == "rev-parse":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if sub == "log":
            return SimpleNamespace(returncode=0, stdout=files, stderr="")
        return SimpleNamespace(returncode=0, stdout=count, stderr="")

    return run


def test_the_row_actually_lands(real_schema_conn):
    """The regression: a NOT NULL `id` made every live INSERT fail silently."""
    factory, raw = real_schema_conn
    verdict = rv.reverify("t1", factory, runner=_runner())
    assert verdict["result"] == "passed"
    assert verdict["written"] is True

    rows = raw.execute(
        "SELECT id, task_id, result, dispatch_source, git_commits "
        "FROM kanban_verifications WHERE task_id = 't1'"
    ).fetchall()
    assert len(rows) == 1, "the write did not land"
    row = rows[0]
    assert row["id"], "id must be populated — it is NOT NULL with no default"
    assert row["id"].startswith("rvfy-")
    assert row["result"] == "passed"
    assert row["dispatch_source"] == rv.DISPATCH_SOURCE


def test_ids_are_unique_across_calls(real_schema_conn):
    """id is a PRIMARY KEY — a fixed value would collide on the second write."""
    factory, raw = real_schema_conn
    rv.reverify("t1", factory, runner=_runner())
    rv.reverify("t1", factory, runner=_runner())
    ids = [r["id"] for r in raw.execute(
        "SELECT id FROM kanban_verifications WHERE task_id='t1'").fetchall()]
    assert len(ids) == 2 and len(set(ids)) == 2


def test_failed_verdict_also_lands(real_schema_conn):
    factory, raw = real_schema_conn
    verdict = rv.reverify("t1", factory, runner=_runner(files="", count="0"))
    assert verdict["result"] == "failed" and verdict["written"] is True
    row = raw.execute(
        "SELECT result FROM kanban_verifications WHERE task_id='t1'").fetchone()
    assert row["result"] == "failed"


def test_dry_run_leaves_the_table_empty(real_schema_conn):
    factory, raw = real_schema_conn
    rv.reverify("t1", factory, runner=_runner(), dry_run=True)
    assert raw.execute(
        "SELECT COUNT(*) c FROM kanban_verifications").fetchone()["c"] == 0


def test_every_not_null_column_without_a_default_is_supplied(real_schema_conn):
    """Guards the whole class, not just `id`.

    If someone adds a NOT NULL column to kanban_verifications, this fails here
    instead of silently in production behind a best-effort except.
    """
    factory, raw = real_schema_conn
    rv.reverify("t1", factory, runner=_runner())
    cols = raw.execute("PRAGMA table_info(kanban_verifications)").fetchall()
    required = {c["name"] for c in cols if c["notnull"] and c["dflt_value"] is None}
    row = dict(raw.execute(
        "SELECT * FROM kanban_verifications WHERE task_id='t1'").fetchone())
    missing = {c for c in required if row.get(c) is None}
    assert not missing, f"NOT NULL column(s) left null by the INSERT: {missing}"
