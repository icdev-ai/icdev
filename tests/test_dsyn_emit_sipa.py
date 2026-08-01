# CUI // SP-CTI
"""Tests for dsyn-emit-05: SIPA canvas_events emission."""
from __future__ import annotations
import json
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_SCHEMA = """CREATE TABLE IF NOT EXISTS canvas_events (
    id TEXT PRIMARY KEY, source_canvas TEXT, target_canvas TEXT,
    event_type TEXT, payload_json TEXT, created_at TEXT,
    tenant_id TEXT DEFAULT '', classification TEXT DEFAULT 'CUI', consumed_at TEXT
);"""

class _ShimConn:
    def __init__(self):
        self._db = sqlite3.connect(":memory:")
        self._db.row_factory = sqlite3.Row
        self._db.execute(_SCHEMA); self._db.commit()
    def execute(self, sql, params=()):
        return self._db.execute(sql.replace("%s", "?"), params)
    def commit(self): self._db.commit()
    def close(self): self._db.close()
    def __enter__(self): return self
    def __exit__(self, *_): self.commit()

@pytest.fixture
def shim(): return _ShimConn()

@contextmanager
def _patch(shim):
    # get_canvas_connection, NOT get_connection. event_emitter writes to
    # canvas_events, which (migration 039) has no tenant_id/classification
    # columns — so it deliberately uses the RLS-disabled canvas connection
    # (e59929a98), or get_connection()'s predicate injection would raise
    # UndefinedColumn on PostgreSQL. Patching the old name raised
    # "module does not have the attribute 'get_connection'" and every test
    # here errored, which is not the same as the code being broken.
    @contextmanager
    def _gc(): yield shim
    with patch("tools.integrity.event_emitter.get_canvas_connection", _gc): yield shim

def test_emit_vulnerability_found(shim):
    from tools.integrity import event_emitter
    with _patch(shim):
        ok = event_emitter.emit_vulnerability_found(
            "tools/malicious.py", "unauthorized_capability", "critical", assessment_id="42"
        )
    assert ok
    row = shim.execute("SELECT * FROM canvas_events WHERE event_type='sipa.vulnerability_found'").fetchone()
    assert row is not None
    p = json.loads(row["payload_json"])
    assert p["file_path"] == "tools/malicious.py"
    assert p["severity"] == "critical"

def test_emit_quarantine_triggered(shim):
    from tools.integrity import event_emitter
    with _patch(shim):
        ok = event_emitter.emit_quarantine_triggered(
            "evil.py", assessment_id="99", reason="malware_signature"
        )
    assert ok
    row = shim.execute("SELECT * FROM canvas_events WHERE event_type='sipa.quarantine_triggered'").fetchone()
    assert row is not None
    p = json.loads(row["payload_json"])
    assert p["reason"] == "malware_signature"

def test_emit_failure_returns_false():
    from tools.integrity import event_emitter
    @contextmanager
    def _boom():
        c = _ShimConn()
        c.execute = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("fail"))
        yield c
    with patch("tools.integrity.event_emitter.get_canvas_connection", _boom):
        assert event_emitter.emit_vulnerability_found("f.py", "vuln", "high") is False

def test_source_and_target_canvas(shim):
    from tools.integrity import event_emitter
    with _patch(shim):
        event_emitter.emit_quarantine_triggered("bad.py")
    row = shim.execute("SELECT * FROM canvas_events").fetchone()
    assert row["source_canvas"] == "sipa"
    assert row["target_canvas"] == "dic"
