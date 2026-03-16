#!/usr/bin/env python3
"""Pulse bridge — push analyzed intelligence to ICDEV Pulse for article generation.

Takes a fully analyzed topic (perspectives + bias scores + contrarian view)
and formats it as a Pulse-ready draft that Claude Code can refine and publish.
"""
import logging

from src.db import dynamo
from src.services import contrarian_service, credibility_db

logger = logging.getLogger("intaas.services.pulse_bridge")


def export_for_pulse(topic_id: str) -> dict:
    """Export a topic analysis as a Pulse-ready blog draft.

    Returns a structured dict with:
      - title suggestion
      - body markdown (perspectives + bias + contrarian)
      - metadata (sources, languages, scores)
    """
    topic = dynamo.get_topic(topic_id)
    if not topic:
        return {"error": "Topic not found"}

    perspectives = dynamo.get_perspectives(topic_id)
    if not perspectives:
        return {"error": "No perspectives available"}

    title = topic.get("title", "Untitled")

    # Build markdown body
    body = f"# Multiperspectivity Analysis: {title}\n\n"
    body += (
        f"*{len(perspectives)} sources analyzed across "
        f"{int(topic.get('language_count', 1))} languages.*\n\n"
    )

    # Perspectives section
    body += "## How Different Sources Cover This Story\n\n"
    for p in perspectives[:10]:
        source = p.get("source_name", "Unknown")
        ptitle = p.get("title", "")
        score = float(p.get("bias_composite", 3.0))
        color = p.get("bias_color", "green")
        cred = credibility_db.lookup_source(p.get("url", ""))
        cred_label = cred.get("credibility", "?")
        political = cred.get("political", "unknown")

        body += f"### {source} ({cred_label}, {political})\n"
        body += f"**{ptitle}** — Bias: {color.upper()} ({score:.1f}/5.0)\n\n"
        summary = p.get("summary", "")[:300]
        if summary:
            body += f"> {summary}\n\n"

    # Contrarian section
    contrarian = contrarian_service.generate_contrarian(
        title, perspectives,
    )
    if contrarian.get("contrarian_view"):
        body += "## The Contrarian View\n\n"
        body += f"**Dominant narrative:** {contrarian.get('dominant_narrative', '')}\n\n"
        body += f"**Contrarian position:** {contrarian.get('contrarian_view', '')}\n\n"
        if contrarian.get("key_question"):
            body += f"**Key question:** *{contrarian['key_question']}*\n\n"
        if contrarian.get("blind_spot"):
            body += f"**Blind spot:** {contrarian['blind_spot']}\n\n"

    # Metadata
    source_domains = [p.get("source_name", "") for p in perspectives]
    avg_bias = sum(float(p.get("bias_composite", 3.0)) for p in perspectives) / max(len(perspectives), 1)

    return {
        "status": "exported",
        "topic_id": topic_id,
        "pulse_draft": {
            "title": f"Multiperspectivity: {title}",
            "body_markdown": body,
            "tags": ["multiperspectivity", "bias-analysis", "intaas"],
            "category": "intelligence",
        },
        "metadata": {
            "sources_count": len(perspectives),
            "languages_count": int(topic.get("language_count", 1)),
            "avg_bias_score": round(avg_bias, 2),
            "source_domains": source_domains[:10],
        },
    }
