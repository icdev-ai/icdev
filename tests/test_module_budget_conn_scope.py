"""Regression tests for connection handling in module_budget_tracker.

Every entry point here used to do ``conn = _get_conn()`` ... ``conn.close()``
with no try/finally, so any exception in between leaked an open connection
holding an implicit write transaction.

That mattered far beyond a missed close. Budget usage is recorded from
``ChatManager._agent_loop`` background threads, which outlive the test that
started them. A transaction leaked there blocks later DDL on other connections
until the busy timeout expires, and the autouse leak guard attributes it to
whichever unrelated test is finishing at the time.

These tests assert the invariant directly -- no connection survives the call in
a transaction -- on both the success and the failure path. The failure path is
the one that regressed; a test that only covers success passes against the
original buggy code.
"""
import sqlite3

import pytest

from tools.budget import module_budget_tracker as mbt


@pytest.fixture
def track_connections(monkeypatch):
    """Record every connection module_budget_tracker opens during a call."""
    opened = []
    real_get_conn = mbt._get_conn

    def _tracking():
        conn = real_get_conn()
        opened.append(conn)
        return conn

    monkeypatch.setattr(mbt, "_get_conn", _tracking)
    return opened


def _still_in_transaction(conn) -> bool:
    """Whether *conn* is still open AND holding a transaction.

    ``get_connection`` hands back a ``StorageConnection`` wrapper that exposes
    no ``in_transaction`` and no ``__getattr__`` passthrough, so the state has
    to be read off the wrapped DB-API connection via ``_conn``. Asking the
    wrapper directly always answers False -- which is precisely the mistake
    that made the first version of the fix a no-op.
    """
    raw = getattr(conn, "_conn", conn)
    try:
        return bool(raw.in_transaction)
    except (sqlite3.ProgrammingError, AttributeError):
        # Closed (the outcome we want), or a non-sqlite backend that does not
        # report the flag at all.
        return False


def _assert_no_leaks(opened):
    assert opened, "expected the call to open at least one connection"
    leaked = [c for c in opened if _still_in_transaction(c)]
    assert not leaked, f"{len(leaked)} of {len(opened)} connection(s) left in a transaction"


def test_dashboard_leaves_no_open_transaction(track_connections):
    result = mbt.get_module_budget_dashboard()
    assert result["status"] == "ok"
    _assert_no_leaks(track_connections)


def test_check_budget_leaves_no_open_transaction(track_connections):
    module = next(iter(mbt.VALID_MODULES))
    mbt.check_module_budget(module)
    _assert_no_leaks(track_connections)


def test_no_leak_when_the_call_raises(track_connections, monkeypatch):
    """The path that actually regressed.

    ``_ensure_tables`` is made to open a write transaction and then fail,
    standing in for the real failure modes (locked database, schema mismatch,
    rejected INSERT). Against the pre-fix code this leaves the connection open
    and in a transaction; ``_conn_scope`` rolls it back and closes it.
    """
    module = next(iter(mbt.VALID_MODULES))

    def _boom(conn):
        conn.execute("CREATE TABLE IF NOT EXISTS _leak_probe (x INTEGER)")
        conn.execute("INSERT INTO _leak_probe VALUES (1)")  # opens a write txn
        raise RuntimeError("simulated failure inside the connection scope")

    monkeypatch.setattr(mbt, "_ensure_tables", _boom)

    with pytest.raises(RuntimeError, match="simulated failure"):
        mbt.record_module_usage(module, cost_usd=0.01, function="regression-probe")

    _assert_no_leaks(track_connections)


def test_the_failed_write_is_rolled_back_not_committed(track_connections, monkeypatch):
    """Unfinished work from a failed path must not be silently persisted.

    ``_conn_scope`` rolls back rather than commits on the way out, so a partial
    write from an aborted call leaves nothing behind.
    """
    module = next(iter(mbt.VALID_MODULES))

    def _boom(conn):
        conn.execute("CREATE TABLE IF NOT EXISTS _leak_probe_rollback (x INTEGER)")
        conn.commit()  # the table itself is committed; the row below is not
        conn.execute("INSERT INTO _leak_probe_rollback VALUES (42)")
        raise RuntimeError("simulated failure after a partial write")

    monkeypatch.setattr(mbt, "_ensure_tables", _boom)

    with pytest.raises(RuntimeError):
        mbt.record_module_usage(module, cost_usd=0.01, function="rollback-probe")

    monkeypatch.undo()
    conn = mbt._get_conn()
    try:
        rows = conn.execute("SELECT x FROM _leak_probe_rollback").fetchall()
    finally:
        conn.close()
    assert rows == [], f"uncommitted row survived an aborted call: {rows}"
