# [CUI // SP-CTI]
"""Unit tests for tools/network/topology_validator.py (ndc-qa-01).

Covers validate_topology / validate_all against a temp SQLite canvas DB:
  - a well-formed topology (no issues, passes),
  - a malformed topology (unknown device type, orphan/dangling edge,
    duplicate node id) with and without --fix,
  - an empty topology graph,
  - a missing topology (error path),
  - validate_all over an empty DB.

The module's get_connection() is monkeypatched to a close-protected in-memory
SQLite StorageConnection so no production DB is touched, and the reclassifier
(_classify_device_type) is patched for deterministic reclassification.
"""

import json
import sqlite3

import pytest

from tools.db.storage import StorageConnection
from tools.network import topology_validator as tv


class _NoCloseConn:
    """StorageConnection wrapper whose .close() is a no-op so the shared
    in-memory DB survives the validator's close-on-exit calls."""

    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        if name == "close":
            return lambda: None
        return getattr(self._conn, name)


@pytest.fixture
def canvas_db(monkeypatch):
    """In-memory canvas DB with a topologies table, wired into the validator."""
    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    raw.executescript(
        """
        CREATE TABLE topologies (
            id TEXT PRIMARY KEY,
            name TEXT,
            description TEXT,
            graph_json TEXT DEFAULT '{"nodes":[],"edges":[]}',
            template_id TEXT,
            classification TEXT DEFAULT 'CUI',
            created_at TEXT,
            updated_at TEXT
        );
        """
    )
    raw.commit()
    shared = _NoCloseConn(StorageConnection(raw, "sqlite"))
    monkeypatch.setattr(tv, "get_connection", lambda *a, **k: shared)
    return raw


def _insert_topology(raw, tid, name, graph):
    raw.execute(
        "INSERT INTO topologies (id, name, graph_json, created_at) VALUES (?, ?, ?, ?)",
        (tid, name, json.dumps(graph), "2026-01-01T00:00:00Z"),
    )
    raw.commit()


# ── Well-formed topology ───────────────────────────────────────────────────────


def test_validate_well_formed_topology_no_issues(canvas_db):
    graph = {
        "nodes": [
            {"id": "r1", "type": "router", "label": "R1", "site": "DC1"},
            {"id": "sw1", "type": "switch", "label": "SW1", "site": "DC1"},
        ],
        "edges": [{"id": "e1", "source": "r1", "target": "sw1"}],
    }
    _insert_topology(canvas_db, "T1", "Clean", graph)

    result = tv.validate_topology("T1")

    assert result["topology_id"] == "T1"
    assert result["issues_found"] == 0
    assert result["total_nodes"] == 2
    assert result["total_edges"] == 1
    assert result["passed"] is True


# ── Malformed topology ─────────────────────────────────────────────────────────


def test_validate_malformed_topology_detects_issues(canvas_db, monkeypatch):
    # Force deterministic reclassification of the "unknown" node.
    from tools.network import network_ingester
    monkeypatch.setattr(
        network_ingester, "_classify_device_type",
        lambda label, raw_type="": "firewall" if "FW" in label else "unknown",
    )
    graph = {
        "nodes": [
            {"id": "n1", "type": "router", "label": "R1"},
            {"id": "n2", "type": "unknown", "label": "FW-1"},   # reclassifiable
            {"id": "n1", "type": "switch", "label": "dup"},      # duplicate id
        ],
        "edges": [
            {"id": "e1", "source": "n1", "target": "n2"},        # valid
            {"id": "e2", "source": "n1", "target": "nX"},        # dangling target
        ],
    }
    _insert_topology(canvas_db, "T2", "Messy", graph)

    result = tv.validate_topology("T2", fix=False)

    kinds = {i["type"] for i in result["issues"]}
    assert "unknown_device_type" in kinds
    assert "orphan_edge" in kinds
    assert "duplicate_node_id" in kinds
    assert result["issues_found"] >= 3
    # No fixes requested -> none applied.
    assert result["fixes_applied"] == 0


def test_validate_malformed_topology_with_fix_applies_and_persists(canvas_db, monkeypatch):
    from tools.network import network_ingester
    monkeypatch.setattr(
        network_ingester, "_classify_device_type",
        lambda label, raw_type="": "firewall",
    )
    graph = {
        "nodes": [
            {"id": "n1", "type": "router", "label": "R1"},
            {"id": "n2", "type": "unknown", "label": "FW-1"},
        ],
        "edges": [
            {"id": "e1", "source": "n1", "target": "n2"},
            {"id": "e2", "source": "n1", "target": "ghost"},  # dangling -> removed
        ],
    }
    _insert_topology(canvas_db, "T3", "Fixable", graph)

    result = tv.validate_topology("T3", fix=True)

    actions = {f["action"] for f in result["fixes"]}
    assert "reclassify" in actions
    assert "remove_orphan_edges" in actions
    assert result["fixes_applied"] >= 2

    # Persisted graph: n2 reclassified, dangling edge removed.
    row = canvas_db.execute("SELECT graph_json FROM topologies WHERE id='T3'").fetchone()
    saved = json.loads(row["graph_json"])
    n2 = next(n for n in saved["nodes"] if n["id"] == "n2")
    assert n2["type"] == "firewall"
    assert all(e.get("target") != "ghost" for e in saved["edges"])


# ── Empty topology ─────────────────────────────────────────────────────────────


def test_validate_empty_topology_passes(canvas_db):
    _insert_topology(canvas_db, "T4", "Empty", {"nodes": [], "edges": []})
    result = tv.validate_topology("T4")
    assert result["issues_found"] == 0
    assert result["total_nodes"] == 0
    assert result["total_edges"] == 0
    assert result["passed"] is True


# ── Missing topology (error path) ──────────────────────────────────────────────


def test_validate_missing_topology_returns_error(canvas_db):
    result = tv.validate_topology("does-not-exist")
    assert "error" in result
    assert "does-not-exist" in result["error"]


# ── validate_all ───────────────────────────────────────────────────────────────


def test_validate_all_empty_db(canvas_db):
    result = tv.validate_all()
    assert result["topologies_checked"] == 0
    assert result["total_issues"] == 0
    assert result["all_passed"] is True


def test_validate_all_aggregates_multiple(canvas_db):
    _insert_topology(canvas_db, "A1", "Clean",
                     {"nodes": [{"id": "a", "type": "router", "label": "A"}],
                      "edges": []})
    _insert_topology(canvas_db, "A2", "Clean2",
                     {"nodes": [{"id": "b", "type": "switch", "label": "B"}],
                      "edges": []})
    result = tv.validate_all()
    assert result["topologies_checked"] == 2
    assert result["total_issues"] == 0
    assert result["all_passed"] is True
