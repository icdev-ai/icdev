#!/usr/bin/env python3
"""GDELT service — fetch articles from the Global Database of Events, Language, and Tone.

GDELT DOC 2.0 API:
  - Full-text search across 65 machine-translated languages
  - 15-minute update cycle (near real-time)
  - Free, unlimited access
  - Tone/sentiment metadata per article

Docs: https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/
"""
import hashlib
import logging
import re
import time
from urllib.parse import quote_plus, urlparse

import requests

logger = logging.getLogger("intaas.services.gdelt")

GDELT_DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
TIMEOUT = 45
MIN_REQUEST_INTERVAL = 6  # GDELT requires 1 req per 5s — use 6s for safety

# Module-level rate limiter + cache (persists across warm Lambda invocations)
_last_request_time = 0.0
_cache: dict[str, tuple[float, list]] = {}  # {query: (timestamp, results)}
CACHE_TTL = 900  # 15 min (matches GDELT's cache-control)


def _rate_limit_wait():
    """Block until safe to call GDELT (6s between requests)."""
    global _last_request_time
    now = time.time()
    elapsed = now - _last_request_time
    if elapsed < MIN_REQUEST_INTERVAL:
        wait = MIN_REQUEST_INTERVAL - elapsed
        logger.info("GDELT rate limit: waiting %.1fs", wait)
        time.sleep(wait)
    _last_request_time = time.time()


def _cache_key(keywords: str, timespan: str) -> str:
    return f"{keywords.lower().strip()}|{timespan}"


def _get_cached(key: str) -> list | None:
    if key in _cache:
        ts, results = _cache[key]
        if time.time() - ts < CACHE_TTL:
            logger.info("GDELT cache hit: %s (%d results)", key, len(results))
            return results
        del _cache[key]
    return None


def extract_keywords_from_url(url: str) -> str:
    """Extract search keywords from a URL path.

    Converts: https://foxnews.com/live-news/us-iran-israel-war-latest-march-15
    To: "us iran israel war"
    """
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    segments = path.split("/")
    slug = segments[-1] if segments else ""
    words = re.split(r"[-_/]", slug)

    stop_words = {
        "live", "news", "latest", "update", "updates", "breaking",
        "video", "watch", "read", "more", "full", "story", "article",
        "com", "www", "html", "htm", "php", "asp", "index",
    }
    date_pattern = re.compile(
        r"^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"
        r"|\d{1,4}|2[0-9]{3})$",
        re.IGNORECASE,
    )

    keywords = [
        w for w in words
        if len(w) > 1
        and w.lower() not in stop_words
        and not date_pattern.match(w)
    ]
    return " ".join(keywords[:8])


def extract_keywords_from_text(text: str) -> str:
    """Extract key search terms from article text or title."""
    words = text[:200].split()
    stop = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been",
        "has", "have", "had", "do", "does", "did", "will", "would",
        "could", "should", "may", "might", "can", "shall", "to",
        "of", "in", "for", "on", "with", "at", "by", "from", "as",
        "into", "through", "during", "before", "after", "and", "but",
        "or", "nor", "not", "so", "yet", "both", "either", "neither",
        "this", "that", "these", "those", "it", "its", "he", "she",
    }
    keywords = [
        w.strip(".,;:!?\"'()[]")
        for w in words
        if len(w) > 2 and w.lower() not in stop
    ]
    return " ".join(keywords[:8])


def search_articles(
    query: str,
    max_results: int = 20,
    translate: bool = True,
    timespan: str = "7d",
    source_url: str = "",
) -> list[dict]:
    """Search GDELT DOC 2.0 API for articles matching a query.

    Handles URL inputs by extracting keywords automatically.
    """
    if query.startswith("http://") or query.startswith("https://"):
        keywords = extract_keywords_from_url(query)
    elif len(query.split()) <= 2 and "/" in query:
        keywords = extract_keywords_from_url(f"https://{query}")
    else:
        keywords = (
            extract_keywords_from_text(query)
            if len(query) > 100
            else query
        )

    if not keywords:
        keywords = query[:50]

    logger.info("GDELT search: '%s' (from: '%s')", keywords, query[:80])

    # Check cache first (avoids unnecessary API calls)
    cache_k = _cache_key(keywords, timespan)
    cached = _get_cached(cache_k)
    if cached is not None:
        return cached

    params = {
        "query": keywords,
        "mode": "ArtList",
        "maxrecords": str(min(max_results, 250)),
        "format": "json",
        "timespan": timespan,
    }

    # Rate limit: wait if needed (6s between GDELT requests)
    _rate_limit_wait()

    try:
        url = (
            f"{GDELT_DOC_URL}?"
            + "&".join(f"{k}={quote_plus(v)}" for k, v in params.items())
        )
        logger.info("GDELT URL: %s", url)
        resp = requests.get(url, timeout=TIMEOUT)
        resp.raise_for_status()
        raw = resp.text.strip()
        if not raw or not raw.startswith("{"):
            logger.warning(
                "GDELT empty/non-JSON (%d bytes): %s",
                len(raw), raw[:100],
            )
            return []
        import json as json_mod
        data = json_mod.loads(raw)
    except requests.RequestException as e:
        logger.warning("GDELT API error: %s", e)
        return []
    except (ValueError, KeyError) as e:
        logger.warning("GDELT parse error: %s", e)
        return []

    articles = data.get("articles", [])
    logger.info("GDELT returned %d articles", len(articles))

    results = []
    seen_domains = set()

    for art in articles[:max_results]:
        domain = art.get("domain", "unknown")
        source_id = hashlib.sha256(domain.encode()).hexdigest()[:12]

        if domain in seen_domains and len(results) < max_results // 2:
            continue
        seen_domains.add(domain)

        results.append({
            "title": art.get("title", ""),
            "url": art.get("url", ""),
            "source_id": source_id,
            "source_domain": domain,
            "source_name": domain.replace(
                "www.", "",
            ).split(".")[0].title(),
            "language": art.get("language", "English"),
            "tone": float(art.get("tone", 0)),
            "published_at": art.get("seendate", ""),
            "content": art.get("title", ""),
            "country": art.get("sourcecountry", ""),
            "image": art.get("socialimage", ""),
        })

    # Cache results (15 min TTL)
    if results:
        _cache[cache_k] = (time.time(), results)

    return results


def get_tone_chart(query: str, timespan: str = "30d") -> dict:
    """Get GDELT tone timeline for a query."""
    if query.startswith("http"):
        query = extract_keywords_from_url(query)

    params = {
        "query": query,
        "mode": "TimelineTone",
        "format": "json",
        "timespan": timespan,
    }
    _rate_limit_wait()
    try:
        url = (
            f"{GDELT_DOC_URL}?"
            + "&".join(f"{k}={quote_plus(v)}" for k, v in params.items())
        )
        resp = requests.get(url, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.warning("GDELT tone chart error: %s", e)
        return {}
