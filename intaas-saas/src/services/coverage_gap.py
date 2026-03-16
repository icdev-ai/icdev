#!/usr/bin/env python3
"""Coverage gap detector — finds facts omitted by most sources.

Deterministic approach:
  1. Extract key phrases from each article
  2. Build frequency matrix (which phrases appear in which articles)
  3. Flag phrases that appear in < 20% of sources as potential gaps
  4. Score gap significance by: rarity × article count × phrase importance
"""
import logging
import re
from collections import Counter

logger = logging.getLogger("intaas.services.coverage_gap")


def detect_gaps(articles: list[dict], min_sources: int = 2) -> list[dict]:
    """Detect coverage gaps across articles about the same topic.

    Args:
        articles: List of article dicts with 'content' and 'title' keys.
        min_sources: Minimum articles required for gap analysis.

    Returns:
        List of gap dicts with fact_omitted, sources_mentioning, significance.
    """
    if len(articles) < min_sources:
        return []

    # Extract key phrases from each article
    article_phrases = []
    for article in articles:
        text = f"{article.get('title', '')} {article.get('content', '')}"
        phrases = _extract_key_phrases(text)
        article_phrases.append(set(phrases))

    # Build frequency matrix
    all_phrases = Counter()
    for phrases in article_phrases:
        for phrase in phrases:
            all_phrases[phrase] += 1

    total_sources = len(articles)
    gaps = []

    for phrase, count in all_phrases.items():
        coverage_ratio = count / total_sources

        # Gap = phrase mentioned by few sources but seems important
        if 0 < coverage_ratio < 0.3 and count >= 1:
            significance = (1 - coverage_ratio) * min(count / total_sources, 1.0)
            gaps.append({
                "fact_omitted": phrase,
                "sources_mentioning": count,
                "sources_total": total_sources,
                "coverage_ratio": round(coverage_ratio, 3),
                "significance": round(significance, 3),
                "explanation": (
                    f"Only {count} of {total_sources} sources mention this. "
                    f"{'This may be a deliberate omission.' if coverage_ratio < 0.1 else 'This could be a blind spot.'}"
                ),
            })

    # Sort by significance
    gaps.sort(key=lambda g: g["significance"], reverse=True)
    return gaps[:20]


def _extract_key_phrases(text: str, max_phrases: int = 50) -> list[str]:
    """Extract key phrases from text using simple NLP heuristics.

    Uses capitalized proper nouns, quoted phrases, and named entities.
    """
    phrases = []

    # Quoted phrases
    quoted = re.findall(r'"([^"]{10,80})"', text)
    phrases.extend(quoted[:10])

    # Capitalized proper nouns (2+ word sequences)
    proper_nouns = re.findall(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b", text)
    phrases.extend(proper_nouns[:15])

    # Numbers with context (statistics, dates, amounts)
    stats = re.findall(r"(\d[\d,.]*\s*(?:percent|%|million|billion|trillion|thousand|hundred))", text.lower())
    phrases.extend(stats[:10])

    # Dollar amounts
    dollars = re.findall(r"(\$[\d,.]+\s*(?:million|billion|trillion)?)", text)
    phrases.extend(dollars[:5])

    # Deduplicate and limit
    seen = set()
    unique = []
    for p in phrases:
        normalized = p.strip().lower()
        if normalized not in seen and len(normalized) > 5:
            seen.add(normalized)
            unique.append(p.strip())

    return unique[:max_phrases]
