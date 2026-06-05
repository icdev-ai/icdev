# CUI // SP-CTI
"""Regression: StorageConnection.executescript isolates per-statement failures.

Root cause of the CI E2E "relation \"projects\" does not exist" failure: the
PostgreSQL branch of executescript ran every statement in one transaction and,
on any statement error, issued a bare conn.rollback() — discarding every
object created earlier in the script. Dependent statements (indexes, FKs, the
baseline ALTER TABLE projects) then failed because the table had been wiped.

Fix: wrap each statement in a SAVEPOINT so a failing statement rolls back only
itself. These tests use a fake DB-API connection so they run on any backend
(the default CI Test job forces sqlite); the savepoint discipline is asserted
directly.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.db.storage import StorageConnection  # noqa: E402


class _FakeCursor:
    def __init__(self, log, fail_substr):
        self._log = log
        self._fail = fail_substr

    def execute(self, sql, params=None):
        self._log.append(sql)
        if self._fail and self._fail in sql:
            raise RuntimeError("simulated statement failure")


class _FakeConn:
    """Minimal psycopg2-like connection that records SQL and rollback calls."""

    def __init__(self, fail_substr=None):
        self.log: list[str] = []
        self.fail_substr = fail_substr
        self.full_rollbacks = 0
        self.commits = 0

    def cursor(self):
        return _FakeCursor(self.log, self.fail_substr)

    def rollback(self):
        self.full_rollbacks += 1

    def commit(self):
        self.commits += 1


SCRIPT = "CREATE TABLE a (id INT); THIS_IS_A_BAD_STATEMENT; CREATE TABLE b (id INT)"


def test_failing_statement_does_not_trigger_full_rollback():
    conn = _FakeConn(fail_substr="BAD_STATEMENT")
    StorageConnection(conn, "postgresql").executescript(SCRIPT)
    # The catastrophic full-transaction rollback must NOT be used.
    assert conn.full_rollbacks == 0
    # Exactly one SAVEPOINT per statement (3 statements).
    assert conn.log.count("SAVEPOINT icdev_es_stmt") == 3
    # The failure path rolled back only to the savepoint.
    assert "ROLLBACK TO SAVEPOINT icdev_es_stmt" in conn.log


def test_statements_after_a_failure_still_execute():
    conn = _FakeConn(fail_substr="BAD_STATEMENT")
    StorageConnection(conn, "postgresql").executescript(SCRIPT)
    # Both good statements (before AND after the failure) ran.
    assert any("CREATE TABLE a" in s for s in conn.log)
    assert any("CREATE TABLE b" in s for s in conn.log)
    assert conn.commits == 1


def test_all_succeed_releases_each_savepoint():
    conn = _FakeConn(fail_substr=None)  # nothing fails
    StorageConnection(conn, "postgresql").executescript(SCRIPT)
    assert conn.full_rollbacks == 0
    assert conn.log.count("SAVEPOINT icdev_es_stmt") == 3
    assert conn.log.count("RELEASE SAVEPOINT icdev_es_stmt") == 3
    assert "ROLLBACK TO SAVEPOINT icdev_es_stmt" not in conn.log
