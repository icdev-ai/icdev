# CUI // SP-CTI
"""Tests for dsyn-emit-03: ZIG canvas_events emission."""
from __future__ import annotations
import json
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Translating wrapper — event_emitter authors %s for PostgreSQL.
from _sql_compat import connect as _tconnect

_SCHEMA = """CREATE TABLE IF NOT EXISTS canvas_events (
    id TEXT PRIMARY KEY, source_canvas TEXT, target_canvas TEXT,
    event_type TEXT, payload_json TEXT, created_at TEXT,
    tenant_id TEXT DEFAULT '', classification TEXT DEFAULT 'CUI', consumed_at TEXT
);"""

def _new_conn():
    conn = _tconnect(":memory:")
    conn.execute(_SCHEMA)
    conn.commit()
    return conn

@pytest.fixture
def shim(): return _new_conn()

@contextmanager
def _patch(shim):
    @contextmanager
    def _gc(): yield shim
    # The emitter opens the RLS-disabled canvas connection (PR #720), not
    # get_connection() — patching the latter raises AttributeError.
    with patch("tools.security.zig.event_emitter.get_canvas_connection", _gc): yield shim

def test_emit_posture_score_drop(shim):
    from tools.security.zig import event_emitter
    with _patch(shim):
        ok = event_emitter.emit_posture_score_drop("identity", 80.0, 70.0, project_id="proj-1")
    assert ok
    row = shim.execute("SELECT * FROM canvas_events WHERE event_type='zig.posture_score_drop'").fetchone()
    assert row is not None
    p = json.loads(row["payload_json"])
    assert p["pillar_name"] == "identity"
    assert p["score_drop"] == 10.0

def test_emit_pillar_gap_detected(shim):
    from tools.security.zig import event_emitter
    with _patch(shim):
        ok = event_emitter.emit_pillar_gap_detected("data", 65.0, threshold=70.0)
    assert ok
    row = shim.execute("SELECT * FROM canvas_events WHERE event_type='zig.pillar_gap_detected'").fetchone()
    assert row is not None
    p = json.loads(row["payload_json"])
    assert p["pillar_name"] == "data"
    assert p["current_score"] == 65.0
    assert p["threshold"] == 70.0

def test_emit_failure_returns_false(shim):
    from tools.security.zig import event_emitter
    @contextmanager
    def _boom():
        c = _new_conn()
        c.execute = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("fail"))
        yield c
    with patch("tools.security.zig.event_emitter.get_canvas_connection", _boom):
        assert event_emitter.emit_posture_score_drop("x", 80.0, 60.0) is False

def test_source_and_target_canvas(shim):
    from tools.security.zig import event_emitter
    with _patch(shim):
        event_emitter.emit_pillar_gap_detected("devices", 50.0)
    row = shim.execute("SELECT * FROM canvas_events").fetchone()
    assert row["source_canvas"] == "zig"
    assert row["target_canvas"] == "dic"
