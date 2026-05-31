# CUI // SP-CTI
"""DIC Analytics Engine — document-level analytics, pattern detection, anomaly detection,
and scenario impact analysis over the KG and RAG layers.

All queries use get_connection() so RLS applies.
"""
from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _conn():
    from tools.db.storage import get_connection
    return get_connection()


def _dic_graph_ids(conn) -> list[str]:
    """Return kg_graph ids that belong to DIC-ingested documents only.

    Filters by source_doc_id IN dic_documents so ICDEV system KG graphs
    (compliance, canvas topology, etc.) are excluded.
    """
    try:
        cur = conn.execute(
            "SELECT g.id FROM kg_graphs g "
            "INNER JOIN dic_documents d ON d.doc_id = g.source_doc_id "
            "WHERE g.source_doc_id IS NOT NULL"
        )
        return [row[0] for row in cur.fetchall()]
    except Exception:
        return []


def _safe(conn, sql: str, params: tuple = ()) -> list[dict]:
    try:
        cur = conn.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    except Exception as exc:
        logger.warning("dic.analytics: query error: %s", exc)
        return []


# ── Entity Frequency ──────────────────────────────────────────────────────────

def entity_frequency(collection_id: str | None = None, limit: int = 50) -> dict:
    """Return entity frequency distribution across all KG nodes.

    Returns:
        {
          by_type: {entity_type: [{label, count, centrality}]},
          top_entities: [{label, entity_type, count, centrality}],
          type_counts: {entity_type: int},
          total: int
        }
    """
    conn = _conn()
    try:
        gids = _dic_graph_ids(conn)
        if not gids:
            return {"by_type": {}, "top_entities": [], "type_counts": {}, "total": 0, "empty": True,
                    "message": "No DIC documents ingested yet. Upload documents to see analytics."}
        ph = ",".join(["?" for _ in gids])
        rows = _safe(
            conn,
            f"SELECT n.label, n.entity_type, COUNT(*) as freq, AVG(n.centrality) as avg_centrality "
            f"FROM kg_nodes n WHERE n.graph_id IN ({ph}) GROUP BY n.label, n.entity_type ORDER BY freq DESC LIMIT ?",
            tuple(gids) + (limit * 4,),
        )
    finally:
        conn.close()

    by_type: dict[str, list] = defaultdict(list)
    for r in rows:
        by_type[r["entity_type"]].append({
            "label": r["label"],
            "count": r["freq"],
            "centrality": round(float(r["avg_centrality"] or 0), 4),
        })

    type_counts = {t: len(items) for t, items in by_type.items()}
    top = sorted(
        [{"label": r["label"], "entity_type": r["entity_type"],
          "count": r["freq"], "centrality": round(float(r["avg_centrality"] or 0), 4)}
         for r in rows],
        key=lambda x: (x["count"], x["centrality"]),
        reverse=True,
    )[:limit]

    return {
        "by_type": dict(by_type),
        "top_entities": top,
        "type_counts": type_counts,
        "total": sum(type_counts.values()),
    }


# ── Co-occurrence ─────────────────────────────────────────────────────────────

def co_occurrence(min_weight: float = 0.0, limit: int = 60) -> dict:
    """Return entity co-occurrence pairs from KG edges, sorted by weight.

    Returns:
        {
          pairs: [{source, target, relationship, weight}],
          hot_pairs: top N by weight
        }
    """
    conn = _conn()
    try:
        gids = _dic_graph_ids(conn)
        if not gids:
            return {"pairs": [], "hot_pairs": [], "total": 0, "empty": True}
        ph = ",".join(["?" for _ in gids])
        rows = _safe(
            conn,
            f"SELECT src.label AS source, tgt.label AS target, e.relationship, e.weight "
            f"FROM kg_edges e "
            f"JOIN kg_nodes src ON src.id = e.source_id "
            f"JOIN kg_nodes tgt ON tgt.id = e.target_id "
            f"WHERE src.graph_id IN ({ph}) "
            f"AND (e.weight IS NULL OR e.weight >= ?) "
            f"ORDER BY e.weight DESC LIMIT ?",
            tuple(gids) + (min_weight, limit),
        )
    finally:
        conn.close()

    return {
        "pairs": rows,
        "hot_pairs": rows[:20],
        "total": len(rows),
    }


# ── Anomaly Detection ─────────────────────────────────────────────────────────

def detect_anomalies() -> dict:
    """Detect structural anomalies in the DIC knowledge graph.

    Anomaly types:
      orphans       — entities with no edges (isolated, not referenced)
      single_source — entities appearing in only 1 chunk (tribal knowledge risk)
      high_centrality — critical hubs whose removal would fragment the graph
      contradictions  — same entity pair with conflicting relationship types
      stale_docs    — documents with no KG nodes generated (ingest may have failed)
    """
    conn = _conn()
    try:
        gids = _dic_graph_ids(conn)
        if not gids:
            return {
                "severity": "low", "orphans": [], "single_source": [], "hubs": [],
                "contradictions": [], "stale_docs": [], "empty": True,
                "message": "No DIC documents ingested yet.",
                "summary": {"orphan_count": 0, "single_source_count": 0, "hub_count": 0,
                            "contradiction_count": 0, "stale_doc_count": 0},
            }
        ph = ",".join(["?" for _ in gids])
        gids_t = tuple(gids)

        # Orphaned nodes (scoped to DIC graphs)
        orphans = _safe(
            conn,
            f"SELECT n.label, n.entity_type FROM kg_nodes n "
            f"WHERE n.graph_id IN ({ph}) "
            f"AND n.id NOT IN (SELECT source_id FROM kg_edges) "
            f"AND n.id NOT IN (SELECT target_id FROM kg_edges) "
            f"ORDER BY n.entity_type, n.label LIMIT 100",
            gids_t,
        )

        # Single-source nodes (scoped to DIC graphs)
        single_source = _safe(
            conn,
            f"SELECT label, entity_type, source_chunk_id FROM kg_nodes "
            f"WHERE graph_id IN ({ph}) AND source_chunk_id IS NOT NULL "
            f"GROUP BY label HAVING COUNT(DISTINCT source_chunk_id) = 1 "
            f"ORDER BY entity_type LIMIT 100",
            gids_t,
        )

        # High-centrality hubs (scoped to DIC graphs)
        hubs = _safe(
            conn,
            f"SELECT label, entity_type, centrality FROM kg_nodes "
            f"WHERE graph_id IN ({ph}) AND centrality IS NOT NULL "
            f"ORDER BY centrality DESC LIMIT 10",
            gids_t,
        )

        # Contradictions (scoped to DIC graphs)
        contradictions = _safe(
            conn,
            f"SELECT src.label AS source, tgt.label AS target, "
            f"COUNT(DISTINCT e.relationship) AS rel_count, "
            f"GROUP_CONCAT(DISTINCT e.relationship) AS relationships "
            f"FROM kg_edges e "
            f"JOIN kg_nodes src ON src.id = e.source_id "
            f"JOIN kg_nodes tgt ON tgt.id = e.target_id "
            f"WHERE src.graph_id IN ({ph}) "
            f"GROUP BY src.label, tgt.label "
            f"HAVING rel_count > 1 "
            f"ORDER BY rel_count DESC LIMIT 50",
            gids_t,
        )

        # Documents with no KG nodes
        stale_docs = _safe(
            conn,
            "SELECT d.doc_id, d.title FROM dic_documents d "
            "WHERE d.doc_id NOT IN ("
            "  SELECT DISTINCT g.source_doc_id FROM kg_graphs g "
            "  WHERE g.source_doc_id IS NOT NULL"
            ") LIMIT 50",
        )
    except Exception as exc:
        logger.warning("dic.analytics: anomaly detection error: %s", exc)
        orphans, single_source, hubs, contradictions, stale_docs = [], [], [], [], []
    finally:
        conn.close()

    severity = "low"
    if len(contradictions) > 5 or len(stale_docs) > 2:
        severity = "high"
    elif len(orphans) > 20 or len(single_source) > 10:
        severity = "medium"

    return {
        "severity": severity,
        "orphans": orphans,
        "single_source": single_source,
        "hubs": hubs,
        "contradictions": contradictions,
        "stale_docs": stale_docs,
        "summary": {
            "orphan_count": len(orphans),
            "single_source_count": len(single_source),
            "hub_count": len(hubs),
            "contradiction_count": len(contradictions),
            "stale_doc_count": len(stale_docs),
        },
    }


# ── Pattern Detection ─────────────────────────────────────────────────────────

_DOC_PATTERNS = [
    {
        "id": "HIERARCHICAL",
        "name": "Hierarchical Authority",
        "description": "Clear chain of command — entities with many downstream dependencies and few upstream.",
        "flags": ["has_hubs", "hub_ratio_high"],
    },
    {
        "id": "NETWORK_MESH",
        "name": "Dense Knowledge Mesh",
        "description": "Highly interconnected — most entities reference many others. Rich but fragile to node removal.",
        "flags": ["high_edge_density", "low_orphan_ratio"],
    },
    {
        "id": "SILOED",
        "name": "Knowledge Silos",
        "description": "Disconnected clusters — knowledge lives in isolated groups with few cross-links.",
        "flags": ["high_orphan_ratio", "low_edge_density"],
    },
    {
        "id": "STAR_TOPOLOGY",
        "name": "Central Concept Dominance",
        "description": "One or few concepts dominate — high centrality concentration. Single point of failure risk.",
        "flags": ["has_hubs", "low_edge_density"],
    },
    {
        "id": "TEMPORAL",
        "name": "Sequential / Temporal",
        "description": "Linear or near-linear chain of concepts — suggests procedural or narrative structure.",
        "flags": ["chain_like", "low_orphan_ratio"],
    },
]


def detect_patterns() -> dict:
    """Detect structural patterns in the DIC knowledge graph.

    Returns:
        {
          patterns: [{id, name, description, confidence, signals}],
          dominant: str,
          flags: dict
        }
    """
    conn = _conn()
    try:
        gids = _dic_graph_ids(conn)
        if not gids:
            return {"patterns": [], "dominant": "UNKNOWN", "flags": {}, "empty": True,
                    "stats": {"node_count": 0, "edge_count": 0, "orphan_count": 0, "hub_count": 0,
                              "edge_density": 0.0, "orphan_ratio": 0.0}}
        ph = ",".join(["?" for _ in gids])
        gids_t = tuple(gids)
        node_count = (conn.execute(f"SELECT COUNT(*) FROM kg_nodes WHERE graph_id IN ({ph})", gids_t).fetchone() or [0])[0]
        edge_count = (conn.execute(
            f"SELECT COUNT(*) FROM kg_edges e JOIN kg_nodes n ON n.id = e.source_id WHERE n.graph_id IN ({ph})",
            gids_t,
        ).fetchone() or [0])[0]
        orphan_count = (conn.execute(
            f"SELECT COUNT(*) FROM kg_nodes WHERE graph_id IN ({ph}) AND id NOT IN "
            f"(SELECT source_id FROM kg_edges) AND id NOT IN (SELECT target_id FROM kg_edges)",
            gids_t,
        ).fetchone() or [0])[0]
        hub_count = (conn.execute(
            f"SELECT COUNT(*) FROM kg_nodes WHERE graph_id IN ({ph}) AND centrality IS NOT NULL AND centrality > 0.5",
            gids_t,
        ).fetchone() or [0])[0]
    except Exception as exc:
        logger.warning("dic.analytics: pattern detection error: %s", exc)
        return {"patterns": [], "dominant": "UNKNOWN", "flags": {}}
    finally:
        conn.close()

    nc = max(node_count, 1)
    ec = max(edge_count, 1)
    orphan_ratio = orphan_count / nc
    edge_density = edge_count / (nc * (nc - 1) / 2) if nc > 1 else 0
    hub_ratio = hub_count / nc

    flags = {
        "has_hubs": hub_count > 0,
        "hub_ratio_high": hub_ratio > 0.1,
        "high_edge_density": edge_density > 0.1,
        "low_edge_density": edge_density < 0.02,
        "high_orphan_ratio": orphan_ratio > 0.4,
        "low_orphan_ratio": orphan_ratio < 0.15,
        "chain_like": edge_count > 0 and edge_count < nc * 1.5,
    }

    scored = []
    for p in _DOC_PATTERNS:
        matches = sum(1 for f in p["flags"] if flags.get(f, False))
        confidence = int(matches / len(p["flags"]) * 100)
        scored.append({
            "id": p["id"],
            "name": p["name"],
            "description": p["description"],
            "confidence": confidence,
            "signals": [f for f in p["flags"] if flags.get(f, False)],
        })

    scored.sort(key=lambda x: x["confidence"], reverse=True)
    dominant = scored[0]["id"] if scored and scored[0]["confidence"] >= 40 else "UNCLASSIFIED"

    return {
        "patterns": scored,
        "dominant": dominant,
        "flags": flags,
        "stats": {
            "node_count": node_count,
            "edge_count": edge_count,
            "orphan_count": orphan_count,
            "hub_count": hub_count,
            "edge_density": round(edge_density, 4),
            "orphan_ratio": round(orphan_ratio, 4),
        },
    }


# ── Scenario Runner ───────────────────────────────────────────────────────────

def run_scenario(scenario_type: str, entity_label: str | None = None,
                 params: dict | None = None) -> dict:
    """Run a what-if scenario against the KG.

    Scenarios:
      remove_entity    — impact if entity_label is removed from the graph
      change_concept   — reframe entity_label as a different concept
      cross_doc        — compare entity overlap between two documents
      centrality_shift — what if the top hub were removed?
    """
    params = params or {}
    conn = _conn()
    try:
        if scenario_type == "remove_entity":
            return _scenario_remove_entity(conn, entity_label or "")

        elif scenario_type == "centrality_shift":
            top_hub = _safe(
                conn,
                "SELECT label FROM kg_nodes WHERE centrality IS NOT NULL ORDER BY centrality DESC LIMIT 1",
            )
            hub_label = top_hub[0]["label"] if top_hub else entity_label or ""
            return _scenario_remove_entity(conn, hub_label, label_override="Top Hub Removal")

        elif scenario_type == "cross_doc":
            doc_a = params.get("doc_a", "")
            doc_b = params.get("doc_b", "")
            return _scenario_cross_doc(conn, doc_a, doc_b)

        elif scenario_type == "change_concept":
            return _scenario_change_concept(conn, entity_label or "", params.get("new_label", ""))

        else:
            return {"error": f"unknown scenario: {scenario_type}"}
    except Exception as exc:
        logger.warning("dic.analytics: scenario error: %s", exc)
        return {"error": str(exc)}
    finally:
        conn.close()


def _scenario_remove_entity(conn, label: str, label_override: str | None = None) -> dict:
    node = _safe(conn, "SELECT id, label, entity_type, centrality FROM kg_nodes WHERE LOWER(label) LIKE LOWER(?) LIMIT 1", (f"%{label}%",))
    if not node:
        return {"error": f"Entity '{label}' not found in KG"}
    n = node[0]
    edges_out = _safe(conn, "SELECT COUNT(*) as c FROM kg_edges WHERE source_id = ?", (n["id"],))
    edges_in = _safe(conn, "SELECT COUNT(*) as c FROM kg_edges WHERE target_id = ?", (n["id"],))
    affected_nodes = _safe(
        conn,
        "SELECT DISTINCT n.label, n.entity_type FROM kg_nodes n "
        "JOIN kg_edges e ON (e.source_id = n.id OR e.target_id = n.id) "
        "WHERE (e.source_id = ? OR e.target_id = ?) AND n.id != ? LIMIT 30",
        (n["id"], n["id"], n["id"]),
    )
    out_count = (edges_out[0]["c"] if edges_out else 0)
    in_count = (edges_in[0]["c"] if edges_in else 0)
    impact_score = min(100, int((out_count + in_count) * 10 + float(n.get("centrality") or 0) * 50))
    return {
        "scenario": "remove_entity",
        "entity": label_override or n["label"],
        "entity_type": n["entity_type"],
        "impact_score": impact_score,
        "severed_outgoing": out_count,
        "severed_incoming": in_count,
        "affected_neighbors": affected_nodes,
        "risk": "critical" if impact_score >= 70 else "high" if impact_score >= 40 else "medium",
        "interpretation": (
            f"Removing '{n['label']}' severs {out_count + in_count} relationships "
            f"and directly affects {len(affected_nodes)} neighboring concepts. "
            f"Impact score: {impact_score}/100."
        ),
    }


def _scenario_cross_doc(conn, doc_a: str, doc_b: str) -> dict:
    def entities_for(doc_id: str) -> set:
        rows = _safe(
            conn,
            "SELECT DISTINCT n.label FROM kg_nodes n "
            "JOIN kg_graphs g ON g.id = n.graph_id "
            "WHERE g.source_doc_id = ?",
            (doc_id,),
        )
        return {r["label"] for r in rows}

    set_a = entities_for(doc_a)
    set_b = entities_for(doc_b)
    shared = set_a & set_b
    only_a = set_a - set_b
    only_b = set_b - set_a
    overlap_pct = int(len(shared) / max(len(set_a | set_b), 1) * 100)
    return {
        "scenario": "cross_doc",
        "doc_a": doc_a,
        "doc_b": doc_b,
        "shared_concepts": sorted(shared),
        "unique_to_a": sorted(only_a)[:30],
        "unique_to_b": sorted(only_b)[:30],
        "overlap_percent": overlap_pct,
        "interpretation": (
            f"Documents share {len(shared)} concepts ({overlap_pct}% overlap). "
            f"Doc A has {len(only_a)} unique; Doc B has {len(only_b)} unique concepts."
        ),
    }


def _scenario_change_concept(conn, old_label: str, new_label: str) -> dict:
    node = _safe(conn, "SELECT id, label, entity_type FROM kg_nodes WHERE LOWER(label) LIKE LOWER(?) LIMIT 1", (f"%{old_label}%",))
    if not node:
        return {"error": f"Entity '{old_label}' not found"}
    n = node[0]
    rels = _safe(
        conn,
        "SELECT src.label AS source, tgt.label AS target, e.relationship "
        "FROM kg_edges e "
        "JOIN kg_nodes src ON src.id = e.source_id "
        "JOIN kg_nodes tgt ON tgt.id = e.target_id "
        "WHERE e.source_id = ? OR e.target_id = ? LIMIT 30",
        (n["id"], n["id"]),
    )
    return {
        "scenario": "change_concept",
        "original": n["label"],
        "replacement": new_label or "(not specified)",
        "affected_relationships": len(rels),
        "relationships": rels,
        "interpretation": (
            f"Reframing '{n['label']}' as '{new_label or '(new concept)'}' would affect "
            f"{len(rels)} existing relationships in the knowledge graph."
        ),
    }


# ── Full Analytics Bundle ─────────────────────────────────────────────────────

def run_full_analytics() -> dict:
    """Run all analytics in one call — for the dashboard summary view."""
    freq = entity_frequency(limit=30)
    cooc = co_occurrence(limit=40)
    anom = detect_anomalies()
    patt = detect_patterns()
    return {
        "entity_frequency": freq,
        "co_occurrence": cooc,
        "anomalies": anom,
        "patterns": patt,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
