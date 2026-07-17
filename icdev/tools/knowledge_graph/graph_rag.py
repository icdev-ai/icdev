#!/usr/bin/env python3
# CUI // SP-CTI
"""GraphRAG retrieval module — scoring profiles, context compression.

Retrieves relevant nodes/edges from the ICDEV™ Knowledge Graph using
configurable scoring profiles (D-KARL-1), ontology-aware hierarchy
boosting (D-ONTO-1), and optional scanner-tier LLM compression
(D-KARL-2, zero Claude tokens).

Scoring Profiles:
    compliance  — edge_weight 0.4, centrality 0.3, recency 0.3, ontology 0.2
    exploratory — edge_weight 0.2, centrality 0.5, recency 0.3, ontology 0.1
    provenance  — edge_weight 0.5, centrality 0.2, recency 0.3, ontology 0.0
    security    — edge_weight 0.3, centrality 0.4, recency 0.3, ontology 0.2
    ontology    — edge_weight 0.2, centrality 0.2, recency 0.2, ontology 0.4

Usage:
    python tools/knowledge_graph/graph_rag.py --query "zero trust" --project-id sparkpilot --json
    python tools/knowledge_graph/graph_rag.py --query "AC-2 compliance" --profile compliance --json
    python tools/knowledge_graph/graph_rag.py --query "explore gaps" --profile exploratory --no-compress --json
    python tools/knowledge_graph/graph_rag.py --query "AWS ALB siblings" --profile ontology --json
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import argparse
import hashlib
import json
import math
import struct
import tempfile
import time
from tools.db.storage import get_connection, is_pg
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = get_logger("icdev.knowledge_graph.graph_rag")

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# ---------------------------------------------------------------------------
# Scoring Profile Definitions (D-KARL-1)
# ---------------------------------------------------------------------------

SCORING_PROFILES: Dict[str, Dict[str, float]] = {
    "compliance": {
        "edge_weight": 0.4,
        "centrality": 0.3,
        "recency": 0.3,
        "ontology_weight": 0.2,
    },
    "exploratory": {
        "edge_weight": 0.2,
        "centrality": 0.5,
        "recency": 0.3,
        "ontology_weight": 0.1,
    },
    "provenance": {
        "edge_weight": 0.5,
        "centrality": 0.2,
        "recency": 0.3,
        "ontology_weight": 0.0,
    },
    "security": {
        "edge_weight": 0.3,
        "centrality": 0.4,
        "recency": 0.3,
        "ontology_weight": 0.2,
    },
    "network_infrastructure": {
        "edge_weight": 0.4,
        "centrality": 0.4,
        "recency": 0.2,
        "ontology_weight": 0.2,
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
        "ontology_weight": 0.0,
    },
    # Ontology-aware profile — prioritises class-hierarchy traversal
    # (rdfs:subClassOf / sibling expansion) over recency.
    "ontology": {
        "edge_weight": 0.2,
        "centrality": 0.2,
        "recency": 0.2,
        "ontology_weight": 0.4,
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
    # Ontology-aware queries — class hierarchies, taxonomies, type families
    "ontology": [
        "ontology",
        "class",
        "subclass",
        "superclass",
        "hierarchy",
        "type",
        "category",
        "family",
        "related types",
        "sibling",
        "parent class",
        "child class",
        "rdfs",
        "owl",
        "taxonomy",
        "taxonomy of",
        "all types of",
        "all kinds of",
        "what are the",
        "load balancer",
        "load balancers",
    ],
}

# Recency decay: 30-day half-life (consistent with D168 memory config pattern)
RECENCY_HALF_LIFE_DAYS = 30.0

# ---------------------------------------------------------------------------
# Ontology Hierarchy Fallback (used until full ontology_catalog is deployed)
# ---------------------------------------------------------------------------

_ONTOLOGY_FALLBACK: Dict[str, List[Dict[str, Any]]] = {
    # Load-balancer family — rdfs:subClassOf hierarchy
    "load_balancer": [
        {"type": "aws_alb", "predicate": "rdfs:subClassOf", "distance": 1},
        {"type": "aws_nlb", "predicate": "rdfs:subClassOf", "distance": 1},
        {"type": "azure_appgw", "predicate": "rdfs:subClassOf", "distance": 1},
        {"type": "gcp_lb", "predicate": "rdfs:subClassOf", "distance": 1},
    ],
    "aws_alb": [
        {"type": "load_balancer", "predicate": "rdfs:superClassOf", "distance": 1},
        {"type": "aws_nlb", "predicate": "rdfs:subClassOf", "distance": 2},
        {"type": "azure_appgw", "predicate": "rdfs:subClassOf", "distance": 2},
        {"type": "gcp_lb", "predicate": "rdfs:subClassOf", "distance": 2},
        {"type": "aws_family", "predicate": "hasProvider", "distance": 1},
    ],
    "aws_nlb": [
        {"type": "load_balancer", "predicate": "rdfs:superClassOf", "distance": 1},
        {"type": "aws_alb", "predicate": "rdfs:subClassOf", "distance": 2},
        {"type": "azure_appgw", "predicate": "rdfs:subClassOf", "distance": 2},
        {"type": "gcp_lb", "predicate": "rdfs:subClassOf", "distance": 2},
        {"type": "aws_family", "predicate": "hasProvider", "distance": 1},
    ],
    "azure_appgw": [
        {"type": "load_balancer", "predicate": "rdfs:superClassOf", "distance": 1},
        {"type": "aws_alb", "predicate": "rdfs:subClassOf", "distance": 2},
        {"type": "aws_nlb", "predicate": "rdfs:subClassOf", "distance": 2},
        {"type": "gcp_lb", "predicate": "rdfs:subClassOf", "distance": 2},
        {"type": "azure_family", "predicate": "hasProvider", "distance": 1},
    ],
    "gcp_lb": [
        {"type": "load_balancer", "predicate": "rdfs:superClassOf", "distance": 1},
        {"type": "aws_alb", "predicate": "rdfs:subClassOf", "distance": 2},
        {"type": "aws_nlb", "predicate": "rdfs:subClassOf", "distance": 2},
        {"type": "azure_appgw", "predicate": "rdfs:subClassOf", "distance": 2},
        {"type": "gcp_family", "predicate": "hasProvider", "distance": 1},
    ],
    # Compliance — NISTControl satisfies frameworks
    "nist_control": [
        {"type": "compliance_framework", "predicate": "satisfiesFramework", "distance": 1},
    ],
    "fedramp_control": [
        {"type": "compliance_framework", "predicate": "satisfiesFramework", "distance": 1},
    ],
    "cmmc_control": [
        {"type": "compliance_framework", "predicate": "satisfiesFramework", "distance": 1},
    ],
    "control": [
        {"type": "compliance_framework", "predicate": "satisfiesFramework", "distance": 1},
    ],
    # Security — threat-actor linkage
    "threat_actor": [
        {"type": "security_object", "predicate": "hasActor", "distance": 1},
    ],
    "cve": [
        {"type": "security_object", "predicate": "hasActor", "distance": 2},
    ],
    "vulnerability": [
        {"type": "security_object", "predicate": "hasActor", "distance": 2},
    ],
    # Provider families (network_infrastructure)
    "aws_vpc": [{"type": "aws_family", "predicate": "hasProvider", "distance": 1}],
    "azure_vnet": [{"type": "azure_family", "predicate": "hasProvider", "distance": 1}],
    "gcp_vpc": [{"type": "gcp_family", "predicate": "hasProvider", "distance": 1}],
}


# ---------------------------------------------------------------------------
# Config Loader (D-KARL-1 / D-ONTO-1)
# ---------------------------------------------------------------------------


def _load_config() -> Dict[str, Any]:
    """Load knowledge graph config from args/knowledge_graph_config.yaml."""
    config_path = BASE_DIR / "args" / "knowledge_graph_config.yaml"
    try:
        import yaml

        with open(config_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


_CONFIG = _load_config()
_GRAPH_RAG_CONFIG: Dict[str, Any] = _CONFIG.get("graph_rag", {})

# Override hardcoded profiles with YAML config if present
_config_profiles = _GRAPH_RAG_CONFIG.get("profiles", {})
if isinstance(_config_profiles, dict):
    for name, weights in _config_profiles.items():
        if isinstance(weights, dict) and "edge_weight" in weights:
            SCORING_PROFILES[name] = {
                "edge_weight": float(weights.get("edge_weight", 0.2)),
                "centrality": float(weights.get("centrality", 0.3)),
                "recency": float(weights.get("recency", 0.3)),
                "ontology_weight": float(weights.get("ontology_weight", 0.0)),
            }

ONTOLOGY_PATH_DISTANCE_DECAY = float(
    _GRAPH_RAG_CONFIG.get("defaults", {}).get("ontology_path_distance_decay", 1.0)
)

# ---------------------------------------------------------------------------
# Ontology Hierarchy Helpers (D-ONTO-1)
# ---------------------------------------------------------------------------


def _ontology_shortest_distance(
    from_type: str,
    to_type: str,
    relations: Dict[str, List[Dict[str, Any]]],
) -> Optional[int]:
    """BFS shortest-path distance between two entity types in the ontology graph.

    Traverses rdfs:subClassOf / rdfs:superClassOf as bidirectional edges,
    and treats sibling relations as undirected.  Returns ``None`` if no
    path exists.
    """
    from collections import deque

    start = from_type.lower()
    goal = to_type.lower()
    if start == goal:
        return 0

    # Build adjacency list
    adj: Dict[str, set] = {}
    for subject, entries in relations.items():
        s = subject.lower()
        for e in entries:
            t = e["type"].lower()
            pred = e.get("predicate", "")
            adj.setdefault(s, set()).add(t)
            # Hierarchy and sibling predicates are traversable both ways
            if "subClassOf" in pred or "superClassOf" in pred or pred in ("sibling",):
                adj.setdefault(t, set()).add(s)

    queue = deque([(start, 0)])
    visited = {start}
    while queue:
        current, dist = queue.popleft()
        for neighbor in adj.get(current, set()):
            if neighbor == goal:
                return dist + 1
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, dist + 1))
    return None


# ---------------------------------------------------------------------------
# DB Helpers
# ---------------------------------------------------------------------------


def _get_db() -> Any:
    """Get a connection to icdev.db."""
    conn = get_connection()
    if not is_pg(conn):
        conn.execute("PRAGMA journal_mode=WAL")  # pg-ok: SQLite init-fallback only, guarded by is_pg
    return conn


def _ensure_tables(conn: Any) -> None:
    """Ensure kg tables exist (idempotent).

    PG-primary: on PostgreSQL these tables are owned by the consolidated
    schema / migrations (pg_consolidated.sql), which carry columns this
    bootstrap omits (classification, embedding_vec, source_chunk_id,
    ontology_id). Re-creating them at runtime would drift from the real
    schema, so this is a no-op on PG. SQLite (tests / init-fallback)
    still needs the bootstrap.
    """
    if is_pg(conn):
        return
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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS kg_ontology (
            id TEXT PRIMARY KEY,
            graph_id TEXT NOT NULL REFERENCES kg_graphs(id),
            subject_type TEXT NOT NULL,
            predicate TEXT NOT NULL,
            object_type TEXT NOT NULL,
            path_distance INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_kgo_subject ON kg_ontology(subject_type)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_kgo_object ON kg_ontology(object_type)
    """)
    conn.commit()


def _now() -> str:
    """ISO-8601 timestamp in UTC."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _query_hash(query: str) -> str:
    """SHA-256 hash of query for dedup/logging."""
    return hashlib.sha256(query.strip().lower().encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Ontology Helpers (D-ONTO-1)
# ---------------------------------------------------------------------------


def _load_ontology_relations(
    conn: Any,
    graph_ids: List[str],
    fallback: bool = True,
) -> Dict[str, List[Dict[str, Any]]]:
    """Load ontology relations for the given graphs.

    Queries ``kg_ontology`` and merges with the in-memory fallback hierarchy.
    Returns a dict mapping ``subject_type`` → list of relation dicts.

    Args:
        conn: Database connection.
        graph_ids: Graph IDs to scope the query.
        fallback: Whether to merge hardcoded fallback mappings when the
            DB table has no rows for a type.

    Returns:
        Dict of subject_type → [{"type": str, "predicate": str, "distance": int}, ...]
    """
    relations: Dict[str, List[Dict[str, Any]]] = {}

    if graph_ids:
        placeholders = ",".join("%s" for _ in graph_ids)
        try:
            rows = conn.execute(
                f"SELECT subject_type, predicate, object_type, path_distance FROM kg_ontology WHERE graph_id IN ({placeholders})",  # nosec B608 -- table/column names are internal constants, not user input
                list(graph_ids),
            ).fetchall()
            for r in rows:
                st = r["subject_type"].lower()
                entry: Dict[str, Any] = {
                    "type": r["object_type"].lower(),
                    "predicate": r["predicate"],
                    "distance": max(int(r["path_distance"] or 1), 1),
                }
                relations.setdefault(st, []).append(entry)
        except Exception:
            pass  # kg_ontology not available (PostgreSQL backend without migration)

    # Load subclass closure from the canonical connection (PostgreSQL or SQLite).
    try:
        closure_rows = conn.execute(
            "SELECT subclass, superclass, distance FROM ontology_subclass_closure"
        ).fetchall()
        for _row in closure_rows:
            sub = _row["subclass"].lower().replace(":", "_")
            sup = _row["superclass"].lower().replace(":", "_")
            dist = max(int(_row["distance"] or 1), 1)
            existing_sub = {(e["type"], e["predicate"]) for e in relations.get(sub, [])}
            if (sup, "rdfs:subClassOf") not in existing_sub:
                relations.setdefault(sub, []).append(
                    {"type": sup, "predicate": "rdfs:subClassOf", "distance": dist}
                )
            existing_sup = {(e["type"], e["predicate"]) for e in relations.get(sup, [])}
            if (sub, "rdfs:superClassOf") not in existing_sup:
                relations.setdefault(sup, []).append(
                    {"type": sub, "predicate": "rdfs:superClassOf", "distance": dist}
                )
    except Exception:
        pass  # ontology_subclass_closure not yet built — hardcoded fallback covers it

    if fallback:
        for st, entries in _ONTOLOGY_FALLBACK.items():
            st_lower = st.lower()
            existing = {(e["type"], e["predicate"]) for e in relations.get(st_lower, [])}
            for e in entries:
                key = (e["type"], e["predicate"])
                if key not in existing:
                    relations.setdefault(st_lower, []).append(dict(e))

    return relations


def _get_ontology_related_types(
    entity_types: set,
    relations: Dict[str, List[Dict[str, Any]]],
) -> set:
    """Return all entity types related to the given types via ontology.

    Args:
        entity_types: Set of entity type strings.
        relations: Ontology relations dict from :func:`_load_ontology_relations`.

    Returns:
        Set of related entity type strings.
    """
    related: set = set()
    for et in entity_types:
        et_lower = et.lower()
        for entry in relations.get(et_lower, []):
            related.add(entry["type"])
    return related


def _compute_ontology_bonus(
    node_entity_type: str,
    query_entity_types: set,
    relations: Dict[str, List[Dict[str, Any]]],
    profile: str,
) -> float:
    """Compute ontology-based score bonus for a node using shortest-path distance.

    Formula: sum over query entity types of
        ``1.0 / (1.0 + decay * shortest_distance)``,
    capped at 3.0, then scaled by the profile's ``ontology_weight``.

    Profile-specific emphasis:
        * compliance — extra +0.15 if node is linked to *nist_control*
          via ``satisfiesFramework``.
        * security — extra +0.15 if node is linked to *threat_actor*
          via ``hasActor``.
        * network_infrastructure — extra +0.15 if predicate is ``hasProvider``.

    Args:
        node_entity_type: The node's ``entity_type``.
        query_entity_types: Set of entity types derived from the query.
        relations: Ontology relations dict.
        profile: Scoring profile name.

    Returns:
        Float bonus in ``[0, ontology_weight + 0.3]``.
    """
    net = node_entity_type.lower()
    weights = SCORING_PROFILES.get(profile, SCORING_PROFILES["exploratory"])
    ontology_weight = weights.get("ontology_weight", 0.0)

    total_path_score = 0.0
    profile_extra = 0.0

    for qt in query_entity_types:
        dist = _ontology_shortest_distance(qt, net, relations)
        if dist is not None:
            total_path_score += 1.0 / (1.0 + ONTOLOGY_PATH_DISTANCE_DECAY * dist)
            # Profile-specific direct-relation boosts
            for entry in relations.get(qt.lower(), []):
                if entry["type"] == net:
                    pred = entry.get("predicate", "")
                    if profile == "compliance" and pred == "satisfiesFramework" and qt.lower() == "nist_control":
                        profile_extra = max(profile_extra, 0.15)
                    elif profile == "security" and pred == "hasActor" and qt.lower() == "threat_actor":
                        profile_extra = max(profile_extra, 0.15)
                    elif profile == "network_infrastructure" and pred == "hasProvider":
                        profile_extra = max(profile_extra, 0.15)
            # Reverse check
            for entry in relations.get(net, []):
                if entry["type"] == qt.lower():
                    pred = entry.get("predicate", "")
                    if profile == "compliance" and pred == "satisfiesFramework" and qt.lower() == "nist_control":
                        profile_extra = max(profile_extra, 0.15)
                    elif profile == "security" and pred == "hasActor" and qt.lower() == "threat_actor":
                        profile_extra = max(profile_extra, 0.15)
                    elif profile == "network_infrastructure" and pred == "hasProvider":
                        profile_extra = max(profile_extra, 0.15)

    # General profile bias for any node that carries the target predicate
    for entry in relations.get(net, []):
        pred = entry.get("predicate", "")
        if profile == "compliance" and pred == "satisfiesFramework" and "nist" in entry["type"]:
            profile_extra = max(profile_extra, 0.15)
        elif profile == "security" and pred == "hasActor" and entry["type"] in ("threat_actor", "security_object"):
            profile_extra = max(profile_extra, 0.15)
        elif profile == "network_infrastructure" and pred == "hasProvider":
            profile_extra = max(profile_extra, 0.15)

    bonus = ontology_weight * min(total_path_score, 3.0) + profile_extra
    return round(min(bonus, ontology_weight + 0.3), 6)


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

    # Pick highest; on tie, prefer ontology > compliance > security > provenance > exploratory
    priority = ["ontology", "compliance", "security", "provenance", "exploratory"]
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
    """Embed a query string via the configured embedding provider.

    Uses ``get_embedding_provider()`` — the same abstraction the enricher uses —
    so query and node vectors share one vector space regardless of environment
    (OpenAI-compatible, Gemini, Ollama, ...); it never hardcodes a provider. In
    no-embedding environments the provider raises ``LLMUnavailableError`` (or the
    call otherwise fails), so we return ``None`` and retrieval degrades to
    BM25/keyword + ontology instead of erroring.

    Args:
        query: Text to embed.

    Returns:
        List of floats (provider-dependent dimensionality), or None.
    """
    try:
        from tools.llm import get_embedding_provider

        provider = get_embedding_provider()
        if provider is None:
            return None
        embedding = provider.embed(query)
        if embedding and isinstance(embedding, list):
            return embedding
        return None
    except Exception as exc:  # LLMUnavailableError / ImportError / network / etc.
        logger.debug("Query embedding unavailable: %s", exc)
        return None


def _embedding_to_pg_vector(embedding: List[float]) -> str:
    """Serialize a float list to a pgvector literal ``'[x,y,...]'``."""
    return "[" + ",".join(f"{v:.6f}" for v in embedding) + "]"


def _semantic_candidates(
    conn: Any,
    graph_ids: List[str],
    placeholders: str,
    query_embedding: Optional[List[float]],
    query_embedding_blob: Optional[bytes],
    limit: int,
) -> List[tuple]:
    """Return the top-``limit`` nodes by embedding similarity to the query.

    PostgreSQL: cosine distance is computed **in the database** via pgvector's
    ``<=>`` operator against ``kg_nodes.embedding_vec``; only ``limit`` rows plus
    their similarity leave the DB, instead of streaming every embedding BLOB to
    Python and looping cosine there. A ``vector_dims`` guard drops rows whose
    stored vector dimensionality differs from the query's, so a provider /
    dimension change never errors the query (those rows are simply skipped).

    SQLite (tests / init-fallback): pgvector is unavailable, so fall back to the
    packed-float32 ``embedding`` BLOBs and score with Python cosine.

    Returns ``(similarity, node_dict)`` tuples, highest first; the node dicts
    never carry a raw embedding column.
    """
    if is_pg(conn) and query_embedding:
        vec_str = _embedding_to_pg_vector(query_embedding)
        query_dim = len(query_embedding)
        sql = f"""
            SELECT id, graph_id, label, entity_type, properties,
                   centrality, created_at,
                   1 - (embedding_vec <=> %s::vector) AS _sim
            FROM kg_nodes
            WHERE graph_id IN ({placeholders})
              AND embedding_vec IS NOT NULL
              AND vector_dims(embedding_vec) = %s
            ORDER BY embedding_vec <=> %s::vector
            LIMIT %s
        """  # nosec B608 -- table/column names are internal constants, not user input
        params = [vec_str] + list(graph_ids) + [query_dim, vec_str, limit]
        rows = conn.execute(sql, params).fetchall()
        out: List[tuple] = []
        for r in rows:
            d = dict(r)
            sim = float(d.pop("_sim", 0.0) or 0.0)
            out.append((sim, d))
        return out

    # SQLite / no-pgvector fallback: Python cosine over packed-float32 BLOBs.
    if query_embedding_blob is None:
        return []
    emb_sql = f"""
        SELECT id, graph_id, label, entity_type, properties,
               centrality, created_at, embedding
        FROM kg_nodes
        WHERE graph_id IN ({placeholders})
          AND embedding IS NOT NULL
    """  # nosec B608 -- table/column names are internal constants, not user input
    emb_rows = conn.execute(emb_sql, list(graph_ids)).fetchall()
    sim_scored: List[tuple] = []
    for row in emb_rows:
        d = dict(row)
        blob = d.pop("embedding", None)
        if blob is None:
            continue
        sim_scored.append((_cosine_similarity(query_embedding_blob, bytes(blob)), d))
    sim_scored.sort(key=lambda x: x[0], reverse=True)
    return sim_scored[:limit]


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


_RRF_K = 60  # RRF constant — larger values reduce the impact of top-rank gaps


def _score_nodes(
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, Any]],
    profile: str,
    query_terms: List[str],
    neighbor_ids: Optional[set] = None,
    ontology_relations: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    query_entity_types: Optional[set] = None,
) -> List[Dict[str, Any]]:
    """Score and rank nodes using profile weights, neighbor boosting, RRF fusion,
    and ontology-aware hierarchy bonuses.

    Score formula:
        score = w_edge * avg_edge_weight
              + w_centrality * centrality
              + w_recency * recency_score
              + relevance_bonus   (keyword hit, up to +0.3)
              + embedding_bonus   (semantic similarity, up to +0.2)
              + neighbor_boost    (+0.3 if node is a 1-hop neighbor of a matched node)
              + ontology_bonus    (hierarchy proximity, profile-scaled)

    hybrid_rank is a Reciprocal Rank Fusion (RRF) of BM25 and semantic rankings,
    with the BM25 score pre-boosted by +0.3 for neighbor nodes.

    Args:
        nodes: List of node dicts with id, label, entity_type,
               centrality, created_at, properties.
        edges: List of edge dicts with source_id, target_id, weight.
        profile: Scoring profile name.
        query_terms: Lowercased query tokens.
        neighbor_ids: Set of node IDs that are 1-hop neighbors of matched nodes
                      (not directly keyword/semantic matched). These receive a
                      +0.3 score boost and a pre-boosted BM25 rank for RRF.
        ontology_relations: Ontology relations dict from :func:`_load_ontology_relations`.
        query_entity_types: Set of entity types derived from the query.

    Returns:
        Nodes sorted by score descending, each augmented with 'score',
        'hybrid_rank', 'is_neighbor', and 'ontology_bonus'.
    """
    weights = SCORING_PROFILES.get(profile, SCORING_PROFILES["exploratory"])
    w_edge = weights["edge_weight"]
    w_centrality = weights["centrality"]
    w_recency = weights["recency"]

    _neighbor_ids = neighbor_ids or set()

    # Build per-node average edge weight
    node_edge_weights: Dict[str, List[float]] = {}
    for edge in edges:
        src = edge.get("source_id", "")
        tgt = edge.get("target_id", "")
        w = float(edge.get("weight", 1.0))
        node_edge_weights.setdefault(src, []).append(w)
        node_edge_weights.setdefault(tgt, []).append(w)

    # Compute per-node BM25-like and semantic scores for RRF
    bm25_scores: Dict[str, float] = {}
    semantic_scores_map: Dict[str, float] = {}
    for node in nodes:
        nid = node.get("id", "")
        label = (node.get("label", "") or "").lower()
        entity_type = (node.get("entity_type", "") or "").lower()
        props_str = node.get("properties", "{}")
        searchable = f"{label} {entity_type} {props_str}".lower()

        # BM25-like term-frequency score; neighbor nodes get +0.3 pre-boost
        tf_score = float(sum(searchable.count(t) for t in query_terms)) if query_terms else 0.0
        if nid in _neighbor_ids:
            tf_score += 0.3
        bm25_scores[nid] = tf_score

        sem = node.get("_embedding_sim")
        semantic_scores_map[nid] = float(sem) if sem is not None else 0.0

    # Build RRF scores: 1/(k + bm25_rank) + 1/(k + semantic_rank)
    bm25_ranked = sorted(nodes, key=lambda n: bm25_scores.get(n.get("id", ""), 0.0), reverse=True)
    sem_ranked = sorted(nodes, key=lambda n: semantic_scores_map.get(n.get("id", ""), 0.0), reverse=True)

    rrf_scores: Dict[str, float] = {n.get("id", ""): 0.0 for n in nodes}
    for rank, node in enumerate(bm25_ranked, 1):
        nid = node.get("id", "")
        rrf_scores[nid] = rrf_scores.get(nid, 0.0) + 1.0 / (_RRF_K + rank)
    for rank, node in enumerate(sem_ranked, 1):
        nid = node.get("id", "")
        rrf_scores[nid] = rrf_scores.get(nid, 0.0) + 1.0 / (_RRF_K + rank)

    # Normalize RRF scores to [0, 1]
    max_rrf = max(rrf_scores.values()) if rrf_scores else 1.0
    if max_rrf > 0:
        rrf_scores = {k: v / max_rrf for k, v in rrf_scores.items()}

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

        # Neighbor boost: +0.3 for nodes reachable via 1-hop from matched nodes
        neighbor_boost = 0.3 if nid in _neighbor_ids else 0.0

        # Ontology bonus: boost nodes related via class hierarchy
        ontology_bonus = 0.0
        ontology_path_distance = None
        if ontology_relations and query_entity_types:
            ontology_bonus = _compute_ontology_bonus(
                entity_type, query_entity_types, ontology_relations, profile
            )
            distances = [
                d
                for qt in query_entity_types
                if (d := _ontology_shortest_distance(entity_type, qt, ontology_relations)) is not None
            ]
            if distances:
                ontology_path_distance = min(distances)

        node_copy = dict(node)
        node_copy["score"] = round(base_score + relevance_bonus + embedding_bonus + neighbor_boost + ontology_bonus, 6)
        node_copy["hybrid_rank"] = round(rrf_scores.get(nid, 0.0), 6)
        node_copy["is_neighbor"] = nid in _neighbor_ids
        node_copy["ontology_bonus"] = ontology_bonus
        node_copy["ontology_path_distance"] = ontology_path_distance
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
    conn: Any,
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
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
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
                "SELECT id FROM kg_graphs WHERE id = %s",
                (graph_id,),
            ).fetchall()
        elif project_id:
            graphs = conn.execute(
                "SELECT id FROM kg_graphs WHERE project_id = %s",
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
        placeholders = ",".join("%s" for _ in graph_ids)

        if query_terms:
            # Search label and properties for query terms
            like_clauses = []
            like_params: List[str] = []
            for term in query_terms:
                # Match label, properties, AND entity_type so that NL queries
                # like "firewall" hit nodes whose label is abbreviated
                # (NYC-FWLL-01) but whose entity_type is 'network_firewall'.
                like_clauses.append(
                    "(LOWER(label) LIKE %s OR LOWER(properties) LIKE %s OR LOWER(entity_type) LIKE %s)"
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
                LIMIT %s
            """  # nosec B608 -- table/column names are internal constants, not user input
            rows = conn.execute(sql, list(graph_ids) + [top_k * 3]).fetchall()
            matched_nodes = [dict(r) for r in rows]

        # Semantic search: augment keyword results with embedding similarity.
        # On PG this runs pgvector <=> in-database (only the top rows return);
        # on SQLite it falls back to Python cosine over BLOBs. See
        # _semantic_candidates.
        if query_embedding is not None:
            semantic_search_used = True
            matched_id_set_kw = {n["id"] for n in matched_nodes}
            sem_limit = top_k * 3

            for sim, node_dict in _semantic_candidates(
                conn,
                graph_ids,
                placeholders,
                query_embedding,
                query_embedding_blob,
                sem_limit,
            ):
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

        # Ontology-aware expansion: fetch sibling nodes for profiles with
        # ontology_weight > 0.  This ensures "Find all load balancers"
        # returns AWS ALB, Azure AppGW, GCP LB even when only the parent
        # class or one sibling was keyword-matched.
        query_entity_types = {n.get("entity_type", "").lower() for n in matched_nodes if n.get("entity_type")}
        ontology_relations = _load_ontology_relations(conn, graph_ids)
        selected_weights = SCORING_PROFILES.get(selected_profile, SCORING_PROFILES["exploratory"])
        if selected_weights.get("ontology_weight", 0.0) > 0:
            related_types = _get_ontology_related_types(query_entity_types, ontology_relations)
            if related_types:
                new_types = related_types - query_entity_types
                if new_types:
                    graph_ph = ",".join("%s" for _ in graph_ids)
                    type_ph = ",".join("%s" for _ in new_types)
                    type_sql = f"""
                        SELECT id, graph_id, label, entity_type, properties,
                               centrality, created_at
                        FROM kg_nodes
                        WHERE graph_id IN ({graph_ph})
                          AND LOWER(entity_type) IN ({type_ph})
                        LIMIT %s
                    """  # nosec B608 -- table/column names are internal constants, not user input
                    type_params = list(graph_ids) + list(new_types) + [top_k * 3]
                    type_rows = conn.execute(type_sql, type_params).fetchall()
                    existing_ids = {n["id"] for n in matched_nodes}
                    for r in type_rows:
                        node = dict(r)
                        if node["id"] not in existing_ids:
                            matched_nodes.append(node)
                            existing_ids.add(node["id"])

        # Step 3: Expand to 1-hop neighborhood
        matched_ids = [n["id"] for n in matched_nodes]
        id_placeholders = ",".join("%s" for _ in matched_ids)

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
            nbr_placeholders = ",".join("%s" for _ in neighbor_ids)
            nbr_sql = f"""
                SELECT id, graph_id, label, entity_type, properties,
                       centrality, created_at
                FROM kg_nodes
                WHERE id IN ({nbr_placeholders})
            """  # nosec B608 -- table/column names are internal constants, not user input
            nbr_rows = conn.execute(nbr_sql, list(neighbor_ids)).fetchall()
            for r in nbr_rows:
                matched_nodes.append(dict(r))

        # Step 4: Score nodes (pass neighbor_ids for +0.3 boost and RRF hybrid_rank)
        scored_nodes = _score_nodes(
            matched_nodes,
            all_edges,
            selected_profile,
            query_terms,
            neighbor_ids=neighbor_ids,
            ontology_relations=ontology_relations,
            query_entity_types=query_entity_types,
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
        # Sanitize nodes for JSON serialization (drop raw binary / internal keys).
        # hybrid_rank and is_neighbor are computed fields — keep them.
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
            "neighbor_count": len(neighbor_ids),
            "rrf_fusion": True,
            "ontology_expansion": selected_weights.get("ontology_weight", 0.0) > 0,
            "query_entity_types": sorted(query_entity_types),
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
                "SELECT id FROM kg_graphs WHERE id = %s", (graph_id,)
            ).fetchall()
        elif project_id:
            scope_rows = conn.execute(
                "SELECT id FROM kg_graphs WHERE project_id = %s", (project_id,)
            ).fetchall()
        else:
            scope_rows = conn.execute("SELECT id FROM kg_graphs").fetchall()

        scope_ids = [dict(r)["id"] for r in scope_rows]

        expansions: Dict[str, List[str]] = {hid: [] for hid in hit_ids}
        hit_set = set(hit_ids)

        if scope_ids:
            scope_ph = ",".join("%s" for _ in scope_ids)
            id_ph = ",".join("%s" for _ in hit_ids)
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
