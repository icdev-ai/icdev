#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""Knowledge Graph Enricher — centrality calculation + entity embeddings (D-KARL-7).

Computes and persists:
    1. Centrality: weighted average of degree + betweenness (BFS-based, deterministic)
    2. Embeddings: nomic-embed-text via Ollama for semantic node search

All centrality computation is deterministic and air-gap safe.
Embeddings require Ollama with nomic-embed-text model.

Usage:
    python tools/knowledge_graph/enricher.py --graph-id <id> --centrality --json
    python tools/knowledge_graph/enricher.py --graph-id <id> --embeddings --json
    python tools/knowledge_graph/enricher.py --graph-id <id> --all --json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import struct
import sys
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger("icdev.knowledge_graph.enricher")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def _load_config() -> Dict[str, Any]:
    """Load enrichment config from knowledge_graph_config.yaml."""
    config_path = BASE_DIR / "args" / "knowledge_graph_config.yaml"
    if not config_path.exists():
        return {}
    try:
        import yaml

        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        return cfg.get("enrichment", {})
    except Exception:
        return {}


DEFAULT_CONFIG = {
    "centrality": {
        "degree_weight": 0.6,
        "betweenness_weight": 0.4,
        "max_exact_nodes": 500,
    },
    "embeddings": {
        "enabled": True,
        "batch_size": 20,
    },
}


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


def _get_db():
    from tools.db.storage import get_connection, is_pg

    conn = get_connection()
    if not is_pg(conn):
        conn.execute("PRAGMA journal_mode=WAL")  # pg-ok: SQLite init-fallback only, guarded by is_pg
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Centrality Computation (deterministic, no LLM)
# ---------------------------------------------------------------------------


def _build_adjacency(nodes: List[Dict], edges: List[Dict]) -> Dict[str, Set[str]]:
    """Build undirected adjacency list."""
    adj: Dict[str, Set[str]] = defaultdict(set)
    for n in nodes:
        adj[n["id"]]  # Ensure isolated nodes appear
    for e in edges:
        adj[e["source_id"]].add(e["target_id"])
        adj[e["target_id"]].add(e["source_id"])
    return adj


def _degree_centrality(adj: Dict[str, Set[str]]) -> Dict[str, float]:
    """Compute degree centrality: degree(node) / (N-1)."""
    n = len(adj)
    if n <= 1:
        return {nid: 0.0 for nid in adj}
    return {nid: len(neighbors) / (n - 1) for nid, neighbors in adj.items()}


def _betweenness_centrality(adj: Dict[str, Set[str]], max_nodes: int = 500) -> Dict[str, float]:
    """Compute betweenness centrality via BFS shortest paths.

    For graphs <= max_nodes, compute exact. Otherwise sample.
    """
    node_ids = list(adj.keys())
    n = len(node_ids)
    if n <= 1:
        return {nid: 0.0 for nid in node_ids}

    betweenness: Dict[str, float] = {nid: 0.0 for nid in node_ids}

    # Use all nodes if small, sample if large
    sources = node_ids if n <= max_nodes else node_ids[:max_nodes]

    for source in sources:
        # BFS from source
        stack: List[str] = []
        predecessors: Dict[str, List[str]] = {nid: [] for nid in node_ids}
        sigma: Dict[str, int] = {nid: 0 for nid in node_ids}
        sigma[source] = 1
        dist: Dict[str, int] = {nid: -1 for nid in node_ids}
        dist[source] = 0

        queue = deque([source])
        while queue:
            v = queue.popleft()
            stack.append(v)
            for w in adj.get(v, set()):
                # First visit?
                if dist[w] < 0:
                    dist[w] = dist[v] + 1
                    queue.append(w)
                # Shortest path?
                if dist[w] == dist[v] + 1:
                    sigma[w] += sigma[v]
                    predecessors[w].append(v)

        # Accumulate betweenness
        delta: Dict[str, float] = {nid: 0.0 for nid in node_ids}
        while stack:
            w = stack.pop()
            for v in predecessors[w]:
                if sigma[w] > 0:
                    delta[v] += (sigma[v] / sigma[w]) * (1 + delta[w])
            if w != source:
                betweenness[w] += delta[w]

    # Normalize
    scale = 2.0 / ((n - 1) * (n - 2)) if n > 2 else 1.0
    if n > max_nodes:
        scale *= n / max_nodes  # Adjust for sampling

    return {nid: val * scale for nid, val in betweenness.items()}


def compute_centrality(
    graph_id: str,
    conn: Optional[sqlite3.Connection] = None,
) -> Dict[str, Any]:
    """Compute and persist centrality scores for a graph (D-KARL-7).

    Returns summary with node centrality values.
    """
    config = {**DEFAULT_CONFIG.get("centrality", {}), **_load_config().get("centrality", {})}
    degree_w = config.get("degree_weight", 0.6)
    between_w = config.get("betweenness_weight", 0.4)
    max_exact = config.get("max_exact_nodes", 500)

    close_conn = conn is None
    if conn is None:
        conn = _get_db()

    nodes = [
        dict(r)
        for r in conn.execute(
            "SELECT id, label, entity_type FROM kg_nodes WHERE graph_id = %s",
            (graph_id,),
        ).fetchall()
    ]

    edges = [
        dict(r)
        for r in conn.execute(
            "SELECT id, source_id, target_id FROM kg_edges WHERE graph_id = %s",
            (graph_id,),
        ).fetchall()
    ]

    if not nodes:
        if close_conn:
            conn.close()
        return {"status": "error", "error": "No nodes found"}

    adj = _build_adjacency(nodes, edges)
    degree = _degree_centrality(adj)
    betweenness = _betweenness_centrality(adj, max_exact)

    # Weighted average
    centrality: Dict[str, float] = {}
    for nid in adj:
        centrality[nid] = degree_w * degree.get(nid, 0) + between_w * betweenness.get(nid, 0)

    # Persist
    for nid, score in centrality.items():
        conn.execute("UPDATE kg_nodes SET centrality = %s WHERE id = %s", (score, nid))
    conn.commit()

    # Build result
    node_scores = []
    for n in nodes:
        node_scores.append(
            {
                "id": n["id"],
                "label": n["label"],
                "entity_type": n["entity_type"],
                "centrality": round(centrality.get(n["id"], 0), 6),
                "degree": round(degree.get(n["id"], 0), 6),
                "betweenness": round(betweenness.get(n["id"], 0), 6),
            }
        )

    node_scores.sort(key=lambda x: x["centrality"], reverse=True)

    if close_conn:
        conn.close()

    return {
        "status": "ok",
        "graph_id": graph_id,
        "nodes_enriched": len(centrality),
        "weights": {"degree": degree_w, "betweenness": between_w},
        "node_scores": node_scores,
    }


# ---------------------------------------------------------------------------
# Embedding Computation
# ---------------------------------------------------------------------------


def _float_list_to_blob(floats: List[float]) -> bytes:
    """Pack float list into binary BLOB for SQLite storage."""
    return struct.pack(f"{len(floats)}f", *floats)


def compute_embeddings(
    graph_id: str,
    conn: Optional[sqlite3.Connection] = None,
) -> Dict[str, Any]:
    """Compute and persist entity embeddings for a graph (D-KARL-7).

    Uses nomic-embed-text via Ollama (same as RAG pipeline).
    """
    config = {**DEFAULT_CONFIG.get("embeddings", {}), **_load_config().get("embeddings", {})}
    if not config.get("enabled", True):
        return {"status": "disabled", "message": "Embeddings disabled in config"}

    batch_size = config.get("batch_size", 20)

    close_conn = conn is None
    if conn is None:
        conn = _get_db()

    nodes = [
        dict(r)
        for r in conn.execute(
            "SELECT id, label, entity_type FROM kg_nodes WHERE graph_id = %s",
            (graph_id,),
        ).fetchall()
    ]

    if not nodes:
        if close_conn:
            conn.close()
        return {"status": "error", "error": "No nodes found"}

    # Get embedding provider
    try:
        from tools.llm import get_embedding_provider

        provider = get_embedding_provider()
        if provider is None:
            if close_conn:
                conn.close()
            return {"status": "error", "error": "No embedding provider available"}
    except Exception as exc:
        # get_embedding_provider() raises LLMUnavailableError (not None) when no
        # embedding provider is configured/reachable; degrade gracefully rather
        # than crash. Callers fall back to keyword/BM25 retrieval.
        if close_conn:
            conn.close()
        return {"status": "error", "error": f"Embedding provider unavailable: {exc}"}

    from tools.db.storage import is_pg

    pg = is_pg(conn)
    embedded_count = 0
    errors = 0

    # Process in batches
    for i in range(0, len(nodes), batch_size):
        batch = nodes[i : i + batch_size]
        for node in batch:
            text = f"{node['entity_type']}: {node['label']}"
            try:
                embedding = provider.embed(text)
                if embedding and isinstance(embedding, list):
                    blob = _float_list_to_blob(embedding)
                    if pg:
                        # PG: also populate the pgvector column so graph_rag can
                        # rank with in-DB <=> instead of streaming every BLOB.
                        vec_str = "[" + ",".join(f"{v:.6f}" for v in embedding) + "]"
                        conn.execute(
                            "UPDATE kg_nodes SET embedding = %s, embedding_vec = %s::vector "
                            "WHERE id = %s",
                            (blob, vec_str, node["id"]),
                        )
                    else:
                        conn.execute(
                            "UPDATE kg_nodes SET embedding = %s WHERE id = %s",
                            (blob, node["id"]),
                        )
                    embedded_count += 1
            except Exception as exc:
                logger.debug("Embedding failed for %s: %s", node["label"], exc)
                errors += 1

        conn.commit()

    if close_conn:
        conn.close()

    return {
        "status": "ok",
        "graph_id": graph_id,
        "nodes_embedded": embedded_count,
        "errors": errors,
        "total_nodes": len(nodes),
    }


# ---------------------------------------------------------------------------
# Combined enrichment
# ---------------------------------------------------------------------------


def enrich_graph(
    graph_id: str,
    centrality: bool = True,
    embeddings: bool = True,
) -> Dict[str, Any]:
    """Run full enrichment pipeline on a graph."""
    results: Dict[str, Any] = {"graph_id": graph_id, "status": "ok"}

    if centrality:
        results["centrality"] = compute_centrality(graph_id)

    if embeddings:
        results["embeddings"] = compute_embeddings(graph_id)

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Knowledge Graph Enricher — centrality + embeddings (D-KARL-7)",
    )
    parser.add_argument("--graph-id", required=True, help="Graph to enrich")
    parser.add_argument("--centrality", action="store_true", help="Compute centrality")
    parser.add_argument("--embeddings", action="store_true", help="Compute embeddings")
    parser.add_argument("--all", action="store_true", help="Compute both")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    do_centrality = args.centrality or args.all
    do_embeddings = args.embeddings or args.all

    if not do_centrality and not do_embeddings:
        do_centrality = True  # Default to centrality

    result = enrich_graph(args.graph_id, centrality=do_centrality, embeddings=do_embeddings)

    if args.json_output:
        # Remove embedding BLOBs from output
        print(json.dumps(result, indent=2, default=str))
    else:
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
