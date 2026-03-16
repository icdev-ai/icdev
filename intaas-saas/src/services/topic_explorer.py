#!/usr/bin/env python3
"""Topic explorer — find related topics through shared entities.

Compares entities across topics to find connections:
  - Same people mentioned in different topics
  - Same organizations appearing across stories
  - Same policies/events linking topics
"""
import logging

from src.db import dynamo
from src.services import entity_extractor

logger = logging.getLogger("intaas.services.topic_explorer")


def find_related_topics(
    topic_id: str, max_results: int = 10,
) -> list[dict]:
    """Find topics related to the given topic by shared entities."""
    # Get current topic's articles and extract entities
    current_articles = dynamo.get_articles_for_topic(topic_id)
    if not current_articles:
        return []

    current_entities = set()
    for article in current_articles:
        entities = entity_extractor.extract_entities_deterministic(
            (article.get("title", "") + " " + article.get("content", "")),
            max_entities=15,
        )
        for e in entities:
            name = e.get("name", "").lower()
            if name and len(name) > 3:
                current_entities.add(name)

    if not current_entities:
        return []

    # Compare against all other topics
    all_topics = dynamo.list_topics(limit=100)
    related = []

    for topic in all_topics:
        other_id = topic.get("topic_id", "")
        if other_id == topic_id or int(topic.get("source_count", 0)) == 0:
            continue

        other_articles = dynamo.get_articles_for_topic(other_id)
        other_entities = set()
        for article in other_articles:
            entities = entity_extractor.extract_entities_deterministic(
                (article.get("title", "") + " " + article.get("content", "")),
                max_entities=10,
            )
            for e in entities:
                name = e.get("name", "").lower()
                if name and len(name) > 3:
                    other_entities.add(name)

        if not other_entities:
            continue

        shared = current_entities & other_entities
        if shared:
            shared_pct = round(
                len(shared) / min(len(current_entities), len(other_entities)) * 100,
            )
            related.append({
                "topic_id": other_id,
                "title": topic.get("title", ""),
                "shared_entities": len(shared),
                "shared_pct": shared_pct,
                "shared_names": sorted(shared)[:10],
                "source_count": int(topic.get("source_count", 0)),
            })

    related.sort(key=lambda x: x["shared_entities"], reverse=True)
    return related[:max_results]
