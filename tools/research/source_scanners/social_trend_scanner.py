#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""Social Trend Scanner — multi-source scraping for Reddit, GitHub, and Hacker News.

Fetches trending content from three social/developer platforms and returns
normalized signal dicts compatible with the research_signals schema. Each
source degrades gracefully: missing API keys skip authenticated calls and fall
back to public endpoints; network errors log an error signal and continue.

Sources:
    - Reddit: public JSON API (no key required); optional PRAW for OAuth
    - GitHub: unauthenticated search (60 req/hr); elevated with GITHUB_TOKEN
    - Hacker News: Algolia HN Search API (no key required)

Usage:
    python tools/research/source_scanners/social_trend_scanner.py --scan --json
    python tools/research/source_scanners/social_trend_scanner.py --scan --source reddit --keywords "AI,govtech" --json
    python tools/research/source_scanners/social_trend_scanner.py --scan --subreddits "artificial,MachineLearning" --json
    python tools/research/source_scanners/social_trend_scanner.py --scan --github-topics "government,ai" --min-stars 50 --json
    python tools/research/source_scanners/social_trend_scanner.py --scan --hn-story-type top --hn-max 30 --json
"""

import argparse
import hashlib
import json
import math
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# ---------------------------------------------------------------------------
# Graceful optional imports
# ---------------------------------------------------------------------------
try:
    import requests as _requests_lib
    _HAS_REQUESTS = True
except ImportError:
    _requests_lib = None  # type: ignore[assignment]
    _HAS_REQUESTS = False

try:
    from tools.http.client import request as _http_request
    _HAS_HTTP_CLIENT = True
except ImportError:
    _http_request = None  # type: ignore[assignment]
    _HAS_HTTP_CLIENT = False

try:
    import praw as _praw_lib
    _HAS_PRAW = True
except ImportError:
    _praw_lib = None  # type: ignore[assignment]
    _HAS_PRAW = False

try:
    from tools.audit.audit_logger import log_event as _audit_log
    _HAS_AUDIT = True
except ImportError:
    _HAS_AUDIT = False

    def _audit_log(**kwargs):  # type: ignore[misc]
        return -1


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_TIMEOUT = 20
MAX_BODY_LEN = 2000
MAX_TITLE_LEN = 500

REDDIT_BASE = "https://www.reddit.com"
GITHUB_API = "https://api.github.com"
HN_ALGOLIA = "https://hn.algolia.com/api/v1"
HN_FIREBASE = "https://hacker-news.firebaseio.com/v0"

_DEFAULT_SUBREDDITS = ["technology", "programming", "artificial", "MachineLearning"]
_DEFAULT_GITHUB_TOPICS = ["govtech", "government-ai", "openai", "llm"]
_DEFAULT_KEYWORDS = ["AI", "government", "technology", "security"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _signal_id() -> str:
    return f"rsig-{uuid.uuid4().hex[:12]}"


def _content_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _make_signal(
    source_type: str,
    title: str,
    body: str,
    url: str,
    author: str | None,
    upvotes: int,
    citations: int,
    keywords: list,
    metadata: dict,
) -> dict:
    hash_input = f"{source_type}_{url or title}"
    return {
        "id": _signal_id(),
        "session_id": None,
        "source": "social_trend",
        "source_type": source_type,
        "title": title[:MAX_TITLE_LEN],
        "body": body[:MAX_BODY_LEN],
        "url": url,
        "author": author,
        "upvotes": upvotes,
        "citations": citations,
        "sentiment": None,
        "content_hash": _content_hash(hash_input),
        "keywords": json.dumps(keywords[:20]),
        "metadata": json.dumps(metadata),
        "discovered_at": _now(),
    }


def _error_signal(source_type: str, error_msg: str) -> dict:
    return {
        "id": _signal_id(),
        "session_id": None,
        "source": "social_trend",
        "source_type": "scan_error",
        "title": f"Scan error: {source_type}",
        "body": str(error_msg)[:MAX_BODY_LEN],
        "url": "",
        "author": None,
        "upvotes": 0,
        "citations": 0,
        "sentiment": None,
        "content_hash": _content_hash(f"{source_type}_error_{_now()[:10]}"),
        "keywords": "[]",
        "metadata": json.dumps({"error": str(error_msg), "source_type": source_type}),
        "discovered_at": _now(),
    }


def _dedup_key(title: str, url: str) -> str:
    """Stable deduplication key: SHA-256 of normalized title + url."""
    norm_title = " ".join((title or "").lower().split())
    norm_url = (url or "").lower().rstrip("/")
    return hashlib.sha256(f"{norm_title}|{norm_url}".encode("utf-8")).hexdigest()[:16]


def _compute_dual_score(
    signal: dict,
    keywords: list,
    max_upvotes: float,
    max_citations: float,
) -> dict:
    """Compute relevance + fun dual score for a single signal.

    Relevance: fraction of keywords appearing in title+body (0..1).
    Fun: log-normalized engagement (upvotes 70%, citations 30%) (0..1).
    Total: equal-weight average of relevance and fun (0..1).
    """
    title = signal.get("title", "") or ""
    body = signal.get("body", "") or ""
    combined_lower = (title + " " + body).lower()

    if keywords:
        hits = sum(1 for kw in keywords if kw.lower() in combined_lower)
        relevance = hits / len(keywords)
    else:
        relevance = 0.5

    upvotes = max(0, signal.get("upvotes", 0) or 0)
    citations = max(0, signal.get("citations", 0) or 0)

    norm_up = math.log1p(upvotes) / math.log1p(max_upvotes) if max_upvotes > 0 else 0.0
    norm_cit = math.log1p(citations) / math.log1p(max_citations) if max_citations > 0 else 0.0
    fun = norm_up * 0.7 + norm_cit * 0.3

    total = relevance * 0.5 + fun * 0.5

    return {
        "relevance": round(relevance, 4),
        "fun": round(fun, 4),
        "total": round(total, 4),
    }


def merge_and_score_clusters(
    sources_map: dict,
    keywords: list | None = None,
) -> list:
    """Merge per-source signal clusters, deduplicate, and apply dual scoring.

    Deduplication key: first-occurrence-wins on SHA-256 of normalized title+url.
    Sorting: descending by total score.

    Args:
        sources_map: Dict keyed by source name; values are either a list of
            signal dicts or a sub-dict with a ``signals`` key (the format
            returned by ``scan_social_trends``).
        keywords: Optional keywords used for relevance scoring.

    Returns:
        Deduplicated list of signal dicts each enriched with a ``scores`` key
        ``{relevance, fun, total}``, sorted by ``total`` descending.
    """
    all_signals: list = []
    for source_data in sources_map.values():
        if isinstance(source_data, dict):
            sigs = source_data.get("signals", [])
        elif isinstance(source_data, list):
            sigs = source_data
        else:
            continue
        all_signals.extend(sigs)

    seen_keys: set = set()
    deduped: list = []
    for sig in all_signals:
        if sig.get("source_type") == "scan_error":
            continue
        key = _dedup_key(sig.get("title", ""), sig.get("url", ""))
        if key in seen_keys:
            continue
        seen_keys.add(key)
        deduped.append(sig)

    if not deduped:
        return []

    max_upvotes = float(max((s.get("upvotes", 0) or 0) for s in deduped) or 1)
    max_citations = float(max((s.get("citations", 0) or 0) for s in deduped) or 1)
    kw_list = list(keywords or [])

    scored: list = []
    for sig in deduped:
        enriched = dict(sig)
        enriched["scores"] = _compute_dual_score(sig, kw_list, max_upvotes, max_citations)
        scored.append(enriched)

    scored.sort(key=lambda s: s["scores"]["total"], reverse=True)
    return scored


def _http_get(url: str, headers: dict | None = None, params: dict | None = None) -> tuple:
    """GET with graceful failure.

    Returns (data, error_str). data is parsed JSON dict or {'_raw': text}.
    """
    if _HAS_HTTP_CLIENT and _http_request is not None:
        try:
            resp = _http_request("GET", url, headers=headers, params=params, timeout=DEFAULT_TIMEOUT)
            if resp.status_code == 429:
                return None, "rate_limited"
            if resp.status_code == 403:
                return None, "forbidden"
            if resp.status_code == 404:
                return None, "not_found"
            resp.raise_for_status()
            try:
                return resp.json(), None
            except (json.JSONDecodeError, ValueError):
                return {"_raw": resp.text}, None
        except Exception as exc:
            return None, str(exc)

    if _HAS_REQUESTS and _requests_lib is not None:
        try:
            resp = _requests_lib.get(url, headers=headers, params=params, timeout=DEFAULT_TIMEOUT)
            if resp.status_code == 429:
                return None, "rate_limited"
            if resp.status_code == 403:
                return None, "forbidden"
            if resp.status_code == 404:
                return None, "not_found"
            resp.raise_for_status()
            try:
                return resp.json(), None
            except (json.JSONDecodeError, ValueError):
                return {"_raw": resp.text}, None
        except Exception as exc:
            return None, str(exc)

    return None, "no_http_library"


def _audit(event_type: str, action: str, details: dict | None = None) -> None:
    if _HAS_AUDIT:
        try:
            _audit_log(
                event_type=event_type,
                actor="social-trend-scanner",
                action=action,
                details=json.dumps(details) if details else None,
                project_id="research-engine",
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Reddit
# ---------------------------------------------------------------------------
def fetch_reddit_trends(
    subreddits: list | None = None,
    keywords: list | None = None,
    sort: str = "hot",
    max_per_sub: int = 25,
    min_upvotes: int = 5,
    delay: float = 2.0,
) -> list:
    """Fetch trending posts from Reddit subreddits.

    Uses the public Reddit JSON API (no credentials required). Falls back
    to PRAW OAuth when REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET are set
    in the environment, which allows up to 60 req/min instead of ~1 req/sec.

    Args:
        subreddits: List of subreddit names (without r/ prefix).
        keywords: Optional keyword filter — posts not matching any keyword are skipped.
        sort: Sort order ('hot', 'new', 'top', 'rising').
        max_per_sub: Maximum posts to collect per subreddit.
        min_upvotes: Skip posts below this score.
        delay: Seconds to wait between subreddit requests.

    Returns:
        List of normalized signal dicts with source_type='reddit'.
    """
    signals: list = []
    subs = subreddits or _DEFAULT_SUBREDDITS
    kw_filter = [k.lower() for k in (keywords or [])]

    # Try PRAW (authenticated, higher rate limits)
    praw_reddit = None
    if _HAS_PRAW and _praw_lib is not None:
        client_id = os.environ.get("REDDIT_CLIENT_ID", "")
        client_secret = os.environ.get("REDDIT_CLIENT_SECRET", "")
        user_agent = os.environ.get("REDDIT_USER_AGENT", "ICDEV-ResearchEngine/1.0")
        if client_id and client_secret:
            try:
                praw_reddit = _praw_lib.Reddit(
                    client_id=client_id,
                    client_secret=client_secret,
                    user_agent=user_agent,
                )
            except Exception:
                praw_reddit = None

    headers = {"User-Agent": "ICDEV-ResearchEngine/1.0 (GovTech research; CUI // SP-CTI)"}

    for subreddit in subs:
        sub_name = subreddit.lstrip("r/").strip()

        # --- PRAW path ---
        if praw_reddit is not None:
            try:
                sub_obj = praw_reddit.subreddit(sub_name)
                fetch_fn = {
                    "hot": sub_obj.hot,
                    "new": sub_obj.new,
                    "top": sub_obj.top,
                    "rising": sub_obj.rising,
                }.get(sort, sub_obj.hot)
                posts = list(fetch_fn(limit=max_per_sub))
                for post in posts:
                    score = getattr(post, "score", 0) or 0
                    if score < min_upvotes:
                        continue
                    title = getattr(post, "title", "") or ""
                    selftext = getattr(post, "selftext", "") or ""
                    if kw_filter:
                        combined = (title + " " + selftext).lower()
                        if not any(kw in combined for kw in kw_filter):
                            continue
                    post_url = f"https://www.reddit.com{post.permalink}" if hasattr(post, "permalink") else ""
                    signals.append(
                        _make_signal(
                            source_type="reddit",
                            title=title,
                            body=selftext,
                            url=post_url,
                            author=str(post.author) if post.author else None,
                            upvotes=score,
                            citations=getattr(post, "num_comments", 0) or 0,
                            keywords=kw_filter[:20],
                            metadata={
                                "subreddit": sub_name,
                                "score": score,
                                "num_comments": getattr(post, "num_comments", 0),
                                "post_id": post.id,
                                "platform": "reddit",
                                "auth": "praw",
                            },
                        )
                    )
                time.sleep(delay)
                continue
            except Exception as exc:
                signals.append(_error_signal("reddit", f"praw r/{sub_name}: {exc}"))
                time.sleep(delay)
                continue

        # --- Public JSON API path ---
        url = f"{REDDIT_BASE}/r/{sub_name}/{sort}.json"
        data, err = _http_get(url, headers=headers, params={"limit": min(max_per_sub, 100)})
        if err:
            signals.append(_error_signal("reddit", f"r/{sub_name}: {err}"))
            time.sleep(delay)
            continue

        children = []
        if isinstance(data, dict):
            listing = data.get("data", {})
            if isinstance(listing, dict):
                children = listing.get("children", [])

        for child in children[:max_per_sub]:
            post = child.get("data", {}) if isinstance(child, dict) else {}
            if not post:
                continue
            score = post.get("score", 0) or 0
            if score < min_upvotes:
                continue
            title = post.get("title", "") or ""
            selftext = post.get("selftext", "") or ""
            if kw_filter:
                combined = (title + " " + selftext).lower()
                if not any(kw in combined for kw in kw_filter):
                    continue
            permalink = post.get("permalink", "")
            post_url = f"https://www.reddit.com{permalink}" if permalink else ""
            signals.append(
                _make_signal(
                    source_type="reddit",
                    title=title,
                    body=selftext,
                    url=post_url,
                    author=post.get("author"),
                    upvotes=score,
                    citations=post.get("num_comments", 0),
                    keywords=kw_filter[:20],
                    metadata={
                        "subreddit": sub_name,
                        "score": score,
                        "num_comments": post.get("num_comments", 0),
                        "post_id": post.get("id", ""),
                        "created_utc": post.get("created_utc", 0),
                        "platform": "reddit",
                        "auth": "public",
                    },
                )
            )

        time.sleep(delay)

    _audit("social_trend.reddit", f"Fetched {len(signals)} Reddit signals from {len(subs)} subreddits")
    return signals


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------
def fetch_github_trends(
    queries: list | None = None,
    topics: list | None = None,
    min_stars: int = 10,
    max_per_query: int = 30,
    delay: float = 2.0,
) -> list:
    """Search GitHub for trending repositories.

    Uses the GitHub Search REST API. Authentication is optional: without a
    token the rate limit is 10 requests/min; with GITHUB_TOKEN or GH_TOKEN
    in the environment it rises to 30 requests/min.

    Args:
        queries: Free-text queries (e.g. ['govtech AI', 'zero trust']).
        topics: GitHub topic slugs (combined into a topic: query).
        min_stars: Minimum star count filter.
        max_per_query: Maximum repositories to collect per query.
        delay: Seconds to wait between API requests.

    Returns:
        List of normalized signal dicts with source_type='github'.
    """
    signals: list = []

    # Build query list from queries + topics
    search_queries: list = list(queries or [])
    for topic in topics or _DEFAULT_GITHUB_TOPICS:
        slug = topic.lower().replace(" ", "-")
        search_queries.append(f"topic:{slug}")
    if not search_queries:
        search_queries = [f"topic:{t.lower()}" for t in _DEFAULT_GITHUB_TOPICS]

    headers: dict = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    if token:
        headers["Authorization"] = f"Bearer {token}"
    else:
        # No token — log skip for OAuth-only endpoints but continue on public search
        _audit("social_trend.github", "No GITHUB_TOKEN found; using unauthenticated API (10 req/min limit)")

    for query in search_queries:
        url = f"{GITHUB_API}/search/repositories"
        params = {
            "q": f"{query} stars:>={min_stars}",
            "sort": "stars",
            "order": "desc",
            "per_page": min(max_per_query, 100),
        }

        data, err = _http_get(url, headers=headers, params=params)
        if err:
            signals.append(_error_signal("github", f"search '{query}': {err}"))
            time.sleep(delay)
            continue

        items = []
        if isinstance(data, dict):
            items = data.get("items", [])
            if not isinstance(items, list):
                items = []

        for repo in items[:max_per_query]:
            name = repo.get("full_name", "")
            description = (repo.get("description", "") or "")
            repo_url = repo.get("html_url", "")
            stars = repo.get("stargazers_count", 0)
            forks = repo.get("forks_count", 0)
            language = repo.get("language", "")
            repo_topics = repo.get("topics", [])
            updated_at = repo.get("updated_at", "")
            owner_login = (repo.get("owner", {}) or {}).get("login")

            signals.append(
                _make_signal(
                    source_type="github",
                    title=name,
                    body=description,
                    url=repo_url,
                    author=owner_login,
                    upvotes=stars,
                    citations=forks,
                    keywords=repo_topics[:20],
                    metadata={
                        "query": query,
                        "stars": stars,
                        "forks": forks,
                        "language": language,
                        "topics": repo_topics,
                        "updated_at": updated_at,
                        "platform": "github",
                        "auth": "token" if token else "public",
                    },
                )
            )

        time.sleep(delay)

    _audit("social_trend.github", f"Fetched {len(signals)} GitHub signals from {len(search_queries)} queries")
    return signals


# ---------------------------------------------------------------------------
# Hacker News
# ---------------------------------------------------------------------------
def fetch_hackernews_trends(
    keywords: list | None = None,
    story_type: str = "top",
    max_items: int = 30,
    min_score: int = 10,
    delay: float = 1.0,
) -> list:
    """Fetch trending stories from Hacker News.

    Uses two complementary paths — no API key is required for either:
    1. Algolia HN Search API: keyword-aware full-text search with scores/dates.
    2. Official HN Firebase API: live top/new/best/ask/show story IDs + item details.

    The Algolia path fires when keywords are supplied; the Firebase path
    provides top/new/best context regardless.

    Args:
        keywords: Optional keyword filter for Algolia search.
        story_type: Firebase story list to fetch ('top', 'new', 'best', 'ask', 'show').
        max_items: Maximum stories to return in total.
        min_score: Skip stories below this HN score.
        delay: Seconds to sleep between Firebase item fetches.

    Returns:
        List of normalized signal dicts with source_type='hackernews'.
    """
    signals: list = []
    seen_ids: set = set()

    # --- Algolia keyword search (fast, supports filters) ---
    if keywords:
        for keyword in keywords[:5]:
            url = f"{HN_ALGOLIA}/search"
            params = {
                "query": keyword,
                "tags": "story",
                "hitsPerPage": min(max_items, 50),
                "numericFilters": f"points>={min_score}",
            }
            data, err = _http_get(url, params=params)
            if err:
                signals.append(_error_signal("hackernews", f"algolia search '{keyword}': {err}"))
                time.sleep(delay)
                continue

            hits = []
            if isinstance(data, dict):
                hits = data.get("hits", [])
                if not isinstance(hits, list):
                    hits = []

            for hit in hits:
                item_id = str(hit.get("objectID", ""))
                if item_id in seen_ids:
                    continue
                seen_ids.add(item_id)

                title = hit.get("title", "") or ""
                url_field = hit.get("url", "") or ""
                author = hit.get("author", "")
                score = hit.get("points", 0) or 0
                num_comments = hit.get("num_comments", 0) or 0
                created_at = hit.get("created_at", "")

                hn_url = f"https://news.ycombinator.com/item?id={item_id}" if item_id else url_field

                signals.append(
                    _make_signal(
                        source_type="hackernews",
                        title=title,
                        body=hit.get("story_text", "") or "",
                        url=url_field or hn_url,
                        author=author,
                        upvotes=score,
                        citations=num_comments,
                        keywords=[keyword],
                        metadata={
                            "hn_id": item_id,
                            "hn_url": hn_url,
                            "num_comments": num_comments,
                            "created_at": created_at,
                            "search_keyword": keyword,
                            "platform": "hackernews",
                            "source_api": "algolia",
                        },
                    )
                )

            time.sleep(delay)

    # --- Firebase official API: top/new/best/ask/show stories ---
    valid_types = {"top", "new", "best", "ask", "show"}
    feed = story_type if story_type in valid_types else "top"
    id_url = f"{HN_FIREBASE}/{feed}stories.json"
    id_data, id_err = _http_get(id_url)

    if id_err:
        signals.append(_error_signal("hackernews", f"firebase {feed}stories: {id_err}"))
    else:
        story_ids = []
        if isinstance(id_data, list):
            story_ids = id_data
        elif isinstance(id_data, dict) and "_raw" in id_data:
            try:
                story_ids = json.loads(id_data["_raw"])
            except (json.JSONDecodeError, ValueError):
                story_ids = []

        remaining = max_items - len(signals)
        for item_id in story_ids[:remaining * 3]:
            if len([s for s in signals if s.get("source_type") == "hackernews" and
                    json.loads(s.get("metadata", "{}")).get("source_api") == "firebase"]) >= remaining:
                break

            if str(item_id) in seen_ids:
                continue

            item_url = f"{HN_FIREBASE}/item/{item_id}.json"
            item_data, item_err = _http_get(item_url)
            if item_err or not isinstance(item_data, dict):
                time.sleep(delay)
                continue

            score = item_data.get("score", 0) or 0
            if score < min_score:
                time.sleep(delay)
                continue

            title = item_data.get("title", "") or ""
            story_url = item_data.get("url", "") or ""
            author = item_data.get("by", "") or None
            num_comments = len(item_data.get("kids", []))
            hn_url = f"https://news.ycombinator.com/item?id={item_id}"

            # Apply keyword filter if provided
            if keywords:
                combined = title.lower()
                if not any(kw.lower() in combined for kw in keywords):
                    time.sleep(delay)
                    continue

            seen_ids.add(str(item_id))
            signals.append(
                _make_signal(
                    source_type="hackernews",
                    title=title,
                    body=item_data.get("text", "") or "",
                    url=story_url or hn_url,
                    author=author,
                    upvotes=score,
                    citations=num_comments,
                    keywords=keywords or [],
                    metadata={
                        "hn_id": str(item_id),
                        "hn_url": hn_url,
                        "num_comments": num_comments,
                        "story_type": feed,
                        "item_type": item_data.get("type", "story"),
                        "platform": "hackernews",
                        "source_api": "firebase",
                    },
                )
            )

            time.sleep(delay)

    _audit("social_trend.hackernews", f"Fetched {len(signals)} HN signals (type={story_type})")
    return signals


# ---------------------------------------------------------------------------
# Unified scan entry point
# ---------------------------------------------------------------------------
def scan_social_trends(
    sources: list | None = None,
    subreddits: list | None = None,
    keywords: list | None = None,
    github_topics: list | None = None,
    github_queries: list | None = None,
    min_stars: int = 10,
    hn_story_type: str = "top",
    hn_max: int = 30,
    hn_min_score: int = 10,
    reddit_sort: str = "hot",
    max_per_source: int = 50,
    delay: float = 1.5,
) -> dict:
    """Run all (or selected) social trend scanners and return combined signals.

    Args:
        sources: List subset of ['reddit', 'github', 'hackernews']. Defaults to all three.
        subreddits: Subreddit names for Reddit scan.
        keywords: Cross-source keyword filter (Reddit posts, HN search).
        github_topics: GitHub topic slugs.
        github_queries: Free-text GitHub search queries.
        min_stars: Minimum GitHub star count.
        hn_story_type: HN Firebase feed ('top', 'new', 'best', 'ask', 'show').
        hn_max: Max HN items.
        hn_min_score: Min HN score.
        reddit_sort: Reddit sort order ('hot', 'new', 'top', 'rising').
        max_per_source: Max items per sub/query/topic.
        delay: Base delay between requests per source.

    Returns:
        Dict with per-source signal lists and totals.
    """
    active = set(sources) if sources else {"reddit", "github", "hackernews"}
    results: dict = {}
    all_signals: list = []

    if "reddit" in active:
        try:
            reddit_signals = fetch_reddit_trends(
                subreddits=subreddits,
                keywords=keywords,
                sort=reddit_sort,
                max_per_sub=max_per_source,
                delay=delay,
            )
        except Exception as exc:
            reddit_signals = [_error_signal("reddit", str(exc))]
        results["reddit"] = {"signals": reddit_signals, "count": len(reddit_signals)}
        all_signals.extend(reddit_signals)

    if "github" in active:
        try:
            github_signals = fetch_github_trends(
                queries=github_queries,
                topics=github_topics,
                min_stars=min_stars,
                max_per_query=max_per_source,
                delay=delay,
            )
        except Exception as exc:
            github_signals = [_error_signal("github", str(exc))]
        results["github"] = {"signals": github_signals, "count": len(github_signals)}
        all_signals.extend(github_signals)

    if "hackernews" in active:
        try:
            hn_signals = fetch_hackernews_trends(
                keywords=keywords,
                story_type=hn_story_type,
                max_items=hn_max,
                min_score=hn_min_score,
                delay=delay,
            )
        except Exception as exc:
            hn_signals = [_error_signal("hackernews", str(exc))]
        results["hackernews"] = {"signals": hn_signals, "count": len(hn_signals)}
        all_signals.extend(hn_signals)

    total = len(all_signals)
    errors = sum(1 for s in all_signals if s.get("source_type") == "scan_error")

    merged = merge_and_score_clusters(results, keywords=keywords)

    _audit(
        "social_trend.scan",
        f"Social trend scan complete: {total} signals ({errors} errors), {len(merged)} merged unique",
        {"total": total, "errors": errors, "merged": len(merged), "sources": list(active)},
    )

    return {
        "scanned_at": _now(),
        "sources": list(active),
        "total_signals": total,
        "error_count": errors,
        "merged_count": len(merged),
        "merged_signals": merged,
        "results": results,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _print_human(result: dict) -> None:
    print("=" * 70)
    print("  ICDEV™ Social Trend Scanner  —  CUI // SP-CTI")
    print("=" * 70)
    print(f"  Scanned at:   {result.get('scanned_at', '')}")
    print(f"  Sources:      {', '.join(result.get('sources', []))}")
    print(f"  Total signals:{result.get('total_signals', 0):>6d}")
    print(f"  Merged unique:{result.get('merged_count', 0):>6d}")
    print(f"  Errors:       {result.get('error_count', 0):>6d}")
    print()
    for src, info in result.get("results", {}).items():
        count = info.get("count", 0)
        err = sum(1 for s in info.get("signals", []) if s.get("source_type") == "scan_error")
        print(f"  {src:<14s} {count:4d} signals  ({err} errors)")
        for sig in info.get("signals", [])[:3]:
            if sig.get("source_type") == "scan_error":
                continue
            title = sig.get("title", "")[:65]
            ups = sig.get("upvotes", 0)
            print(f"    [{ups:>5d}] {title}")
        if count > 3:
            print(f"           ... {count - 3} more")
    merged = result.get("merged_signals", [])
    if merged:
        print()
        print("  Top 5 by dual score (relevance + fun):")
        for sig in merged[:5]:
            scores = sig.get("scores", {})
            total = scores.get("total", 0.0)
            rel = scores.get("relevance", 0.0)
            fun = scores.get("fun", 0.0)
            src = sig.get("source_type", "?")[:8]
            title = sig.get("title", "")[:52]
            print(f"    [{total:.3f}] rel={rel:.2f} fun={fun:.2f}  [{src}] {title}")
    print("=" * 70)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ICDEV™ Social Trend Scanner — Reddit, GitHub, Hacker News (CUI // SP-CTI)"
    )
    parser.add_argument("--scan", action="store_true", required=True, help="Run the social trend scan")
    parser.add_argument("--source", type=str, help="Comma-separated sources (reddit,github,hackernews)")
    parser.add_argument("--subreddits", type=str, help="Comma-separated subreddit names")
    parser.add_argument("--keywords", type=str, help="Comma-separated keyword filter")
    parser.add_argument("--github-topics", type=str, help="Comma-separated GitHub topic slugs")
    parser.add_argument("--github-queries", type=str, help="Comma-separated GitHub search queries")
    parser.add_argument("--min-stars", type=int, default=10, help="Minimum GitHub stars (default: 10)")
    parser.add_argument("--hn-story-type", type=str, default="top", help="HN feed: top|new|best|ask|show")
    parser.add_argument("--hn-max", type=int, default=30, help="Max HN stories (default: 30)")
    parser.add_argument("--hn-min-score", type=int, default=10, help="Min HN score (default: 10)")
    parser.add_argument("--reddit-sort", type=str, default="hot", help="Reddit sort: hot|new|top|rising")
    parser.add_argument("--max-per-source", type=int, default=25, help="Max items per query/sub (default: 25)")
    parser.add_argument("--delay", type=float, default=1.5, help="Delay between requests in seconds")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable output")

    args = parser.parse_args()

    sources = [s.strip() for s in args.source.split(",")] if args.source else None
    subreddits = [s.strip() for s in args.subreddits.split(",")] if args.subreddits else None
    keywords = [k.strip() for k in args.keywords.split(",")] if args.keywords else None
    github_topics = [t.strip() for t in args.github_topics.split(",")] if args.github_topics else None
    github_queries = [q.strip() for q in args.github_queries.split(",")] if args.github_queries else None

    try:
        result = scan_social_trends(
            sources=sources,
            subreddits=subreddits,
            keywords=keywords,
            github_topics=github_topics,
            github_queries=github_queries,
            min_stars=args.min_stars,
            hn_story_type=args.hn_story_type,
            hn_max=args.hn_max,
            hn_min_score=args.hn_min_score,
            reddit_sort=args.reddit_sort,
            max_per_source=args.max_per_source,
            delay=args.delay,
        )

        if args.human:
            _print_human(result)
        else:
            print(json.dumps(result, indent=2, default=str))

    except Exception as exc:
        error = {"error": str(exc)}
        if args.human:
            print(f"ERROR: {exc}", file=sys.stderr)
        else:
            print(json.dumps(error, indent=2))
        sys.exit(1)


if __name__ == "__main__":
    main()
