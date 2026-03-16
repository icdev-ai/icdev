#!/usr/bin/env python3
"""Knowledge graph service — entity/relationship network across articles in a topic.

Builds a cross-source entity graph from extracted entities:
  - Nodes: entities (people, orgs, locations, policies, statistics)
  - Edges: co-occurrence in articles (which entities appear together)
  - Source attribution: which source mentions which entity

Enables:
  - Entity frequency matrix (who mentions whom)
  - Omission heatmap (which entities are missing from which sources)
  - Source independence score (same entities = same press release)
  - Hub entity detection (most-connected entities)
"""
import logging
from collections import Counter, defaultdict

from src.services import entity_extractor

logger = logging.getLogger("intaas.services.knowledge_graph")


def build_entity_graph(articles: list[dict], perspectives: list[dict]) -> dict:
    """Build a knowledge graph from articles in a topic.

    Returns entity frequency matrix, source-entity map,
    omission analysis, and hub entities.
    """
    # Extract entities from each article
    source_entities: dict[str, list[dict]] = {}
    all_entities: list[dict] = []
    entity_sources: dict[str, set] = defaultdict(set)

    for article in articles:
        source = article.get("source_id", "unknown")
        source_name = ""
        for p in perspectives:
            if p.get("article_id") == article.get("article_id"):
                source_name = p.get("source_name", source)
                break

        entities = entity_extractor.extract_from_article(article)
        source_entities[source_name or source] = entities

        for entity in entities:
            name = entity.get("name", "").strip()
            if name:
                entity_sources[name.lower()].add(source_name or source)
                all_entities.append({
                    **entity,
                    "source": source_name or source,
                    "article_id": article.get("article_id", ""),
                })

    total_sources = len(source_entities)
    if total_sources == 0:
        return {"entities": [], "sources": 0, "omissions": [], "hubs": []}

    # Entity frequency (how many sources mention each entity)
    entity_freq = Counter()
    entity_types = {}
    for entity in all_entities:
        name = entity.get("name", "").lower()
        entity_freq[name] += 1
        if name not in entity_types:
            entity_types[name] = entity.get("type", "unknown")

    # Hub entities (mentioned by most sources)
    hubs = []
    for name, sources in sorted(
        entity_sources.items(),
        key=lambda x: len(x[1]),
        reverse=True,
    )[:20]:
        hubs.append({
            "name": name,
            "display_name": _best_display_name(name, all_entities),
            "type": entity_types.get(name, "unknown"),
            "source_count": len(sources),
            "total_sources": total_sources,
            "coverage_pct": round(len(sources) / total_sources * 100),
            "sources": sorted(sources),
        })

    # Omission analysis (entities mentioned by few sources)
    omissions = []
    for name, sources in entity_sources.items():
        coverage = len(sources) / total_sources
        if 0 < coverage < 0.3 and entity_freq[name] >= 1:
            omissions.append({
                "entity": _best_display_name(name, all_entities),
                "type": entity_types.get(name, "unknown"),
                "mentioned_by": sorted(sources),
                "mentioned_count": len(sources),
                "total_sources": total_sources,
                "coverage_pct": round(coverage * 100),
                "significance": round(1 - coverage, 2),
            })

    omissions.sort(key=lambda x: x["significance"], reverse=True)

    # Source independence score (how unique are each source's entities?)
    independence = _compute_independence(source_entities)

    # Source-entity matrix (for frontend heatmap)
    matrix = _build_matrix(source_entities, hubs[:10])

    return {
        "total_entities": len(entity_freq),
        "total_sources": total_sources,
        "hubs": hubs,
        "omissions": omissions[:20],
        "independence": independence,
        "matrix": matrix,
    }


def _compute_independence(source_entities: dict) -> list[dict]:
    """Compute source independence score.

    Sources that share many entities with others are likely
    republishing the same press release. Unique entities = independent reporting.
    """
    results = []

    for source, entities in source_entities.items():
        entity_names = {e.get("name", "").lower() for e in entities}
        if not entity_names:
            results.append({
                "source": source,
                "unique_entities": 0,
                "shared_entities": 0,
                "independence_score": 0.0,
            })
            continue

        # Count how many entities are unique to this source
        unique = 0
        shared = 0
        for name in entity_names:
            other_sources = sum(
                1 for other, other_ents in source_entities.items()
                if other != source
                and name in {e.get("name", "").lower() for e in other_ents}
            )
            if other_sources == 0:
                unique += 1
            else:
                shared += 1

        total = unique + shared
        score = round(unique / total, 2) if total > 0 else 0.0

        results.append({
            "source": source,
            "unique_entities": unique,
            "shared_entities": shared,
            "total_entities": total,
            "independence_score": score,
        })

    results.sort(key=lambda x: x["independence_score"], reverse=True)
    return results


def _build_matrix(
    source_entities: dict, top_entities: list[dict],
) -> dict:
    """Build source × entity matrix for heatmap visualization."""
    entity_names = [h["name"] for h in top_entities]
    sources = sorted(source_entities.keys())

    rows = []
    for source in sources:
        ent_names = {e.get("name", "").lower() for e in source_entities.get(source, [])}
        row = {
            "source": source,
            "entities": {
                name: 1 if name in ent_names else 0
                for name in entity_names
            },
        }
        rows.append(row)

    return {
        "entity_columns": [
            {"name": h["name"], "display": h["display_name"], "type": h["type"]}
            for h in top_entities
        ],
        "rows": rows,
    }


def _best_display_name(lower_name: str, all_entities: list[dict]) -> str:
    """Find the best capitalized version of an entity name."""
    for e in all_entities:
        if e.get("name", "").lower() == lower_name:
            return e["name"]
    return lower_name
