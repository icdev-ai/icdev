#!/usr/bin/env python3
"""Contrarian view generator — deliberately argues the opposite position.

Given a topic's perspectives, generates a well-reasoned contrarian view
that challenges the dominant narrative with evidence. Forces readers to
consider alternative explanations.
"""
import logging

import requests

from src import config

logger = logging.getLogger("intaas.services.contrarian")


def generate_contrarian(
    topic_title: str,
    perspectives: list[dict],
    max_length: int = 500,
) -> dict:
    """Generate a contrarian view for a topic.

    Returns deterministic template if LLM unavailable,
    LLM-enhanced analysis if Anthropic API is configured.
    """
    # Build summary of dominant narratives
    summaries = []
    for p in perspectives[:10]:
        title = p.get("title", "")
        source = p.get("source_name", "")
        if title:
            summaries.append(f"- {source}: {title}")
    narrative_summary = "\n".join(summaries) if summaries else "No perspectives available"

    # Deterministic fallback
    if not config.LLM_JUDGE_ENABLED or not config.ANTHROPIC_API_KEY:
        return _template_contrarian(topic_title, perspectives)

    # LLM-enhanced contrarian
    prompt = f"""You are a critical thinking expert. Given coverage of this topic, \
generate a CONTRARIAN VIEW that challenges the dominant narrative.

Topic: {topic_title}

Current coverage:
{narrative_summary}

Instructions:
1. Identify the DOMINANT narrative (what most sources agree on)
2. Present a well-reasoned CONTRARIAN position with evidence
3. Ask what evidence would DISPROVE the dominant view
4. Note what ALL sources are IGNORING
5. Be intellectually honest — contrarian doesn't mean conspiracy

Return JSON:
{{"dominant_narrative": "what most sources say",
 "contrarian_view": "the opposite argument with reasoning",
 "key_question": "the question nobody is asking",
 "blind_spot": "what all sources ignore",
 "evidence_needed": "what evidence would settle this debate"}}

Return ONLY valid JSON."""

    try:
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": config.ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": config.LLM_JUDGE_MODEL,
                "max_tokens": 512,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data.get("content", [{}])[0].get("text", "")

        import json
        import re
        content = re.sub(r"^```json?\s*", "", content.strip())
        content = re.sub(r"\s*```$", "", content)
        parsed = json.loads(content)

        return {
            "status": "generated",
            "method": "llm",
            "dominant_narrative": parsed.get("dominant_narrative", ""),
            "contrarian_view": parsed.get("contrarian_view", ""),
            "key_question": parsed.get("key_question", ""),
            "blind_spot": parsed.get("blind_spot", ""),
            "evidence_needed": parsed.get("evidence_needed", ""),
            "model": config.LLM_JUDGE_MODEL,
        }
    except Exception as e:
        logger.warning("Contrarian LLM failed: %s", e)
        return _template_contrarian(topic_title, perspectives)


def _template_contrarian(title: str, perspectives: list[dict]) -> dict:
    """Deterministic contrarian template when LLM unavailable."""
    n = len(perspectives)
    return {
        "status": "template",
        "method": "deterministic",
        "dominant_narrative": (
            f"{n} sources cover this topic. "
            "Consider what narrative they share."
        ),
        "contrarian_view": (
            "What if the opposite of the dominant narrative is true? "
            "What evidence would support that position?"
        ),
        "key_question": (
            "What question is NO source asking about this topic?"
        ),
        "blind_spot": (
            "Look at what ALL sources avoid discussing. "
            "The absence of coverage can be as revealing as the coverage itself."
        ),
        "evidence_needed": (
            "What single piece of evidence would most change "
            "your mind about this topic?"
        ),
    }
