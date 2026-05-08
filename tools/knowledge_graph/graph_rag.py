#!/usr/bin/env python3
# CUI // SP-CTI
"""GraphRAG retrieval module — scoring profiles, context compression.

Retrieves relevant nodes/edges from the ICDEV™ Knowledge Graph using
configurable scoring profiles (D-KARL-1) and optional scanner-tier
LLM compression (D-KARL-2, zero Claude tokens).

Scoring Profiles:
    compliance  — edge_weight 0.4, centrality 0.3, recency 0.3
    exploratory — edge_weight 0.2, centrality 0.5, recency 0.3
    provenance  — edge_weight 0.5, centrality 0.2, recency 0.3
    security    — edge_weight 0.3, centrality 0.4, recency 0.3

Usage:
    python tools/knowledge_graph/graph_rag.py --query "zero trust" --project-id sparkpilot --json
    python tools/knowledge_graph/graph_rag.py --query "AC-2 compliance" --profile compliance --json
    python tools/knowledge_graph/graph_rag.py --query "explore gaps" --profile exploratory --no-compress --json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
import sqlite3
import struct
import tempfile
import time
import urllib.request
import urllib.error
from tools.db.storage import get_connection
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("icdev.knowledge_graph.graph_rag")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
ICDEV_DB = BASE_DIR / "data" / "icdev.db"

# ---------------------------------------------------------------------------
# Scoring Profile Definitions (D-KARL-1)
# ---------------------------------------------------------------------------

SCORING_PROFILES: Dict[str, Dict[str, float]] = {
    "compliance": {
        "edge_weight": 0.4,
        "centrality": 0.3,
        "recency": 0.3,
    },
    "exploratory": {
        "edge_weight": 0.2,
        "centrality": 0.5,
        "recency": 0.3,
    },
    "provenance": {
        "edge_weight": 0.5,
        "centrality": 0.2,
        "recency": 0.3,
    },
    "security": {
        "edge_weight": 0.3,
        "centrality": 0.4,
        "recency": 0.3,
    },
    "network_infrastructure": {
        "edge_weight": 0.4,
        "centrality": 0.4,
        "recency": 0.2,
    },
    # Phase 4 (Internal Awareness Engine) — for Q&A over the
    # kg-icdev-self-awareness component graph. Edge weight matters
    # most (structural relationships: executes/uses_tool/owns_table),
    # centrality next (hub components like canvases score higher),
    # recency is lowest because component structure changes slowly
    # vs. recent health snapshots.
    "internal_awareness": {
        "edge_weight": 0.4,
        "centrality": 0.4,
        "recency": 0.2,
    },
}

# Keywords for auto-detecting profile from query text
PROFILE_KEYWORDS: Dict[str, List[str]] = {
    "compliance": [
        "compliance",
        "compliant",
        "audit",
        "nist",
        "fedramp",
        "cmmc",
        "stig",
        "poam",
        "ssp",
        "ato",
        "control",
        "800-53",
        "oscal",
        "hipaa",
        "pci",
        "cjis",
        "regulation",
        "regulatory",
        "framework",
        "authorization",
        "accreditation",
        "assessment",
    ],
    "exploratory": [
        "explore",
        "discover",
        "gap",
        "gaps",
        "find",
        "search",
        "what",
        "overview",
        "landscape",
        "broad",
        "unknown",
        "investigate",
        "opportunities",
        "patterns",
        "trends",
        "related",
        "connections",
    ],
    "provenance": [
        "provenance",
        "lineage",
        "origin",
        "trace",
        "tracing",
        "source",
        "chain",
        "custody",
        "history",
        "audit trail",
        "evidence",
        "artifact",
        "derivation",
        "prov",
        "w3c",
    ],
    "security": [
        "security",
        "secure",
        "threat",
        "vulnerability",
        "cve",
        "attack",
        "injection",
        "sast",
        "exploit",
        "malware",
        "zero trust",
        "zta",
        "encryption",
        "authentication",
        "authorization",
        "rbac",
        "atlas",
        "mitre",
        "owasp",
        "stride",
        "penetration",
    ],
    "network_infrastructure": [
        "network",
        "router",
        "switch",
        "firewall",
        "redundancy",
        "failover",
        "resiliency",
        "link",
        "circuit",
        "bandwidth",
        "latency",
        "eol",
        "end of life",
        "spof",
        "single point of failure",
        "blast radius",
        "wan",
        "vpn",
        "load balancer",
        "uptime",
        "sla",
        "topology",
        "capacity",
        "vdi",
        "vendor risk",
        "config drift",
    ],
    # Phase 4 — Internal Awareness Engine Q&A
    "internal_awareness": [
        "icdev",
        "component",
        "components",
        "skill",
        "skills",
        "mcp",
        "mcp server",
        "canvas",
        "canvases",
        "goal",
        "goals",
        "reflex",
        "reflexes",
        "break",
        "broken",
        "regress",
        "regression",
        "drift",
        "dependency",
        "dependencies",
        "imports",
        "import",
        "how does",
        "what does",
        "where is",
        "which tool",
        "which skill",
        "self",
        "self-awareness",
        "awareness",
        "enablement",
    ],
}

# Recency decay: 30-day half-life (consistent with D168 memory config pattern)
RECENCY_HALF_LIFE_DAYS = 30.0


# ---------------------------------------------------------------------------
# DB Helpers
# ---------------------------------------------------------------------------


def _get_db() -> sqlite3.Connection:
    """Get a connection to icdev.db."""
    conn = get_connection()
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _ensure_tables(conn: sqlite3.Connection) -> None:
    """Ensure kg tables exist (idempotent)."""
    conn.execute("""
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
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS kg_nodes (
            id TEXT PRIMARY KEY,
            graph_id TEXT NOT NULL REFERENCES kg_graphs(id),
            label TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            properties TEXT DEFAULT '{}',
            embedding BLOB,
            centrality REAL DEFAULT 0.0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS kg_edges (
            id TEXT PRIMARY KEY,
            graph_id TEXT NOT NULL REFERENCES kg_graphs(id),
            source_id TEXT NOT NULL REFERENCES kg_nodes(id),
            target_id TEXT NOT NULL REFERENCES kg_nodes(id),
            relationship TEXT NOT NULL,
            weight REAL DEFAULT 1.0,
            properties TEXT DEFAULT '{}',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS kg_retrieval_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            graph_id TEXT NOT NULL REFERENCES kg_graphs(id),
            query TEXT NOT NULL,
            query_hash TEXT NOT NULL,
            profile TEXT DEFAULT 'exploratory',
            nodes_returned INTEGER DEFAULT 0,
            edges_returned INTEGER DEFAULT 0,
            compression_applied INTEGER DEFAULT 0,
            retrieval_ms INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()


def _now() -> str:
    """ISO-8601 timestamp in UTC."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _query_hash(query: str) -> str:
    """SHA-256 hash of query for dedup/logging."""
    return hashlib.sha256(query.strip().lower().encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Profile Auto-Detection (D-KARL-1)
# ---------------------------------------------------------------------------


def _auto_detect_profile(query: str) -> str:
    """Detect scoring profile from query keywords.

    Counts keyword matches against each profile and returns the one
    with the most hits. Falls back to 'exploratory' on ties or zero
    matches.

    Args:
        query: The user's search query.

    Returns:
        Profile name string.
    """
    query_lower = query.lower()
    scores: Dict[str, int] = {}

    for profile, keywords in PROFILE_KEYWORDS.items():
        count = 0
        for kw in keywords:
            if kw in query_lower:
                count += 1
        scores[profile] = count

    max_score = max(scores.values()) if scores else 0
    if max_score == 0:
        return "exploratory"

    # Pick highest; on tie, prefer compliance > security > provenance > exploratory
    priority = ["compliance", "security", "provenance", "exploratory"]
    for p in priority:
        if scores.get(p, 0) == max_score:
            return p

    return "exploratory"


# ---------------------------------------------------------------------------
# Semantic Search Helpers
# ---------------------------------------------------------------------------


def _cosine_similarity(a: bytes, b: bytes) -> float:
    """Compute cosine similarity between two embedding BLOBs.

    Embeddings are stored as packed float32 arrays (same format as
    enricher.py: ``struct.pack(f'{len(vec)}f', *vec)``).

    Args:
        a: First embedding BLOB.
        b: Second embedding BLOB.

    Returns:
        Cosine similarity in [-1, 1], or 0.0 on error.
    """
    try:
        dim_a = len(a) // 4
        dim_b = len(b) // 4
        if dim_a != dim_b or dim_a == 0:
            return 0.0
        vec_a = struct.unpack(f"{dim_a}f", a)
        vec_b = struct.unpack(f"{dim_b}f", b)
        dot = sum(x * y for x, y in zip(vec_a, vec_b))
        norm_a = math.sqrt(sum(x * x for x in vec_a))
        norm_b = math.sqrt(sum(x * x for x in vec_b))
        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0
        return dot / (norm_a * norm_b)
    except (struct.error, TypeError, ValueError):
        return 0.0


def _embed_query(query: str) -> Optional[List[float]]:
    """Get embedding for a query string via Ollama nomic-embed-text.

    Uses the same model as enricher.py for consistent vector space.
    Falls back to None on any failure (Ollama down, model not loaded, etc.).

    Args:
        query: Text to embed.

    Returns:
        List of floats (768 dimensions for nomic-embed-text), or None.
    """
    try:
        payload = json.dumps(
            {
                "model": "nomic-embed-text",
                "input": query,
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            "http://localhost:11434/api/embed",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:  # nosec B310 -- URL scheme validated; internal/configured endpoints only
            body = json.loads(resp.read().decode("utf-8"))
        embeddings = body.get("embeddings")
        if embeddings and len(embeddings) > 0 and len(embeddings[0]) > 0:
            return embeddings[0]
        return None
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        logger.debug("Query embedding unavailable: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Node Scoring (D-KARL-1)
# ---------------------------------------------------------------------------


def _compute_recency_score(created_at: str) -> float:
    """Exponential time-decay recency score.

    Formula: 2^(-(age_days / half_life))
    Consistent with D168 memory time-decay pattern.

    Args:
        created_at: ISO-8601 timestamp string.

    Returns:
        Float in (0, 1], where 1.0 = just created.
    """
    try:
        # Handle both 'YYYY-MM-DD HH:MM:SS' and ISO-8601 formats
        ts_str = created_at.replace("T", " ").replace("Z", "")
        ts = datetime.strptime(ts_str[:19], "%Y-%m-%d %H:%M:%S")
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        age_days = max((now - ts).total_seconds() / 86400.0, 0.0)
        return math.pow(2, -(age_days / RECENCY_HALF_LIFE_DAYS))
    except (ValueError, TypeError):
        return 0.5  # safe default for unparseable timestamps


def _score_nodes(
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
    profile: str,
    query_terms: List[str],
) -> List[Dict[str, Any]]:
    """Score and rank nodes using profile weights.

    Score formula:
        score = w_edge * avg_edge_weight
              + w_centrality * centrality
              + w_recency * recency_score

    A text-match relevance bonus is added for direct keyword hits.

    Args:
        nodes: List of node dicts with id, label, entity_type,
               centrality, created_at, properties.
        edges: List of edge dicts with source_id, target_id, weight.
        profile: Scoring profile name.
        query_terms: Lowercased query tokens.

    Returns:
        Nodes sorted by score descending, each augmented with 'score'.
    """
    weights = SCORING_PROFILES.get(profile, SCORING_PROFILES["exploratory"])
    w_edge = weights["edge_weight"]
    w_centrality = weights["centrality"]
    w_recency = weights["recency"]

    # Build per-node average edge weight
    node_edge_weights: Dict[str, List[float]] = {}
    for edge in edges:
        src = edge.get("source_id", "")
        tgt = edge.get("target_id", "")
        w = float(edge.get("weight", 1.0))
        node_edge_weights.setdefault(src, []).append(w)
        node_edge_weights.setdefault(tgt, []).append(w)

    scored = []
    for node in nodes:
        nid = node.get("id", "")
        centrality = float(node.get("centrality", 0.0))
        created_at = node.get("created_at", "")
        label = (node.get("label", "") or "").lower()
        entity_type = (node.get("entity_type", "") or "").lower()
        props_str = node.get("properties", "{}")

        # Average edge weight for this node
        ew_list = node_edge_weights.get(nid, [])
        avg_ew = sum(ew_list) / len(ew_list) if ew_list else 0.0
        # Normalize to [0, 1] — cap at 1.0
        avg_ew = min(avg_ew, 1.0)

        recency = _compute_recency_score(created_at)

        base_score = w_edge * avg_ew + w_centrality * min(centrality, 1.0) + w_recency * recency

        # Text-match relevance bonus (up to +0.3)
        relevance_bonus = 0.0
        searchable = f"{label} {entity_type} {props_str}".lower()
        for term in query_terms:
            if term in searchable:
                relevance_bonus += 0.1
        relevance_bonus = min(relevance_bonus, 0.3)

        # Embedding similarity bonus (additive, up to +0.2)
        embedding_bonus = 0.0
        embedding_sim = node.get("_embedding_sim")
        if embedding_sim is not None:
            embedding_bonus = 0.2 * float(embedding_sim)

        node_copy = dict(node)
        node_copy["score"] = round(base_score + relevance_bonus + embedding_bonus, 6)
        scored.append(node_copy)

    scored.sort(key=lambda n: n["score"], reverse=True)
    return scored


# ---------------------------------------------------------------------------
# Context Formatting
# ---------------------------------------------------------------------------


def _format_context(
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
) -> str:
    """Format scored nodes and their edges as readable text.

    Output is designed for LLM consumption — concise, structured,
    with relationship labels.

    Args:
        nodes: Scored and sorted node dicts.
        edges: Relevant edge dicts.

    Returns:
        Multi-line text context string.
    """
    if not nodes:
        return "(No relevant knowledge graph nodes found.)"

    # Build node-id set for edge filtering
    node_ids = {n["id"] for n in nodes}

    lines = ["=== Knowledge Graph Context ===", ""]

    # Nodes section
    lines.append("--- Entities ---")
    for i, node in enumerate(nodes, 1):
        label = node.get("label", "?")
        etype = node.get("entity_type", "?")
        score = node.get("score", 0.0)
        props = node.get("properties", "{}")
        # Parse properties for summary
        try:
            props_dict = json.loads(props) if isinstance(props, str) else props
        except (json.JSONDecodeError, TypeError):
            props_dict = {}
        summary_parts = []
        for k, v in list(props_dict.items())[:3]:
            summary_parts.append(f"{k}={v}")
        summary = ", ".join(summary_parts) if summary_parts else ""
        detail = f" ({summary})" if summary else ""
        lines.append(f"  {i}. [{etype}] {label}{detail}  (score: {score:.3f})")

    lines.append("")

    # Edges section — only include edges between returned nodes
    relevant_edges = [e for e in edges if e.get("source_id") in node_ids or e.get("target_id") in node_ids]

    if relevant_edges:
        lines.append("--- Relationships ---")
        # Build label lookup
        label_map = {n["id"]: n.get("label", "?") for n in nodes}
        seen = set()
        for edge in relevant_edges:
            src = edge.get("source_id", "")
            tgt = edge.get("target_id", "")
            rel = edge.get("relationship", "related_to")
            w = float(edge.get("weight", 1.0))
            key = (src, tgt, rel)
            if key in seen:
                continue
            seen.add(key)
            src_label = label_map.get(src, src[:8])
            tgt_label = label_map.get(tgt, tgt[:8])
            lines.append(f"  {src_label} --[{rel} w={w:.2f}]--> {tgt_label}")

    lines.append("")
    lines.append(f"(Total: {len(nodes)} entities, {len(relevant_edges)} relationships)")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Context Compression (D-KARL-2)
# ---------------------------------------------------------------------------


def _compress_context(context: str, query: str) -> str:
    """Compress verbose neighborhood context via scanner-tier LLM.

    Uses memory_consolidation function routing (scanner tier, qwen3.5,
    zero Claude tokens). Falls back to returning uncompressed context
    on any error.

    Args:
        context: Raw formatted context string.
        query: Original user query (for relevance focus).

    Returns:
        Compressed context string, or original if compression fails.
    """
    if not context or len(context) < 200:
        # Too short to compress meaningfully
        return context

    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        router = LLMRouter()
        prompt = (
            "You are a knowledge graph context compressor. "
            "Compress the following graph neighborhood context into a concise, "
            "information-dense summary that preserves all key entities, "
            "relationships, and facts relevant to the query. Remove redundancy "
            "and boilerplate. Keep entity names, relationship types, and scores "
            "intact. Output only the compressed context, no preamble.\n\n"
            f"Query: {query}\n\n"
            f"Context to compress:\n{context}"
        )

        request = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            system_prompt="Compress knowledge graph context. Be concise.",
            max_tokens=2048,
            temperature=0.1,
        )

        response = router.invoke("memory_consolidation", request)
        compressed = (response.content or "").strip()

        if compressed and len(compressed) > 50:
            logger.info(
                "Context compressed: %d -> %d chars (%.0f%% reduction)",
                len(context),
                len(compressed),
                (1 - len(compressed) / len(context)) * 100,
            )
            return compressed

        # Compression produced too little output — return original
        return context

    except Exception as exc:
        logger.warning("Context compression failed: %s — returning uncompressed", exc)
        return context


# ---------------------------------------------------------------------------
# Retrieval Logging (append-only, NIST AU)
# ---------------------------------------------------------------------------


def _log_retrieval(
    conn: sqlite3.Connection,
    graph_id: str,
    query: str,
    profile: str,
    nodes_returned: int,
    edges_returned: int,
    compression_applied: bool,
    retrieval_ms: int,
) -> None:
    """Log retrieval to kg_retrieval_log (append-only)."""
    try:
        conn.execute(
            """INSERT INTO kg_retrieval_log
               (graph_id, query, query_hash, profile,
                nodes_returned, edges_returned,
                compression_applied, retrieval_ms, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                graph_id,
                query,
                _query_hash(query),
                profile,
                nodes_returned,
                edges_returned,
                1 if compression_applied else 0,
                retrieval_ms,
                _now(),
            ),
        )
        conn.commit()
    except Exception as exc:
        logger.warning("Failed to log retrieval: %s", exc)


# ---------------------------------------------------------------------------
# Main Retrieval Function
# ---------------------------------------------------------------------------


def retrieve(
    query: str,
    project_id: Optional[str] = None,
    profile: Optional[str] = None,
    top_k: int = 10,
    compress: bool = True,
    graph_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Retrieve relevant nodes/edges from the knowledge graph.

    Pipeline:
        1. Auto-detect profile if not specified
        2. Search nodes by keyword matching against query terms
        3. Expand to 1-hop neighborhood (connected edges + neighbor nodes)
        4. Score each node using profile weights
        5. Sort by score, take top_k
        6. Format as context string
        7. Optionally compress via scanner-tier LLM
        8. Log to kg_retrieval_log
        9. Return result dict

    Args:
        query: Search query text.
        project_id: Optional project filter.
        profile: Scoring profile name (auto-detected if None).
        top_k: Maximum nodes to return.
        compress: Whether to apply LLM context compression.

    Returns:
        Dict with status, profile, context, and metadata.
    """
    start_ms = int(time.time() * 1000)
    profile_auto_detected = False

    # Step 1: Profile selection
    if profile and profile in SCORING_PROFILES:
        selected_profile = profile
    else:
        selected_profile = _auto_detect_profile(query)
        profile_auto_detected = True

    # Tokenize query
    query_terms = [t.lower() for t in query.split() if len(t) >= 2]

    conn = _get_db()
    try:
        _ensure_tables(conn)

        # Determine which graph(s) to search. Per-canvas /ask endpoints
        # pass graph_id directly because their kg_graphs rows often have
        # project_id=NULL (the project_id fallback is for project-scoped
        # KGs where multiple graphs live under one project).
        if graph_id:
            graphs = conn.execute(
                "SELECT id FROM kg_graphs WHERE id = ?",
                (graph_id,),
            ).fetchall()
        elif project_id:
            graphs = conn.execute(
                "SELECT id FROM kg_graphs WHERE project_id = ?",
                (project_id,),
            ).fetchall()
        else:
            graphs = conn.execute("SELECT id FROM kg_graphs").fetchall()

        graph_ids = [g["id"] for g in graphs]

        if not graph_ids:
            elapsed_ms = int(time.time() * 1000) - start_ms
            return {
                "status": "ok",
                "query": query,
                "profile": selected_profile,
                "profile_auto_detected": profile_auto_detected,
                "nodes_matched": 0,
                "nodes_returned": 0,
                "edges_returned": 0,
                "nodes": [],
                "edges": [],
                "compressed": False,
                "semantic_search": False,
                "context": "(No knowledge graphs found for the given project.)",
                "retrieval_ms": elapsed_ms,
            }

        # Embed the query for semantic search (graceful degradation)
        query_embedding = _embed_query(query)
        query_embedding_blob: Optional[bytes] = None
        semantic_search_used = False
        if query_embedding is not None:
            query_embedding_blob = struct.pack(
                f"{len(query_embedding)}f",
                *query_embedding,
            )

        # Step 2: Search nodes by keyword matching
        # Build LIKE clauses for each query term
        matched_nodes: List[Dict[str, Any]] = []
        placeholders = ",".join("?" for _ in graph_ids)

        if query_terms:
            # Search label and properties for query terms
            like_clauses = []
            like_params: List[str] = []
            for term in query_terms:
                # Match label, properties, AND entity_type so that NL queries
                # like "firewall" hit nodes whose label is abbreviated
                # (NYC-FWLL-01) but whose entity_type is 'network_firewall'.
                like_clauses.append(
                    "(LOWER(label) LIKE ? OR LOWER(properties) LIKE ? OR LOWER(entity_type) LIKE ?)"
                )
                like_params.extend([f"%{term}%", f"%{term}%", f"%{term}%"])

            where_likes = " OR ".join(like_clauses)
            sql = f"""
                SELECT id, graph_id, label, entity_type, properties,
                       centrality, created_at
                FROM kg_nodes
                WHERE graph_id IN ({placeholders})
                  AND ({where_likes})
            """  # nosec B608 -- table/column names are internal constants, not user input
            params: list = list(graph_ids) + like_params
            rows = conn.execute(sql, params).fetchall()
            matched_nodes = [dict(r) for r in rows]
        else:
            # No useful query terms — return top-centrality nodes
            sql = f"""
                SELECT id, graph_id, label, entity_type, properties,
                       centrality, created_at
                FROM kg_nodes
                WHERE graph_id IN ({placeholders})
                ORDER BY centrality DESC
                LIMIT ?
            """  # nosec B608 -- table/column names are internal constants, not user input
            rows = conn.execute(sql, list(graph_ids) + [top_k * 3]).fetchall()
            matched_nodes = [dict(r) for r in rows]

        # Semantic search: augment keyword results with embedding similarity
        if query_embedding_blob is not None:
            semantic_search_used = True
            matched_id_set_kw = {n["id"] for n in matched_nodes}

            # Fetch all nodes with embeddings from target graphs
            emb_sql = f"""
                SELECT id, graph_id, label, entity_type, properties,
                       centrality, created_at, embedding
                FROM kg_nodes
                WHERE graph_id IN ({placeholders})
                  AND embedding IS NOT NULL
            """  # nosec B608 -- table/column names are internal constants, not user input
            emb_rows = conn.execute(emb_sql, list(graph_ids)).fetchall()

            # Score each node by cosine similarity to query
            sim_scored: List[tuple] = []
            for row in emb_rows:
                row_dict = dict(row)
                emb_blob = row_dict.pop("embedding", None)
                if emb_blob is None:
                    continue
                sim = _cosine_similarity(query_embedding_blob, emb_blob)
                sim_scored.append((sim, row_dict))

            # Sort by similarity descending, take top_k * 3
            sim_scored.sort(key=lambda x: x[0], reverse=True)
            sem_limit = top_k * 3

            for sim, node_dict in sim_scored[:sem_limit]:
                node_dict["_embedding_sim"] = sim
                if node_dict["id"] not in matched_id_set_kw:
                    matched_nodes.append(node_dict)
                    matched_id_set_kw.add(node_dict["id"])
                else:
                    # Tag existing keyword-matched node with similarity
                    for existing in matched_nodes:
                        if existing["id"] == node_dict["id"]:
                            existing["_embedding_sim"] = sim
                            break

        total_matched = len(matched_nodes)

        if not matched_nodes:
            elapsed_ms = int(time.time() * 1000) - start_ms
            # Log empty retrieval
            if graph_ids:
                _log_retrieval(
                    conn,
                    graph_ids[0],
                    query,
                    selected_profile,
                    0,
                    0,
                    False,
                    elapsed_ms,
                )
            return {
                "status": "ok",
                "query": query,
                "profile": selected_profile,
                "profile_auto_detected": profile_auto_detected,
                "nodes_matched": 0,
                "nodes_returned": 0,
                "edges_returned": 0,
                "nodes": [],
                "edges": [],
                "compressed": False,
                "semantic_search": semantic_search_used,
                "context": "(No matching nodes found for the query.)",
                "retrieval_ms": elapsed_ms,
            }

        # Step 3: Expand to 1-hop neighborhood
        matched_ids = [n["id"] for n in matched_nodes]
        id_placeholders = ",".join("?" for _ in matched_ids)

        # Get edges connected to matched nodes
        edge_sql = f"""
            SELECT id, graph_id, source_id, target_id,
                   relationship, weight, properties, created_at
            FROM kg_edges
            WHERE graph_id IN ({placeholders})
              AND (source_id IN ({id_placeholders})
                   OR target_id IN ({id_placeholders}))
        """  # nosec B608 -- table/column names are internal constants, not user input
        edge_params = list(graph_ids) + matched_ids + matched_ids
        edge_rows = conn.execute(edge_sql, edge_params).fetchall()
        all_edges = [dict(r) for r in edge_rows]

        # Collect neighbor node IDs not already in matched set
        matched_id_set = set(matched_ids)
        neighbor_ids = set()
        for edge in all_edges:
            src = edge.get("source_id", "")
            tgt = edge.get("target_id", "")
            if src not in matched_id_set:
                neighbor_ids.add(src)
            if tgt not in matched_id_set:
                neighbor_ids.add(tgt)

        # Fetch neighbor nodes
        if neighbor_ids:
            nbr_placeholders = ",".join("?" for _ in neighbor_ids)
            nbr_sql = f"""
                SELECT id, graph_id, label, entity_type, properties,
                       centrality, created_at
                FROM kg_nodes
                WHERE id IN ({nbr_placeholders})
            """  # nosec B608 -- table/column names are internal constants, not user input
            nbr_rows = conn.execute(nbr_sql, list(neighbor_ids)).fetchall()
            for r in nbr_rows:
                matched_nodes.append(dict(r))

        # Step 4: Score nodes
        scored_nodes = _score_nodes(
            matched_nodes,
            all_edges,
            selected_profile,
            query_terms,
        )

        # Step 5: Take top_k
        top_nodes = scored_nodes[:top_k]

        # Filter edges to only those connecting top_k nodes
        top_ids = {n["id"] for n in top_nodes}
        top_edges = [e for e in all_edges if e.get("source_id") in top_ids or e.get("target_id") in top_ids]

        # Step 6: Format context
        raw_context = _format_context(top_nodes, top_edges)

        # Step 7: Compress (optional)
        compressed = False
        if compress and len(raw_context) > 300:
            final_context = _compress_context(raw_context, query)
            compressed = final_context != raw_context
        else:
            final_context = raw_context

        elapsed_ms = int(time.time() * 1000) - start_ms

        # Step 8: Log retrieval (append-only)
        primary_graph_id = graph_ids[0] if graph_ids else "unknown"
        _log_retrieval(
            conn,
            primary_graph_id,
            query,
            selected_profile,
            len(top_nodes),
            len(top_edges),
            compressed,
            elapsed_ms,
        )

        # Step 9: Return result
        # Sanitize nodes for JSON serialization (drop internal keys, bytea)
        serializable_nodes = []
        for n in top_nodes:
            sn = {k: v for k, v in n.items()
                  if k not in ("_embedding_sim", "embedding", "embedding_vec")
                  and not isinstance(v, (bytes, memoryview))}
            serializable_nodes.append(sn)

        serializable_edges = []
        for e in top_edges:
            se = {k: v for k, v in e.items()
                  if not isinstance(v, (bytes, memoryview))}
            serializable_edges.append(se)

        return {
            "status": "ok",
            "query": query,
            "profile": selected_profile,
            "profile_auto_detected": profile_auto_detected,
            "nodes_matched": total_matched,
            "nodes_returned": len(top_nodes),
            "edges_returned": len(top_edges),
            "nodes": serializable_nodes,
            "edges": serializable_edges,
            "compressed": compressed,
            "semantic_search": semantic_search_used,
            "context": final_context,
            "retrieval_ms": elapsed_ms,
        }

    except Exception as exc:
        elapsed_ms = int(time.time() * 1000) - start_ms
        logger.error("GraphRAG retrieval failed: %s", exc)
        return {
            "status": "error",
            "query": query,
            "profile": selected_profile if profile else "unknown",
            "profile_auto_detected": profile_auto_detected,
            "nodes_matched": 0,
            "nodes_returned": 0,
            "edges_returned": 0,
            "nodes": [],
            "edges": [],
            "compressed": False,
            "semantic_search": False,
            "context": f"Retrieval error: {exc}",
            "retrieval_ms": elapsed_ms,
        }
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Graph-Hop Expansion for Top-K BM25 Hits
# ---------------------------------------------------------------------------


def expand_bm25_top_k(
    hit_ids: List[str],
    graph_id: Optional[str] = None,
    project_id: Optional[str] = None,
    cache_key: Optional[str] = None,
) -> Dict[str, Any]:
    """Expand top-K BM25 hits to their direct (1-hop) neighbors.

    For each node ID in *hit_ids*, queries kg_edges for all directly connected
    edges and collects the IDs of nodes on the other end.  Results are written
    to a temp file so that a process restart or timeout does not lose the
    expansion output.

    Args:
        hit_ids:    Node IDs representing the top-K BM25 hits.
        graph_id:   Restrict edge traversal to a single graph.
        project_id: Restrict edge traversal to all graphs under a project.
        cache_key:  Stable identifier for the temp-file name
                    (default: first-16 hex chars of SHA-256 of sorted hit_ids).

    Returns:
        Dict with keys:
            status        — "ok" or "error"
            hit_ids       — echo of input
            expansions    — {hit_id: [neighbor_id, ...], ...}
            all_neighbors — deduplicated flat list of every neighbor ID
            cache_file    — absolute path where results were persisted
            elapsed_ms    — wall-clock duration
    """
    start_ms = int(time.time() * 1000)

    if not hit_ids:
        return {
            "status": "ok",
            "hit_ids": [],
            "expansions": {},
            "all_neighbors": [],
            "cache_file": None,
            "elapsed_ms": 0,
        }

    if cache_key is None:
        cache_key = hashlib.sha256(
            "|".join(sorted(hit_ids)).encode()
        ).hexdigest()[:16]

    cache_path = Path(tempfile.gettempdir()) / f"icdev_graph_hop_{cache_key}.json"

    conn = _get_db()
    try:
        _ensure_tables(conn)

        # Determine graph scope
        if graph_id:
            scope_rows = conn.execute(
                "SELECT id FROM kg_graphs WHERE id = ?", (graph_id,)
            ).fetchall()
        elif project_id:
            scope_rows = conn.execute(
                "SELECT id FROM kg_graphs WHERE project_id = ?", (project_id,)
            ).fetchall()
        else:
            scope_rows = conn.execute("SELECT id FROM kg_graphs").fetchall()

        scope_ids = [dict(r)["id"] for r in scope_rows]

        expansions: Dict[str, List[str]] = {hid: [] for hid in hit_ids}
        hit_set = set(hit_ids)

        if scope_ids:
            scope_ph = ",".join("?" for _ in scope_ids)
            id_ph = ",".join("?" for _ in hit_ids)
            edge_rows = conn.execute(
                f"""
                SELECT source_id, target_id
                FROM kg_edges
                WHERE graph_id IN ({scope_ph})
                  AND (source_id IN ({id_ph}) OR target_id IN ({id_ph}))
                """,  # nosec B608 — column/table names are internal constants
                list(scope_ids) + list(hit_ids) + list(hit_ids),
            ).fetchall()

            for row in edge_rows:
                r = dict(row)
                src = r.get("source_id", "")
                tgt = r.get("target_id", "")
                if src in hit_set and tgt not in hit_set:
                    expansions[src].append(tgt)
                if tgt in hit_set and src not in hit_set:
                    expansions[tgt].append(src)

        # Deduplicate neighbor lists while preserving insertion order
        for hid in expansions:
            expansions[hid] = list(dict.fromkeys(expansions[hid]))

        all_neighbors = list(
            dict.fromkeys(nbr for nbrs in expansions.values() for nbr in nbrs)
        )

        elapsed_ms = int(time.time() * 1000) - start_ms
        result: Dict[str, Any] = {
            "status": "ok",
            "hit_ids": hit_ids,
            "expansions": expansions,
            "all_neighbors": all_neighbors,
            "cache_file": str(cache_path),
            "elapsed_ms": elapsed_ms,
        }

        # Persist for restart/timeout recovery
        cache_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")

        return result

    except Exception as exc:
        logger.error("expand_bm25_top_k failed: %s", exc)
        elapsed_ms = int(time.time() * 1000) - start_ms
        error_result: Dict[str, Any] = {
            "status": "error",
            "hit_ids": hit_ids,
            "expansions": {},
            "all_neighbors": [],
            "cache_file": str(cache_path),
            "elapsed_ms": elapsed_ms,
            "error": str(exc),
        }
        try:
            cache_path.write_text(
                json.dumps(error_result, indent=2, default=str), encoding="utf-8"
            )
        except Exception:
            pass
        return error_result
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="GraphRAG retrieval with scoring profiles (D-KARL-1/2)",
    )
    parser.add_argument(
        "--query",
        default=None,
        help="Search query text (required unless --expand-hits is used)",
    )
    parser.add_argument(
        "--project-id",
        default=None,
        help="Optional project ID filter",
    )
    parser.add_argument(
        "--profile",
        default=None,
        choices=list(SCORING_PROFILES.keys()),
        help="Scoring profile (auto-detected from query if omitted)",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=10,
        help="Maximum nodes to return (default: 10)",
    )
    parser.add_argument(
        "--no-compress",
        action="store_true",
        help="Skip scanner-tier LLM context compression",
    )
    parser.add_argument(
        "--expand-hits",
        nargs="+",
        metavar="NODE_ID",
        help="Expand top-K BM25 hit node IDs to 1-hop neighbors (space-separated IDs)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON",
    )

    args = parser.parse_args()

    if not args.expand_hits and not args.query:
        parser.error("--query is required unless --expand-hits is used")

    if args.expand_hits:
        result = expand_bm25_top_k(
            hit_ids=args.expand_hits,
            project_id=args.project_id,
        )
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            print(f"Status:        {result.get('status')}")
            print(f"Hit IDs:       {result.get('hit_ids')}")
            print(f"All neighbors: {result.get('all_neighbors')}")
            print(f"Cache file:    {result.get('cache_file')}")
            print(f"Time:          {result.get('elapsed_ms')} ms")
            for hid, nbrs in result.get("expansions", {}).items():
                print(f"  {hid} → {nbrs}")
        return

    result = retrieve(
        query=args.query,
        project_id=args.project_id,
        profile=args.profile,
        top_k=args.top_k,
        compress=not args.no_compress,
    )

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        # Human-readable output
        status = result.get("status", "?")
        profile = result.get("profile", "?")
        auto = " (auto-detected)" if result.get("profile_auto_detected") else ""
        print(f"Status:   {status}")
        print(f"Profile:  {profile}{auto}")
        print(f"Matched:  {result.get('nodes_matched', 0)} nodes")
        print(f"Returned: {result.get('nodes_returned', 0)} nodes, {result.get('edges_returned', 0)} edges")
        print(f"Compressed: {result.get('compressed', False)}")
        print(f"Semantic:   {result.get('semantic_search', False)}")
        print(f"Time:     {result.get('retrieval_ms', 0)} ms")
        print()
        print(result.get("context", ""))


if __name__ == "__main__":
    main()
