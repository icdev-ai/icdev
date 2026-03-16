#!/usr/bin/env python3
"""NewsAPI service — fetch articles from NewsAPI.org (50,000+ sources).

Free tier: 100 requests/day, 1-month old articles.
Set NEWSAPI_KEY env var to enable.
"""
import logging
import os
import time

import requests

logger = logging.getLogger("intaas.services.newsapi")

NEWSAPI_URL = "https://newsapi.org/v2/everything"
NEWSAPI_KEY = os.environ.get("NEWSAPI_KEY", "")
TIMEOUT = 15
_last_request = 0.0


def search_articles(query: str, max_results: int = 10) -> list[dict]:
    """Search NewsAPI for articles. Returns empty if no API key."""
    global _last_request
    if not NEWSAPI_KEY:
        return []

    now = time.time()
    if now - _last_request < 2:
        time.sleep(2 - (now - _last_request))
    _last_request = time.time()

    try:
        resp = requests.get(
            NEWSAPI_URL,
            params={
                "q": query,
                "pageSize": min(max_results, 100),
                "sortBy": "relevancy",
                "language": "en",
                "apiKey": NEWSAPI_KEY,
            },
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning("NewsAPI error: %s", e)
        return []

    results = []
    for art in data.get("articles", [])[:max_results]:
        source = art.get("source", {})
        results.append({
            "title": art.get("title", ""),
            "url": art.get("url", ""),
            "source_id": source.get("id", source.get("name", "unknown")),
            "source_domain": source.get("name", "unknown"),
            "source_name": source.get("name", "Unknown"),
            "language": "English",
            "tone": 0,
            "published_at": art.get("publishedAt", ""),
            "content": art.get("content", art.get("description", "")),
            "country": "",
            "image": art.get("urlToImage", ""),
            "provider": "newsapi",
        })
    return results
