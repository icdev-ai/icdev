# CUI // SP-CTI
"""Tests for tools.pipeline.delta — compute_delta() — 7 cases.

compute_delta reads the canonical ``pdc_snapshots`` canvas store (pdx-data-01)
through ``tools.pipeline.delta._load_snapshot``, which returns a graph dict of
shape ``{"nodes": [...], "edges": [...]}``. Unit tests patch that helper; the
final test exercises the real path end-to-end: twin.take_snapshot twice against
a seeded in-memory canvas DB, then compute_delta over the two snapshot ids.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.pipeline.delta import compute_delta  # noqa: E402


def _snap(nodes: list, edges: list) -> dict:
    """A loaded-snapshot graph dict as returned by delta._load_snapshot."""
    return {"nodes": nodes, "edges": edges}


# ── 1. Identical snapshots ────────────────────────────────────────────────────

def test_identical_snapshots_return_empty_delta():
    nodes = [{"id": "n1", "type": "build"}, {"id": "n2", "type": "scan"}]
    edges = [{"id": "e1", "source": "n1", "target": "n2"}]
    with patch("tools.pipeline.delta._load_snapshot", side_effect=[_snap(nodes, edges), _snap(nodes, edges)]):
        delta = compute_delta("snap-a", "snap-b")
    assert delta["nodes"] == {"added": [], "removed": [], "modified": []}
    assert delta["edges"] == {"added": [], "removed": [], "modified": []}


# ── 2. Added nodes ────────────────────────────────────────────────────────────

def test_added_nodes_detected():
    baseline = [{"id": "n1", "type": "build"}]
    proposed = [{"id": "n1", "type": "build"}, {"id": "n2", "type": "scan"}]
    with patch("tools.pipeline.delta._load_snapshot", side_effect=[_snap(baseline, []), _snap(proposed, [])]):
        delta = compute_delta("snap-a", "snap-b")
    assert len(delta["nodes"]["added"]) == 1
    assert delta["nodes"]["added"][0]["id"] == "n2"
    assert delta["nodes"]["removed"] == []
    assert delta["nodes"]["modified"] == []


# ── 3. Removed nodes ──────────────────────────────────────────────────────────

def test_removed_nodes_detected():
    baseline = [{"id": "n1", "type": "build"}, {"id": "n2", "type": "scan"}]
    proposed = [{"id": "n1", "type": "build"}]
    with patch("tools.pipeline.delta._load_snapshot", side_effect=[_snap(baseline, []), _snap(proposed, [])]):
        delta = compute_delta("snap-a", "snap-b")
    assert len(delta["nodes"]["removed"]) == 1
    assert delta["nodes"]["removed"][0]["id"] == "n2"
    assert delta["nodes"]["added"] == []


# ── 4. Modified nodes (stable-ID rename detection) ────────────────────────────

def test_modified_nodes_detected_via_stable_id():
    baseline = [{"id": "n1", "type": "build-runner", "label": "Old Runner"}]
    proposed = [{"id": "n1", "type": "build-runner", "label": "New Runner"}]
    with patch("tools.pipeline.delta._load_snapshot", side_effect=[_snap(baseline, []), _snap(proposed, [])]):
        delta = compute_delta("snap-a", "snap-b")
    assert len(delta["nodes"]["modified"]) == 1
    mod = delta["nodes"]["modified"][0]
    assert mod["baseline"]["label"] == "Old Runner"
    assert mod["proposed"]["label"] == "New Runner"
    assert delta["nodes"]["added"] == []
    assert delta["nodes"]["removed"] == []


# ── 5. Edge structural changes ────────────────────────────────────────────────

def test_edge_added_and_removed():
    nodes = [{"id": "n1"}, {"id": "n2"}, {"id": "n3"}]
    baseline_edges = [{"id": "e1", "source": "n1", "target": "n2"}]
    proposed_edges = [{"id": "e2", "source": "n2", "target": "n3"}]
    with patch(
        "tools.pipeline.delta._load_snapshot",
        side_effect=[_snap(nodes, baseline_edges), _snap(nodes, proposed_edges)],
    ):
        delta = compute_delta("snap-a", "snap-b")
    assert delta["edges"]["added"][0]["id"] == "e2"
    assert delta["edges"]["removed"][0]["id"] == "e1"
    assert delta["edges"]["modified"] == []


# ── 6. Deterministic ordering ─────────────────────────────────────────────────

def test_output_is_deterministically_sorted():
    """added/removed lists must be sorted by stable ID regardless of input order."""
    baseline = [{"id": "n3", "type": "scan"}, {"id": "n1", "type": "build"}]
    proposed = [{"id": "n4", "type": "deploy"}, {"id": "n2", "type": "test"}, {"id": "n1", "type": "build"}]
    with patch("tools.pipeline.delta._load_snapshot", side_effect=[_snap(baseline, []), _snap(proposed, [])]):
        delta = compute_delta("snap-a", "snap-b")
    added_ids = [n["id"] for n in delta["nodes"]["added"]]
    removed_ids = [n["id"] for n in delta["nodes"]["removed"]]
    assert added_ids == sorted(added_ids)
    assert removed_ids == sorted(removed_ids)


# ── 7. Full path: twin.take_snapshot ×2 → pdc_snapshots → compute_delta ────────

def test_full_path_take_snapshot_twice_then_compute_delta():
    """End-to-end over the consolidated store.

    Seed a pipeline in an in-memory canvas DB, edit its graph between two
    twin.take_snapshot calls, then prove compute_delta reads pdc_snapshots and
    reports the real added/removed nodes. The canvas get_connection is patched
    to a shared StorageConnection (translates %s→? on SQLite; close() is a
    no-op so the in-memory DB survives across take_snapshot/compute_delta).
    """
    import tools.pipeline.db.init_db as canvas_db
    from tools.db.storage import StorageConnection
    from tools.pipeline.twin import take_snapshot

    raw = sqlite3.connect(":memory:")
    raw.row_factory = sqlite3.Row
    raw.execute(
        "CREATE TABLE pipelines (id TEXT PRIMARY KEY, name TEXT, graph_json TEXT)"
    )
    raw.execute(
        """CREATE TABLE pdc_snapshots (
            id TEXT PRIMARY KEY, pipeline_id TEXT, label TEXT,
            graph_json TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
            node_count INTEGER DEFAULT 0, edge_count INTEGER DEFAULT 0,
            created_by TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    raw.commit()

    sconn = StorageConnection(raw, "sqlite")

    class _NoCloseConn:
        """Share one underlying connection; make close() a no-op."""

        def execute(self, *a, **k):
            return sconn.execute(*a, **k)

        def commit(self):
            return sconn.commit()

        def close(self):
            pass

        def set_security_context(self, *_a, **_k):
            pass

    with patch.object(canvas_db, "get_connection", lambda *a, **k: _NoCloseConn()):
        # baseline graph: n1, n2 (edge n1->n2)
        raw.execute(
            "INSERT INTO pipelines (id, name, graph_json) VALUES (?,?,?)",
            (
                "pipe-1",
                "Demo",
                json.dumps({
                    "nodes": [{"id": "n1", "type": "build"}, {"id": "n2", "type": "scan"}],
                    "edges": [{"id": "e1", "source": "n1", "target": "n2"}],
                }),
            ),
        )
        raw.commit()
        snap_a = take_snapshot("pipe-1", label="baseline")

        # proposed graph: drop n2/e1, add n3 (edge n1->n3)
        raw.execute(
            "UPDATE pipelines SET graph_json=? WHERE id=?",
            (
                json.dumps({
                    "nodes": [{"id": "n1", "type": "build"}, {"id": "n3", "type": "deploy"}],
                    "edges": [{"id": "e2", "source": "n1", "target": "n3"}],
                }),
                "pipe-1",
            ),
        )
        raw.commit()
        snap_b = take_snapshot("pipe-1", label="proposed")

        delta = compute_delta(snap_a["id"], snap_b["id"])

    added_node_ids = [n["id"] for n in delta["nodes"]["added"]]
    removed_node_ids = [n["id"] for n in delta["nodes"]["removed"]]
    assert added_node_ids == ["n3"]
    assert removed_node_ids == ["n2"]
    added_edge_ids = [e["id"] for e in delta["edges"]["added"]]
    removed_edge_ids = [e["id"] for e in delta["edges"]["removed"]]
    assert added_edge_ids == ["e2"]
    assert removed_edge_ids == ["e1"]
    raw.close()
