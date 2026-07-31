"""Tests for the transaction-leak guard (tsh-leak-01).

The guard's own failure path cannot be asserted from inside a test — the
failure would be this test's — so detection is tested against the registry in
_txn_guard and the wiring is tested against the fixture list. The end-to-end
behaviour (a leaking test errors at teardown, the next test still passes) is
exercised by tests/test_txn_guard_e2e.py, which runs pytest in a subprocess.
"""
import sqlite3
import threading

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


def test_origin_records_the_opening_thread_and_frame(tmp_path):
    """The registry remembers who opened a connection, not just that it exists."""
    conn = sqlite3.connect(str(tmp_path / "origin.db"))
    try:
        thread_name, frames = _txn_guard.origin_of(conn)
        assert thread_name == threading.current_thread().name
        # The opening line is this test, not the conftest wrapper around it.
        assert any(__name__.rsplit(".", 1)[-1] in f for f in frames), frames
        assert not any("in track" in f or "_tracking_connect" in f for f in frames), frames
    finally:
        conn.close()


def test_origin_is_recorded_for_connections_opened_off_thread(tmp_path):
    """A background thread's connection is attributed to that thread, not the test.

    This is the mis-attribution the origin registry exists for: the guard fails
    whichever test happens to be running, so the report has to say where the
    connection actually came from.
    """
    opened = []

    def _open():
        conn = sqlite3.connect(str(tmp_path / "offthread.db"))
        opened.append(conn)
        # Close on the creating thread: sqlite3 objects are thread-affine, and
        # the origin lookup below needs only the object's identity, not the
        # connection. Leaving it open would also trip the guard being tested.
        conn.close()

    worker = threading.Thread(target=_open, name="probe-worker")
    worker.start()
    worker.join()

    thread_name, frames = _txn_guard.origin_of(opened[0])
    assert thread_name == "probe-worker"
    assert any("in _open" in f for f in frames), frames


def test_describe_origin_survives_an_untracked_connection():
    """Diagnostics must never raise on a connection the registry never saw."""

    class NotAConnection:
        pass

    described = _txn_guard.describe_origin(NotAConnection())
    # An untracked connection is a different defect from a leaked transaction,
    # so the report says so instead of printing a blank.
    assert "not attributable" in described


def test_guard_fixture_is_autouse(request):
    """The guard runs for every test without being requested."""
    assert "assert_no_leaked_transaction" in request.fixturenames


@pytest.mark.allow_open_transaction
def test_marker_is_registered():
    """The opt-out marker resolves (no unknown-marker warning)."""
    assert True
