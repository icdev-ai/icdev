# CUI // SP-CTI
"""Regression test: SIPA integrity event_emitter must write only real canvas_events columns.

``canvas_events`` (migration 039) has ONLY
``id, source_canvas, target_canvas, event_type, payload_json, created_at, consumed_at``
— deliberately NO ``tenant_id`` / ``classification`` columns.

The OLD ``tools/integrity/event_emitter.py::_emit`` inserted into
``canvas_events`` naming ``tenant_id`` and ``classification`` as table columns
(and opened the write via the RLS-injecting ``get_connection()``).  On
PostgreSQL that raises ``UndefinedColumn: column "tenant_id" of relation
"canvas_events"`` and aborts the whole transaction — so the SIPA reflex's
``sipa.vulnerability_found`` publish killed the entire assessment.  ``_emit``
swallows the exception and returns ``False``, so the OLD path silently drops the
event.

The fix routes the write through ``get_canvas_connection()`` (RLS disabled),
inserts only the real migration-039 columns, and folds tenant/classification
into ``payload_json`` under ``_security_context`` so scoping is preserved.

How this test distinguishes OLD vs NEW: the real migration-039 schema below has
no ``tenant_id``/``classification`` columns.  Under the OLD code the INSERT names
those columns, which SQLite rejects with
``OperationalError: table canvas_events has no column named tenant_id`` — caught
by ``_emit`` → returns ``False``, no row lands, and the assertions fail.  Under
the NEW code the INSERT matches the schema → returns ``True`` and the row lands.
"""
from __future__ import annotations

import importlib
import json

import pytest

from tools.integrity import event_emitter

# Same module object the emitter's get_canvas_connection resolves to (shim-aware).
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


@pytest.fixture
def canvas_events_db(tmp_path, monkeypatch):
    """Seed a temp SQLite DB with the real (RLS-free) canvas_events schema."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    db_path = tmp_path / "icdev.db"
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))

    seed = storage.get_connection()
    try:
        seed.execute(_CANVAS_EVENTS_DDL)
        seed.commit()
    finally:
        seed.close()
    return db_path


def _read_row(event_type):
    conn = storage.get_connection()
    try:
        return conn.execute(
            "SELECT payload_json, consumed_at FROM canvas_events "
            "WHERE event_type=?",
            (event_type,),
        ).fetchone()
    finally:
        conn.close()


def test_emit_vulnerability_found_lands_row(canvas_events_db):
    """emit_vulnerability_found must return True and persist a row.

    Under the OLD code the INSERT referenced nonexistent tenant_id/classification
    columns, so _emit caught the error and returned False with no row written.
    """
    ok = event_emitter.emit_vulnerability_found(
        file_path="tools/posture.py",
        finding_type="dynamic_import",
        severity="high",
        assessment_id="assessment-162",
        project_id="proj-1",
        tenant_id="tenant_a",
        classification="CUI",
    )
    assert ok is True

    row = _read_row("sipa.vulnerability_found")
    assert row is not None
    payload_json = row[0] if isinstance(row, (list, tuple)) else row["payload_json"]
    consumed_at = row[1] if isinstance(row, (list, tuple)) else row["consumed_at"]

    payload = json.loads(payload_json)
    # Scoping is carried in the payload, not as table columns.
    assert payload["_security_context"]["tenant_id"] == "tenant_a"
    assert payload["_security_context"]["clearance"] == "CUI"
    # Original event fields still present.
    assert payload["file_path"] == "tools/posture.py"
    assert payload["severity"] == "high"
    # Freshly emitted events are unconsumed.
    assert consumed_at is None


def test_emit_quarantine_triggered_lands_row(canvas_events_db):
    """emit_quarantine_triggered must also return True and persist a row."""
    ok = event_emitter.emit_quarantine_triggered(
        file_path="tools/posture.py",
        assessment_id="assessment-162",
        reason="unauthorized dynamic import",
        project_id="proj-1",
        tenant_id="tenant_b",
        classification="CUI",
    )
    assert ok is True

    row = _read_row("sipa.quarantine_triggered")
    assert row is not None
    payload_json = row[0] if isinstance(row, (list, tuple)) else row["payload_json"]
    payload = json.loads(payload_json)
    assert payload["_security_context"]["tenant_id"] == "tenant_b"
    assert payload["reason"] == "unauthorized dynamic import"
