# CUI // SP-CTI
"""Tests for dsyn-emit-06: DevSecOps canvas_events emission."""
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
    with patch("tools.devsecops.event_emitter.get_canvas_connection", _gc): yield shim

def test_emit_pipeline_stage_failed(shim):
    from tools.devsecops import event_emitter
    with _patch(shim):
        ok = event_emitter.emit_pipeline_stage_failed("proj-1", "sast_scan", severity="high")
    assert ok
    row = shim.execute("SELECT * FROM canvas_events WHERE event_type='devsecops.pipeline_stage_failed'").fetchone()
    assert row is not None
    p = json.loads(row["payload_json"])
    assert p["stage_name"] == "sast_scan"
    assert p["pipeline_id"] == "proj-1"

def test_emit_stig_finding_new(shim):
    from tools.devsecops import event_emitter
    with _patch(shim):
        ok = event_emitter.emit_stig_finding_new("proj-2", "stig_compliance", severity="CAT1", control_id="V-220001")
    assert ok
    row = shim.execute("SELECT * FROM canvas_events WHERE event_type='devsecops.stig_finding_new'").fetchone()
    assert row is not None
    p = json.loads(row["payload_json"])
    assert p["control_id"] == "V-220001"
    assert p["severity"] == "CAT1"

def test_emit_failure_returns_false():
    from tools.devsecops import event_emitter
    @contextmanager
    def _boom():
        c = _new_conn()
        c.execute = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("fail"))
        yield c
    with patch("tools.devsecops.event_emitter.get_canvas_connection", _boom):
        assert event_emitter.emit_pipeline_stage_failed("p", "s") is False

def test_record_pipeline_stage_failure_integration(monkeypatch):
    """record_pipeline_stage_failure() wraps emit correctly."""
    from tools.devsecops import pipeline_security_generator
    emitted = []
    monkeypatch.setattr("tools.devsecops.event_emitter.emit_pipeline_stage_failed",
                        lambda *a, **k: emitted.append(True) or True)
    result = pipeline_security_generator.record_pipeline_stage_failure(
        "proj-3", "dependency_audit", severity="high", change_summary="SCA failed"
    )
    assert result is True
    assert len(emitted) == 1

def test_source_and_target_canvas(shim):
    from tools.devsecops import event_emitter
    with _patch(shim):
        event_emitter.emit_pipeline_stage_failed("p", "sast")
    row = shim.execute("SELECT * FROM canvas_events").fetchone()
    assert row["source_canvas"] == "devsecops"
    assert row["target_canvas"] == "dic"
