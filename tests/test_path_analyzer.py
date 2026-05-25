"""Tests for tools.network.path_analyzer — NDC path reachability engine."""
from unittest.mock import patch


from tools.network.path_analyzer import (
    _extract_protocols,
    _is_acl_blocked,
    find_paths,
)


def _make_graph(*edge_tuples, extra_nodes=None):
    """Build a minimal graph for path_analyzer (nodes as dict, edges as list)."""
    nodes: dict = {}
    edges: list = []
    for e in edge_tuples:
        src, dst = e[0], e[1]
        edge: dict = {"source": src, "target": dst}
        if len(e) > 2:
            edge.update(e[2])
        edges.append(edge)
        nodes.setdefault(src, {"id": src, "label": src})
        nodes.setdefault(dst, {"id": dst, "label": dst})
    if extra_nodes:
        nodes.update(extra_nodes)
    return {"nodes": nodes, "edges": edges}


# ── Unit: _is_acl_blocked ────────────────────────────────────────────────────

def test_acl_blocked_deny_action():
    edges = [{"acl_rules": [{"action": "deny", "rule": "deny all"}]}]
    assert _is_acl_blocked(edges) is True


def test_acl_blocked_drop_action():
    edges = [{"acl_rules": [{"action": "drop"}]}]
    assert _is_acl_blocked(edges) is True


def test_acl_not_blocked_permit():
    edges = [{"acl_rules": [{"action": "permit"}]}]
    assert _is_acl_blocked(edges) is False


def test_acl_no_rules():
    edges = [{"protocol": "BGP"}]
    assert _is_acl_blocked(edges) is False


# ── Unit: _extract_protocols ─────────────────────────────────────────────────

def test_extract_protocols_string():
    edges = [{"protocol": "BGP"}, {"protocol": "OSPF"}]
    assert _extract_protocols(edges) == ["BGP", "OSPF"]


def test_extract_protocols_list():
    edges = [{"protocols": ["BGP", "MPLS"]}]
    protos = _extract_protocols(edges)
    assert "BGP" in protos
    assert "MPLS" in protos


def test_extract_protocols_deduped():
    edges = [{"protocol": "BGP"}, {"protocol": "BGP"}]
    assert _extract_protocols(edges) == ["BGP"]


# ── find_paths: direct single-hop path ──────────────────────────────────────

def test_direct_path():
    g = _make_graph(("A", "B"))
    r = find_paths("A", "B", g)
    assert r["reachable"] is True
    assert r["path_count"] == 1
    assert r["paths"][0]["hops"] == ["A", "B"]
    assert r["paths"][0]["hop_count"] == 1


# ── find_paths: disconnected graph returns unreachable ───────────────────────

def test_no_path_unreachable():
    g = _make_graph(("A", "B"), ("C", "D"))
    r = find_paths("A", "D", g)
    assert r["reachable"] is False
    assert r["path_count"] == 0
    assert r["paths"] == []


# ── find_paths: multiple paths all discovered ────────────────────────────────

def test_multiple_paths():
    g = _make_graph(("A", "B"), ("B", "D"), ("A", "C"), ("C", "D"))
    r = find_paths("A", "D", g)
    assert r["reachable"] is True
    assert r["path_count"] == 2
    hop_lists = [p["hops"] for p in r["paths"]]
    assert ["A", "B", "D"] in hop_lists
    assert ["A", "C", "D"] in hop_lists


# ── find_paths: ACL blocked (all paths blocked) ──────────────────────────────

def test_acl_blocked_path():
    acl_deny = {"acl_rules": [{"action": "deny", "rule": "deny all"}]}
    g = _make_graph(("A", "B", acl_deny))
    r = find_paths("A", "B", g)
    assert r["blocked_by_acl"] is True
    assert r["paths"][0]["acl_blocked"] is True
    assert r["reachable"] is False


# ── find_paths: mixed — one open path, one blocked ───────────────────────────

def test_mixed_open_and_blocked():
    acl_deny = {"acl_rules": [{"action": "deny", "rule": "deny all"}]}
    # A→B open, A→B→C→D: not relevant; A→C (blocked), C→D open
    g = _make_graph(
        ("A", "B", acl_deny), ("B", "D"),
        ("A", "C"), ("C", "D"),
    )
    r = find_paths("A", "D", g)
    assert r["reachable"] is True
    assert r["blocked_by_acl"] is True


# ── find_paths: protocols collected per path ─────────────────────────────────

def test_protocols_collected():
    g = _make_graph(("A", "B", {"protocol": "BGP"}), ("B", "C", {"protocol": "OSPF"}))
    r = find_paths("A", "C", g)
    assert r["reachable"] is True
    protos = r["paths"][0]["protocols"]
    assert "BGP" in protos
    assert "OSPF" in protos


# ── find_paths: src == dst ───────────────────────────────────────────────────

def test_src_equals_dst():
    g = _make_graph(("A", "B"))
    r = find_paths("A", "A", g)
    assert r["reachable"] is True
    assert r["paths"][0]["hop_count"] == 0


# ── find_paths: node not found → unreachable ────────────────────────────────

def test_node_not_found_returns_unreachable():
    g = _make_graph(("A", "B"))
    with patch("tools.network.path_analyzer._resolve_node_id", return_value=None):
        r = find_paths("NONEXISTENT", "B", g)
    assert r["reachable"] is False
    assert r["path_count"] == 0


# ── find_paths: fuzzy resolve maps query to real node id ────────────────────

def test_fuzzy_node_resolve():
    nodes = {
        "tgw-01": {"id": "tgw-01", "label": "Transit Gateway"},
        "vpc-a": {"id": "vpc-a", "label": "VPC-A"},
    }
    edges = [{"source": "tgw-01", "target": "vpc-a"}]
    g = {"nodes": nodes, "edges": edges}
    with patch("tools.network.path_analyzer._resolve_node_id", return_value="tgw-01"):
        r = find_paths("transit gateway", "vpc-a", g)
    assert r["reachable"] is True
    assert r["src"] == "tgw-01"


# ── find_paths: max_depth limits traversal ───────────────────────────────────

def test_max_depth_blocks_long_path():
    # A→B→C→D requires 3 hops; max_depth=2 must block it
    g = _make_graph(("A", "B"), ("B", "C"), ("C", "D"))
    r = find_paths("A", "D", g, max_depth=2)
    assert r["path_count"] == 0
    assert r["reachable"] is False
