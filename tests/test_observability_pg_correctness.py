#!/usr/bin/env python3
# CUI // SP-CTI
"""PG-correctness tests for the observability I/O layer (obx-trc-02).

These modules route all reads/writes through tools.db.storage.get_connection,
which targets PostgreSQL by default and ignores the SQLite `.db` path. The
regressions guarded here:

  1. The `self._db_path.exists()` file gate must NOT short-circuit writes when
     the effective backend is PostgreSQL (otherwise every observability write
     silently no-ops on a clean PG deployment — real data loss).
  2. Exception handlers must catch backend-appropriate DB errors (psycopg2.*),
     not only sqlite3.Error, so a PG error during flush is caught, not
     propagated into the traced caller.
  3. SQLiteSpan.end() must restore the parent as the active span (reset the
     saved contextvar token), so sibling spans keep the correct parent/trace.
  4. Buffered spans must be flushed at process exit (atexit), so up to
     buffer_size-1 spans are not lost when the process ends.

Shim-aware patching: the modules do `from tools.db.storage import get_connection`,
so the bound name lives in each module's own namespace. We patch that name on
the exact module object via importlib + monkeypatch.setattr. `tools.*` and
`icdev.tools.*` are distinct module objects; the code-under-test imports the
`tools.*` variant, so we patch that one.
"""

import importlib
import sqlite3

import pytest

from tools.db.storage import StorageConnection

TRACER_MOD = "tools.observability.sqlite_tracer"
PROV_MOD = "tools.observability.provenance.prov_recorder"


# ---------------------------------------------------------------------------
# Shared in-memory StorageConnection stub
# ---------------------------------------------------------------------------


def _make_shared_conn():
    """A single in-memory SQLite conn with the observability tables."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE otel_spans (
            id TEXT PRIMARY KEY,
            trace_id TEXT NOT NULL,
            parent_span_id TEXT,
            name TEXT NOT NULL,
            kind TEXT DEFAULT 'INTERNAL',
            start_time TEXT NOT NULL,
            end_time TEXT,
            duration_ms INTEGER DEFAULT 0,
            status_code TEXT DEFAULT 'UNSET',
            status_message TEXT,
            attributes TEXT,
            events TEXT,
            agent_id TEXT,
            project_id TEXT,
            classification TEXT DEFAULT 'CUI'
        );
        CREATE TABLE prov_entities (
            id TEXT PRIMARY KEY,
            entity_type TEXT,
            label TEXT,
            content_hash TEXT,
            content TEXT,
            attributes TEXT,
            trace_id TEXT,
            span_id TEXT,
            agent_id TEXT,
            project_id TEXT,
            classification TEXT
        );
        """
    )
    conn.commit()
    return conn


def _connection_factory(shared):
    """Return a get_connection replacement backed by `shared`.

    close() is a no-op so the in-memory DB survives across the many
    get_connection/close cycles the modules perform.
    """

    class _Persistent(StorageConnection):
        def close(self):  # keep the shared in-memory DB alive
            pass

    def factory(*_args, **_kwargs):
        return _Persistent(shared, "sqlite")

    return factory


@pytest.fixture
def shared_conn():
    conn = _make_shared_conn()
    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# (a) file gate must not defeat PG-primary writes
# ---------------------------------------------------------------------------


def test_flush_writes_under_pg_backend_without_db_file(tmp_path, monkeypatch, shared_conn):
    """Backend reports postgresql, no .db file — flush must NOT no-op."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "postgresql")
    mod = importlib.import_module(TRACER_MOD)
    monkeypatch.setattr(mod, "get_connection", _connection_factory(shared_conn))

    missing_db = tmp_path / "does_not_exist.db"
    assert not missing_db.exists()

    tracer = mod.SQLiteTracer(db_path=missing_db, agent_id="a1", project_id="p1")
    tracer._buffer_size = 1  # flush immediately on span end

    with tracer.start_span("op_under_pg") as span:
        span.set_attribute("k", "v")

    rows = shared_conn.execute("SELECT name, attributes FROM otel_spans").fetchall()
    assert len(rows) == 1, "span INSERT did not execute — file gate wrongly no-oped under PG"
    assert rows[0]["name"] == "op_under_pg"


def test_prov_recorder_writes_under_pg_backend_without_db_file(tmp_path, monkeypatch, shared_conn):
    """ProvRecorder.record_entity must write under PG even with no .db file."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "postgresql")
    mod = importlib.import_module(PROV_MOD)
    monkeypatch.setattr(mod, "get_connection", _connection_factory(shared_conn))

    missing_db = tmp_path / "no_such.db"
    recorder = mod.ProvRecorder(db_path=missing_db, project_id="p1")

    entity_id = recorder.record_entity("prompt", "hello")
    assert entity_id is not None, "record_entity no-oped under PG (file gate defeated writes)"

    rows = shared_conn.execute("SELECT id, label FROM prov_entities").fetchall()
    assert len(rows) == 1
    assert rows[0]["id"] == entity_id


def test_sqlite_backend_missing_file_still_gates(tmp_path, monkeypatch):
    """Under sqlite backend, a missing .db file still short-circuits (no crash)."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    mod = importlib.import_module(TRACER_MOD)

    def _boom(*_a, **_k):  # get_connection must never be reached
        raise AssertionError("get_connection called despite missing sqlite file")

    monkeypatch.setattr(mod, "get_connection", _boom)

    tracer = mod.SQLiteTracer(db_path=tmp_path / "absent.db")
    tracer._buffer_size = 1
    with tracer.start_span("discarded"):
        pass  # spans silently discarded, no exception


# ---------------------------------------------------------------------------
# (b) backend-appropriate exception handling
# ---------------------------------------------------------------------------


def test_pg_error_during_flush_is_caught(tmp_path, monkeypatch, shared_conn):
    """A psycopg2-style error during flush is caught, not propagated."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "postgresql")
    mod = importlib.import_module(TRACER_MOD)

    class FakePGError(Exception):
        """Stand-in for psycopg2.Error."""

    # Register the fake PG error in the module's error tuple, as psycopg2.Error
    # would be on a real PG install.
    monkeypatch.setattr(mod, "_DB_ERRORS", (sqlite3.Error, FakePGError))

    def _raise(*_a, **_k):
        raise FakePGError("connection reset by peer")

    monkeypatch.setattr(mod, "get_connection", _raise)

    tracer = mod.SQLiteTracer(db_path=tmp_path / "x.db")
    tracer._buffer_size = 1

    # end() -> _record_span -> flush -> get_connection raises FakePGError.
    # Must not propagate into the traced caller.
    with tracer.start_span("crashes_on_flush"):
        pass  # if the error escaped, this line raises


def test_prov_pg_error_is_caught(tmp_path, monkeypatch):
    """record_entity swallows a psycopg2-style error and returns None."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "postgresql")
    mod = importlib.import_module(PROV_MOD)

    class FakePGError(Exception):
        pass

    monkeypatch.setattr(mod, "_DB_ERRORS", (sqlite3.Error, FakePGError))

    def _raise(*_a, **_k):
        raise FakePGError("deadlock detected")

    monkeypatch.setattr(mod, "get_connection", _raise)

    recorder = mod.ProvRecorder(db_path=tmp_path / "x.db")
    assert recorder.record_entity("prompt", "boom") is None


# ---------------------------------------------------------------------------
# (c) nested-span parent restoration
# ---------------------------------------------------------------------------


def test_nested_spans_restore_parent_on_end(tmp_path, monkeypatch):
    """Ending a nested span must re-expose its parent as the active span.

    Sequence: start A (root), start B (child of A), end B, start C.
    C must be parented to A on the same trace. Before the fix, end B set the
    active span to None, orphaning C into a brand-new root trace.
    """
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    mod = importlib.import_module(TRACER_MOD)

    tracer = mod.SQLiteTracer(db_path=tmp_path / "absent.db")  # gate discards, no DB

    a = tracer.start_span("A")
    assert a.parent_span_id is None

    b = tracer.start_span("B")
    assert b.parent_span_id == a.span_id
    b.end()

    c = tracer.start_span("C")
    assert c.parent_span_id == a.span_id, "sibling C orphaned — token not reset on end()"
    assert c.trace_id == a.trace_id

    c.end()
    a.end()


# ---------------------------------------------------------------------------
# (d) atexit flush drains the buffer
# ---------------------------------------------------------------------------


def test_atexit_flush_drains_buffer(tmp_path, monkeypatch, shared_conn):
    """Buffered-but-unflushed spans are written by the atexit flush hook."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "postgresql")
    mod = importlib.import_module(TRACER_MOD)
    monkeypatch.setattr(mod, "get_connection", _connection_factory(shared_conn))

    tracer = mod.SQLiteTracer(db_path=tmp_path / "absent.db")
    tracer._buffer_size = 10  # keep spans buffered

    for i in range(3):
        with tracer.start_span(f"buffered_{i}"):
            pass

    assert len(tracer._buffer) == 3, "spans should still be buffered below threshold"

    tracer._atexit_flush()

    assert tracer._buffer == [], "atexit flush did not drain the buffer"
    rows = shared_conn.execute("SELECT COUNT(*) AS n FROM otel_spans").fetchone()
    assert rows["n"] == 3


def test_atexit_flush_safe_when_db_gone(tmp_path, monkeypatch):
    """The atexit hook never raises, even if the DB layer is unavailable."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "postgresql")
    mod = importlib.import_module(TRACER_MOD)

    def _raise(*_a, **_k):
        raise RuntimeError("interpreter shutting down")

    monkeypatch.setattr(mod, "get_connection", _raise)

    tracer = mod.SQLiteTracer(db_path=tmp_path / "absent.db")
    tracer._buffer_size = 10
    with tracer.start_span("late"):
        pass

    tracer._atexit_flush()  # must not raise
