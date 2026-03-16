#!/usr/bin/env python3
"""Perspectives routes — side-by-side source comparison and coverage gaps."""
import logging

from fastapi import APIRouter, Request

from urllib.parse import urlparse

from src.db import dynamo
from src.services import (
    bias_scorer,
    claim_tracker,
    contrarian_service,
    coverage_gap,
    credibility_db,
    graph_analysis,
    judge_service,
    knowledge_graph,
    narrative_tracker,
    pulse_bridge,
    report_generator,
    topic_explorer,
    translation_service,
)

logger = logging.getLogger("intaas.routes.perspectives")
router = APIRouter()


@router.get("/{topic_id}")
async def get_perspectives(topic_id: str, request: Request):
    """Get side-by-side perspectives for a topic.

    Returns all sources covering this topic with their bias scores,
    sorted by bias diversity to maximize perspective spread.
    """
    topic = dynamo.get_topic(topic_id)
    if not topic:
        return {"error": "Topic not found"}

    perspectives = dynamo.get_perspectives(topic_id)
    if not perspectives:
        # Generate perspectives from articles with hybrid scoring
        articles = dynamo.get_articles_for_topic(topic_id)
        for article in articles:
            content = article.get("content", "")
            title_text = article.get("title", "")

            # Hybrid scoring: deterministic + LLM judge
            judge_result = await judge_service.evaluate_with_prometheus(
                text=content or title_text,
                rubric_name="bias_forensics",
                article_id=article.get("article_id", ""),
            )

            if judge_result.get("method") == "hybrid":
                composite = judge_result["composite_score"]
                color = judge_result["color"]
                dim_scores = {
                    k: v.get("score", 3.0) if isinstance(v, dict) else v
                    for k, v in judge_result.get("dimension_scores", {}).items()
                }
            else:
                # Deterministic fallback
                det = bias_scorer.score_article(content, title_text)
                composite = det["composite"]
                color = det["color"]
                dim_scores = det["dimensions"]

            article_url = article.get("url", "")
            domain = urlparse(article_url).netloc.replace("www.", "") if article_url else ""
            source_name = domain or article.get("source_id", "Unknown")
            lang = article.get("language", "en")
            summary_text = (content or title_text)[:500]

            # Translate non-English once and cache in DynamoDB
            title_en = ""
            summary_en = ""
            if translation_service.needs_translation(title_text, lang):
                title_en = translation_service.translate_to_english(title_text, lang)
                summary_en = translation_service.translate_to_english(summary_text, lang)

            dynamo.store_perspective(
                topic_id=topic_id,
                source_id=article.get("source_id", "unknown"),
                source_name=source_name,
                article_id=article.get("article_id", ""),
                title=title_text,
                url=article_url,
                summary=summary_text,
                bias_composite=composite,
                bias_color=color,
                language=lang,
                title_en=title_en,
                summary_en=summary_en,
            )
            dynamo.store_bias_score(
                article.get("article_id", ""),
                dim_scores,
                composite,
                color,
            )
        perspectives = dynamo.get_perspectives(topic_id)

    # Build perspective list with credibility
    # Translations are done ONCE during generation and cached in DynamoDB
    # (title_en / summary_en fields). No LLM calls on subsequent visits.
    result_perspectives = []
    for p in perspectives:
        # Use cached translations if available, otherwise original
        title = p.get("title_en") or p.get("title", "")
        summary = p.get("summary_en") or p.get("summary", "")
        translated = bool(p.get("title_en"))

        entry = {
            "source_id": p.get("source_id", ""),
            "source_name": p.get("source_name", ""),
            "article_id": p.get("article_id", ""),
            "title": title,
            "title_original": p.get("title", "") if translated else "",
            "url": p.get("url", ""),
            "summary": summary,
            "bias_composite": float(p.get("bias_composite", 3.0)),
            "bias_color": p.get("bias_color", "green"),
            "language": p.get("language", "en"),
            "translated": translated,
            "credibility": credibility_db.lookup_source(
                p.get("url", p.get("source_name", "")),
            ),
        }
        result_perspectives.append(entry)

    return {
        "topic_id": topic_id,
        "title": topic.get("title", ""),
        "perspective_count": len(result_perspectives),
        "perspectives": result_perspectives,
    }


@router.get("/{topic_id}/entities")
async def get_entity_analysis(topic_id: str, request: Request):
    """Extract and analyze entities. Cached after first run."""
    topic = dynamo.get_topic(topic_id)
    if not topic:
        return {"error": "Topic not found"}

    # Check cache first
    cached = dynamo.get_cached_result(topic_id, "entities")
    if cached:
        cached["cached"] = True
        return cached

    articles = dynamo.get_articles_for_topic(topic_id)
    perspectives = dynamo.get_perspectives(topic_id)
    if not articles:
        return {"error": "No articles to analyze", "topic_id": topic_id}

    graph = knowledge_graph.build_entity_graph(articles, perspectives)
    graph["topic_id"] = topic_id
    graph["title"] = topic.get("title", "")

    # Cache for future visits
    dynamo.store_cached_result(topic_id, "entities", graph)
    return graph


@router.get("/{topic_id}/narrative")
async def get_narrative_timeline(topic_id: str, request: Request):
    """Get narrative timeline — how coverage framing evolves over time."""
    return narrative_tracker.get_timeline(topic_id)


@router.post("/{topic_id}/narrative/snapshot")
async def take_narrative_snapshot(topic_id: str, request: Request):
    """Take a new narrative snapshot for shift detection."""
    return narrative_tracker.take_snapshot(topic_id)


@router.get("/{topic_id}/narrative/shifts")
async def get_narrative_shifts(topic_id: str, request: Request):
    """Detect narrative shifts between snapshots."""
    return narrative_tracker.detect_shifts(topic_id)


@router.get("/{topic_id}/claims")
async def get_claims(topic_id: str, request: Request):
    """Extract and cross-reference claims across articles. Cached."""
    return claim_tracker.extract_claims(topic_id)


@router.get("/{topic_id}/related")
async def get_related_topics(topic_id: str, request: Request):
    """Find topics related to this one through shared entities."""
    related = topic_explorer.find_related_topics(topic_id)
    return {"topic_id": topic_id, "related": related, "count": len(related)}


@router.get("/{topic_id}/graph-analysis")
async def get_graph_analysis(topic_id: str, request: Request):
    """Full graph analysis. Cached after first run."""
    topic = dynamo.get_topic(topic_id)
    if not topic:
        return {"error": "Topic not found"}

    # Check cache first
    cached = dynamo.get_cached_result(topic_id, "graph_analysis")
    if cached:
        cached["cached"] = True
        return cached

    articles = dynamo.get_articles_for_topic(topic_id)
    perspectives = dynamo.get_perspectives(topic_id)
    if not articles:
        return {"error": "No articles to analyze"}

    result = graph_analysis.full_analysis(articles, perspectives)
    result["topic_id"] = topic_id
    result["title"] = topic.get("title", "")

    # Cache for future visits
    dynamo.store_cached_result(topic_id, "graph_analysis", result)
    return result


@router.get("/{topic_id}/report/{template}")
async def generate_report(topic_id: str, template: str, request: Request):
    """Generate a formatted intelligence report.

    Templates: sitrep, intel_summary, deep_dive, executive_brief, threat_warning
    """
    return report_generator.generate_report(topic_id, template)


@router.get("/{topic_id}/contrarian")
async def get_contrarian(topic_id: str, request: Request):
    """Generate a contrarian view challenging the dominant narrative."""
    topic = dynamo.get_topic(topic_id)
    if not topic:
        return {"error": "Topic not found"}

    perspectives = dynamo.get_perspectives(topic_id)
    result = contrarian_service.generate_contrarian(
        topic.get("title", ""),
        perspectives,
    )
    result["topic_id"] = topic_id
    return result


@router.get("/{topic_id}/pulse-export")
async def export_to_pulse(topic_id: str, request: Request):
    """Export topic analysis as Pulse-ready blog draft."""
    return pulse_bridge.export_for_pulse(topic_id)


@router.get("/{topic_id}/gaps")
async def get_coverage_gaps(topic_id: str, request: Request):
    """Detect coverage gaps — what facts are being omitted by most sources."""
    topic = dynamo.get_topic(topic_id)
    if not topic:
        return {"error": "Topic not found"}

    articles = dynamo.get_articles_for_topic(topic_id)
    gaps = coverage_gap.detect_gaps(articles)

    return {
        "topic_id": topic_id,
        "title": topic.get("title", ""),
        "total_sources": len(articles),
        "gaps_detected": len(gaps),
        "gaps": gaps,
    }
