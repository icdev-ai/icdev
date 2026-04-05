#!/usr/bin/env python3
# CUI // SP-CTI
"""GraphRAG retrieval module — scoring profiles, context compression.

Retrieves relevant nodes/edges from the ICDEV Knowledge Graph using
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
import time
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
}

# Keywords for auto-detecting profile from query text
PROFILE_KEYWORDS: Dict[str, List[str]] = {
    "compliance": [
        "compliance", "compliant", "audit", "nist", "fedramp", "cmmc",
        "stig", "poam", "ssp", "ato", "control", "800-53", "oscal",
        "hipaa", "pci", "cjis", "regulation", "regulatory", "framework",
        "authorization", "accreditation", "assessment",
    ],
    "exploratory": [
        "explore", "discover", "gap", "gaps", "find", "search", "what",
        "overview", "landscape", "broad", "unknown", "investigate",
        "opportunities", "patterns", "trends", "related", "connections",
    ],
    "provenance": [
        "provenance", "lineage", "origin", "trace", "tracing", "source",
        "chain", "custody", "history", "audit trail", "evidence",
        "artifact", "derivation", "prov", "w3c",
    ],
    "security": [
        "security", "secure", "threat", "vulnerability", "cve", "attack",
        "injection", "sast", "exploit", "malware", "zero trust", "zta",
        "encryption", "authentication", "authorization", "rbac",
        "atlas", "mitre", "owasp", "stride", "penetration",
    ],
}

# Recency decay: 30-day half-life (consistent with D168 memory config pattern)
RECENCY_HALF_LIFE_DAYS = 30.0


# ---------------------------------------------------------------------------
# DB Helpers
# ---------------------------------------------------------------------------

def _get_db() -> sqlite3.Connection:
    """Get a connection to icdev.db."""
    conn = sqlite3.connect(str(ICDEV_DB))
    conn.row_factory = sqlite3.Row
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
        now = datetime.utcnow()
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

        base_score = (
            w_edge * avg_ew
            + w_centrality * min(centrality, 1.0)
            + w_recency * recency
        )

        # Text-match relevance bonus (up to +0.3)
        relevance_bonus = 0.0
        searchable = f"{label} {entity_type} {props_str}".lower()
        for term in query_terms:
            if term in searchable:
                relevance_bonus += 0.1
        relevance_bonus = min(relevance_bonus, 0.3)

        node_copy = dict(node)
        node_copy["score"] = round(base_score + relevance_bonus, 6)
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
    relevant_edges = [
        e for e in edges
        if e.get("source_id") in node_ids or e.get("target_id") in node_ids
    ]

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
                len(context), len(compressed),
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

        # Determine which graph(s) to search
        if project_id:
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
                "compressed": False,
                "context": "(No knowledge graphs found for the given project.)",
                "retrieval_ms": elapsed_ms,
            }

        # Step 2: Search nodes by keyword matching
        # Build LIKE clauses for each query term
        matched_nodes: List[Dict[str, Any]] = []
        placeholders = ",".join("?" for _ in graph_ids)

        if query_terms:
            # Search label and properties for query terms
            like_clauses = []
            like_params: List[str] = []
            for term in query_terms:
                like_clauses.append("(LOWER(label) LIKE ? OR LOWER(properties) LIKE ?)")
                like_params.extend([f"%{term}%", f"%{term}%"])

            where_likes = " OR ".join(like_clauses)
            sql = f"""
                SELECT id, graph_id, label, entity_type, properties,
                       centrality, created_at
                FROM kg_nodes
                WHERE graph_id IN ({placeholders})
                  AND ({where_likes})
            """
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
            """
            rows = conn.execute(sql, list(graph_ids) + [top_k * 3]).fetchall()
            matched_nodes = [dict(r) for r in rows]

        total_matched = len(matched_nodes)

        if not matched_nodes:
            elapsed_ms = int(time.time() * 1000) - start_ms
            # Log empty retrieval
            if graph_ids:
                _log_retrieval(
                    conn, graph_ids[0], query, selected_profile,
                    0, 0, False, elapsed_ms,
                )
            return {
                "status": "ok",
                "query": query,
                "profile": selected_profile,
                "profile_auto_detected": profile_auto_detected,
                "nodes_matched": 0,
                "nodes_returned": 0,
                "edges_returned": 0,
                "compressed": False,
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
        """
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
            """
            nbr_rows = conn.execute(nbr_sql, list(neighbor_ids)).fetchall()
            for r in nbr_rows:
                matched_nodes.append(dict(r))

        # Step 4: Score nodes
        scored_nodes = _score_nodes(
            matched_nodes, all_edges, selected_profile, query_terms,
        )

        # Step 5: Take top_k
        top_nodes = scored_nodes[:top_k]

        # Filter edges to only those connecting top_k nodes
        top_ids = {n["id"] for n in top_nodes}
        top_edges = [
            e for e in all_edges
            if e.get("source_id") in top_ids or e.get("target_id") in top_ids
        ]

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
        return {
            "status": "ok",
            "query": query,
            "profile": selected_profile,
            "profile_auto_detected": profile_auto_detected,
            "nodes_matched": total_matched,
            "nodes_returned": len(top_nodes),
            "edges_returned": len(top_edges),
            "compressed": compressed,
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
            "compressed": False,
            "context": f"Retrieval error: {exc}",
            "retrieval_ms": elapsed_ms,
        }
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
        "--query", required=True,
        help="Search query text",
    )
    parser.add_argument(
        "--project-id", default=None,
        help="Optional project ID filter",
    )
    parser.add_argument(
        "--profile", default=None,
        choices=list(SCORING_PROFILES.keys()),
        help="Scoring profile (auto-detected from query if omitted)",
    )
    parser.add_argument(
        "--top-k", type=int, default=10,
        help="Maximum nodes to return (default: 10)",
    )
    parser.add_argument(
        "--no-compress", action="store_true",
        help="Skip scanner-tier LLM context compression",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output as JSON",
    )

    args = parser.parse_args()

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
        print(f"Returned: {result.get('nodes_returned', 0)} nodes, "
              f"{result.get('edges_returned', 0)} edges")
        print(f"Compressed: {result.get('compressed', False)}")
        print(f"Time:     {result.get('retrieval_ms', 0)} ms")
        print()
        print(result.get("context", ""))


if __name__ == "__main__":
    main()
