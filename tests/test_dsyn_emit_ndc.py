# CUI // SP-CTI
"""Tests for dsyn-emit-01: NDC canvas_events emission on topology/config changes."""
from __future__ import annotations

import json
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# ── Minimal in-memory SQLite DB for canvas_events ────────────────────────────

_SCHEMA = """
CREATE TABLE IF NOT EXISTS canvas_events (
    id TEXT PRIMARY KEY,
    source_canvas TEXT,
    target_canvas TEXT,
    event_type TEXT,
    payload_json TEXT,
    created_at TEXT,
    tenant_id TEXT DEFAULT '',
    classification TEXT DEFAULT 'CUI',
    consumed_at TEXT
);
"""


class _ShimConn:
    """Thin wrapper: translates %s → ? so PG-style calls work on SQLite."""

    def __init__(self):
        self._db = sqlite3.connect(":memory:")
        self._db.row_factory = sqlite3.Row
        self._db.execute(_SCHEMA)
        self._db.commit()

    def execute(self, sql, params=()):
        translated = sql.replace("%s", "?")
        return self._db.execute(translated, params)

    def commit(self):
        self._db.commit()

    def close(self):
        self._db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.commit()


@pytest.fixture
def shim():
    return _ShimConn()


@contextmanager
def _patch_get_conn(shim):
    """Patch tools.db.storage.get_connection to yield the shim."""
    from contextlib import contextmanager as _cm

    @_cm
    def _fake_gc():
        yield shim

    with patch("tools.ndc.event_emitter.get_connection", _fake_gc):
        yield shim


# ── event_emitter unit tests ──────────────────────────────────────────────────

def test_emit_topology_change_inserts_row(shim):
    from tools.ndc import event_emitter
    with _patch_get_conn(shim):
        ok = event_emitter.emit_topology_change(
            topology_id="topo-abc",
            change_summary="Node removed from zone A",
            affected_segments=["seg1", "seg2"],
        )
    assert ok is True
    row = shim.execute(
        "SELECT * FROM canvas_events WHERE event_type='ndc.topology.drift_detected'"
    ).fetchone()
    assert row is not None
    payload = json.loads(row["payload_json"])
    assert payload["device_id"] == "topo-abc"
    assert "change_summary" in payload
    assert "affected_segments" in payload


def test_emit_config_push_inserts_row(shim):
    from tools.ndc import event_emitter
    with _patch_get_conn(shim):
        ok = event_emitter.emit_config_push(
            topology_id="topo-xyz",
            change_summary="Generated Ansible inventory for topo-xyz",
        )
    assert ok is True
    row = shim.execute(
        "SELECT * FROM canvas_events WHERE event_type='ndc.config.push'"
    ).fetchone()
    assert row is not None
    payload = json.loads(row["payload_json"])
    assert payload["device_id"] == "topo-xyz"


def test_emit_baseline_deviation_inserts_row(shim):
    from tools.ndc import event_emitter
    drifted = [{"device_id": "dev-1", "status": "drifted"},
               {"device_id": "dev-2", "status": "no_running_config"}]
    with _patch_get_conn(shim):
        ok = event_emitter.emit_baseline_deviation(
            topology_id="topo-def",
            drifted_devices=drifted,
            drifted_count=2,
        )
    assert ok is True
    row = shim.execute(
        "SELECT * FROM canvas_events WHERE event_type='ndc.baseline.deviation'"
    ).fetchone()
    assert row is not None
    payload = json.loads(row["payload_json"])
    assert payload["drifted_count"] == 2
    assert "dev-1" in payload["affected_segments"]


def test_emit_failure_returns_false_and_does_not_raise(shim):
    """A DB failure must not propagate — returns False silently."""
    from tools.ndc import event_emitter
    from contextlib import contextmanager

    @contextmanager
    def _exploding_gc():
        cm = _ShimConn()

        def _bad_execute(*_a, **_kw):
            raise RuntimeError("simulated DB failure")

        cm.execute = _bad_execute
        yield cm

    with patch("tools.ndc.event_emitter.get_connection", _exploding_gc):
        result = event_emitter.emit_topology_change("t1", "change")
    assert result is False


def test_emit_sets_source_and_target_canvas(shim):
    from tools.ndc import event_emitter
    with _patch_get_conn(shim):
        event_emitter.emit_topology_change("t2", "test change")
    row = shim.execute("SELECT * FROM canvas_events").fetchone()
    assert row["source_canvas"] == "ndc"
    assert row["target_canvas"] == "dic"


# ── manage_configs diff integration ──────────────────────────────────────────

def _make_nc_db():
    db = sqlite3.connect(":memory:")
    db.execute(
        "CREATE TABLE ni_device_configs "
        "(id TEXT, device_id TEXT, config_type TEXT, config_text TEXT, config_hash TEXT, "
        "source TEXT, version INTEGER, created_at TEXT)"
    )
    db.commit()
    return db


def test_manage_configs_diff_emits_when_drifted(monkeypatch):
    """manage_configs(action='diff') calls emit_baseline_deviation when drift found."""
    nc_db = _make_nc_db()
    nc_db.execute(
        "INSERT INTO ni_device_configs VALUES (?,?,?,?,?,?,?,?)",
        ("id1", "router-1", "generated", "conf text", "hash-golden", "gen", 1, "2024-01-01")
    )
    nc_db.execute(
        "INSERT INTO ni_device_configs VALUES (?,?,?,?,?,?,?,?)",
        ("id2", "router-1", "running", "conf text different", "hash-running", "collect", 1, "2024-01-01")
    )
    nc_db.commit()

    import tools.network.network_intelligence as ni
    monkeypatch.setattr(ni, "_get_nc_conn", lambda: nc_db)

    emitted = []

    def _fake_emit_deviation(topology_id, drifted_devices, drifted_count, **kw):
        emitted.append({"topology_id": topology_id, "drifted_count": drifted_count})
        return True

    monkeypatch.setattr(
        "tools.ndc.event_emitter.emit_baseline_deviation", _fake_emit_deviation
    )

    result = ni.manage_configs("topo-test", "diff")
    assert result["drifted_count"] == 1
    assert len(emitted) == 1
    assert emitted[0]["drifted_count"] == 1


def test_manage_configs_diff_no_emit_when_no_drift(monkeypatch):
    """No event emitted when all configs match golden baseline."""
    nc_db = _make_nc_db()
    nc_db.execute(
        "INSERT INTO ni_device_configs VALUES (?,?,?,?,?,?,?,?)",
        ("id1", "router-1", "generated", "conf text", "same-hash", "gen", 1, "2024-01-01")
    )
    nc_db.execute(
        "INSERT INTO ni_device_configs VALUES (?,?,?,?,?,?,?,?)",
        ("id2", "router-1", "running", "conf text", "same-hash", "collect", 1, "2024-01-01")
    )
    nc_db.commit()

    import tools.network.network_intelligence as ni
    monkeypatch.setattr(ni, "_get_nc_conn", lambda: nc_db)

    emitted = []

    def _fake_emit_deviation(**kw):
        emitted.append(kw)
        return True

    monkeypatch.setattr(
        "tools.ndc.event_emitter.emit_baseline_deviation", _fake_emit_deviation
    )

    result = ni.manage_configs("topo-ok", "diff")
    assert result["drifted_count"] == 0
    assert len(emitted) == 0


def test_manage_configs_diff_emit_failure_does_not_block(monkeypatch):
    """If emit_baseline_deviation raises, manage_configs still returns the diff result."""
    nc_db = _make_nc_db()
    nc_db.execute(
        "INSERT INTO ni_device_configs VALUES (?,?,?,?,?,?,?,?)",
        ("id1", "router-1", "generated", "conf text", "h1", "gen", 1, "2024-01-01")
    )
    nc_db.execute(
        "INSERT INTO ni_device_configs VALUES (?,?,?,?,?,?,?,?)",
        ("id2", "router-1", "running", "different", "h2", "collect", 1, "2024-01-01")
    )
    nc_db.commit()

    import tools.network.network_intelligence as ni
    monkeypatch.setattr(ni, "_get_nc_conn", lambda: nc_db)

    def _boom(*a, **kw):
        raise RuntimeError("simulated emit failure")

    with patch("tools.ndc.event_emitter.emit_baseline_deviation", _boom):
        result = ni.manage_configs("topo-boom", "diff")

    assert result["action"] == "diff"
    assert result["drifted_count"] == 1
