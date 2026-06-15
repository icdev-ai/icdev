# CUI // SP-CTI
"""Tests for dsyn-emit-08: AI-ify canvas_events emission."""
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
    with patch("tools.aiify.event_emitter.get_connection", _gc): yield shim

def test_emit_canvas_scored(shim):
    from tools.aiify import event_emitter
    with _patch(shim):
        ok = event_emitter.emit_canvas_scored(
            "dic", previous_grade="B", current_grade="A",
            current_score=92.0, scan_id="scan-1"
        )
    assert ok
    row = shim.execute("SELECT * FROM canvas_events WHERE event_type='aiify.canvas_scored'").fetchone()
    assert row is not None
    p = json.loads(row["payload_json"])
    assert p["canvas_name"] == "dic"
    assert p["current_grade"] == "A"
    assert p["current_score"] == 92.0

def test_emit_gap_identified(shim):
    from tools.aiify import event_emitter
    with _patch(shim):
        ok = event_emitter.emit_gap_identified(
            "network", "opp-555", "not_built", value_score=0.88
        )
    assert ok
    row = shim.execute("SELECT * FROM canvas_events WHERE event_type='aiify.gap_identified'").fetchone()
    assert row is not None
    p = json.loads(row["payload_json"])
    assert p["opportunity_id"] == "opp-555"
    assert p["gap_type"] == "not_built"
    assert p["value_score"] == 0.88

def test_emit_failure_returns_false():
    from tools.aiify import event_emitter
    @contextmanager
    def _boom():
        c = _ShimConn()
        c.execute = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("fail"))
        yield c
    with patch("tools.aiify.event_emitter.get_connection", _boom):
        assert event_emitter.emit_canvas_scored("x", "B", "A", 90.0) is False

def test_source_and_target_canvas(shim):
    from tools.aiify import event_emitter
    with _patch(shim):
        event_emitter.emit_gap_identified("compliance", "opp-1", "partial_built", 0.7)
    row = shim.execute("SELECT * FROM canvas_events").fetchone()
    assert row["source_canvas"] == "aiify"
    assert row["target_canvas"] == "dic"

def test_detect_score_anomalies_emits_gap_events(monkeypatch):
    """detect_score_anomalies() calls emit_gap_identified for each anomaly found."""
    from tools.aiify import engine
    emitted = []
    def _fake_emit(canvas_name, opportunity_id, gap_type, value_score, **kw):
        emitted.append({"opp_id": opportunity_id, "gap_type": gap_type})
        return True
    monkeypatch.setattr("tools.aiify.event_emitter.emit_gap_identified", _fake_emit)

    rows = [
        {"opportunity_id": "opp-1", "value_score": 0.9, "feasibility_score": 0.1,
         "risk_score": 0.5, "composite_score": 0.6},  # large delta → anomaly
    ]
    anomalies = engine.detect_score_anomalies(rows, thresholds={
        "value_feasibility_max_delta": 0.3,
        "component_outlier_floor": 0.05,
        "component_outlier_ceiling": 0.95,
    })
    assert len(anomalies) >= 1
    assert len(emitted) >= 1
    assert emitted[0]["opp_id"] == "opp-1"

def test_detect_score_anomalies_no_emit_when_none(monkeypatch):
    """detect_score_anomalies() does NOT emit when no anomalies found."""
    from tools.aiify import engine
    emitted = []
    def _fake_emit(**kw): emitted.append(True); return True
    monkeypatch.setattr("tools.aiify.event_emitter.emit_gap_identified", _fake_emit)

    rows = [
        {"opportunity_id": "opp-ok", "value_score": 0.7, "feasibility_score": 0.65,
         "risk_score": 0.5, "composite_score": 0.65},
    ]
    engine.detect_score_anomalies(rows, thresholds={
        "value_feasibility_max_delta": 0.5,
        "component_outlier_floor": 0.05,
        "component_outlier_ceiling": 0.95,
    })
    assert len(emitted) == 0
