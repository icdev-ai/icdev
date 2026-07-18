#!/usr/bin/env python3
# CUI // SP-CTI
"""Regression tests for pgrt-sweep-04: evidence_chain._table_exists (class-B fix).

Before this fix the module's local ``_table_exists`` issued
``SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=%s`` against the
RAW sqlite3 connections opened by ``_open_sqlite`` for the PDC/NDC/SDC canvas
databases. sqlite3 uses qmark (``?``) paramstyle, so the ``%s`` placeholder with
a bound parameter raised ``sqlite3.ProgrammingError`` — silently swallowed by the
surrounding ``try/except`` — and every canvas evidence event was dropped. On the
PostgreSQL-backed icdev audit connection the bare ``sqlite_master`` reference
raised as well.

The probe now delegates to the shared backend-aware
``tools.db.storage.table_exists`` helper. These tests prove:

  1. the raw-sqlite3 canvas path no longer raises and finds real tables, and
  2. the PostgreSQL code path issues an ``information_schema`` query.
"""

import sqlite3
from pathlib import Path

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# 1. Raw sqlite3 canvas path — the exact connection type _open_sqlite returns.
# ---------------------------------------------------------------------------


def test_table_exists_raw_sqlite_finds_table(tmp_path):
    """_table_exists must work on a RAW sqlite3 connection (regression: the old
    ``%s`` placeholder raised sqlite3.ProgrammingError on qmark paramstyle)."""
    from tools.compliance.evidence_chain import _table_exists

    db = tmp_path / "pipeline_canvas.db"
    conn = sqlite3.connect(str(db))
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE pc_audit (id INTEGER PRIMARY KEY, action TEXT)")
    conn.commit()

    # Present table → True (previously raised ProgrammingError → swallowed → skip)
    assert _table_exists(conn, "pc_audit") is True
    # Absent table → False (never raises)
    assert _table_exists(conn, "does_not_exist") is False
    conn.close()


def test_collect_pdc_events_executes_evidence_path(tmp_path, monkeypatch):
    """End-to-end: with a populated canvas DB, the PDC evidence path now yields
    events. Before the fix _table_exists raised on the raw sqlite3 connection and
    collect_pdc_events silently returned []."""
    import tools.compliance.evidence_chain as ec

    db = tmp_path / "pipeline_canvas.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        "CREATE TABLE pc_audit ("
        "id INTEGER PRIMARY KEY, action TEXT, entity_type TEXT, entity_id TEXT, "
        "details TEXT, user_id TEXT, classification TEXT, ts TEXT)"
    )
    conn.execute(
        "INSERT INTO pc_audit (action, entity_type, entity_id, details, user_id, "
        "classification, ts) VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("pipeline_scan", "pipeline", "p-1", "{}", "alice", "CUI", "2026-07-17 10:00:00"),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(ec, "PDC_DB", db)
    events = ec.collect_pdc_events()

    assert len(events) == 1, "canvas evidence path must execute and emit the event"
    assert events[0]["source"] == "pdc"
    assert events[0]["actor"] == "alice"
    assert events[0]["action"] == "pipeline_scan"


# ---------------------------------------------------------------------------
# 2. PostgreSQL code path — probe must hit information_schema, not sqlite_master.
# ---------------------------------------------------------------------------


class _RecordingCursor:
    def __init__(self):
        self.sql = None
        self.params = None

    def execute(self, sql, params=None):
        self.sql = sql
        self.params = params

    def fetchone(self):
        return (1,)


class _RecordingRaw:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


class _FakePGConn:
    """Duck-types a StorageConnection: exposes ``_backend`` and ``_conn``."""

    _backend = "postgresql"

    def __init__(self, raw):
        self._conn = raw


def test_table_exists_pg_uses_information_schema():
    """On a PostgreSQL connection the shared helper (used by _table_exists) must
    query information_schema.tables with the table name bound as a parameter."""
    from tools.compliance.evidence_chain import _table_exists

    cur = _RecordingCursor()
    conn = _FakePGConn(_RecordingRaw(cur))

    assert _table_exists(conn, "audit_trail") is True
    assert "information_schema.tables" in cur.sql.lower()
    assert "sqlite_master" not in cur.sql.lower()
    assert cur.params == ("audit_trail",)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
