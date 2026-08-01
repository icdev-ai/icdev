# CUI // SP-CTI
"""Regression test: canvas_events access must skip RLS predicate injection.

`canvas_events` (migration 039) is a canvas table with NO ``tenant_id`` /
``classification`` columns.  When the event bus is invoked while a
SecurityContext is active (e.g. the SIPA integrity reflex publishing a
``sipa.vulnerability_found`` event inside an authenticated request), the old
code opened the DB via ``get_connection()``.  That auto-injects the global
``tenant_id`` / ``classification IN (...)`` RLS predicate into every
SELECT/UPDATE, which on PostgreSQL raises ``UndefinedColumn`` and aborts the
whole transaction — blocking the reflex.

The fix routes the ``canvas_events`` reads/writes through
``get_canvas_connection()`` (RLS disabled).  Event scoping still lives in the
JSON payload (``_security_context``), so nothing is lost.

These tests run on the conftest SQLite backend.  SQLite does not raise the exact
PostgreSQL ``UndefinedColumn``, but the RLS injector rewrites the SQL to
reference a ``classification`` column that does not exist on ``canvas_events`` —
which SQLite reports as ``OperationalError: no such column: classification``.
So the OLD code path fails here and the NEW code path (no injection) passes,
reliably distinguishing the two.
"""
from __future__ import annotations

import importlib
import json

import pytest

from tools.canvas import event_bus
from tools.canvas.event_bus import dispatch_pending, publish, subscribe
from tools.security.security_context import SecurityContext

# Resolve the storage module through the compat shim so it is the SAME module
# object whose get_connection/get_canvas_connection event_bus calls (tools.* and
# icdev.tools.* must not diverge — see CLAUDE.md shim-aware monkeypatch note).
storage = importlib.import_module("tools.db.storage")

# Real canvas schema from migration 039 — deliberately NO tenant_id/classification.
_CANVAS_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS canvas_events (
    id             TEXT NOT NULL,
    source_canvas  TEXT NOT NULL,
    target_canvas  TEXT,
    event_type     TEXT NOT NULL,
    payload_json   TEXT NOT NULL DEFAULT '{}',
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    consumed_at    TEXT,
    PRIMARY KEY (id)
)
"""

_AUDIT_TRAIL_DDL = """
CREATE TABLE IF NOT EXISTS audit_trail (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id     TEXT,
    event_type     TEXT,
    actor          TEXT,
    action         TEXT,
    details        TEXT,
    affected_files TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at     TEXT DEFAULT (datetime('now'))
)
"""


@pytest.fixture
def active_security_context(tmp_path, monkeypatch):
    """Point the event bus at a temp DB and simulate an active SecurityContext.

    In production the context is bound to the connection by ``get_connection()``
    via Flask's ``g.security_context``.  Here we monkeypatch the ``get_connection``
    symbol *inside event_bus* to attach a real SecurityContext, faithfully
    reproducing the request-scoped RLS condition without a Flask app.

    ``get_canvas_connection`` (the fix) is NOT patched — it calls the real
    ``storage.get_connection`` internally and then clears the context, so the
    canvas path stays RLS-free.
    """

    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    db_path = tmp_path / "icdev.db"
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))

    # Create the canvas + audit tables in the temp DB via the storage layer.
    seed = storage.get_connection()
    try:
        seed.execute(_CANVAS_EVENTS_DDL)
        seed.execute(_AUDIT_TRAIL_DDL)
        seed.commit()
    finally:
        seed.close()

    ctx = SecurityContext(
        user_id="tester",
        role="analyst",
        tenant_id="tenant_a",
        classification="CUI",
    )

    def _context_bound_get_connection(db_path=None):
        conn = storage.get_connection(db_path=db_path)
        conn.set_security_context(ctx)
        return conn

    # event_bus.get_connection is used for the audit-only writes; patching it to
    # attach the context mirrors production and proves the fix keeps canvas_events
    # off the RLS path even when a context is live.
    monkeypatch.setattr(event_bus, "get_connection", _context_bound_get_connection)

    event_bus._LISTENERS.clear()
    yield ctx
    event_bus._LISTENERS.clear()


def test_publish_and_dispatch_pending_with_active_context(active_security_context):
    """publish() + dispatch_pending() must not raise while a context is active.

    On the OLD code path, dispatch_pending's SELECT/UPDATE against canvas_events
    is rewritten with a ``classification`` predicate the table lacks, raising
    ``OperationalError: no such column`` (the SQLite analogue of the PostgreSQL
    ``UndefinedColumn`` that blocks the SIPA reflex).  The fix must publish and
    dispatch cleanly.
    """
    received: list[dict] = []

    def handler(event_id, canvas_id, event_type, payload):
        received.append({"event_id": event_id, "payload": payload})

    subscribe("sipa", "sipa.vulnerability_found", handler,
              subscriber_context={"tenant_id": "tenant_a", "clearance": "CUI"})

    # Publish with an active security context — this is the SIPA reflex scenario.
    event_id = publish(
        "sipa",
        "sipa.vulnerability_found",
        {"finding": "component-drift"},
        target_canvas="integrity",
        security_context={"tenant_id": "tenant_a", "clearance": "CUI"},
    )
    assert event_id

    # dispatch_pending exercises SELECT + UPDATE on canvas_events — the calls that
    # the RLS injector would rewrite on the old path.  Must not raise.
    fired = dispatch_pending("integrity")
    assert fired == 1

    # Row landed and was marked consumed via the canvas (non-RLS) connection.
    conn = storage.get_connection()
    try:
        row = conn.execute(
            "SELECT payload_json, consumed_at FROM canvas_events WHERE id=?",
            (event_id,),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    payload_json = row[0] if isinstance(row, (list, tuple)) else row["payload_json"]
    consumed_at = row[1] if isinstance(row, (list, tuple)) else row["consumed_at"]
    # Scoping lives in the payload, not table columns.
    loaded = json.loads(payload_json)
    assert loaded["_security_context"]["tenant_id"] == "tenant_a"
    assert consumed_at is not None


def test_canvas_events_ops_use_non_rls_connection(active_security_context, monkeypatch):
    """The canvas_events reads/writes must go through get_canvas_connection.

    Spy on get_canvas_connection and assert publish() + dispatch_pending() route
    their DB access through it (security_context=None), never the RLS-injecting
    get_connection().
    """

    calls = {"canvas": 0}
    real_get_canvas = storage.get_canvas_connection

    def _spy_get_canvas_connection(canvas_env_var=None):
        calls["canvas"] += 1
        conn = real_get_canvas(canvas_env_var)
        # Canvas connection must NOT carry a security context (no RLS injection).
        assert getattr(conn, "_security_context", None) is None
        return conn

    monkeypatch.setattr(event_bus, "get_canvas_connection", _spy_get_canvas_connection)

    event_id = publish(
        "sipa", "sipa.vulnerability_found", {"finding": "x"},
        target_canvas="integrity",
        security_context={"tenant_id": "tenant_a", "clearance": "CUI"},
    )
    dispatch_pending("integrity")

    # publish (1) + dispatch_pending (1) each acquire the canvas connection.
    assert calls["canvas"] >= 2
    assert event_id
