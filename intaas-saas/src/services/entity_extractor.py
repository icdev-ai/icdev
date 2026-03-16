#!/usr/bin/env python3
"""Entity extraction service — extract people, organizations, locations, events from articles.

Two modes:
  1. Deterministic (no LLM) — regex + capitalization heuristics (fast, free)
  2. LLM-enhanced (Claude Haiku) — structured entity extraction with types + confidence

Extracted entities power the knowledge graph for:
  - Cross-source entity comparison (who mentions whom)
  - Omission detection (who's missing from which sources)
  - Source independence scoring (same entities = same press release)
  - Relationship discovery (expert quoted → organization → policy beneficiary)
"""
import logging
import re

import requests

from src import config

logger = logging.getLogger("intaas.services.entity_extractor")

ENTITY_TYPES = [
    "person", "organization", "location", "event",
    "policy", "statistic", "claim", "document",
]


def extract_entities_deterministic(text: str, max_entities: int = 30) -> list[dict]:
    """Extract entities using deterministic heuristics (no LLM).

    Strategy:
      1. Proper nouns (2+ word capitalized sequences)
      2. Quoted phrases (claims, statements)
      3. Named organizations (Inc, Corp, Ltd, Agency, Department)
      4. Locations (country/city patterns)
      5. Statistics (numbers with context)
    """
    entities = []
    seen = set()

    # Proper nouns (2+ capitalized words in sequence)
    for match in re.finditer(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b", text):
        name = match.group(1).strip()
        if name.lower() not in seen and len(name) > 4:
            seen.add(name.lower())
            entity_type = _guess_type(name)
            entities.append({
                "name": name,
                "type": entity_type,
                "confidence": 0.6,
                "method": "deterministic",
            })

    # Organizations (keywords)
    org_patterns = [
        r"\b([A-Z][A-Za-z\s]+(?:Inc|Corp|Ltd|LLC|Agency|Department|Ministry|Bureau|Commission|Committee|Foundation|Institute|University|Bank))\b",
    ]
    for pattern in org_patterns:
        for match in re.finditer(pattern, text):
            name = match.group(1).strip()
            if name.lower() not in seen and len(name) > 5:
                seen.add(name.lower())
                entities.append({
                    "name": name,
                    "type": "organization",
                    "confidence": 0.7,
                    "method": "deterministic",
                })

    # Statistics
    for match in re.finditer(
        r"(\$[\d,.]+\s*(?:billion|million|trillion)?|\d+(?:\.\d+)?%|\d+(?:,\d{3})+)",
        text,
    ):
        val = match.group(1).strip()
        if val not in seen:
            seen.add(val)
            # Get surrounding context (±30 chars)
            start = max(0, match.start() - 30)
            end = min(len(text), match.end() + 30)
            context = text[start:end].strip()
            entities.append({
                "name": val,
                "type": "statistic",
                "confidence": 0.8,
                "context": context,
                "method": "deterministic",
            })

    return entities[:max_entities]


def extract_entities_llm(text: str, max_entities: int = 20) -> list[dict]:
    """Extract entities using Claude Haiku for structured extraction."""
    if not config.LLM_JUDGE_ENABLED or not config.ANTHROPIC_API_KEY:
        return extract_entities_deterministic(text, max_entities)

    prompt = f"""Extract key entities from this news article. Return JSON array.

Entity types: person, organization, location, event, policy, statistic, claim, document

For each entity:
- name: the entity name
- type: one of the types above
- confidence: 0.0-1.0
- context: brief context (how it appears in the article)

Article text:
{text[:4000]}

Return ONLY a JSON array like:
[{{"name": "...", "type": "person", "confidence": 0.9, "context": "..."}}]"""

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
                "max_tokens": 1024,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()
        content = data.get("content", [{}])[0].get("text", "")

        import json
        content = re.sub(r"^```json?\s*", "", content.strip())
        content = re.sub(r"\s*```$", "", content)
        entities = json.loads(content)

        if isinstance(entities, list):
            for e in entities:
                e["method"] = "llm"
            return entities[:max_entities]
    except Exception as e:
        logger.warning("LLM entity extraction failed: %s", e)

    return extract_entities_deterministic(text, max_entities)


def extract_from_article(article: dict) -> list[dict]:
    """Extract entities from an article dict."""
    content = article.get("content", "")
    title = article.get("title", "")
    text = f"{title}\n\n{content}" if title else content

    if len(text) < 50:
        return []

    # Use LLM for substantial articles, deterministic for short ones
    if len(text) > 300 and config.LLM_JUDGE_ENABLED:
        return extract_entities_llm(text)
    return extract_entities_deterministic(text)


def _guess_type(name: str) -> str:
    """Guess entity type from name patterns."""
    name_lower = name.lower()
    if any(w in name_lower for w in [
        "university", "institute", "department", "agency",
        "commission", "ministry", "bank", "fund", "corp",
    ]):
        return "organization"
    if any(w in name_lower for w in [
        "act", "agreement", "treaty", "resolution", "policy",
        "regulation", "directive", "order",
    ]):
        return "policy"
    # Default: assume person for proper nouns
    return "person"
