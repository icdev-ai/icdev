# [CUI // SP-CTI]
"""Tests for tools/security_canvas/attack_graph_builder.py — 5 cases."""

from tools.security_canvas.attack_graph_builder import (
    build_attack_graph,
    get_ttp_for_stride_category,
    stride_rows_to_results,
)

# ── Shared fixtures ───────────────────────────────────────────────────────────

_GRAPH_DATA = {
    "nodes": [
        {"id": "client", "type": "asset-client",   "label": "User Client"},
        {"id": "web",    "type": "asset-server",   "label": "Web Server"},
        {"id": "db",     "type": "asset-database", "label": "Database"},
        {"id": "store",  "type": "asset-storage",  "label": "File Store"},
    ],
    "edges": [
        {"id": "e1", "source": "client", "target": "web",   "encrypted": False, "authenticated": False},
        {"id": "e2", "source": "web",    "target": "db",    "encrypted": True,  "authenticated": True},
        {"id": "e3", "source": "web",    "target": "store", "encrypted": False, "authenticated": True},
    ],
    "boundaries": [],
}

_STRIDE_ONE_SPOOF = {
    "threats": [
        {
            "id": "STRIDE-S-client-web___",
            "category": "S",
            "category_name": "Spoofing",
            "title": "Spoofing: User Client → Web Server",
            "description": "Unauthenticated flow",
            "affected": "User Client, Web Server",
            "affected_assets": ["client", "web"],
            "crosses_boundary": False,
            "nist_controls": ["IA-2"],
            "likelihood": "high",
            "impact": "medium",
        }
    ],
    "total": 1,
    "by_category": {"S": 1},
}

_STRIDE_MULTI = {
    "threats": [
        {
            "id": "STRIDE-S-a1-a2",
            "category": "S",
            "affected_assets": ["a1", "a2"],
            "crosses_boundary": False,
            "title": "Spoof a1→a2",
            "affected": "A1, A2",
        },
        {
            "id": "STRIDE-T-a3-a4",
            "category": "T",
            "affected_assets": ["a3", "a4"],
            "crosses_boundary": True,
            "title": "Tamper a3→a4",
            "affected": "A3, A4",
        },
        {
            "id": "STRIDE-I-a5-a6",
            "category": "I",
            "affected_assets": ["a5", "a6"],
            "crosses_boundary": True,
            "title": "Disclose a5→a6",
            "affected": "A5, A6",
        },
        {
            "id": "STRIDE-E-a7-a8",
            "category": "E",
            "affected_assets": ["a7", "a8"],
            "crosses_boundary": False,
            "title": "EoP a7→a8",
            "affected": "A7, A8",
        },
    ],
    "total": 4,
    "by_category": {"S": 1, "T": 1, "I": 1, "E": 1},
}

_ATTACK_PATH_RESULT = {
    "attack_paths": [
        {
            "id": "AP-client-db____-0",
            "entry":  {"id": "client", "label": "User Client", "type": "asset-client"},
            "target": {"id": "db",     "label": "Database",    "type": "asset-database"},
            "hops": [
                {"node_id": "client", "node_label": "User Client", "node_type": "asset-client"},
                {"node_id": "web",    "node_label": "Web Server",  "node_type": "asset-server"},
                {"node_id": "db",     "node_label": "Database",    "node_type": "asset-database"},
            ],
            "risk_score": 65.0,
            "risk_level": "high",
            "mitigations": [],
        }
    ],
    "total_paths": 1,
    "critical_paths": 0,
}


# ── Test 1: empty STRIDE result → empty graph ─────────────────────────────────


def test_empty_returns_empty_graph():
    result = build_attack_graph({"threats": []})
    assert result["total_nodes"] == 0
    assert result["total_edges"] == 0
    assert result["nodes"] == []
    assert result["edges"] == []


# ── Test 2: single Spoofing threat → 2 nodes, 1 edge with T1078 ───────────────


def test_spoofing_builds_two_nodes_one_edge_with_t1078():
    result = build_attack_graph(_STRIDE_ONE_SPOOF, graph_data=_GRAPH_DATA)

    assert result["total_nodes"] == 2
    assert result["total_edges"] == 1

    node_ids = {n["id"] for n in result["nodes"]}
    assert node_ids == {"client", "web"}

    edge = result["edges"][0]
    assert edge["edge_type"] == "stride_threat"
    assert edge["stride_category"] == "S"
    assert edge["ttp_id"] == "T1078"   # Valid Accounts — maps from MITRE_ATTACK_TECHNIQUES
    assert edge["tactic_id"] == "TA0001"
    assert edge["source"] == "client"
    assert edge["target"] == "web"

    # Spoofing node carries 'S' in stride_categories
    client_node = next(n for n in result["nodes"] if n["id"] == "client")
    assert "S" in client_node["stride_categories"]


# ── Test 3: multiple STRIDE categories → distinct TTPs ────────────────────────


def test_stride_categories_map_distinct_ttps():
    result = build_attack_graph(_STRIDE_MULTI)

    assert result["total_nodes"] == 8    # 4 threats × 2 unique asset IDs each
    assert result["total_edges"] == 4

    ttp_ids = {e["ttp_id"] for e in result["edges"]}
    # S→T1078, T→T1059, I→T1005, E→T1068 — all distinct
    assert ttp_ids == {"T1078", "T1059", "T1005", "T1068"}

    # Verify confidence increases when threat crosses a boundary
    tamper_edge = next(e for e in result["edges"] if e["stride_category"] == "T")
    spoof_edge  = next(e for e in result["edges"] if e["stride_category"] == "S")
    assert tamper_edge["confidence"] == 0.9   # crosses_boundary=True
    assert spoof_edge["confidence"]  == 0.7   # crosses_boundary=False


# ── Test 4: attack path → hop edges annotated with TTPs ──────────────────────


def test_attack_path_hops_produce_hop_edges():
    result = build_attack_graph(
        {"threats": []},
        attack_path_result=_ATTACK_PATH_RESULT,
    )

    # 3-hop path → 3 nodes, 2 hop edges (client→web, web→db)
    assert result["total_nodes"] == 3
    assert result["total_edges"] == 2

    hop_edges = [e for e in result["edges"] if e["edge_type"] == "attack_hop"]
    assert len(hop_edges) == 2

    # First hop: client→web; target=asset-server → lateral_movement / T1021
    first = next(e for e in hop_edges if e["source"] == "client")
    assert first["ttp_id"] == "T1021"
    assert first["tactic_key"] == "lateral_movement"
    assert first["hop_index"] == 0

    # Second hop: web→db; target=asset-database → collection / T1005
    second = next(e for e in hop_edges if e["source"] == "web")
    assert second["ttp_id"] == "T1005"
    assert second["tactic_key"] == "collection"
    assert second["hop_index"] == 1

    # risk_score carried through
    assert first["risk_score"] == 65.0


# ── Test 5: stride_rows_to_results converts DB rows and output is deterministic


def test_stride_rows_to_results_and_deterministic_output():
    rows = [
        {
            "id": "row-001",
            "threat_category": "I",
            "title": "Info Disclosure on web→db",
            "description": "Unencrypted DB flow",
            "affected_assets": '["web", "db"]',
            "likelihood": "high",
            "impact": "high",
            "crosses_boundary": False,
        },
        {
            "id": "row-002",
            "threat_category": "D",
            "title": "DoS on client→web",
            "description": "Internet-facing server",
            "affected_assets": '["client", "web"]',
            "likelihood": "medium",
            "impact": "high",
            "crosses_boundary": False,
        },
    ]

    stride_results = stride_rows_to_results(rows)
    assert stride_results["total"] == 2
    assert stride_results["by_category"] == {"I": 1, "D": 1}

    result_a = build_attack_graph(stride_results, graph_data=_GRAPH_DATA)
    result_b = build_attack_graph(stride_results, graph_data=_GRAPH_DATA)

    # Determinism: identical calls produce identical output
    assert result_a == result_b

    # 3 unique assets (web, db, client), 2 edges
    assert result_a["total_nodes"] == 3
    assert result_a["total_edges"] == 2

    ttp_ids = {e["ttp_id"] for e in result_a["edges"]}
    assert "T1005" in ttp_ids   # I → collection
    assert "T1499" in ttp_ids   # D → impact

    # get_ttp_for_stride_category helper works for all 6 categories
    for cat in ("S", "T", "R", "I", "D", "E"):
        meta = get_ttp_for_stride_category(cat)
        assert meta["ttp_id"] != ""
        assert meta["tactic_id"].startswith("TA")
