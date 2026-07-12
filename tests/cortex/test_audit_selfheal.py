# CUI // SP-CTI
"""Audit/session self-heal tests (Cortex analysis item 5).

record_audit / ensure_session must create the cortex tables on demand when they
are absent — a fresh PostgreSQL bootstrapped from pg_consolidated.sql (which
predates migrations 262/263) otherwise silently loses the first NIST-AU row.
"""
from __future__ import annotations

import importlib
import sqlite3

# tools.cortex.db.__init__ re-exports the init_db FUNCTION, shadowing the module
# attribute — import the module object explicitly to patch its globals.
idb = importlib.import_module("tools.cortex.db.init_db")
from tools.db.storage import StorageConnection  # noqa: E402


def _fresh_conn():
    # StorageConnection so record_audit's %s placeholders translate, but with NO
    # cortex tables pre-created — simulates the fresh-bootstrap gap.
    return StorageConnection(sqlite3.connect(":memory:"), "sqlite")


def test_record_audit_self_heals_missing_table(monkeypatch):
    monkeypatch.setattr(idb, "_SCHEMA_ENSURED", False)
    sc = _fresh_conn()
    aid = idb.record_audit(
        {"operation": "cortex.complete", "tenant_id": "t1", "outcomes": {}},
        conn=sc,
    )
    assert aid
    row = sc.execute(
        "SELECT id, function FROM cortex_audit WHERE id = %s", (aid,)
    ).fetchone()
    assert row is not None  # table was created by _ensure_schema, row persisted


def test_ensure_session_self_heals_missing_table(monkeypatch):
    monkeypatch.setattr(idb, "_SCHEMA_ENSURED", False)
    sc = _fresh_conn()

    class _Ctx:
        session_id = "sess-1"
        tenant_id = "t1"
        classification = "CUI"
        user_id = "u1"
        domain = ""
        air_gap = False

    sid = idb.ensure_session(_Ctx(), conn=sc)
    assert sid == "sess-1"
    row = sc.execute(
        "SELECT id FROM cortex_sessions WHERE id = %s", ("sess-1",)
    ).fetchone()
    assert row is not None


def test_ensure_schema_short_circuits_when_already_ensured(monkeypatch):
    monkeypatch.setattr(idb, "_SCHEMA_ENSURED", True)
    calls = []
    monkeypatch.setattr(idb, "init_db", lambda conn=None: calls.append(1))
    idb._ensure_schema(object())
    assert calls == []  # flag set -> init_db not called again
