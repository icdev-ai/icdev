
"""
Tier 2 — GraphRAG & the Knowledge Graph
Goal: Turn a vector-search seed set into a connected-fact neighborhood by walking
      kg_edges — the core move in GraphRAG.

Plain RAG (kg_search / rag_search) finds chunks that are *similar* to the query.
GraphRAG adds a second step: from the seed nodes the vector search returned, it walks
the Knowledge Graph's `kg_edges` (real columns: graph_id, source_id, target_id,
relationship) to pull in *connected* facts a pure similarity search would miss.
tools/knowledge_graph/graph_rag.py::retrieve() implements this — the semantic step runs
in-database with pgvector `<=>` on PostgreSQL (or a Python-cosine fallback on SQLite),
then the 1-hop neighborhood is expanded over kg_edges. NOTE: kg_edges is ONE table
SHARED across every canvas graph (partitioned by graph_id) — never purge it by label,
and always scope a traversal by graph_id. This exercise models the graph-expansion hop
with the stdlib; we use simplified source/target/label edge keys.
"""

from collections import deque


# ── Step 1: Build an adjacency map from kg_edges ──────────────────────────────

def build_adjacency(edges: list[dict]) -> dict:
    """TODO: Build an UNDIRECTED adjacency map from kg_edges.

    Each edge is {"source": s, "target": t, "label": ...}. GraphRAG traverses in
    both directions, so for every edge add t to adj[s] AND s to adj[t].
    Use a set of neighbors per node.
    Return: {node: set(neighbor_nodes)}
    """
    # YOUR CODE HERE
    pass


# ── Step 2: k-hop neighborhood (BFS) ──────────────────────────────────────────

def k_hop_neighbors(adjacency: dict, start: str, k: int) -> set:
    """TODO: Return all nodes reachable within `k` hops of `start`.

    Breadth-first search from `start`, following adjacency, stopping after k levels.
    EXCLUDE `start` itself from the result. If start is not in adjacency, return an
    empty set. k <= 0 returns an empty set.
    """
    # YOUR CODE HERE
    pass


# ── Step 3: GraphRAG expansion ────────────────────────────────────────────────

def graphrag_expand(seed_nodes: list, edges: list[dict], k: int = 1) -> list:
    """TODO: Expand vector-search seeds into their connected neighborhood.

    1. adjacency = build_adjacency(edges)
    2. Start with the set of seed_nodes.
    3. For each seed, add its k_hop_neighbors(adjacency, seed, k).
    4. Return the full set (seeds + neighbors) as a SORTED list.
    """
    # YOUR CODE HERE
    pass


# Demo
if __name__ == "__main__":
    edges = [
        {"source": "AC-2", "target": "IA-2", "label": "depends_on"},
        {"source": "IA-2", "target": "IA-5", "label": "depends_on"},
        {"source": "AC-2", "target": "AU-2", "label": "mentions"},
    ]
    print("1-hop from AC-2:", graphrag_expand(["AC-2"], edges, 1))
    print("2-hop from AC-2:", graphrag_expand(["AC-2"], edges, 2))
