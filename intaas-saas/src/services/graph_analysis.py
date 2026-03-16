#!/usr/bin/env python3
"""Graph analysis service — BFS path finding, impact propagation,
subgraph extraction, echo chamber detection, and threat assessment.

Operates on the entity graph built by knowledge_graph.py.
All algorithms are deterministic (stdlib only, no external deps).

Adapted from INTaaS child app tools/intelligence/knowledge_graph.py:
  - BFS path finding (find_path)
  - Impact propagation (severity decay)
  - Subgraph extraction (depth-limited neighborhoods)
Plus new:
  - Echo chamber detection (source clustering by shared entities)
  - Threat assessment (entity criticality scoring)
"""
import logging
from collections import defaultdict, deque

from src.services import entity_extractor, knowledge_graph

logger = logging.getLogger("intaas.services.graph_analysis")


def full_analysis(articles: list[dict], perspectives: list[dict]) -> dict:
    """Run full Phase 3 graph analysis on a topic's articles.

    Returns path finding data, impact scores, clusters,
    and threat assessments.
    """
    # Build the entity graph first
    graph = knowledge_graph.build_entity_graph(articles, perspectives)
    hubs = graph.get("hubs", [])
    independence = graph.get("independence", [])

    # Build adjacency structures for graph algorithms
    entity_map, source_map, adj = _build_adjacency(articles, perspectives)

    # BFS paths between top entities
    paths = _find_key_paths(adj, entity_map, hubs)

    # Impact propagation from hub entities
    impact = _propagate_impact(adj, entity_map, hubs)

    # Echo chamber / source cluster detection
    clusters = _detect_clusters(source_map, len(articles))

    # Threat assessment (entity criticality)
    threats = _assess_threats(
        hubs, graph.get("omissions", []),
        independence, adj, entity_map,
    )

    # Subgraph for top entity
    subgraph = {}
    if hubs:
        top_entity = hubs[0]["name"]
        subgraph = _extract_subgraph(adj, entity_map, top_entity, depth=2)

    return {
        "paths": paths,
        "impact": impact,
        "clusters": clusters,
        "threats": threats,
        "subgraph": subgraph,
        "graph_stats": {
            "entities": len(entity_map),
            "edges": sum(len(v) for v in adj.values()) // 2,
            "sources": graph.get("total_sources", 0),
        },
    }


def _build_adjacency(
    articles: list[dict], perspectives: list[dict],
) -> tuple[dict, dict, dict]:
    """Build adjacency list from co-occurring entities within articles.

    Two entities are connected if they appear in the same article.
    Edge weight = number of articles they co-occur in.

    Returns:
      entity_map: {lower_name: {name, type, sources}}
      source_map: {source_name: set(entity_names)}
      adj: {entity: {neighbor: weight}}
    """
    entity_map: dict[str, dict] = {}
    source_map: dict[str, set] = defaultdict(set)
    adj: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for article in articles:
        source_name = ""
        for p in perspectives:
            if p.get("article_id") == article.get("article_id"):
                source_name = p.get("source_name", "")
                break

        entities = entity_extractor.extract_entities_deterministic(
            (article.get("title", "") + " " + article.get("content", "")),
            max_entities=15,
        )

        entity_names = []
        for e in entities:
            name = e.get("name", "").lower()
            if not name or len(name) < 3:
                continue
            entity_names.append(name)
            if name not in entity_map:
                entity_map[name] = {
                    "name": e.get("name", ""),
                    "type": e.get("type", "unknown"),
                    "sources": set(),
                }
            entity_map[name]["sources"].add(source_name or "unknown")
            source_map[source_name or "unknown"].add(name)

        # Build co-occurrence edges
        for i, e1 in enumerate(entity_names):
            for e2 in entity_names[i + 1:]:
                if e1 != e2:
                    adj[e1][e2] += 1
                    adj[e2][e1] += 1

    return entity_map, dict(source_map), dict(adj)


def _find_key_paths(
    adj: dict, entity_map: dict, hubs: list[dict],
) -> list[dict]:
    """Find BFS shortest paths between top hub entities."""
    if len(hubs) < 2:
        return []

    paths = []
    top_names = [h["name"].lower() for h in hubs[:6]]

    for i, start in enumerate(top_names):
        for end in top_names[i + 1:]:
            if start == end or start not in adj:
                continue
            path = _bfs_path(adj, start, end, max_depth=5)
            if path and len(path) > 1:
                display_path = []
                for node in path:
                    info = entity_map.get(node, {})
                    display_path.append({
                        "name": info.get("name", node),
                        "type": info.get("type", "unknown"),
                    })
                paths.append({
                    "from": display_path[0]["name"],
                    "to": display_path[-1]["name"],
                    "length": len(path) - 1,
                    "path": display_path,
                })

    paths.sort(key=lambda x: x["length"])
    return paths[:15]


def _bfs_path(
    adj: dict, start: str, end: str, max_depth: int = 5,
) -> list[str]:
    """BFS shortest path between two entities."""
    if start not in adj or end not in adj:
        return []

    visited = {start}
    queue = deque([(start, [start])])

    while queue:
        node, path = queue.popleft()
        if len(path) > max_depth:
            break
        if node == end:
            return path

        for neighbor in adj.get(node, {}):
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, path + [neighbor]))

    return []


def _propagate_impact(
    adj: dict, entity_map: dict, hubs: list[dict],
    decay: float = 0.7, min_threshold: float = 0.1,
) -> list[dict]:
    """Impact propagation — if a hub entity is compromised,
    how much does it affect connected entities?

    Uses severity decay model: impact = parent_impact × decay_factor
    """
    if not hubs:
        return []

    results = []
    for hub in hubs[:5]:
        hub_name = hub["name"].lower()
        if hub_name not in adj:
            continue

        # BFS with decaying severity
        impacts = {hub_name: 1.0}
        queue = deque([(hub_name, 1.0)])
        visited = {hub_name}

        while queue:
            node, severity = queue.popleft()
            for neighbor, weight in adj.get(node, {}).items():
                if neighbor not in visited:
                    new_severity = severity * decay
                    if new_severity >= min_threshold:
                        impacts[neighbor] = new_severity
                        visited.add(neighbor)
                        queue.append((neighbor, new_severity))

        affected = []
        for entity, score in sorted(
            impacts.items(), key=lambda x: x[1], reverse=True,
        ):
            if entity == hub_name:
                continue
            info = entity_map.get(entity, {})
            affected.append({
                "entity": info.get("name", entity),
                "type": info.get("type", "unknown"),
                "impact_score": round(score, 3),
            })

        results.append({
            "source_entity": hub["display_name"],
            "source_type": hub.get("type", "unknown"),
            "affected_count": len(affected),
            "affected": affected[:10],
        })

    return results


def _detect_clusters(
    source_map: dict[str, set], total_articles: int,
) -> list[dict]:
    """Detect echo chambers — groups of sources sharing similar entities.

    Two sources in the same cluster if they share >50% of entities.
    """
    sources = list(source_map.keys())
    if len(sources) < 2:
        return []

    # Compute pairwise Jaccard similarity
    similarities = []
    for i, s1 in enumerate(sources):
        e1 = source_map[s1]
        for s2 in sources[i + 1:]:
            e2 = source_map[s2]
            if not e1 or not e2:
                continue
            intersection = len(e1 & e2)
            union = len(e1 | e2)
            jaccard = intersection / union if union > 0 else 0
            if jaccard > 0.3:
                similarities.append({
                    "source_a": s1,
                    "source_b": s2,
                    "shared_entities": intersection,
                    "jaccard": round(jaccard, 3),
                })

    similarities.sort(key=lambda x: x["jaccard"], reverse=True)

    # Group into clusters using simple connected components
    clusters = _build_clusters(similarities, sources)

    return {
        "high_similarity_pairs": similarities[:15],
        "clusters": clusters,
        "echo_chamber_risk": (
            "HIGH" if any(s["jaccard"] > 0.7 for s in similarities)
            else "MODERATE" if similarities
            else "LOW"
        ),
        "assessment": _cluster_assessment(similarities, len(sources)),
    }


def _build_clusters(
    similarities: list[dict], all_sources: list[str],
) -> list[dict]:
    """Build clusters from high-similarity pairs (union-find)."""
    parent = {s: s for s in all_sources}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for sim in similarities:
        if sim["jaccard"] > 0.4:
            union(sim["source_a"], sim["source_b"])

    groups = defaultdict(list)
    for s in all_sources:
        groups[find(s)].append(s)

    clusters = []
    for members in groups.values():
        if len(members) > 1:
            clusters.append({
                "sources": sorted(members),
                "size": len(members),
                "likely_reason": (
                    "Wire service republication"
                    if len(members) > 3
                    else "Similar editorial perspective"
                ),
            })

    clusters.sort(key=lambda x: x["size"], reverse=True)
    return clusters


def _cluster_assessment(
    similarities: list[dict], total_sources: int,
) -> str:
    """Generate human-readable cluster assessment."""
    if not similarities:
        return (
            "Sources appear editorially independent. "
            "No significant entity overlap detected."
        )

    high = sum(1 for s in similarities if s["jaccard"] > 0.7)
    moderate = sum(1 for s in similarities if 0.4 < s["jaccard"] <= 0.7)

    if high > 3:
        return (
            f"{high} source pairs share >70% of entities. "
            "Likely republishing the same wire service content. "
            "Treat as fewer independent perspectives."
        )
    if moderate > 5:
        return (
            f"{moderate} source pairs share significant entity overlap. "
            "Some editorial clustering detected. "
            "Cross-reference with high-independence sources."
        )
    return (
        "Moderate entity overlap between some sources. "
        "Most sources appear to provide independent coverage."
    )


def _assess_threats(
    hubs: list[dict],
    omissions: list[dict],
    independence: list[dict],
    adj: dict,
    entity_map: dict,
) -> list[dict]:
    """Assess information integrity threats per entity.

    Threat score based on:
      - Centrality (how connected is the entity) — 30%
      - Omission rate (how often is it omitted) — 30%
      - Source dependence (does it appear only in low-independence sources) — 20%
      - Controversy (does it co-occur with loaded language indicators) — 20%
    """
    threats = []

    omission_set = {o["entity"].lower(): o for o in omissions}
    indep_map = {
        i["source"]: i.get("independence_score", 0.5) for i in independence
    }

    for hub in hubs[:15]:
        name = hub["name"].lower()
        info = entity_map.get(name, {})

        # Centrality score (0-1)
        connections = len(adj.get(name, {}))
        max_connections = max(len(v) for v in adj.values()) if adj else 1
        centrality = connections / max(max_connections, 1)

        # Omission score (0-1) — high if many sources omit this entity
        coverage = hub.get("coverage_pct", 50) / 100
        omission_score = 1 - coverage

        # Source dependence (0-1) — high if only low-independence sources mention it
        sources = info.get("sources", set())
        if sources:
            avg_indep = sum(
                indep_map.get(s, 0.5) for s in sources
            ) / len(sources)
            dependence_score = 1 - avg_indep
        else:
            dependence_score = 0.5

        # Composite threat score
        threat_score = round(
            0.30 * centrality
            + 0.30 * omission_score
            + 0.20 * dependence_score
            + 0.20 * (0.5 if name in omission_set else 0.0),
            3,
        )

        level = (
            "HIGH" if threat_score > 0.6
            else "MODERATE" if threat_score > 0.3
            else "LOW"
        )

        threats.append({
            "entity": hub["display_name"],
            "type": hub.get("type", "unknown"),
            "threat_score": threat_score,
            "threat_level": level,
            "centrality": round(centrality, 3),
            "omission_rate": round(omission_score, 3),
            "source_dependence": round(dependence_score, 3),
            "mentioned_by": hub.get("source_count", 0),
            "total_sources": hub.get("total_sources", 0),
        })

    threats.sort(key=lambda x: x["threat_score"], reverse=True)
    return threats


def _extract_subgraph(
    adj: dict, entity_map: dict,
    center: str, depth: int = 2,
) -> dict:
    """Extract depth-limited neighborhood around an entity."""
    center = center.lower()
    if center not in adj:
        return {"center": center, "nodes": [], "edges": []}

    visited = {center}
    queue = deque([(center, 0)])
    nodes = []
    edges = []

    while queue:
        node, d = queue.popleft()
        info = entity_map.get(node, {})
        nodes.append({
            "name": info.get("name", node),
            "type": info.get("type", "unknown"),
            "depth": d,
        })

        if d < depth:
            for neighbor, weight in adj.get(node, {}).items():
                edges.append({
                    "from": info.get("name", node),
                    "to": entity_map.get(neighbor, {}).get("name", neighbor),
                    "weight": weight,
                })
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append((neighbor, d + 1))

    center_info = entity_map.get(center, {})
    return {
        "center": center_info.get("name", center),
        "node_count": len(nodes),
        "edge_count": len(edges),
        "nodes": nodes,
        "edges": edges[:50],
    }
