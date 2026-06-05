# CUI // SP-CTI
"""Regression: PostgreSQL executescript statement isolation (savepoints).

Root cause of the CI E2E "relation \"projects\" does not exist" failure: the PG
branch of executescript ran every statement in one transaction and, on any
error, issued a bare conn.rollback() — discarding every object created earlier,
so dependent statements then failed and cascaded the whole baseline load.

Fix (`_pg_exec_statements`): each statement runs in its own SAVEPOINT so a
failure rolls back only itself; comment-only / empty chunks are skipped; and
autocommit connections (where SAVEPOINT is unavailable) fall back to direct
per-statement execution. These tests use a fake DB-API connection so they run
on any backend (the default CI Test job forces sqlite).
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.db.storage import StorageConnection, _pg_exec_statements  # noqa: E402


class _FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, params=None):
        self.conn.log.append(sql)
        rule = self.conn.rules.get(sql.strip())
        if rule:
            raise RuntimeError(rule)


class _FakeConn:
    """Records SQL, rollbacks, commits. `rules` maps a statement to an error
    message it should always raise. `no_savepoint=True` simulates autocommit
    (SAVEPOINT itself raises)."""

    def __init__(self, rules=None, no_savepoint=False):
        self.log: list[str] = []
        self.rules = dict(rules or {})
        if no_savepoint:
            self.rules["SAVEPOINT icdev_es_stmt"] = "can only be used in transaction blocks"
        self.full_rollbacks = 0
        self.commits = 0

    def cursor(self):
        return _FakeCursor(self)

    def rollback(self):
        self.full_rollbacks += 1

    def commit(self):
        self.commits += 1


def test_failing_statement_never_triggers_full_rollback():
    conn = _FakeConn(rules={"BROKEN": "boom"})
    StorageConnection(conn, "postgresql").executescript("OK_A; BROKEN; OK_B")
    # The catastrophic full-transaction rollback must NOT be used.
    assert conn.full_rollbacks == 0
    assert conn.commits == 1


def test_statements_after_a_failure_still_execute():
    conn = _FakeConn(rules={"BROKEN": "boom"})
    StorageConnection(conn, "postgresql").executescript("OK_A; BROKEN; OK_B")
    # Both good statements (before AND after the failure) ran; failure isolated.
    assert any("OK_A" in s for s in conn.log)
    assert any("OK_B" in s for s in conn.log)
    assert "ROLLBACK TO SAVEPOINT icdev_es_stmt" in conn.log


def test_comment_only_chunks_are_skipped():
    conn = _FakeConn()
    _pg_exec_statements(conn.cursor(), "-- just a comment\n; CREATE TABLE x (id INT)", "postgresql")
    # The comment chunk is never sent to the DB; only the real statement is.
    assert not any("just a comment" in s for s in conn.log)
    assert any("CREATE TABLE x" in s for s in conn.log)


def test_autocommit_connection_falls_back_to_direct_execution():
    # SAVEPOINT raises (autocommit) -> statements run directly, no crash.
    conn = _FakeConn(no_savepoint=True)
    _pg_exec_statements(conn.cursor(), "CREATE TABLE a (id INT); CREATE TABLE b (id INT)", "postgresql")
    assert any("CREATE TABLE a" in s for s in conn.log)
    assert any("CREATE TABLE b" in s for s in conn.log)
    assert conn.full_rollbacks == 0
