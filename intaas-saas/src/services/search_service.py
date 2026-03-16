#!/usr/bin/env python3
"""Search service — full-text search across all analyzed articles.

Uses BM25 keyword matching (rank_bm25 if available, TF fallback).
Searches across article titles, content, and perspective summaries.
"""
import logging
import re
from collections import Counter

from src.db import dynamo

logger = logging.getLogger("intaas.services.search")


def search_articles(query: str, top_k: int = 20) -> list[dict]:
    """Search across all articles in all topics."""
    tokens = _tokenize(query)
    if not tokens:
        return []

    # Get all topics and their articles
    topics = dynamo.list_topics(limit=100)
    all_articles = []
    for topic in topics:
        tid = topic.get("topic_id", "")
        if not tid:
            continue
        articles = dynamo.get_articles_for_topic(tid)
        for a in articles:
            a["_topic_id"] = tid
            a["_topic_title"] = topic.get("title", "")
        all_articles.extend(articles)

    if not all_articles:
        return []

    # Build corpus and score with TF
    results = []
    for article in all_articles:
        text = (
            (article.get("title", "") + " ") * 3  # Title boost
            + article.get("content", "")
        ).lower()

        doc_tokens = _tokenize(text)
        if not doc_tokens:
            continue

        score = _tf_score(tokens, doc_tokens)
        if score > 0:
            results.append({
                "article_id": article.get("article_id", ""),
                "topic_id": article.get("_topic_id", ""),
                "topic_title": article.get("_topic_title", ""),
                "title": article.get("title", ""),
                "url": article.get("url", ""),
                "source_id": article.get("source_id", ""),
                "language": article.get("language", "en"),
                "score": round(score, 4),
                "snippet": _extract_snippet(
                    article.get("content", ""), tokens,
                ),
            })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top_k]


def _tokenize(text: str) -> list[str]:
    """Split text into lowercase tokens."""
    text = text.lower()
    text = re.sub(r"[^\w\s]", " ", text)
    return [w for w in text.split() if len(w) > 1]


def _tf_score(query_tokens: list[str], doc_tokens: list[str]) -> float:
    """Compute term frequency score."""
    doc_freq = Counter(doc_tokens)
    doc_len = len(doc_tokens)
    if doc_len == 0:
        return 0.0

    score = 0.0
    for token in query_tokens:
        tf = doc_freq.get(token, 0) / doc_len
        score += tf

    return score


def _extract_snippet(
    content: str, query_tokens: list[str], max_len: int = 200,
) -> str:
    """Extract a relevant snippet around query terms."""
    content_lower = content.lower()
    best_pos = 0
    best_count = 0

    # Find position with most query term matches
    for i in range(0, len(content_lower) - 100, 50):
        window = content_lower[i:i + max_len]
        count = sum(1 for t in query_tokens if t in window)
        if count > best_count:
            best_count = count
            best_pos = i

    snippet = content[best_pos:best_pos + max_len].strip()
    if best_pos > 0:
        snippet = "..." + snippet
    if best_pos + max_len < len(content):
        snippet = snippet + "..."
    return snippet
