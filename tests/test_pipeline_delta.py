# CUI // SP-CTI
"""Tests for tools.pipeline.delta — compute_delta() — 6 cases."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.pipeline.delta import compute_delta  # noqa: E402


def _snap(nodes: list, edges: list) -> dict:
    return {"nodes_json": json.dumps(nodes), "edges_json": json.dumps(edges)}


# ── 1. Identical snapshots ────────────────────────────────────────────────────

def test_identical_snapshots_return_empty_delta():
    nodes = [{"id": "n1", "type": "build"}, {"id": "n2", "type": "scan"}]
    edges = [{"id": "e1", "source": "n1", "target": "n2"}]
    with patch("tools.pipeline.delta.get_snapshot", side_effect=[_snap(nodes, edges), _snap(nodes, edges)]):
        delta = compute_delta("snap-a", "snap-b")
    assert delta["nodes"] == {"added": [], "removed": [], "modified": []}
    assert delta["edges"] == {"added": [], "removed": [], "modified": []}


# ── 2. Added nodes ────────────────────────────────────────────────────────────

def test_added_nodes_detected():
    baseline = [{"id": "n1", "type": "build"}]
    proposed = [{"id": "n1", "type": "build"}, {"id": "n2", "type": "scan"}]
    with patch("tools.pipeline.delta.get_snapshot", side_effect=[_snap(baseline, []), _snap(proposed, [])]):
        delta = compute_delta("snap-a", "snap-b")
    assert len(delta["nodes"]["added"]) == 1
    assert delta["nodes"]["added"][0]["id"] == "n2"
    assert delta["nodes"]["removed"] == []
    assert delta["nodes"]["modified"] == []


# ── 3. Removed nodes ──────────────────────────────────────────────────────────

def test_removed_nodes_detected():
    baseline = [{"id": "n1", "type": "build"}, {"id": "n2", "type": "scan"}]
    proposed = [{"id": "n1", "type": "build"}]
    with patch("tools.pipeline.delta.get_snapshot", side_effect=[_snap(baseline, []), _snap(proposed, [])]):
        delta = compute_delta("snap-a", "snap-b")
    assert len(delta["nodes"]["removed"]) == 1
    assert delta["nodes"]["removed"][0]["id"] == "n2"
    assert delta["nodes"]["added"] == []


# ── 4. Modified nodes (stable-ID rename detection) ────────────────────────────

def test_modified_nodes_detected_via_stable_id():
    baseline = [{"id": "n1", "type": "build-runner", "label": "Old Runner"}]
    proposed = [{"id": "n1", "type": "build-runner", "label": "New Runner"}]
    with patch("tools.pipeline.delta.get_snapshot", side_effect=[_snap(baseline, []), _snap(proposed, [])]):
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
        "tools.pipeline.delta.get_snapshot",
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
    with patch("tools.pipeline.delta.get_snapshot", side_effect=[_snap(baseline, []), _snap(proposed, [])]):
        delta = compute_delta("snap-a", "snap-b")
    added_ids = [n["id"] for n in delta["nodes"]["added"]]
    removed_ids = [n["id"] for n in delta["nodes"]["removed"]]
    assert added_ids == sorted(added_ids)
    assert removed_ids == sorted(removed_ids)
