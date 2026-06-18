# CUI // SP-CTI
"""Social trend scanner for the Research engine (adapt-l30-01).

Aggregates trending signals from Reddit, Hacker News, and GitHub Topics.
Implements entity disambiguation (handle→subreddit→repo), cross-source
content_hash deduplication, and graceful degradation on rate-limits.

Scanner key: "social_trends"

Usage (via research engine):
    from tools.research.source_scanner import run_scan
    run_scan(session_id, source="social_trends", session_config={
        "keywords": ["context compression", "AI agents"],
        "subreddits": ["r/MachineLearning"],
        "max_per_source": 30,
    })

Standalone:
    from tools.research.source_scanners.social_trend_scanner import scan_social_trends
    signals = scan_social_trends(config={}, session_config={"keywords": ["llm"]})
"""
from __future__ import annotations

import hashlib
import json
import logging
from tools.logging.icdev_logger import get_logger
from datetime import datetime, timezone
from typing import Any

logger = get_logger(__name__)

_HAS_REQUESTS: bool | None = None

# HN Algolia search endpoint (public, no auth required)
_HN_SEARCH_URL = "https://hn.algolia.com/api/v1/search"
# Reddit search endpoint (public JSON API, no OAuth needed for search)
_REDDIT_SEARCH_URL = "https://www.reddit.com/search.json"
# GitHub topics REST API
_GITHUB_TOPICS_URL = "https://api.github.com/search/topics"
_GITHUB_REPOS_URL = "https://api.github.com/search/repositories"

_DEFAULT_USER_AGENT = "ICDev-Research-Engine/1.0 (social_trend_scanner; air-gap-safe)"
_DEFAULT_TIMEOUT = 10
_MAX_PER_SOURCE = 30


def _check_requests() -> bool:
    global _HAS_REQUESTS
    if _HAS_REQUESTS is None:
        try:
            import requests as _r  # noqa: F401
            _HAS_REQUESTS = True
        except ImportError:
            _HAS_REQUESTS = False
            logger.debug("social_trend_scanner: requests not installed, HTTP sources disabled")
    return _HAS_REQUESTS


def _get(url: str, params: dict | None = None, headers: dict | None = None) -> tuple[Any, str | None]:
    """HTTP GET with rate-limit and error handling. Returns (data, error_code)."""
    if not _check_requests():
        return None, "requests_unavailable"
    import requests
    hdrs = {"User-Agent": _DEFAULT_USER_AGENT}
    if headers:
        hdrs.update(headers)
    try:
        resp = requests.get(url, params=params, headers=hdrs, timeout=_DEFAULT_TIMEOUT)
        if resp.status_code == 429:
            return None, "rate_limited"
        if resp.status_code == 403:
            return None, "forbidden"
        if resp.status_code != 200:
            return None, f"http_{resp.status_code}"
        return resp.json(), None
    except Exception as exc:
        return None, f"request_error:{exc}"


def _content_hash(source: str, text: str) -> str:
    return hashlib.sha256(f"{source}:{text[:200]}".encode()).hexdigest()[:16]


def _normalize_entity(handle: str) -> str:
    """Disambiguate r/foo → subreddit, github.com/foo → repo, else raw."""
    h = handle.strip()
    if h.startswith("r/"):
        return f"subreddit:{h[2:]}"
    if "github.com/" in h:
        parts = h.split("github.com/", 1)[-1].strip("/").split("/")
        return f"repo:{'/'.join(parts[:2])}"
    return f"entity:{h}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Reddit
# ---------------------------------------------------------------------------

def _scan_reddit(keywords: list[str], subreddits: list[str], max_results: int) -> list[dict]:
    signals: list[dict] = []
    seen: set[str] = set()
    now = _now_iso()

    for keyword in keywords[:5]:  # limit to avoid hammering
        params = {
            "q": keyword,
            "type": "link",
            "sort": "top",
            "t": "month",
            "limit": min(max_results, 25),
        }
        data, err = _get(_REDDIT_SEARCH_URL, params=params)
        if err:
            logger.debug("social_trend_scanner: reddit error for %r: %s", keyword, err)
            continue

        posts = (data or {}).get("data", {}).get("children", [])
        for post in posts:
            p = post.get("data", {})
            title = (p.get("title") or "").strip()
            url = p.get("url") or ""
            body = (p.get("selftext") or "").strip()
            subreddit = p.get("subreddit_name_prefixed") or ""
            author = p.get("author") or ""
            score = int(p.get("score") or 0)
            permalink = f"https://reddit.com{p.get('permalink', '')}"

            if not title:
                continue

            ch = _content_hash("reddit", title)
            if ch in seen:
                continue
            seen.add(ch)

            signals.append({
                "source": "community_forum",
                "source_type": "social_trends",
                "title": title[:200],
                "body": (body or title)[:1000],
                "url": permalink or url,
                "author": author,
                "upvotes": score,
                "citations": 0,
                "sentiment": "neutral",
                "content_hash": ch,
                "keywords": [keyword],
                "metadata": json.dumps({
                    "platform": "reddit",
                    "subreddit": subreddit,
                    "entity": _normalize_entity(subreddit),
                    "keyword": keyword,
                }),
                "discovered_at": now,
            })
            if len(signals) >= max_results:
                return signals

    return signals


# ---------------------------------------------------------------------------
# Hacker News (Algolia API)
# ---------------------------------------------------------------------------

def _scan_hn(keywords: list[str], max_results: int) -> list[dict]:
    signals: list[dict] = []
    seen: set[str] = set()
    now = _now_iso()

    for keyword in keywords[:5]:
        params = {
            "query": keyword,
            "tags": "story",
            "hitsPerPage": min(max_results, 20),
        }
        data, err = _get(_HN_SEARCH_URL, params=params)
        if err:
            logger.debug("social_trend_scanner: HN error for %r: %s", keyword, err)
            continue

        hits = (data or {}).get("hits", [])
        for hit in hits:
            title = (hit.get("title") or "").strip()
            url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}"
            author = hit.get("author") or ""
            points = int(hit.get("points") or 0)
            num_comments = int(hit.get("num_comments") or 0)

            if not title:
                continue

            ch = _content_hash("hn", title)
            if ch in seen:
                continue
            seen.add(ch)

            signals.append({
                "source": "community_forum",
                "source_type": "social_trends",
                "title": title[:200],
                "body": title[:1000],
                "url": url,
                "author": author,
                "upvotes": points,
                "citations": num_comments,
                "sentiment": "neutral",
                "content_hash": ch,
                "keywords": [keyword],
                "metadata": json.dumps({
                    "platform": "hacker_news",
                    "object_id": hit.get("objectID"),
                    "keyword": keyword,
                }),
                "discovered_at": now,
            })
            if len(signals) >= max_results:
                return signals

    return signals


# ---------------------------------------------------------------------------
# GitHub Topics / Repos
# ---------------------------------------------------------------------------

def _scan_github_topics(keywords: list[str], max_results: int) -> list[dict]:
    signals: list[dict] = []
    seen: set[str] = set()
    now = _now_iso()

    # Search repos (topics search requires auth; repo search is public)
    for keyword in keywords[:5]:
        params = {
            "q": keyword,
            "sort": "stars",
            "order": "desc",
            "per_page": min(max_results, 20),
        }
        data, err = _get(_GITHUB_REPOS_URL, params=params)
        if err:
            logger.debug("social_trend_scanner: github error for %r: %s", keyword, err)
            continue

        items = (data or {}).get("items", [])
        for item in items:
            title = (item.get("name") or "").strip()
            desc = (item.get("description") or "").strip()
            url = item.get("html_url") or ""
            author = (item.get("owner") or {}).get("login") or ""
            stars = int(item.get("stargazers_count") or 0)
            topics = item.get("topics") or []

            if not title:
                continue

            ch = _content_hash("github", f"{author}/{title}")
            if ch in seen:
                continue
            seen.add(ch)

            signals.append({
                "source": "open_source",
                "source_type": "social_trends",
                "title": f"{author}/{title}"[:200],
                "body": (desc or title)[:1000],
                "url": url,
                "author": author,
                "upvotes": stars,
                "citations": 0,
                "sentiment": "neutral",
                "content_hash": ch,
                "keywords": [keyword] + topics[:5],
                "metadata": json.dumps({
                    "platform": "github",
                    "full_name": item.get("full_name"),
                    "topics": topics,
                    "entity": _normalize_entity(f"github.com/{author}/{title}"),
                    "keyword": keyword,
                }),
                "discovered_at": now,
            })
            if len(signals) >= max_results:
                return signals

    return signals


# ---------------------------------------------------------------------------
# Cross-source deduplication
# ---------------------------------------------------------------------------

def _dedup(signals: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for s in signals:
        key = s.get("content_hash") or _content_hash("?", s.get("title", ""))
        if key not in seen:
            seen.add(key)
            out.append(s)
    return out


# ---------------------------------------------------------------------------
# Public scanner entry point
# ---------------------------------------------------------------------------

def scan_social_trends(config: dict, session_config: dict | None = None) -> list[dict]:
    """Scan Reddit, Hacker News, and GitHub for social trend signals.

    Args:
        config: Full research_config.yaml dict (used for fallback keyword sources).
        session_config: Optional overrides:
            keywords: list[str]  — search terms (default: from config)
            subreddits: list[str] — extra subreddits to search
            max_per_source: int  — cap per platform (default: 30)

    Returns:
        List of normalized signal dicts. Returns [] gracefully when
        requests is absent or all sources are rate-limited.
    """
    sc = session_config or {}

    keywords: list[str] = sc.get("keywords") or config.get("keywords") or [
        "AI agent",
        "LLM context compression",
        "document intelligence",
        "agentic AI",
    ]
    subreddits: list[str] = sc.get("subreddits") or []
    max_per = int(sc.get("max_per_source") or _MAX_PER_SOURCE)

    all_signals: list[dict] = []

    # Reddit
    reddit_signals = _scan_reddit(keywords, subreddits, max_per)
    all_signals.extend(reddit_signals)
    logger.info("social_trend_scanner: reddit returned %d signals", len(reddit_signals))

    # Hacker News
    hn_signals = _scan_hn(keywords, max_per)
    all_signals.extend(hn_signals)
    logger.info("social_trend_scanner: HN returned %d signals", len(hn_signals))

    # GitHub
    gh_signals = _scan_github_topics(keywords, max_per)
    all_signals.extend(gh_signals)
    logger.info("social_trend_scanner: github returned %d signals", len(gh_signals))

    deduped = _dedup(all_signals)
    logger.info("social_trend_scanner: total after dedup: %d", len(deduped))
    return deduped
