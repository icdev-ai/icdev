# CUI // SP-CTI
"""Coverage for tools/observability_canvas/bus_subscriber.py (obx-test-02).

Exercises the real cross-canvas event bus path end to end:

  bus_subscriber.register()  ->  event_bus.subscribe("odc", "bdc.design.saved", …)
  event_bus.publish("bdc", "bdc.design.saved", {...}, target_canvas="odc")
  ->  _on_bdc_design_saved fires  ->  a boundary-zone node is injected into every
      matching-classification observability_designs row.

The synthetic event mirrors the real producer shape emitted by
tools/boundary_canvas/blueprint.py (~L402): {design_id, classification,
graph_changed}. Both the bus's own connection (canvas_events / audit writes)
and the ODC handler's connection (observability_designs) are monkeypatched —
shim-aware — to the SAME temp SQLite DB so the side effect is observable.

Also asserts malformed-event tolerance: missing keys are logged, never raised.

NIST 800-53: AU-2, SI-4, SC-7
"""
from __future__ import annotations

import importlib
import json
import sqlite3
import sys
import uuid
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_BUS_DDL = """
CREATE TABLE IF NOT EXISTS observability_designs (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT,
    graph_json      TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    template_id     TEXT,
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS canvas_events (
    id             TEXT NOT NULL PRIMARY KEY,
    source_canvas  TEXT NOT NULL,
    target_canvas  TEXT,
    event_type     TEXT NOT NULL,
    payload_json   TEXT NOT NULL DEFAULT '{}',
    created_at     TEXT,
    consumed_at    TEXT
);
CREATE TABLE IF NOT EXISTS audit_trail (
    project_id      TEXT,
    event_type      TEXT,
    actor           TEXT,
    action          TEXT,
    details         TEXT,
    affected_files  TEXT,
    classification  TEXT
);
"""


@pytest.fixture
def bus_env(tmp_path, monkeypatch):
    """Temp DB wired into BOTH the event bus and the ODC canvas connection.

    Also isolates the module-global listener registry so each test starts with
    a clean subscription table.
    """
    from tools.db.storage import StorageConnection

    db_path = tmp_path / "bus.db"
    raw = sqlite3.connect(str(db_path))
    raw.executescript(_BUS_DDL)
    raw.commit()
    raw.close()

    def _conn():
        r = sqlite3.connect(str(db_path))
        r.row_factory = sqlite3.Row
        return StorageConnection(r, "sqlite")

    event_bus = importlib.import_module("tools.canvas.event_bus")
    init_db_mod = importlib.import_module("tools.observability_canvas.db.init_db")

    # Fresh listener registry (module global) so register() is deterministic.
    monkeypatch.setattr(event_bus, "_LISTENERS", {}, raising=True)
    # Bus writes canvas_events/audit_trail; handler writes observability_designs.
    monkeypatch.setattr(event_bus, "get_connection", _conn, raising=True)
    monkeypatch.setattr(init_db_mod, "get_connection", _conn, raising=True)

    return _conn, event_bus


def _seed_design(conn_factory, design_id, classification="CUI", nodes=None):
    conn = conn_factory()
    graph = {"nodes": nodes or [], "edges": []}
    conn.execute(
        "INSERT INTO observability_designs (id, name, graph_json, classification) "
        "VALUES (%s,%s,%s,%s)",
        (design_id, f"design-{design_id}", json.dumps(graph), classification),
    )
    conn.commit()
    conn.close()


def _load_graph(conn_factory, design_id):
    conn = conn_factory()
    row = conn.execute(
        "SELECT graph_json FROM observability_designs WHERE id=%s", (design_id,)
    ).fetchone()
    conn.close()
    return json.loads(row["graph_json"])


# ── Happy path: publish injects a boundary-zone node ─────────────────────────

def test_publish_injects_boundary_zone_node(bus_env):
    conn_factory, event_bus = bus_env
    from tools.observability_canvas import bus_subscriber

    design_id = "odc-match"
    _seed_design(conn_factory, design_id, classification="CUI")

    bus_subscriber.register()

    bdc_design_id = uuid.uuid4().hex
    event_bus.publish(
        "bdc", "bdc.design.saved",
        {"design_id": bdc_design_id, "classification": "CUI", "graph_changed": True},
        target_canvas="odc",
    )

    graph = _load_graph(conn_factory, design_id)
    zone_nodes = [n for n in graph["nodes"] if n.get("type") == "boundary-zone"]
    assert len(zone_nodes) == 1
    zone = zone_nodes[0]
    assert zone["id"] == f"bdc-zone-{bdc_design_id[:8]}"
    assert zone["data"]["bdc_design_id"] == bdc_design_id
    assert zone["data"]["auto_added"] is True
    # canvas_events row persisted by publish().
    conn = conn_factory()
    cnt = conn.execute(
        "SELECT COUNT(*) FROM canvas_events WHERE event_type=%s", ("bdc.design.saved",)
    ).fetchone()[0]
    conn.close()
    assert cnt == 1


def test_injection_is_idempotent_for_same_bdc_design(bus_env):
    conn_factory, event_bus = bus_env
    from tools.observability_canvas import bus_subscriber

    design_id = "odc-idem"
    _seed_design(conn_factory, design_id, classification="CUI")
    bus_subscriber.register()

    bdc_design_id = uuid.uuid4().hex
    payload = {"design_id": bdc_design_id, "classification": "CUI", "graph_changed": True}
    event_bus.publish("bdc", "bdc.design.saved", dict(payload), target_canvas="odc")
    event_bus.publish("bdc", "bdc.design.saved", dict(payload), target_canvas="odc")

    graph = _load_graph(conn_factory, design_id)
    zone_nodes = [n for n in graph["nodes"] if n.get("type") == "boundary-zone"]
    assert len(zone_nodes) == 1  # second publish must not duplicate the node


def test_classification_scoping(bus_env):
    """Only designs sharing the event classification receive the zone node."""
    conn_factory, event_bus = bus_env
    from tools.observability_canvas import bus_subscriber

    _seed_design(conn_factory, "odc-cui", classification="CUI")
    _seed_design(conn_factory, "odc-secret", classification="SECRET")
    bus_subscriber.register()

    event_bus.publish(
        "bdc", "bdc.design.saved",
        {"design_id": uuid.uuid4().hex, "classification": "SECRET", "graph_changed": True},
        target_canvas="odc",
    )

    assert not _load_graph(conn_factory, "odc-cui")["nodes"]
    assert len(_load_graph(conn_factory, "odc-secret")["nodes"]) == 1


def test_graph_unchanged_event_is_ignored(bus_env):
    conn_factory, event_bus = bus_env
    from tools.observability_canvas import bus_subscriber

    _seed_design(conn_factory, "odc-nochange", classification="CUI")
    bus_subscriber.register()

    event_bus.publish(
        "bdc", "bdc.design.saved",
        {"design_id": uuid.uuid4().hex, "classification": "CUI", "graph_changed": False},
        target_canvas="odc",
    )
    assert not _load_graph(conn_factory, "odc-nochange")["nodes"]


# ── Malformed / defensive ────────────────────────────────────────────────────

def test_register_wires_the_right_handler(bus_env):
    conn_factory, event_bus = bus_env
    from tools.observability_canvas import bus_subscriber

    bus_subscriber.register()
    entries = event_bus._LISTENERS.get(("odc", "bdc.design.saved"))
    assert entries is not None and len(entries) == 1
    handler, _ctx = entries[0]
    assert handler is bus_subscriber._on_bdc_design_saved


def test_malformed_event_missing_keys_does_not_raise(bus_env):
    """A payload with no design_id must be tolerated (logged, no exception)."""
    conn_factory, _event_bus = bus_env
    from tools.observability_canvas import bus_subscriber

    _seed_design(conn_factory, "odc-x", classification="CUI")
    # Directly invoke the handler with an empty payload (bus signature).
    bus_subscriber._on_bdc_design_saved("evt-1", "odc", "bdc.design.saved", {})
    # No node injected, and crucially no exception propagated.
    assert not _load_graph(conn_factory, "odc-x")["nodes"]


def test_register_swallows_import_failure(monkeypatch):
    """register() must never raise even if the bus module cannot be imported."""
    import builtins

    from tools.observability_canvas import bus_subscriber

    real_import = builtins.__import__

    def _blocked(name, *args, **kwargs):
        if name == "tools.canvas.event_bus":
            raise ImportError("bus unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _blocked)
    # Should log a warning and return cleanly, not raise.
    bus_subscriber.register()
