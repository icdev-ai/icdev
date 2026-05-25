"""Tests for tools.security_canvas.path_enumerator — 6 cases."""
import pytest

from tools.security_canvas.path_enumerator import enumerate as path_enum


# ── Shared fixtures ──────────────────────────────────────────────────────────

def _graph(*edges, nodes=None, boundaries=None):
    """Build a minimal graph dict from (src, dst[, cost]) tuples."""
    seen: set[str] = set()
    edge_list = []
    for e in edges:
        src, dst = e[0], e[1]
        cost = e[2] if len(e) > 2 else 1.0
        edge_list.append({"source": src, "target": dst, "cost": cost})
        seen.update([src, dst])
    node_list = nodes if nodes is not None else [{"id": nid} for nid in seen]
    return {
        "nodes": node_list,
        "edges": edge_list,
        "boundaries": boundaries or [],
    }


# ── Test 1: direct single-hop path ───────────────────────────────────────────

def test_direct_path():
    g = _graph(("A", "B", 2.5))
    results = path_enum(g, "A", "B")
    assert len(results) == 1
    assert results[0]["path"] == ["A", "B"]
    assert results[0]["cost"] == pytest.approx(2.5)


# ── Test 2: k paths returned sorted by cost ──────────────────────────────────

def test_k_paths_sorted_by_cost():
    # Two routes A→B→D (cost 2) and A→C→D (cost 3)
    g = _graph(("A", "B", 1.0), ("B", "D", 1.0), ("A", "C", 2.0), ("C", "D", 1.0))
    results = path_enum(g, "A", "D", k=5)
    assert len(results) == 2
    assert results[0]["cost"] <= results[1]["cost"]
    paths = [r["path"] for r in results]
    assert ["A", "B", "D"] in paths
    assert ["A", "C", "D"] in paths


# ── Test 3: cycle does not cause infinite loop ────────────────────────────────

def test_cycle_safe():
    # A→B→C→B cycle, but goal is D reachable via A→D
    g = _graph(("A", "B"), ("B", "C"), ("C", "B"), ("A", "D"))
    results = path_enum(g, "A", "D", k=3)
    assert len(results) == 1
    assert results[0]["path"] == ["A", "D"]


# ── Test 4: no path returns empty list ───────────────────────────────────────

def test_no_path_returns_empty():
    g = _graph(("A", "B"), ("C", "D"))
    results = path_enum(g, "A", "D")
    assert results == []


# ── Test 5: cross-IL edge blocked by default ─────────────────────────────────

def test_cross_il_blocked_by_default():
    nodes = [
        {"id": "IL4_src", "il_level": "IL4"},
        {"id": "IL6_node", "il_level": "IL6"},
        {"id": "IL4_goal", "il_level": "IL4"},
    ]
    # Only cross-IL path: IL4_src → IL6_node → IL4_goal
    g = {
        "nodes": nodes,
        "edges": [
            {"source": "IL4_src", "target": "IL6_node", "cost": 1.0},
            {"source": "IL6_node", "target": "IL4_goal", "cost": 1.0},
        ],
        "boundaries": [],
    }
    results = path_enum(g, "IL4_src", "IL4_goal", allow_cross_il=False)
    assert results == [], "cross-IL path must be suppressed when allow_cross_il=False"


# ── Test 6: cross-IL path returned when flag is set ──────────────────────────

def test_cross_il_allowed_with_flag():
    nodes = [
        {"id": "IL4_src", "il_level": "IL4"},
        {"id": "IL6_node", "il_level": "IL6"},
        {"id": "IL4_goal", "il_level": "IL4"},
    ]
    g = {
        "nodes": nodes,
        "edges": [
            {"source": "IL4_src", "target": "IL6_node", "cost": 1.0},
            {"source": "IL6_node", "target": "IL4_goal", "cost": 1.0},
        ],
        "boundaries": [],
    }
    results = path_enum(g, "IL4_src", "IL4_goal", allow_cross_il=True)
    assert len(results) == 1
    assert results[0]["path"] == ["IL4_src", "IL6_node", "IL4_goal"]
    assert results[0]["cost"] == pytest.approx(2.0)
