#!/usr/bin/env python3
"""Claim tracker — extract specific claims and classify stance per source.

"This claim appears in 12 articles. 8 support it, 3 contradict it, 1 is ambiguous."

Extracts factual claims (statistics, quotes, assertions) and uses
Claude Haiku to classify each source's stance on the claim:
  - supports: source presents the claim as true/positive
  - contradicts: source disputes or presents opposing evidence
  - ambiguous: source mentions but doesn't take a clear position
  - omits: source doesn't mention this claim at all
"""
import logging
import re

import requests

from src import config
from src.db import dynamo

logger = logging.getLogger("intaas.services.claim_tracker")


def extract_claims(topic_id: str) -> dict:
    """Extract claims and classify stance per source."""
    cached = dynamo.get_cached_result(topic_id, "claims")
    if cached:
        cached["cached"] = True
        return cached

    articles = dynamo.get_articles_for_topic(topic_id)
    perspectives = dynamo.get_perspectives(topic_id)
    if not articles:
        return {"error": "No articles to analyze"}

    # Build source→content map
    source_content = {}
    for article in articles:
        source = ""
        for p in perspectives:
            if p.get("article_id") == article.get("article_id"):
                source = p.get("source_name", "")
                break
        source = source or article.get("source_id", "unknown")
        content = article.get("content", "") or article.get("title", "")
        source_content[source] = content

    # Extract claims from all articles combined
    all_text = " ".join(source_content.values())
    claims = _extract_claims_from_text(all_text, max_claims=15)

    # For each claim, check which sources mention it and classify stance
    all_sources = list(source_content.keys())
    cross_ref = []

    for claim in claims:
        claim_text = claim["text"]
        claim_lower = claim_text.lower()
        claim_words = set(re.findall(r"\w+", claim_lower))

        supports = []
        contradicts = []
        ambiguous = []
        omits = []

        for source, content in source_content.items():
            content_lower = content.lower()
            content_words = set(re.findall(r"\w+", content_lower))

            # Check if claim is mentioned (keyword overlap)
            overlap = len(claim_words & content_words)
            coverage = overlap / max(len(claim_words), 1)

            if coverage < 0.3:
                omits.append(source)
                continue

            # Classify stance
            stance = _classify_stance(
                claim_text, content, source,
            )
            if stance == "supports":
                supports.append(source)
            elif stance == "contradicts":
                contradicts.append(source)
            else:
                ambiguous.append(source)

        total_mentioning = len(supports) + len(contradicts) + len(ambiguous)
        if total_mentioning == 0:
            continue

        cross_ref.append({
            "claim": claim_text[:300],
            "type": claim["type"],
            "supports": supports,
            "supports_count": len(supports),
            "contradicts": contradicts,
            "contradicts_count": len(contradicts),
            "ambiguous": ambiguous,
            "ambiguous_count": len(ambiguous),
            "omits": omits[:5],
            "omits_count": len(omits),
            "total_sources": len(all_sources),
            "mentioned_by": total_mentioning,
            "coverage_pct": round(
                total_mentioning / max(len(all_sources), 1) * 100,
            ),
            "consensus": _consensus_label(
                len(supports), len(contradicts), len(ambiguous),
            ),
        })

    cross_ref.sort(key=lambda x: x["mentioned_by"], reverse=True)

    result = {
        "topic_id": topic_id,
        "total_claims": len(claims),
        "unique_claims": len(cross_ref),
        "cross_references": cross_ref[:15],
        "total_sources": len(all_sources),
    }

    dynamo.store_cached_result(topic_id, "claims", result)
    return result


def _extract_claims_from_text(text: str, max_claims: int = 15) -> list[dict]:
    """Extract factual claims using deterministic patterns."""
    claims = []
    seen = set()

    # Statistics
    for match in re.finditer(
        r"([^.]*?\d[\d,.]*\s*(?:percent|%|billion|million|trillion|"
        r"thousand|times|fold)[^.]*\.)",
        text, re.IGNORECASE,
    ):
        claim = match.group(1).strip()
        if len(claim) > 20 and claim.lower()[:50] not in seen:
            seen.add(claim.lower()[:50])
            claims.append({"text": claim[:300], "type": "statistic"})

    # Quotes
    for match in re.finditer(
        r'["\u201c]([^"\u201d]{20,200})["\u201d]', text,
    ):
        quote = match.group(1).strip()
        if quote.lower()[:50] not in seen:
            seen.add(quote.lower()[:50])
            claims.append({"text": quote[:300], "type": "quote"})

    # Assertions (said/claimed patterns)
    for match in re.finditer(
        r"(\w[\w\s]+(?:said|stated|claimed|argued|insisted|warned|noted)"
        r"\s+(?:that\s+)?[^.]{10,150}\.)",
        text, re.IGNORECASE,
    ):
        assertion = match.group(1).strip()
        if assertion.lower()[:50] not in seen:
            seen.add(assertion.lower()[:50])
            claims.append({"text": assertion[:300], "type": "assertion"})

    return claims[:max_claims]


def _classify_stance(
    claim: str, source_content: str, source_name: str,
) -> str:
    """Classify a source's stance on a claim.

    Uses Claude Haiku if available, deterministic fallback otherwise.
    """
    # Try LLM classification
    if config.LLM_JUDGE_ENABLED and config.ANTHROPIC_API_KEY:
        return _classify_stance_llm(claim, source_content, source_name)

    # Deterministic fallback: check for contradiction keywords
    return _classify_stance_deterministic(claim, source_content)


def _classify_stance_llm(
    claim: str, content: str, source: str,
) -> str:
    """Use Claude Haiku for stance classification."""
    prompt = (
        f"Does this source SUPPORT, CONTRADICT, or remain "
        f"AMBIGUOUS about the following claim?\n\n"
        f"CLAIM: {claim[:200]}\n\n"
        f"SOURCE ({source}) CONTENT: {content[:500]}\n\n"
        f"Respond with ONLY one word: supports, contradicts, or ambiguous"
    )

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
                "max_tokens": 10,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        answer = (
            data.get("content", [{}])[0].get("text", "")
            .strip().lower().rstrip(".")
        )
        if "support" in answer:
            return "supports"
        if "contradict" in answer:
            return "contradicts"
        return "ambiguous"
    except Exception as e:
        logger.debug("LLM stance failed: %s", e)
        return _classify_stance_deterministic(claim, content)


def _classify_stance_deterministic(claim: str, content: str) -> str:
    """Deterministic stance classification based on keywords."""
    content_lower = content.lower()
    claim_lower = claim.lower()

    # Check for contradiction signals near claim keywords
    contradiction_markers = [
        "however", "but", "despite", "although", "contrary",
        "disputed", "denied", "rejected", "false", "misleading",
        "inaccurate", "incorrect", "not true", "debunked",
        "challenged", "questioned", "critics say", "opponents argue",
    ]

    support_markers = [
        "confirmed", "verified", "according to", "data shows",
        "evidence supports", "study found", "research confirms",
        "officials say", "experts agree", "consistent with",
    ]

    # Find claim keywords in content
    claim_words = set(re.findall(r"\w{4,}", claim_lower))
    relevant_sections = []
    for word in list(claim_words)[:5]:
        idx = content_lower.find(word)
        if idx >= 0:
            start = max(0, idx - 100)
            end = min(len(content_lower), idx + 200)
            relevant_sections.append(content_lower[start:end])

    context = " ".join(relevant_sections)

    contra_count = sum(1 for m in contradiction_markers if m in context)
    support_count = sum(1 for m in support_markers if m in context)

    if contra_count > support_count and contra_count >= 2:
        return "contradicts"
    if support_count > contra_count and support_count >= 1:
        return "supports"
    return "ambiguous"


def _consensus_label(supports: int, contradicts: int, ambiguous: int) -> str:
    """Generate consensus label from stance counts."""
    total = supports + contradicts + ambiguous
    if total == 0:
        return "no_data"
    support_pct = supports / total

    if contradicts == 0 and supports > 0:
        return "unanimous_support"
    if supports == 0 and contradicts > 0:
        return "unanimous_contradiction"
    if support_pct > 0.7:
        return "strong_support"
    if support_pct < 0.3:
        return "strong_contradiction"
    return "disputed"
