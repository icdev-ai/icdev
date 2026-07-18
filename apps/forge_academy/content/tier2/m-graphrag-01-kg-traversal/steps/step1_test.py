
# Auto-grader — GraphRAG kg_edges traversal

edges = [
    {"source": "A", "target": "B", "label": "relates_to"},
    {"source": "B", "target": "C", "label": "relates_to"},
    {"source": "C", "target": "D", "label": "relates_to"},
    {"source": "A", "target": "E", "label": "mentions"},
]

# ── build_adjacency (undirected) ──────────────────────────────────────────────
adj = build_adjacency(edges)
assert adj["A"] == {"B", "E"}, f"A neighbors wrong: {adj.get('A')}"
assert adj["B"] == {"A", "C"}, f"B neighbors wrong: {adj.get('B')}"
assert adj["D"] == {"C"}, f"D neighbors wrong: {adj.get('D')}"
assert adj["E"] == {"A"}
assert build_adjacency([]) == {}

# ── k_hop_neighbors ───────────────────────────────────────────────────────────
assert k_hop_neighbors(adj, "A", 1) == {"B", "E"}
assert k_hop_neighbors(adj, "A", 2) == {"B", "E", "C"}, "2 hops should reach C via B"
assert k_hop_neighbors(adj, "A", 3) == {"B", "E", "C", "D"}
assert "A" not in k_hop_neighbors(adj, "A", 3), "start node must be excluded"
assert k_hop_neighbors(adj, "A", 0) == set()
assert k_hop_neighbors(adj, "ZZ", 2) == set(), "unknown node → empty"

# ── graphrag_expand ───────────────────────────────────────────────────────────
r1 = graphrag_expand(["A"], edges, 1)
assert r1 == ["A", "B", "E"], f"1-hop expansion wrong: {r1}"

r2 = graphrag_expand(["A", "D"], edges, 1)
assert r2 == ["A", "B", "C", "D", "E"], f"multi-seed expansion wrong: {r2}"

# result is a sorted list, seeds always included
r3 = graphrag_expand(["C"], edges, 1)
assert r3 == ["B", "C", "D"], f"seed C 1-hop wrong: {r3}"
assert isinstance(r3, list)

print("PASS: GraphRAG expansion walks kg_edges to pull connected facts around vector seeds.")
