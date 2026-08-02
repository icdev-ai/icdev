# CUI // SP-CTI
"""Tests for dsyn-emit-02: Network Canvas canvas_events emission.

tsr-canv-01-d4: 4 failed / 0 passed -> 0 failed / 4 passed. Same cause as
test_dsyn_emit_ndc.py — patched get_connection, emitter uses
get_canvas_connection().
"""
from __future__ import annotations
import json
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests import _sql_compat  # noqa: E402

_SCHEMA = """
CREATE TABLE IF NOT EXISTS canvas_events (
    id TEXT PRIMARY KEY, source_canvas TEXT, target_canvas TEXT,
    event_type TEXT, payload_json TEXT, created_at TEXT,
    tenant_id TEXT DEFAULT '', classification TEXT DEFAULT 'CUI', consumed_at TEXT
);
"""

def _shim_conn():
    """In-memory canvas_events DB that translates %s -> ? like the runtime does.

    ``unclosable`` because the emitter writes through ``with get_canvas_connection()``
    and the wrapper's ``__exit__`` would otherwise close the connection — taking the
    in-memory database with it before the test can read the row back.
    """
    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    conn = _sql_compat.translating(raw, unclosable=True)
    conn.execute(_SCHEMA)
    conn.commit()
    return conn

@pytest.fixture
def shim(): return _shim_conn()

@contextmanager
def _patch(shim):
    @contextmanager
    def _gc(): yield shim
    # canvas_events has no tenant_id/classification predicate to satisfy, so the
    # emitter writes through get_canvas_connection() (RLS disabled) — patch that,
    # not get_connection, which the module no longer imports.
    with patch("tools.network.event_emitter.get_canvas_connection", _gc): yield shim

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

def test_emit_failure_returns_false(shim):
    from tools.network import event_emitter
    @contextmanager
    def _boom():
        c = _shim_conn()
        c.execute = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("fail"))
        yield c
    with patch("tools.network.event_emitter.get_canvas_connection", _boom):
        assert event_emitter.emit_migration_phase_complete("p", "m") is False

def test_source_and_target_canvas(shim):
    from tools.network import event_emitter
    with _patch(shim):
        event_emitter.emit_migration_phase_complete("p", "m")
    row = shim.execute("SELECT * FROM canvas_events").fetchone()
    assert row["source_canvas"] == "network"
    assert row["target_canvas"] == "dic"
