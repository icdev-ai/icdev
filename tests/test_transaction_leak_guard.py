"""Tests for the transaction-leak guard (tsh-leak-01).

The guard's own failure path cannot be asserted from inside a test — the
failure would be this test's — so detection is tested against the registry in
_txn_guard and the wiring is tested against the fixture list. The end-to-end
behaviour (a leaking test errors at teardown, the next test still passes) is
exercised by tests/test_txn_guard_e2e.py, which runs pytest in a subprocess.
"""
import sqlite3

import pytest

import _txn_guard


def test_tracker_registers_connections_opened_during_the_test(tmp_path):
    """sqlite3.connect is wrapped, so new connections land in the registry."""
    conn = sqlite3.connect(str(tmp_path / "tracked.db"))
    try:
        assert conn in _txn_guard.TRACKED_CONNECTIONS
    finally:
        conn.close()


def test_committed_work_is_not_reported(tmp_path):
    conn = sqlite3.connect(str(tmp_path / "clean.db"))
    try:
        conn.execute("CREATE TABLE t (id INTEGER)")
        conn.execute("INSERT INTO t (id) VALUES (1)")
        conn.commit()
        assert conn.in_transaction is False
        assert _txn_guard.open_write_transactions() == []
    finally:
        conn.close()


def test_uncommitted_write_is_reported(tmp_path):
    """An open implicit BEGIN is what the guard fails a test on."""
    conn = sqlite3.connect(str(tmp_path / "leaky.db"))
    try:
        conn.execute("CREATE TABLE t (id INTEGER)")
        conn.commit()
        conn.execute("INSERT INTO t (id) VALUES (1)")  # no commit -> open txn
        assert conn.in_transaction is True
        assert conn in _txn_guard.open_write_transactions()
    finally:
        # Clean up so this test does not trip the guard it is testing.
        conn.rollback()
        conn.close()


def test_closed_connections_are_not_reported(tmp_path):
    """A closed connection raises on in_transaction; skip it, do not raise."""
    conn = sqlite3.connect(str(tmp_path / "closed.db"))
    conn.execute("CREATE TABLE t (id INTEGER)")
    conn.execute("INSERT INTO t (id) VALUES (1)")
    conn.close()  # discards the open transaction along with the connection

    assert _txn_guard.open_write_transactions() == []


def test_guard_fixture_is_autouse(request):
    """The guard runs for every test without being requested."""
    assert "assert_no_leaked_transaction" in request.fixturenames


@pytest.mark.allow_open_transaction
def test_marker_is_registered():
    """The opt-out marker resolves (no unknown-marker warning)."""
    assert True
