# CUI // SP-CTI
"""Tests for dsyn-emit-07: CloudForge canvas_events emission."""
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
    @contextmanager
    def _gc(): yield shim
    with patch("tools.cloudforge.event_emitter.get_connection", _gc): yield shim

def test_emit_resource_provisioned(shim):
    from tools.cloudforge import event_emitter
    with _patch(shim):
        ok = event_emitter.emit_resource_provisioned(
            "vpc-12345", "vpc", cloud_provider="aws", region="us-gov-east-1"
        )
    assert ok
    row = shim.execute("SELECT * FROM canvas_events WHERE event_type='cloudforge.resource_provisioned'").fetchone()
    assert row is not None
    p = json.loads(row["payload_json"])
    assert p["resource_id"] == "vpc-12345"
    assert p["resource_type"] == "vpc"
    assert p["cloud_provider"] == "aws"

def test_emit_runbook_executed(shim):
    from tools.cloudforge import event_emitter
    with _patch(shim):
        ok = event_emitter.emit_runbook_executed(
            "rb-001", "Deploy ICDEV Stack", status="success", cloud_provider="azure"
        )
    assert ok
    row = shim.execute("SELECT * FROM canvas_events WHERE event_type='cloudforge.runbook_executed'").fetchone()
    assert row is not None
    p = json.loads(row["payload_json"])
    assert p["runbook_name"] == "Deploy ICDEV Stack"
    assert p["status"] == "success"

def test_emit_failure_returns_false():
    from tools.cloudforge import event_emitter
    @contextmanager
    def _boom():
        c = _ShimConn()
        c.execute = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("fail"))
        yield c
    with patch("tools.cloudforge.event_emitter.get_connection", _boom):
        assert event_emitter.emit_resource_provisioned("r", "vm") is False

def test_source_and_target_canvas(shim):
    from tools.cloudforge import event_emitter
    with _patch(shim):
        event_emitter.emit_runbook_executed("rb-2", "Runbook B", status="failed")
    row = shim.execute("SELECT * FROM canvas_events").fetchone()
    assert row["source_canvas"] == "cloudforge"
    assert row["target_canvas"] == "dic"
