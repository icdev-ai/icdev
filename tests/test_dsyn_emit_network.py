# CUI // SP-CTI
"""Tests for dsyn-emit-02: Network Canvas canvas_events emission."""
from __future__ import annotations
import json
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests._sql_compat import translating  # noqa: E402

_SCHEMA = """
CREATE TABLE IF NOT EXISTS canvas_events (
    id TEXT PRIMARY KEY, source_canvas TEXT, target_canvas TEXT,
    event_type TEXT, payload_json TEXT, created_at TEXT,
    tenant_id TEXT DEFAULT '', classification TEXT DEFAULT 'CUI', consumed_at TEXT
);
"""

@pytest.fixture
def shim():
    """Translating in-memory connection standing in for get_canvas_connection().

    ``unclosable`` because ``_emit`` exits the ``with`` block — which closes the
    connection — before the test reads the row back, and an in-memory database
    dies with its connection.
    """
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.execute(_SCHEMA)
    db.commit()
    yield translating(db, unclosable=True)
    db.close()

@contextmanager
def _patch(shim):
    # event_emitter uses get_canvas_connection (canvas_events has no
    # tenant_id/classification columns, so RLS predicate injection must not run).
    with patch("tools.network.event_emitter.get_canvas_connection", lambda: shim):
        yield shim

def test_emit_migration_phase_complete(shim):
    from tools.network import event_emitter
    with _patch(shim):
        ok = event_emitter.emit_migration_phase_complete("ph-1", "mig-1", phase_name="Phase 1")
    assert ok
    row = shim.execute("SELECT * FROM canvas_events WHERE event_type='network.migration_phase_complete'").fetchone()
    assert row is not None
    p = json.loads(row["payload_json"])
    assert p["phase_name"] == "Phase 1"
    assert p["migration_id"] == "mig-1"

def test_emit_anomaly_detected(shim):
    from tools.network import event_emitter
    with _patch(shim):
        ok = event_emitter.emit_anomaly_detected("mig-2", "phase_rollback", severity="high")
    assert ok
    row = shim.execute("SELECT * FROM canvas_events WHERE event_type='network.anomaly_detected'").fetchone()
    assert row is not None
    p = json.loads(row["payload_json"])
    assert p["anomaly_type"] == "phase_rollback"

def test_emit_failure_returns_false(shim, monkeypatch):
    from tools.network import event_emitter
    def _boom(*_a, **_k): raise RuntimeError("fail")
    monkeypatch.setattr(shim, "execute", _boom)
    with _patch(shim):
        assert event_emitter.emit_migration_phase_complete("p", "m") is False

def test_source_and_target_canvas(shim):
    from tools.network import event_emitter
    with _patch(shim):
        event_emitter.emit_migration_phase_complete("p", "m")
    row = shim.execute("SELECT * FROM canvas_events").fetchone()
    assert row["source_canvas"] == "network"
    assert row["target_canvas"] == "dic"
