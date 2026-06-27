#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""Cross-project Knowledge Graph Federation.

Enables querying, comparing, and merging knowledge graphs across multiple
ICDEV™ projects.  Solves the single-project KG scope limitation by providing
federated search, shared-entity discovery, virtual federated views, and
cross-project compliance coverage analysis.

Functions:
    federated_search   — Search across multiple project graphs simultaneously
    find_shared_entities — Find entities appearing in both projects' graphs
    create_federated_view — Create a virtual graph unioning multiple projects
    cross_project_coverage — Compliance control coverage across projects

Usage:
    python tools/knowledge_graph/federation.py --search "zero trust" --json
    python tools/knowledge_graph/federation.py --search "AC-2" --projects proj-a,proj-b --json
    python tools/knowledge_graph/federation.py --shared proj-a proj-b --json
    python tools/knowledge_graph/federation.py --create-view "all-projects" --projects proj-a,proj-b,proj-c --json
    python tools/knowledge_graph/federation.py --coverage fedramp --json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# =========================================================================
# PATH SETUP
# =========================================================================
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402

# =========================================================================
# HELPERS
# =========================================================================


# ── Ontology alignments (Strategos + GeoSIGINT cross-domain) ──────────────────
ONTOLOGY_ALIGNMENTS: List[Dict[str, str]] = [
    {"source": "war:MilitaryUnit", "target": "geospatial:GeoEntity", "relation": "hasLocation", "assertion": "owl:equivalentClass"},
    {"source": "war:Equipment", "target": "geospatial:WeaponSystem", "relation": "subclassOf", "assertion": "owl:subClassOf"},
    {"source": "strategy:CourseOfAction", "target": "war:MilitaryUnit", "relation": "uses", "assertion": "owl:equivalentClass"},
    {"source": "geospatial:A2ADZone", "target": "security:SecurityBoundary", "relation": "subclassOf", "assertion": "owl:subClassOf"},
    {"source": "geospatial:LandingZone", "target": "strategy:AmphibiousOperation", "relation": "hasLandingZone", "assertion": "owl:equivalentClass"},
    {"source": "war:Vessel", "target": "geospatial:MaritimeMilitiaVessel", "relation": "subclassOf", "assertion": "owl:subClassOf"},
]


def _now() -> str:
    """ISO-8601 timestamp in UTC."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _gen_id(prefix: str, *parts: str) -> str:
    """Generate a deterministic ID from prefix + parts."""
    raw = "|".join(parts)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{digest}"


def _normalize_label(label: str) -> str:
    """Normalize a label for fuzzy matching.

    Lowercases, strips whitespace, removes accents, collapses runs of
    non-alphanumeric characters to a single dash.
    """
    text = label.strip().lower()
    # Remove accents
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    # Collapse non-alnum to dash
    out: list[str] = []
    prev_dash = False
    for c in text:
        if c.isalnum():
            out.append(c)
            prev_dash = False
        elif not prev_dash:
            out.append("-")
            prev_dash = True
    return "".join(out).strip("-")


def _get_db(db_path: Optional[str] = None):
    """Get database connection."""
    if db_path:
        conn = get_connection(db_path=db_path)
    else:
        conn = get_connection()
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _ensure_tables(conn) -> None:
    """Ensure kg tables exist (idempotent)."""
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


def _resolve_graph_ids(conn, project_ids: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """Resolve project IDs to graph records.

    Args:
        conn: DB connection.
        project_ids: Optional list of project IDs.  None = all graphs.

    Returns:
        List of graph row dicts with id, project_id, name, metadata.
    """
    if project_ids:
        placeholders = ",".join("?" for _ in project_ids)
        rows = conn.execute(
            f"SELECT id, project_id, name, metadata FROM kg_graphs "  # nosec B608 -- table/column names are internal constants, not user input
            f"WHERE project_id IN ({placeholders})",
            project_ids,
        ).fetchall()
    else:
        rows = conn.execute("SELECT id, project_id, name, metadata FROM kg_graphs").fetchall()
    return [dict(r) for r in rows]


def _resolve_source_graph_ids(conn, federated_meta: Dict) -> List[str]:
    """Given federated graph metadata, return the list of source graph IDs.

    For a federated view the metadata contains ``source_graph_ids``.
    """
    return federated_meta.get("source_graph_ids", [])


# =========================================================================
# 1. FEDERATED SEARCH
# =========================================================================


def federated_search(
    query: str,
    project_ids: Optional[List[str]] = None,
    profile: Optional[str] = None,
    top_k: int = 10,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Search across multiple project knowledge graphs simultaneously.

    Delegates per-graph retrieval to ``graph_rag.retrieve()`` (reusing
    scoring profiles, semantic search, and 1-hop expansion), then merges
    and deduplicates results across graphs.

    Deduplication: nodes with the same (label, entity_type) after
    normalisation are merged, keeping the highest score and collecting
    source projects.

    Args:
        query: Search query text.
        project_ids: Project IDs to search.  None = all projects.
        profile: Scoring profile (auto-detected if None).
        top_k: Maximum nodes to return after merge.
        db_path: Optional database path override.

    Returns:
        Dict with status, merged results, per-project stats, context.
    """
    start_ms = int(time.time() * 1000)

    conn = _get_db(db_path)
    try:
        _ensure_tables(conn)
        graphs = _resolve_graph_ids(conn, project_ids)

        if not graphs:
            return {
                "status": "ok",
                "query": query,
                "projects_searched": 0,
                "nodes_returned": 0,
                "results": [],
                "per_project": {},
                "retrieval_ms": int(time.time() * 1000) - start_ms,
            }

        # Group graphs by project_id for per-project retrieval
        project_graph_map: Dict[str, List[str]] = defaultdict(list)
        for g in graphs:
            pid = g.get("project_id") or g["id"]
            project_graph_map[pid].append(g["id"])
    finally:
        conn.close()

    # Use graph_rag.retrieve() per project (it handles its own connection)
    from tools.knowledge_graph.graph_rag import retrieve as _rag_retrieve

    per_project: Dict[str, Dict[str, Any]] = {}
    all_nodes: List[Dict[str, Any]] = []

    for pid, gids in project_graph_map.items():
        result = _rag_retrieve(
            query=query,
            project_id=pid,
            profile=profile,
            top_k=top_k * 2,  # over-fetch so merge has candidates
            compress=False,  # we merge raw; caller can compress later
        )
        per_project[pid] = {
            "nodes_matched": result.get("nodes_matched", 0),
            "nodes_returned": result.get("nodes_returned", 0),
            "edges_returned": result.get("edges_returned", 0),
            "profile": result.get("profile", "exploratory"),
        }

        # Parse context back to structured nodes for merge
        # Since retrieve() returns formatted text, we also query raw nodes
        conn2 = _get_db(db_path)
        try:
            _ensure_tables(conn2)
            placeholders = ",".join("?" for _ in gids)
            query_terms = [t.lower() for t in query.split() if len(t) >= 2]

            if query_terms:
                like_clauses = []
                like_params: list = []
                for term in query_terms:
                    like_clauses.append("(LOWER(label) LIKE ? OR LOWER(properties) LIKE ?)")
                    like_params.extend([f"%{term}%", f"%{term}%"])
                where_likes = " OR ".join(like_clauses)
                sql = (
                    f"SELECT n.id, n.graph_id, n.label, n.entity_type, "  # nosec B608 -- table/column names are internal constants, not user input
                    f"n.properties, n.centrality, n.created_at, g.project_id "
                    f"FROM kg_nodes n JOIN kg_graphs g ON n.graph_id = g.id "
                    f"WHERE n.graph_id IN ({placeholders}) "
                    f"AND ({where_likes})"
                )
                params: list = list(gids) + like_params
            else:
                sql = (
                    f"SELECT n.id, n.graph_id, n.label, n.entity_type, "  # nosec B608 -- table/column names are internal constants, not user input
                    f"n.properties, n.centrality, n.created_at, g.project_id "
                    f"FROM kg_nodes n JOIN kg_graphs g ON n.graph_id = g.id "
                    f"WHERE n.graph_id IN ({placeholders}) "
                    f"ORDER BY n.centrality DESC LIMIT ?"
                )
                params = list(gids) + [top_k * 3]

            rows = conn2.execute(sql, params).fetchall()
            for r in rows:
                node = dict(r)
                node["source_project"] = node.pop("project_id", pid)
                all_nodes.append(node)
        finally:
            conn2.close()

    # Deduplicate by normalised (label, entity_type)
    merged: Dict[str, Dict[str, Any]] = {}
    for node in all_nodes:
        key = (
            _normalize_label(node.get("label", "")),
            (node.get("entity_type", "") or "").lower(),
        )
        if key not in merged:
            merged[key] = {
                "label": node.get("label", ""),
                "entity_type": node.get("entity_type", ""),
                "centrality": float(node.get("centrality", 0.0)),
                "properties": node.get("properties", "{}"),
                "source_projects": [node.get("source_project", "unknown")],
                "node_ids": [node.get("id", "")],
                "created_at": node.get("created_at", ""),
            }
        else:
            existing = merged[key]
            src = node.get("source_project", "unknown")
            if src not in existing["source_projects"]:
                existing["source_projects"].append(src)
            existing["node_ids"].append(node.get("id", ""))
            # Keep highest centrality
            c = float(node.get("centrality", 0.0))
            if c > existing["centrality"]:
                existing["centrality"] = c

    # Score: prefer nodes appearing in more projects + higher centrality
    scored = []
    for key, entry in merged.items():
        breadth = len(entry["source_projects"])
        score = round(entry["centrality"] * 0.6 + breadth * 0.4, 6)
        entry["score"] = score
        scored.append(entry)

    scored.sort(key=lambda x: x["score"], reverse=True)
    top_results = scored[:top_k]

    elapsed_ms = int(time.time() * 1000) - start_ms

    return {
        "status": "ok",
        "query": query,
        "projects_searched": len(project_graph_map),
        "nodes_returned": len(top_results),
        "results": top_results,
        "per_project": per_project,
        "retrieval_ms": elapsed_ms,
    }


# =========================================================================
# 2. FIND SHARED ENTITIES
# =========================================================================


def find_shared_entities(
    project_id_a: str,
    project_id_b: str,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Find entities that appear in both projects' knowledge graphs.

    Matching strategies:
        1. Exact label match (case-insensitive)
        2. Normalised label match (accent-stripped, whitespace-collapsed)

    Args:
        project_id_a: First project ID.
        project_id_b: Second project ID.
        db_path: Optional database path override.

    Returns:
        Dict with shared entities, per-project properties, and edge counts.
    """
    conn = _get_db(db_path)
    try:
        _ensure_tables(conn)

        # Get graph IDs for each project
        graphs_a = conn.execute(
            "SELECT id FROM kg_graphs WHERE project_id = %s",
            (project_id_a,),
        ).fetchall()
        graphs_b = conn.execute(
            "SELECT id FROM kg_graphs WHERE project_id = %s",
            (project_id_b,),
        ).fetchall()

        gids_a = [g["id"] for g in graphs_a]
        gids_b = [g["id"] for g in graphs_b]

        if not gids_a or not gids_b:
            return {
                "status": "ok",
                "project_a": project_id_a,
                "project_b": project_id_b,
                "shared_count": 0,
                "shared_entities": [],
                "message": (f"No graphs found for {'project A' if not gids_a else 'project B'}"),
            }

        def _fetch_nodes(gids: List[str]) -> List[Dict[str, Any]]:
            ph = ",".join("?" for _ in gids)
            rows = conn.execute(
                f"SELECT id, graph_id, label, entity_type, properties, "  # nosec B608 -- table/column names are internal constants, not user input
                f"centrality FROM kg_nodes WHERE graph_id IN ({ph})",
                gids,
            ).fetchall()
            return [dict(r) for r in rows]

        def _count_edges(node_id: str) -> int:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM kg_edges WHERE source_id = %s OR target_id = %s",
                (node_id, node_id),
            ).fetchone()
            return row["cnt"] if row else 0

        nodes_a = _fetch_nodes(gids_a)
        nodes_b = _fetch_nodes(gids_b)

        # Build lookup maps: normalised label -> list of nodes
        def _build_map(nodes):
            exact_map: Dict[str, List[Dict]] = defaultdict(list)
            norm_map: Dict[str, List[Dict]] = defaultdict(list)
            for n in nodes:
                label = n.get("label", "")
                exact_map[label.lower()].append(n)
                norm_map[_normalize_label(label)].append(n)
            return exact_map, norm_map

        exact_a, norm_a = _build_map(nodes_a)
        exact_b, norm_b = _build_map(nodes_b)

        shared_entities: List[Dict[str, Any]] = []
        seen_pairs: set = set()

        # Strategy 1: Exact label match
        for label_lower, a_nodes in exact_a.items():
            if label_lower in exact_b:
                b_nodes = exact_b[label_lower]
                for na in a_nodes:
                    for nb in b_nodes:
                        pair_key = (na["id"], nb["id"])
                        if pair_key in seen_pairs:
                            continue
                        seen_pairs.add(pair_key)
                        shared_entities.append(
                            {
                                "label": na.get("label", ""),
                                "entity_type_a": na.get("entity_type", ""),
                                "entity_type_b": nb.get("entity_type", ""),
                                "match_type": "exact",
                                "node_id_a": na["id"],
                                "node_id_b": nb["id"],
                                "centrality_a": round(float(na.get("centrality", 0)), 4),
                                "centrality_b": round(float(nb.get("centrality", 0)), 4),
                                "edge_count_a": _count_edges(na["id"]),
                                "edge_count_b": _count_edges(nb["id"]),
                                "properties_a": na.get("properties", "{}"),
                                "properties_b": nb.get("properties", "{}"),
                            }
                        )

        # Strategy 2: Normalised label match (skip already-found exact)
        for norm_label, a_nodes in norm_a.items():
            if not norm_label:
                continue
            if norm_label in norm_b:
                b_nodes = norm_b[norm_label]
                for na in a_nodes:
                    for nb in b_nodes:
                        pair_key = (na["id"], nb["id"])
                        if pair_key in seen_pairs:
                            continue
                        seen_pairs.add(pair_key)
                        shared_entities.append(
                            {
                                "label": na.get("label", ""),
                                "label_b": nb.get("label", ""),
                                "entity_type_a": na.get("entity_type", ""),
                                "entity_type_b": nb.get("entity_type", ""),
                                "match_type": "normalized",
                                "node_id_a": na["id"],
                                "node_id_b": nb["id"],
                                "centrality_a": round(float(na.get("centrality", 0)), 4),
                                "centrality_b": round(float(nb.get("centrality", 0)), 4),
                                "edge_count_a": _count_edges(na["id"]),
                                "edge_count_b": _count_edges(nb["id"]),
                                "properties_a": na.get("properties", "{}"),
                                "properties_b": nb.get("properties", "{}"),
                            }
                        )

        # Sort by combined centrality descending
        shared_entities.sort(
            key=lambda e: e["centrality_a"] + e["centrality_b"],
            reverse=True,
        )

        return {
            "status": "ok",
            "project_a": project_id_a,
            "project_b": project_id_b,
            "nodes_in_a": len(nodes_a),
            "nodes_in_b": len(nodes_b),
            "shared_count": len(shared_entities),
            "shared_entities": shared_entities,
        }
    finally:
        conn.close()


# =========================================================================
# 3. CREATE FEDERATED VIEW
# =========================================================================


def create_federated_view(
    name: str,
    project_ids: List[str],
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a virtual federated graph that unions nodes/edges from
    multiple project graphs.

    No data is copied.  A new ``kg_graphs`` entry is created with
    ``graph_type: 'federated'`` in its metadata, along with the list
    of source graph IDs.  At read time the source graphs are queried
    dynamically.

    Args:
        name: Human-readable name for the federated view.
        project_ids: Project IDs whose graphs should be included.
        db_path: Optional database path override.

    Returns:
        Dict with the federated graph ID and combined stats.
    """
    conn = _get_db(db_path)
    try:
        _ensure_tables(conn)

        # Resolve source graphs
        graphs = _resolve_graph_ids(conn, project_ids)
        if not graphs:
            return {
                "status": "error",
                "message": "No graphs found for the specified project IDs",
            }

        source_graph_ids = [g["id"] for g in graphs]
        source_projects = list({g.get("project_id") or g["id"] for g in graphs})

        # Compute combined stats without copying
        total_nodes = 0
        total_edges = 0
        for gid in source_graph_ids:
            n_row = conn.execute(
                "SELECT COUNT(*) as cnt FROM kg_nodes WHERE graph_id = %s",
                (gid,),
            ).fetchone()
            e_row = conn.execute(
                "SELECT COUNT(*) as cnt FROM kg_edges WHERE graph_id = %s",
                (gid,),
            ).fetchone()
            total_nodes += n_row["cnt"] if n_row else 0
            total_edges += e_row["cnt"] if e_row else 0

        fed_id = _gen_id("fed", name, *sorted(source_graph_ids))
        now = _now()
        metadata = json.dumps(
            {
                "graph_type": "federated",
                "source_graph_ids": source_graph_ids,
                "source_projects": source_projects,
                "created_by": "federation.py",
            }
        )

        # Upsert: if a view with the same ID already exists, update it
        existing = conn.execute("SELECT id FROM kg_graphs WHERE id = %s", (fed_id,)).fetchone()

        if existing:
            conn.execute(
                "UPDATE kg_graphs SET name = %s, entity_count = %s, "
                "edge_count = %s, metadata = %s, updated_at = %s WHERE id = %s",
                (name, total_nodes, total_edges, metadata, now, fed_id),
            )
        else:
            conn.execute(
                "INSERT INTO kg_graphs "
                "(id, project_id, name, description, entity_count, "
                "edge_count, metadata, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    fed_id,
                    None,  # federated views have no single project
                    name,
                    f"Federated view across {len(source_projects)} projects",
                    total_nodes,
                    total_edges,
                    metadata,
                    now,
                    now,
                ),
            )
        conn.commit()

        return {
            "status": "ok",
            "federated_graph_id": fed_id,
            "name": name,
            "source_graph_ids": source_graph_ids,
            "source_projects": source_projects,
            "total_nodes": total_nodes,
            "total_edges": total_edges,
        }
    finally:
        conn.close()


# =========================================================================
# 4. CROSS-PROJECT COVERAGE
# =========================================================================

# Common compliance frameworks and their control-entity-type mappings
_FRAMEWORK_ENTITY_TYPES: Dict[str, List[str]] = {
    "fedramp": ["control", "nist_control", "fedramp_control", "security_control"],
    "cmmc": ["cmmc_practice", "cmmc_control", "control"],
    "nist": ["nist_control", "control", "security_control"],
    "stig": ["stig_rule", "stig_check", "control"],
    "hipaa": ["hipaa_control", "control"],
}


def cross_project_coverage(
    framework: str,
    project_ids: Optional[List[str]] = None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Check which compliance controls are covered by which projects.

    Searches for nodes whose ``entity_type`` matches known control types
    for the specified framework.  Groups controls by project and computes
    a coverage matrix.

    Args:
        framework: Compliance framework name (fedramp, cmmc, nist, stig, hipaa).
        project_ids: Projects to include.  None = all.
        db_path: Optional database path override.

    Returns:
        Dict with per-project coverage, shared controls, and gap list.
    """
    framework_lower = framework.lower()
    entity_types = _FRAMEWORK_ENTITY_TYPES.get(
        framework_lower,
        ["control", f"{framework_lower}_control"],
    )

    conn = _get_db(db_path)
    try:
        _ensure_tables(conn)
        graphs = _resolve_graph_ids(conn, project_ids)

        if not graphs:
            return {
                "status": "ok",
                "framework": framework,
                "projects_checked": 0,
                "per_project": {},
                "all_controls": [],
                "shared_controls": [],
                "unique_to_project": {},
            }

        # Group graphs by project
        project_graph_map: Dict[str, List[str]] = defaultdict(list)
        for g in graphs:
            pid = g.get("project_id") or g["id"]
            project_graph_map[pid].append(g["id"])

        # Collect controls per project
        project_controls: Dict[str, set] = {}
        control_details: Dict[str, Dict[str, Any]] = {}

        type_placeholders = ",".join("?" for _ in entity_types)

        for pid, gids in project_graph_map.items():
            gid_ph = ",".join("?" for _ in gids)
            sql = (
                f"SELECT n.id, n.label, n.entity_type, n.centrality, "  # nosec B608 -- table/column names are internal constants, not user input
                f"n.properties, g.project_id "
                f"FROM kg_nodes n JOIN kg_graphs g ON n.graph_id = g.id "
                f"WHERE n.graph_id IN ({gid_ph}) "
                f"AND LOWER(n.entity_type) IN ({type_placeholders})"
            )
            params = list(gids) + [t.lower() for t in entity_types]
            rows = conn.execute(sql, params).fetchall()

            controls: set = set()
            for r in rows:
                label = r["label"]
                norm = _normalize_label(label)
                controls.add(norm)
                if norm not in control_details:
                    control_details[norm] = {
                        "label": label,
                        "entity_type": r["entity_type"],
                    }
            project_controls[pid] = controls

        # All controls across all projects
        all_controls_set: set = set()
        for ctrls in project_controls.values():
            all_controls_set |= ctrls

        # Shared controls (in 2+ projects)
        control_project_count: Dict[str, List[str]] = defaultdict(list)
        for pid, ctrls in project_controls.items():
            for c in ctrls:
                control_project_count[c].append(pid)

        shared = [
            {
                "control": control_details.get(c, {}).get("label", c),
                "normalized": c,
                "covered_by": pids,
            }
            for c, pids in control_project_count.items()
            if len(pids) > 1
        ]
        shared.sort(key=lambda x: len(x["covered_by"]), reverse=True)

        # Per-project summary
        per_project: Dict[str, Dict[str, Any]] = {}
        unique_to: Dict[str, List[str]] = {}
        for pid, ctrls in project_controls.items():
            unique = (
                ctrls - set().union(*(project_controls[p] for p in project_controls if p != pid))
                if len(project_controls) > 1
                else ctrls
            )
            per_project[pid] = {
                "total_controls": len(ctrls),
                "unique_controls": len(unique),
                "coverage_pct": round(len(ctrls) / len(all_controls_set) * 100, 1) if all_controls_set else 0.0,
            }
            unique_to[pid] = sorted([control_details.get(c, {}).get("label", c) for c in unique])

        return {
            "status": "ok",
            "framework": framework,
            "projects_checked": len(project_graph_map),
            "total_controls_found": len(all_controls_set),
            "per_project": per_project,
            "shared_controls": shared,
            "unique_to_project": unique_to,
            "all_controls": sorted([control_details.get(c, {}).get("label", c) for c in all_controls_set]),
        }
    finally:
        conn.close()


# =========================================================================
# 5. ONTOLOGY ASSERTIONS
# =========================================================================


def store_ontology_assertions(
    graph_id: str,
    assertions: Optional[List[Dict[str, str]]] = None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Store OWL ontology alignment assertions in a graph's metadata.

    Args:
        graph_id: Target kg_graphs ID.
        assertions: List of assertion dicts. Defaults to ONTOLOGY_ALIGNMENTS.
        db_path: Optional database path override.

    Returns:
        Dict with status and stored assertions.
    """
    conn = _get_db(db_path)
    try:
        _ensure_tables(conn)
        row = conn.execute(
            "SELECT metadata FROM kg_graphs WHERE id = %s", (graph_id,)
        ).fetchone()
        if not row:
            return {"status": "error", "message": f"Graph {graph_id} not found"}

        meta: Dict[str, Any] = json.loads(row["metadata"] or "{}")
        to_store = assertions if assertions is not None else ONTOLOGY_ALIGNMENTS
        meta["ontology_assertions"] = to_store
        meta["ontology_updated_at"] = _now()

        conn.execute(
            "UPDATE kg_graphs SET metadata = %s, updated_at = %s WHERE id = %s",
            (json.dumps(meta), _now(), graph_id),
        )
        conn.commit()
        return {
            "status": "ok",
            "graph_id": graph_id,
            "assertions_stored": len(to_store),
            "assertions": to_store,
        }
    finally:
        conn.close()


def get_ontology_assertions(
    graph_id: str,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Retrieve stored ontology assertions for a graph.

    Args:
        graph_id: Target kg_graphs ID.
        db_path: Optional database path override.

    Returns:
        Dict with status and assertions list.
    """
    conn = _get_db(db_path)
    try:
        _ensure_tables(conn)
        row = conn.execute(
            "SELECT metadata FROM kg_graphs WHERE id = %s", (graph_id,)
        ).fetchone()
        if not row:
            return {"status": "error", "message": f"Graph {graph_id} not found"}

        meta: Dict[str, Any] = json.loads(row["metadata"] or "{}")
        assertions = meta.get("ontology_assertions", [])
        return {
            "status": "ok",
            "graph_id": graph_id,
            "assertions": assertions,
            "count": len(assertions),
        }
    finally:
        conn.close()


# =========================================================================
# CLI
# =========================================================================


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Cross-project Knowledge Graph Federation",
    )

    # Mutually exclusive actions
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--search",
        metavar="QUERY",
        help="Federated search across project graphs",
    )
    group.add_argument(
        "--shared",
        nargs=2,
        metavar=("PROJ_A", "PROJ_B"),
        help="Find shared entities between two projects",
    )
    group.add_argument(
        "--create-view",
        metavar="NAME",
        help="Create a federated view (virtual graph)",
    )
    group.add_argument(
        "--coverage",
        metavar="FRAMEWORK",
        help="Cross-project compliance coverage (fedramp, cmmc, nist, ...)",
    )
    group.add_argument(
        "--store-ontology",
        metavar="GRAPH_ID",
        help="Store ontology assertions in graph metadata",
    )
    group.add_argument(
        "--get-ontology",
        metavar="GRAPH_ID",
        help="Retrieve ontology assertions for a graph",
    )

    parser.add_argument(
        "--projects",
        default=None,
        help="Comma-separated project IDs (default: all)",
    )
    parser.add_argument(
        "--profile",
        default=None,
        help="Scoring profile for search (auto-detected if omitted)",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=10,
        help="Maximum results to return (default: 10)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON",
    )

    args = parser.parse_args()

    project_ids = [p.strip() for p in args.projects.split(",") if p.strip()] if args.projects else None

    result: Dict[str, Any] = {}

    if args.search:
        result = federated_search(
            query=args.search,
            project_ids=project_ids,
            profile=args.profile,
            top_k=args.top_k,
        )
    elif args.shared:
        result = find_shared_entities(
            project_id_a=args.shared[0],
            project_id_b=args.shared[1],
        )
    elif args.create_view:
        if not project_ids:
            parser.error("--create-view requires --projects")
        result = create_federated_view(
            name=args.create_view,
            project_ids=project_ids,
        )
    elif args.coverage:
        result = cross_project_coverage(
            framework=args.coverage,
            project_ids=project_ids,
        )
    elif args.store_ontology:
        result = store_ontology_assertions(graph_id=args.store_ontology)
    elif args.get_ontology:
        result = get_ontology_assertions(graph_id=args.get_ontology)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        _print_human_readable(result, args)


def _print_human_readable(result: Dict[str, Any], args) -> None:
    """Print result in human-readable format."""
    status = result.get("status", "?")
    if status == "error":
        print(f"ERROR: {result.get('message', 'Unknown error')}")
        sys.exit(1)

    if args.search:
        print(f'\n=== Federated Search: "{args.search}" ===\n')
        print(f"  Projects searched: {result.get('projects_searched', 0)}")
        print(f"  Results returned:  {result.get('nodes_returned', 0)}")
        print(f"  Time:              {result.get('retrieval_ms', 0)} ms\n")

        for i, r in enumerate(result.get("results", []), 1):
            sources = ", ".join(r.get("source_projects", []))
            print(
                f"  {i}. [{r.get('entity_type', '?')}] "
                f"{r.get('label', '?')}  "
                f"(score: {r.get('score', 0):.3f}, "
                f"projects: {sources})"
            )

        if result.get("per_project"):
            print("\n  Per-project breakdown:")
            for pid, stats in result["per_project"].items():
                print(f"    {pid}: {stats.get('nodes_matched', 0)} matched, {stats.get('nodes_returned', 0)} returned")

    elif args.shared:
        pa = result.get("project_a", "?")
        pb = result.get("project_b", "?")
        print(f"\n=== Shared Entities: {pa} <-> {pb} ===\n")
        print(f"  Nodes in A: {result.get('nodes_in_a', 0)}")
        print(f"  Nodes in B: {result.get('nodes_in_b', 0)}")
        print(f"  Shared:     {result.get('shared_count', 0)}\n")

        for i, e in enumerate(result.get("shared_entities", [])[:20], 1):
            match = e.get("match_type", "?")
            label = e.get("label", "?")
            label_b = e.get("label_b")
            display = f"{label} / {label_b}" if label_b and label_b != label else label
            print(f"  {i}. [{match}] {display}  (edges: {e.get('edge_count_a', 0)}/{e.get('edge_count_b', 0)})")

    elif args.create_view:
        print("\n=== Federated View Created ===\n")
        print(f"  ID:       {result.get('federated_graph_id', '?')}")
        print(f"  Name:     {result.get('name', '?')}")
        print(f"  Sources:  {len(result.get('source_graph_ids', []))} graphs")
        print(f"  Projects: {', '.join(result.get('source_projects', []))}")
        print(f"  Nodes:    {result.get('total_nodes', 0)}")
        print(f"  Edges:    {result.get('total_edges', 0)}")

    elif args.coverage:
        fw = result.get("framework", "?")
        print(f"\n=== Cross-Project Coverage: {fw} ===\n")
        print(f"  Projects:       {result.get('projects_checked', 0)}")
        print(f"  Total controls: {result.get('total_controls_found', 0)}\n")

        for pid, stats in result.get("per_project", {}).items():
            print(
                f"  {pid}: {stats.get('total_controls', 0)} controls "
                f"({stats.get('coverage_pct', 0)}% coverage, "
                f"{stats.get('unique_controls', 0)} unique)"
            )

        shared = result.get("shared_controls", [])
        if shared:
            print(f"\n  Shared controls ({len(shared)}):")
            for s in shared[:15]:
                print(f"    - {s.get('control', '?')} (in: {', '.join(s.get('covered_by', []))})")

        unique = result.get("unique_to_project", {})
        if unique:
            print("\n  Unique controls per project:")
            for pid, ctrls in unique.items():
                if ctrls:
                    print(f"    {pid}: {', '.join(ctrls[:10])}")

    elif args.store_ontology:
        print("\n=== Ontology Assertions Stored ===\n")
        print(f"  Graph:      {result.get('graph_id', '?')}")
        print(f"  Assertions: {result.get('assertions_stored', 0)}")
        for a in result.get("assertions", [])[:10]:
            print(f"    - {a['source']} {a['assertion']} {a['target']} ({a['relation']})")

    elif args.get_ontology:
        print("\n=== Ontology Assertions ===\n")
        print(f"  Graph:   {result.get('graph_id', '?')}")
        print(f"  Count:   {result.get('count', 0)}\n")
        for a in result.get("assertions", []):
            print(f"    - {a['source']} {a['assertion']} {a['target']} ({a['relation']})")


if __name__ == "__main__":
    main()
