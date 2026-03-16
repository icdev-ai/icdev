#!/usr/bin/env python3
"""MediaCloud service — search 1B+ stories from open-source media research platform.

Free academic API. Set MEDIACLOUD_KEY env var to enable.
Docs: https://search.mediacloud.org/
"""
import logging
import os
import time

import requests

logger = logging.getLogger("intaas.services.mediacloud")

MEDIACLOUD_URL = "https://search.mediacloud.org/api/search"
MEDIACLOUD_KEY = os.environ.get("MEDIACLOUD_KEY", "")
TIMEOUT = 15
_last_request = 0.0


def search_articles(query: str, max_results: int = 10) -> list[dict]:
    """Search MediaCloud. Returns empty if no API key."""
    global _last_request
    if not MEDIACLOUD_KEY:
        return []

    now = time.time()
    if now - _last_request < 2:
        time.sleep(2 - (now - _last_request))
    _last_request = time.time()

    try:
        resp = requests.get(
            MEDIACLOUD_URL,
            params={"q": query, "rows": min(max_results, 100)},
            headers={"Authorization": f"Bearer {MEDIACLOUD_KEY}"},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        logger.warning("MediaCloud error: %s", e)
        return []

    results = []
    for art in data.get("results", data.get("stories", []))[:max_results]:
        results.append({
            "title": art.get("title", ""),
            "url": art.get("url", ""),
            "source_id": art.get("media_id", "unknown"),
            "source_domain": art.get("media_name", "unknown"),
            "source_name": art.get("media_name", "Unknown"),
            "language": art.get("language", "en"),
            "tone": 0,
            "published_at": art.get("publish_date", ""),
            "content": art.get("title", ""),
            "country": "",
            "image": "",
            "provider": "mediacloud",
        })
    return results
