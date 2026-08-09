#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""Knowledge Graph Insight Generator — scanner-tier, zero Claude tokens.

Analyzes a knowledge graph to find bridge gaps (disconnected clusters),
orphan nodes, and generate research questions.  All analysis is deterministic
(BFS components, centrality, keyword matching).  Optional scanner-tier LLM
(qwen3.5) for question refinement only — never required.

Architecture:
    - Loads graph from kg_graphs / kg_nodes / kg_edges tables (D-KARL-1)
    - Bridge gap detection via BFS connected-component analysis
    - Orphan detection: nodes with zero incident edges
    - Question generation: high-centrality gaps, sparse entity types, bridges
    - Scanner-tier LLM (qwen3.5) optional for question refinement (D-GEN-2)
    - All operations are read-only / advisory-only (D110)

Usage:
    python tools/knowledge_graph/insight_generator.py --graph-id <id> --questions --json
    python tools/knowledge_graph/insight_generator.py --graph-id <id> --bridge-gaps --json
    python tools/knowledge_graph/insight_generator.py --graph-id <id> --summary --json
"""

import argparse
import json
import os
import sys
from collections import defaultdict, deque
from pathlib import Path

# =========================================================================
# PATH SETUP
# =========================================================================
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402

ICDEV_DB = BASE_DIR / "data" / "icdev.db"
DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(ICDEV_DB)))

# =========================================================================
# GRACEFUL IMPORTS
# =========================================================================
try:
    from tools.llm.router import LLMRouter

    _HAS_LLM = True
except ImportError:
    _HAS_LLM = False


# =========================================================================
# DATABASE HELPERS
# =========================================================================
def _get_db(db_path=None):
    """Get database connection with dict-like row access."""
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(f"Database not found: {path}\nRun: python tools/db/init_icdev_db.py")
    conn = get_connection(db_path=str(path))
    return conn


def _ensure_tables(conn):
    """Create kg_* tables if they don't exist (for standalone usage)."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS kg_graphs (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            name TEXT NOT NULL,
            description TEXT,
            entity_count INTEGER DEFAULT 0,
            edge_count INTEGER DEFAULT 0,
            metadata TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS kg_nodes (
            id TEXT PRIMARY KEY,
            graph_id TEXT NOT NULL REFERENCES kg_graphs(id),
            label TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            properties TEXT DEFAULT '{}',
            embedding BLOB,
            centrality REAL DEFAULT 0.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS kg_edges (
            id TEXT PRIMARY KEY,
            graph_id TEXT NOT NULL REFERENCES kg_graphs(id),
            source_id TEXT NOT NULL REFERENCES kg_nodes(id),
            target_id TEXT NOT NULL REFERENCES kg_nodes(id),
            relationship TEXT NOT NULL,
            weight REAL DEFAULT 1.0,
            properties TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)


# =========================================================================
# GRAPH LOADING
# =========================================================================
def _load_graph(conn, graph_id):
    """Load nodes and edges for a graph.  Returns (nodes_list, edges_list)."""
    nodes = conn.execute(
        "SELECT id, label, entity_type, centrality, properties FROM kg_nodes WHERE graph_id = %s",
        (graph_id,),
    ).fetchall()
    edges = conn.execute(
        "SELECT id, source_id, target_id, relationship, weight FROM kg_edges WHERE graph_id = %s",
        (graph_id,),
    ).fetchall()
    return nodes, edges


def _build_adjacency(nodes, edges):
    """Build undirected adjacency list from nodes and edges."""
    adj = defaultdict(set)
    node_ids = {n["id"] for n in nodes}
    for e in edges:
        src, tgt = e["source_id"], e["target_id"]
        if src in node_ids and tgt in node_ids:
            adj[src].add(tgt)
            adj[tgt].add(src)
    # Ensure all nodes appear even if isolated
    for n in nodes:
        if n["id"] not in adj:
            adj[n["id"]] = set()
    return adj


def _find_components(adj):
    """BFS-based connected component detection.  Returns list of sets."""
    visited = set()
    components = []
    for node in adj:
        if node in visited:
            continue
        component = set()
        queue = deque([node])
        while queue:
            cur = queue.popleft()
            if cur in visited:
                continue
            visited.add(cur)
            component.add(cur)
            for nbr in adj[cur]:
                if nbr not in visited:
                    queue.append(nbr)
        components.append(component)
    return components


# =========================================================================
# CORE ANALYSIS FUNCTIONS
# =========================================================================
def find_bridge_gaps(graph_id, db_path=None):
    """Find disconnected clusters and potential bridge opportunities."""
    conn = _get_db(db_path)
    _ensure_tables(conn)
    try:
        nodes, edges = _load_graph(conn, graph_id)
        if not nodes:
            return {
                "status": "ok",
                "graph_id": graph_id,
                "connected_components": 0,
                "bridge_gaps": [],
                "orphan_count": 0,
                "orphan_nodes": [],
            }

        node_map = {n["id"]: dict(n) for n in nodes}
        adj = _build_adjacency(nodes, edges)
        components = _find_components(adj)

        # Identify orphan nodes (0 neighbours)
        orphan_ids = [nid for nid, nbrs in adj.items() if len(nbrs) == 0]
        orphan_nodes = [
            {"id": nid, "label": node_map[nid]["label"], "entity_type": node_map[nid]["entity_type"]}
            for nid in orphan_ids
            if nid in node_map
        ]

        # Build entity-type index per component
        comp_types = []
        for comp in components:
            types = defaultdict(list)
            for nid in comp:
                if nid in node_map:
                    types[node_map[nid]["entity_type"]].append(nid)
            comp_types.append(types)

        # Find bridge gap opportunities between component pairs
        bridge_gaps = []
        non_orphan_comps = [(i, c) for i, c in enumerate(components) if len(c) > 1]
        # Include single-node components that are not true orphans
        single_comps = [(i, c) for i, c in enumerate(components) if len(c) == 1 and list(c)[0] not in orphan_ids]
        candidate_comps = non_orphan_comps + single_comps

        for idx_a in range(len(candidate_comps)):
            for idx_b in range(idx_a + 1, len(candidate_comps)):
                ci_a, comp_a = candidate_comps[idx_a]
                ci_b, comp_b = candidate_comps[idx_b]
                types_a = set(comp_types[ci_a].keys())
                types_b = set(comp_types[ci_b].keys())
                shared_types = types_a & types_b

                potential_bridges = []
                for etype in shared_types:
                    nodes_a = comp_types[ci_a][etype]
                    nodes_b = comp_types[ci_b][etype]
                    for na in nodes_a[:3]:  # limit to avoid combinatorial explosion
                        for nb in nodes_b[:3]:
                            potential_bridges.append(
                                {
                                    "from_node": node_map[na]["label"],
                                    "from_id": na,
                                    "to_node": node_map[nb]["label"],
                                    "to_id": nb,
                                    "shared_type": etype,
                                }
                            )

                if shared_types or len(comp_a) > 1 or len(comp_b) > 1:
                    # Summarise each component by its dominant entity type
                    topic_a = (
                        max(comp_types[ci_a], key=lambda t: len(comp_types[ci_a][t])) if comp_types[ci_a] else "unknown"
                    )
                    topic_b = (
                        max(comp_types[ci_b], key=lambda t: len(comp_types[ci_b][t])) if comp_types[ci_b] else "unknown"
                    )

                    bridge_gaps.append(
                        {
                            "component_a": sorted([node_map[n]["label"] for n in comp_a])[:10],
                            "component_a_size": len(comp_a),
                            "component_b": sorted([node_map[n]["label"] for n in comp_b])[:10],
                            "component_b_size": len(comp_b),
                            "potential_bridges": potential_bridges[:10],
                            "suggested_relationship": (
                                f"Connect {topic_a} cluster to {topic_b} cluster"
                                if shared_types
                                else f"Investigate link between {topic_a} and {topic_b} clusters"
                            ),
                        }
                    )

        return {
            "status": "ok",
            "graph_id": graph_id,
            "connected_components": len(components),
            "bridge_gaps": bridge_gaps,
            "orphan_count": len(orphan_nodes),
            "orphan_nodes": orphan_nodes,
        }
    finally:
        conn.close()


def find_orphan_nodes(graph_id, db_path=None):
    """Find nodes with zero edges (isolated entities)."""
    conn = _get_db(db_path)
    _ensure_tables(conn)
    try:
        nodes, edges = _load_graph(conn, graph_id)
        connected = set()
        for e in edges:
            connected.add(e["source_id"])
            connected.add(e["target_id"])

        orphans = [
            {"id": n["id"], "label": n["label"], "entity_type": n["entity_type"]}
            for n in nodes
            if n["id"] not in connected
        ]
        return {
            "status": "ok",
            "graph_id": graph_id,
            "orphan_count": len(orphans),
            "orphan_nodes": orphans,
        }
    finally:
        conn.close()


def generate_questions(graph_id, use_llm=False, db_path=None):
    """Generate research questions from graph structure.

    Questions fall into three categories:
      - bridge_gap:    missing connections between clusters
      - knowledge_gap: entity types with few instances
      - expansion:     high-centrality nodes that could have more relationships
    """
    conn = _get_db(db_path)
    _ensure_tables(conn)
    try:
        nodes, edges = _load_graph(conn, graph_id)
        if not nodes:
            return {"status": "ok", "graph_id": graph_id, "questions": [], "question_count": 0}

        node_map = {n["id"]: dict(n) for n in nodes}
        adj = _build_adjacency(nodes, edges)
        components = _find_components(adj)

        questions = []

        # --- 1. High-centrality nodes (expansion questions) -----------------
        sorted_by_centrality = sorted(nodes, key=lambda n: n["centrality"], reverse=True)
        top_central = sorted_by_centrality[:5]
        for n in top_central:
            if n["centrality"] > 0:
                neighbour_count = len(adj.get(n["id"], set()))
                questions.append(
                    {
                        "question": (f'Are there additional {n["entity_type"]} entities related to "{n["label"]}"?'),
                        "type": "expansion",
                        "priority": "high" if n["centrality"] >= 0.5 else "medium",
                        "context": {
                            "node_id": n["id"],
                            "centrality": round(n["centrality"], 4),
                            "current_neighbours": neighbour_count,
                        },
                    }
                )

        # --- 2. Sparse entity types (knowledge gap questions) ---------------
        type_counts = defaultdict(int)
        for n in nodes:
            type_counts[n["entity_type"]] += 1

        if type_counts:
            avg_count = sum(type_counts.values()) / len(type_counts)
            for etype, count in sorted(type_counts.items(), key=lambda x: x[1]):
                if count < max(avg_count * 0.5, 2):
                    example = next(
                        (n["label"] for n in nodes if n["entity_type"] == etype),
                        etype,
                    )
                    questions.append(
                        {
                            "question": (
                                f'The entity type "{etype}" has only {count} '
                                f'instance(s) (e.g., "{example}"). '
                                f"Are there additional {etype} entities to add?"
                            ),
                            "type": "knowledge_gap",
                            "priority": "medium" if count == 1 else "low",
                            "context": {"entity_type": etype, "count": count},
                        }
                    )

        # --- 3. Bridge gap questions ----------------------------------------
        if len(components) > 1:
            comp_types_list = []
            for comp in components:
                types = defaultdict(list)
                for nid in comp:
                    if nid in node_map:
                        types[node_map[nid]["entity_type"]].append(node_map[nid]["label"])
                comp_types_list.append(types)

            for i in range(min(len(components), 5)):
                for j in range(i + 1, min(len(components), 5)):
                    if len(components[i]) < 2 and len(components[j]) < 2:
                        continue
                    topic_i = (
                        max(
                            comp_types_list[i],
                            key=lambda t: len(comp_types_list[i][t]),
                        )
                        if comp_types_list[i]
                        else "unknown"
                    )
                    topic_j = (
                        max(
                            comp_types_list[j],
                            key=lambda t: len(comp_types_list[j][t]),
                        )
                        if comp_types_list[j]
                        else "unknown"
                    )
                    questions.append(
                        {
                            "question": (f"What connects the {topic_i} cluster to the {topic_j} cluster?"),
                            "type": "bridge_gap",
                            "priority": "high",
                            "context": {
                                "component_a_size": len(components[i]),
                                "component_b_size": len(components[j]),
                            },
                        }
                    )

        # --- 4. Cross-entity-type questions (relationship discovery) --------
        edge_type_pairs = set()
        for e in edges:
            src_type = node_map.get(e["source_id"], {}).get("entity_type")
            tgt_type = node_map.get(e["target_id"], {}).get("entity_type")
            if src_type and tgt_type and src_type != tgt_type:
                edge_type_pairs.add(tuple(sorted([src_type, tgt_type])))

        all_type_pairs = set()
        type_list = list(type_counts.keys())
        for i in range(len(type_list)):
            for j in range(i + 1, len(type_list)):
                all_type_pairs.add(tuple(sorted([type_list[i], type_list[j]])))

        missing_pairs = all_type_pairs - edge_type_pairs
        for ta, tb in list(missing_pairs)[:5]:
            questions.append(
                {
                    "question": (f'What is the relationship between "{ta}" and "{tb}" entities?'),
                    "type": "bridge_gap",
                    "priority": "medium",
                    "context": {"type_a": ta, "type_b": tb},
                }
            )

        # --- 5. Optional LLM refinement (scanner-tier only) -----------------
        if use_llm and _HAS_LLM and questions:
            questions = _refine_questions_llm(graph_id, questions, node_map)

        return {
            "status": "ok",
            "graph_id": graph_id,
            "questions": questions,
            "question_count": len(questions),
        }
    finally:
        conn.close()


def _refine_questions_llm(graph_id, questions, node_map):
    """Optionally refine questions via scanner-tier qwen3.5 (zero Claude tokens)."""
    try:
        router = LLMRouter()
        raw_qs = "\n".join(f"- [{q['type']}] {q['question']}" for q in questions[:15])
        prompt = (
            "Given these research questions about a knowledge graph, "
            "rewrite each to be clearer and more actionable. "
            "Return a JSON array of objects with keys: question, type, priority.\n\n"
            f"Questions:\n{raw_qs}"
        )
        from tools.llm.provider import LLMRequest

        request = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=1024,
        )
        resp = router.invoke("ft_pair_generation", request)
        content = resp.content if resp and resp.content else ""
        # Try to parse JSON from response
        start = content.find("[")
        end = content.rfind("]")
        if start >= 0 and end > start:
            refined = json.loads(content[start : end + 1])
            for i, rq in enumerate(refined):
                if i < len(questions):
                    questions[i]["question"] = rq.get("question", questions[i]["question"])
    except Exception:
        pass  # Graceful degradation — return unrefined questions
    return questions


def graph_summary(graph_id, db_path=None):
    """Quick summary statistics for a knowledge graph."""
    conn = _get_db(db_path)
    _ensure_tables(conn)
    try:
        graph = conn.execute("SELECT * FROM kg_graphs WHERE id = %s", (graph_id,)).fetchone()
        if not graph:
            return {"status": "error", "message": f"Graph {graph_id} not found"}

        nodes, edges = _load_graph(conn, graph_id)
        adj = _build_adjacency(nodes, edges)
        components = _find_components(adj)

        # Entity type distribution
        type_dist = defaultdict(int)
        centrality_sum = 0.0
        for n in nodes:
            type_dist[n["entity_type"]] += 1
            centrality_sum += n["centrality"]

        avg_centrality = centrality_sum / len(nodes) if nodes else 0.0
        orphan_count = sum(1 for nid in adj if len(adj[nid]) == 0)

        return {
            "status": "ok",
            "graph_id": graph_id,
            "name": graph["name"],
            "entity_count": len(nodes),
            "edge_count": len(edges),
            "entity_type_distribution": dict(type_dist),
            "avg_centrality": round(avg_centrality, 4),
            "connected_components": len(components),
            "orphan_count": orphan_count,
        }
    finally:
        conn.close()


# =========================================================================
# CLI
# =========================================================================
def main():
    parser = argparse.ArgumentParser(description="Knowledge Graph Insight Generator — scanner-tier analysis")
    parser.add_argument("--graph-id", required=True, help="Knowledge graph ID")
    parser.add_argument("--questions", action="store_true", help="Generate research questions from graph structure")
    parser.add_argument("--bridge-gaps", action="store_true", help="Find bridge gaps between disconnected clusters")
    parser.add_argument("--orphans", action="store_true", help="Find orphan nodes with zero edges")
    parser.add_argument("--summary", action="store_true", help="Quick graph summary statistics")
    parser.add_argument("--use-llm", action="store_true", help="Use scanner-tier LLM (qwen3.5) for question refinement")
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()

    if not any([args.questions, args.bridge_gaps, args.orphans, args.summary]):
        parser.error("Specify at least one action: --questions, --bridge-gaps, --orphans, --summary")

    result = {}

    if args.summary:
        result = graph_summary(args.graph_id)
    elif args.bridge_gaps:
        result = find_bridge_gaps(args.graph_id)
    elif args.orphans:
        result = find_orphan_nodes(args.graph_id)
    elif args.questions:
        result = generate_questions(args.graph_id, use_llm=args.use_llm)

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        # Human-readable output
        if result.get("status") == "error":
            print(f"ERROR: {result.get('message', 'Unknown error')}")
            sys.exit(1)

        print(f"\n=== Knowledge Graph Insight: {args.graph_id} ===\n")

        if "entity_count" in result:
            print(f"  Entities:   {result['entity_count']}")
            print(f"  Edges:      {result['edge_count']}")
            print(f"  Components: {result['connected_components']}")
            print(f"  Orphans:    {result['orphan_count']}")
            print(f"  Avg Centrality: {result['avg_centrality']}")
            if "entity_type_distribution" in result:
                print("\n  Entity Types:")
                for etype, count in sorted(
                    result["entity_type_distribution"].items(),
                    key=lambda x: -x[1],
                ):
                    print(f"    {etype}: {count}")

        if "bridge_gaps" in result:
            print(f"\n  Connected Components: {result['connected_components']}")
            print(f"  Bridge Gaps Found:    {len(result['bridge_gaps'])}")
            for i, gap in enumerate(result["bridge_gaps"], 1):
                print(f"\n  Gap {i}: {gap['suggested_relationship']}")
                print(f"    Component A ({gap['component_a_size']} nodes): {', '.join(gap['component_a'][:5])}")
                print(f"    Component B ({gap['component_b_size']} nodes): {', '.join(gap['component_b'][:5])}")
                if gap["potential_bridges"]:
                    print(f"    Potential bridges: {len(gap['potential_bridges'])}")

        if "orphan_nodes" in result and "bridge_gaps" not in result:
            print(f"\n  Orphan Nodes: {result['orphan_count']}")
            for orphan in result["orphan_nodes"][:20]:
                print(f"    - {orphan['label']} ({orphan['entity_type']})")

        if "questions" in result:
            print(f"\n  Questions Generated: {result['question_count']}")
            for i, q in enumerate(result["questions"], 1):
                priority_marker = {"high": "!!!", "medium": "!!", "low": "!"}.get(q["priority"], "")
                print(f"\n  {i}. [{q['type']}] {priority_marker} {q['question']}")


if __name__ == "__main__":
    main()
