# CUI // SP-CTI
"""Tests for dsyn-emit-04: Compliance Canvas canvas_events emission."""
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
    with patch("tools.compliance.event_emitter.get_canvas_connection", _gc): yield shim

def test_emit_finding_created(shim):
    from tools.compliance import event_emitter
    with _patch(shim):
        ok = event_emitter.emit_finding_created("AC-17", "CAT1", project_id="proj-1", framework="NIST")
    assert ok
    row = shim.execute("SELECT * FROM canvas_events WHERE event_type='compliance.finding_created'").fetchone()
    assert row is not None
    p = json.loads(row["payload_json"])
    assert p["control_id"] == "AC-17"
    assert p["finding_severity"] == "CAT1"

def test_emit_poam_overdue(shim):
    from tools.compliance import event_emitter
    with _patch(shim):
        ok = event_emitter.emit_poam_overdue("poam-42", "IA-2", "2026-01-01", project_id="proj-1")
    assert ok
    row = shim.execute("SELECT * FROM canvas_events WHERE event_type='compliance.poam_overdue'").fetchone()
    assert row is not None
    p = json.loads(row["payload_json"])
    assert p["poam_id"] == "poam-42"
    assert p["control_id"] == "IA-2"

def test_emit_failure_returns_false(shim):
    from tools.compliance import event_emitter
    @contextmanager
    def _boom():
        c = _new_conn()
        c.execute = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("fail"))
        yield c
    with patch("tools.compliance.event_emitter.get_canvas_connection", _boom):
        assert event_emitter.emit_finding_created("x", "CAT1") is False

def test_source_and_target_canvas(shim):
    from tools.compliance import event_emitter
    with _patch(shim):
        event_emitter.emit_finding_created("SC-28", "high")
    row = shim.execute("SELECT * FROM canvas_events").fetchone()
    assert row["source_canvas"] == "compliance"
    assert row["target_canvas"] == "dic"

def test_ivv_persist_finding_emits_for_critical(shim, monkeypatch):
    """_ivv_persist_finding() emits canvas_event for critical severity."""
    from tools.compliance import ivv_assessor
    emitted = []
    def _fake_emit(control_id, finding_severity, **kw):
        emitted.append({"control_id": control_id, "severity": finding_severity})
        return True
    monkeypatch.setattr("tools.compliance.event_emitter.emit_finding_created", _fake_emit)

    fake_conn = type("C", (), {
        "execute": lambda *a, **k: None,
    })()
    finding = {
        "finding_id": "IV-001",
        "severity": "critical",
        "process_area": "SM",
        "title": "Critical finding",
        "description": "desc",
        "recommendation": "fix it",
    }
    ivv_assessor._ivv_persist_finding(fake_conn, "proj-1", finding)
    assert len(emitted) == 1
    assert emitted[0]["severity"] == "critical"

def test_ivv_persist_finding_no_emit_for_low(shim, monkeypatch):
    """_ivv_persist_finding() does NOT emit for low severity findings."""
    from tools.compliance import ivv_assessor
    emitted = []
    def _fake_emit(*a, **k):
        emitted.append(True)
        return True
    monkeypatch.setattr("tools.compliance.event_emitter.emit_finding_created", _fake_emit)

    fake_conn = type("C", (), {"execute": lambda *a, **k: None})()
    finding = {
        "finding_id": "IV-002",
        "severity": "low",
        "process_area": "SM",
        "title": "Low finding",
        "description": "desc",
        "recommendation": "monitor",
    }
    ivv_assessor._ivv_persist_finding(fake_conn, "proj-1", finding)
    assert len(emitted) == 0
