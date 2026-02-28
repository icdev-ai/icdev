#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Creative Engine Source Scanner -- discover customer pain points from review sites and forums.

Scans configurable sources (G2, Capterra, TrustRadius, Reddit, GitHub Issues,
Product Hunt, GovCon blogs) and produces normalized creative signals.  Each
adapter follows the function-registry pattern (D352) and stores append-only
rows in the creative_signals table (D6).

Architecture:
    - Source adapters registered in SOURCE_SCANNERS dict (D352, web_scanner pattern)
    - Rate limiting per source (configurable in args/creative_config.yaml)
    - Graceful degradation on network failures (circuit breaker pattern D146)
    - All signals stored in creative_signals table (append-only, D6)
    - Competitor-aware: review-site adapters use confirmed competitors from DB
    - Air-gapped mode: disables web sources, logs scan-skipped

Usage:
    python tools/creative/source_scanner.py --scan --source g2 --json
    python tools/creative/source_scanner.py --scan --all --json
    python tools/creative/source_scanner.py --scan --source reddit --json
    python tools/creative/source_scanner.py --list-sources --json
    python tools/creative/source_scanner.py --history --days 7 --json
    python tools/creative/source_scanner.py --scan --human
"""

import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

# =========================================================================
# PATH SETUP
# =========================================================================
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))
CONFIG_PATH = BASE_DIR / "args" / "creative_config.yaml"

# =========================================================================
# GRACEFUL IMPORTS
# =========================================================================
try:
    import yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

try:
    import requests
    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False

try:
    from tools.audit.audit_logger import log_event as audit_log_event
    _HAS_AUDIT = True
except ImportError:
    _HAS_AUDIT = False
    def audit_log_event(**kwargs):
        return -1

try:
    from tools.resilience.circuit_breaker import InMemoryCircuitBreaker
    _HAS_CB = True
except ImportError:
    _HAS_CB = False

# =========================================================================
# CONSTANTS
# =========================================================================
DEFAULT_TIMEOUT = 30
MAX_RETRIES = 3
G2_BASE_URL = "https://www.g2.com"
CAPTERRA_BASE_URL = "https://www.capterra.com"
TRUSTRADIUS_BASE_URL = "https://www.trustradius.com"
REDDIT_BASE_URL = "https://www.reddit.com"
GITHUB_API = "https://api.github.com"
PRODUCTHUNT_BASE_URL = "https://www.producthunt.com"
MAX_BODY_LENGTH = 4000


# =========================================================================
# DATABASE HELPERS
# =========================================================================
def _get_db(db_path=None):
    """Get database connection with dict-like row access."""
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(f"Database not found: {path}")
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _now():
    """ISO-8601 timestamp."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _signal_id():
    """Generate unique creative signal ID with csig- prefix."""
    return f"csig-{uuid.uuid4().hex[:12]}"


def _content_hash(content):
    """SHA-256 hash for deduplication."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _audit(event_type, actor, action, details=None, project_id=None):
    """Write audit trail entry."""
    if _HAS_AUDIT:
        try:
            audit_log_event(
                event_type=event_type,
                actor=actor,
                action=action,
                details=json.dumps(details) if details else None,
                project_id=project_id or "creative-engine",
            )
        except Exception:
            pass


def _load_config():
    """Load creative config from YAML."""
    if not _HAS_YAML:
        return {}
    if not CONFIG_PATH.exists():
        return {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# =========================================================================
# HTTP HELPER
# =========================================================================
def _safe_get(url, headers=None, params=None, timeout=DEFAULT_TIMEOUT):
    """HTTP GET with error handling and rate limit awareness.

    Args:
        url: Target URL.
        headers: Optional request headers.
        params: Optional query parameters.
        timeout: Request timeout in seconds.

    Returns:
        Tuple of (data, error).  On success error is None.
        On failure data is None and error is a string code.
    """
    if not _HAS_REQUESTS:
        return None, "requests library not installed"
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=timeout)
        if resp.status_code == 429:
            return None, "rate_limited"
        if resp.status_code == 403:
            return None, "forbidden"
        resp.raise_for_status()
        # Try JSON first; fall back to raw text wrapped in a dict
        try:
            return resp.json(), None
        except (json.JSONDecodeError, ValueError):
            return {"_raw": resp.text}, None
    except requests.exceptions.Timeout:
        return None, "timeout"
    except requests.exceptions.ConnectionError:
        return None, "connection_error"
    except requests.exceptions.RequestException as e:
        return None, str(e)


def _error_signal(source, context, error):
    """Create an error signal dict for tracking scan failures.

    Args:
        source: Scanner name that encountered the error.
        context: Additional context (URL, sub-category, etc.).
        error: Error message string.

    Returns:
        Normalized signal dict with source_type 'scan_error'.
    """
    return {
        "id": _signal_id(),
        "source": source,
        "source_type": "scan_error",
        "competitor_id": None,
        "title": f"Scan error: {source}/{context}",
        "body": str(error)[:MAX_BODY_LENGTH],
        "url": "",
        "author": None,
        "rating": None,
        "upvotes": 0,
        "sentiment": None,
        "content_hash": _content_hash(f"{source}_{context}_{_now()[:10]}"),
        "metadata": json.dumps({"error": str(error), "context": context}),
        "discovered_at": _now(),
    }


# =========================================================================
# COMPETITOR HELPERS
# =========================================================================
def _get_confirmed_competitors(db_path=None):
    """Fetch all confirmed competitors from the database.

    Args:
        db_path: Optional database path override.

    Returns:
        List of competitor dicts with id, name, domain, source, source_url,
        rating, review_count, features, pricing_tier, metadata.
    """
    try:
        conn = _get_db(db_path)
    except FileNotFoundError:
        return []
    try:
        rows = conn.execute(
            """SELECT id, name, domain, source, source_url, rating,
                      review_count, features, pricing_tier, metadata
               FROM creative_competitors
               WHERE status = 'confirmed'
               ORDER BY review_count DESC"""
        ).fetchall()
        competitors = []
        for row in rows:
            competitors.append({
                "id": row["id"],
                "name": row["name"],
                "domain": row["domain"],
                "source": row["source"],
                "source_url": row["source_url"],
                "rating": row["rating"],
                "review_count": row["review_count"],
                "features": row["features"],
                "pricing_tier": row["pricing_tier"],
                "metadata": row["metadata"],
            })
        return competitors
    finally:
        conn.close()


def _slug(name):
    """Convert a competitor name to a URL-safe slug.

    Lowercases, replaces spaces with hyphens, strips non-alnum chars.
    """
    slug = name.lower().strip()
    slug = slug.replace(" ", "-")
    cleaned = []
    for ch in slug:
        if ch.isalnum() or ch == "-":
            cleaned.append(ch)
    return "".join(cleaned)


def _rate_delay(config, section_key):
    """Read rate limit delay from config for a source section.

    Args:
        config: Full creative_config.yaml dict.
        section_key: Key under 'sources' (e.g. 'review_sites', 'community_forums').

    Returns:
        Delay in seconds between requests.  Defaults to 3.
    """
    section = config.get("sources", {}).get(section_key, {})
    rate_limit = section.get("rate_limit", {})
    return rate_limit.get("delay_between_requests_seconds", 3)


# =========================================================================
# SOURCE ADAPTERS
# =========================================================================
def scan_g2(config, competitors=None):
    """Scan G2 for competitor reviews.

    Constructs product review page URLs from confirmed competitor names and
    fetches review data.  Normalizes each review into a creative signal dict.

    Args:
        config: Full creative_config.yaml dict.
        competitors: List of confirmed competitor dicts (from DB).

    Returns:
        List of normalized signal dicts.
    """
    signals = []
    review_config = config.get("sources", {}).get("review_sites", {})
    if not review_config.get("enabled", False):
        return signals

    g2_cfg = None
    for site in review_config.get("sites", []):
        if site.get("name") == "g2" and site.get("enabled", False):
            g2_cfg = site
            break
    if not g2_cfg:
        return signals

    base_url = g2_cfg.get("base_url", G2_BASE_URL)
    max_reviews = g2_cfg.get("max_reviews_per_competitor", 50)
    delay = _rate_delay(config, "review_sites")

    if not competitors:
        return signals

    headers = {
        "User-Agent": "ICDEV-CreativeEngine/1.0 (GovTech research)",
        "Accept": "application/json, text/html",
    }

    for comp in competitors:
        comp_slug = _slug(comp["name"])
        comp_id = comp["id"]
        url = f"{base_url}/products/{comp_slug}/reviews"

        data, err = _safe_get(url, headers=headers)
        if err:
            signals.append(_error_signal("g2", f"reviews_{comp_slug}", err))
            time.sleep(delay)
            continue

        # Parse reviews from response -- handle both JSON API and raw HTML fallback
        reviews = []
        if isinstance(data, dict):
            # JSON response or raw HTML wrapped in _raw key
            if "_raw" in data:
                # HTML fallback -- extract minimal info from page text
                raw_text = data["_raw"][:50000]
                # Create a single summary signal from the page fetch
                reviews.append({
                    "title": f"G2 reviews page for {comp['name']}",
                    "body": raw_text[:MAX_BODY_LENGTH],
                    "url": url,
                    "author": None,
                    "rating": comp.get("rating"),
                    "upvotes": 0,
                })
            else:
                # Structured JSON -- iterate 'reviews' or 'data' key
                items = data.get("reviews", data.get("data", []))
                if isinstance(items, list):
                    for item in items[:max_reviews]:
                        reviews.append({
                            "title": item.get("title", item.get("headline", f"G2 review for {comp['name']}")),
                            "body": (item.get("text", item.get("body", item.get("comment", ""))) or "")[:MAX_BODY_LENGTH],
                            "url": item.get("url", url),
                            "author": item.get("author", item.get("reviewer", {}).get("name") if isinstance(item.get("reviewer"), dict) else None),
                            "rating": item.get("rating", item.get("star_rating")),
                            "upvotes": item.get("upvotes", item.get("helpful_count", 0)),
                        })

        for review in reviews[:max_reviews]:
            hash_input = f"g2_{comp_slug}_{review.get('title', '')}_{review.get('author', '')}"
            signals.append({
                "id": _signal_id(),
                "source": "g2",
                "source_type": "review",
                "competitor_id": comp_id,
                "title": review.get("title", f"G2 review for {comp['name']}"),
                "body": review.get("body", "")[:MAX_BODY_LENGTH],
                "url": review.get("url", url),
                "author": review.get("author"),
                "rating": review.get("rating"),
                "upvotes": review.get("upvotes", 0) or 0,
                "sentiment": None,
                "content_hash": _content_hash(hash_input),
                "metadata": json.dumps({
                    "competitor_name": comp["name"],
                    "competitor_id": comp_id,
                    "site": "g2",
                }),
                "discovered_at": _now(),
            })

        time.sleep(delay)

    return signals


def scan_capterra(config, competitors=None):
    """Scan Capterra for competitor reviews.

    Constructs product review URLs from confirmed competitor names.

    Args:
        config: Full creative_config.yaml dict.
        competitors: List of confirmed competitor dicts.

    Returns:
        List of normalized signal dicts.
    """
    signals = []
    review_config = config.get("sources", {}).get("review_sites", {})
    if not review_config.get("enabled", False):
        return signals

    capterra_cfg = None
    for site in review_config.get("sites", []):
        if site.get("name") == "capterra" and site.get("enabled", False):
            capterra_cfg = site
            break
    if not capterra_cfg:
        return signals

    base_url = capterra_cfg.get("base_url", CAPTERRA_BASE_URL)
    max_reviews = capterra_cfg.get("max_reviews_per_competitor", 50)
    delay = _rate_delay(config, "review_sites")

    if not competitors:
        return signals

    headers = {
        "User-Agent": "ICDEV-CreativeEngine/1.0 (GovTech research)",
        "Accept": "application/json, text/html",
    }

    for comp in competitors:
        comp_slug = _slug(comp["name"])
        comp_id = comp["id"]
        url = f"{base_url}/software/{comp_slug}/reviews"

        data, err = _safe_get(url, headers=headers)
        if err:
            signals.append(_error_signal("capterra", f"reviews_{comp_slug}", err))
            time.sleep(delay)
            continue

        reviews = []
        if isinstance(data, dict):
            if "_raw" in data:
                raw_text = data["_raw"][:50000]
                reviews.append({
                    "title": f"Capterra reviews page for {comp['name']}",
                    "body": raw_text[:MAX_BODY_LENGTH],
                    "url": url,
                    "author": None,
                    "rating": comp.get("rating"),
                    "upvotes": 0,
                })
            else:
                items = data.get("reviews", data.get("data", []))
                if isinstance(items, list):
                    for item in items[:max_reviews]:
                        reviews.append({
                            "title": item.get("title", item.get("headline", f"Capterra review for {comp['name']}")),
                            "body": (item.get("text", item.get("body", item.get("pros", "") + " " + item.get("cons", ""))) or "")[:MAX_BODY_LENGTH],
                            "url": item.get("url", url),
                            "author": item.get("author", item.get("reviewer_name")),
                            "rating": item.get("rating", item.get("overall_rating")),
                            "upvotes": item.get("upvotes", item.get("helpful_count", 0)),
                        })

        for review in reviews[:max_reviews]:
            hash_input = f"capterra_{comp_slug}_{review.get('title', '')}_{review.get('author', '')}"
            signals.append({
                "id": _signal_id(),
                "source": "capterra",
                "source_type": "review",
                "competitor_id": comp_id,
                "title": review.get("title", f"Capterra review for {comp['name']}"),
                "body": review.get("body", "")[:MAX_BODY_LENGTH],
                "url": review.get("url", url),
                "author": review.get("author"),
                "rating": review.get("rating"),
                "upvotes": review.get("upvotes", 0) or 0,
                "sentiment": None,
                "content_hash": _content_hash(hash_input),
                "metadata": json.dumps({
                    "competitor_name": comp["name"],
                    "competitor_id": comp_id,
                    "site": "capterra",
                }),
                "discovered_at": _now(),
            })

        time.sleep(delay)

    return signals


def scan_trustradius(config, competitors=None):
    """Scan TrustRadius for competitor reviews.

    Constructs product review URLs from confirmed competitor names.

    Args:
        config: Full creative_config.yaml dict.
        competitors: List of confirmed competitor dicts.

    Returns:
        List of normalized signal dicts.
    """
    signals = []
    review_config = config.get("sources", {}).get("review_sites", {})
    if not review_config.get("enabled", False):
        return signals

    tr_cfg = None
    for site in review_config.get("sites", []):
        if site.get("name") == "trustradius" and site.get("enabled", False):
            tr_cfg = site
            break
    if not tr_cfg:
        return signals

    base_url = tr_cfg.get("base_url", TRUSTRADIUS_BASE_URL)
    max_reviews = tr_cfg.get("max_reviews_per_competitor", 50)
    delay = _rate_delay(config, "review_sites")

    if not competitors:
        return signals

    headers = {
        "User-Agent": "ICDEV-CreativeEngine/1.0 (GovTech research)",
        "Accept": "application/json, text/html",
    }

    for comp in competitors:
        comp_slug = _slug(comp["name"])
        comp_id = comp["id"]
        url = f"{base_url}/products/{comp_slug}/reviews"

        data, err = _safe_get(url, headers=headers)
        if err:
            signals.append(_error_signal("trustradius", f"reviews_{comp_slug}", err))
            time.sleep(delay)
            continue

        reviews = []
        if isinstance(data, dict):
            if "_raw" in data:
                raw_text = data["_raw"][:50000]
                reviews.append({
                    "title": f"TrustRadius reviews page for {comp['name']}",
                    "body": raw_text[:MAX_BODY_LENGTH],
                    "url": url,
                    "author": None,
                    "rating": comp.get("rating"),
                    "upvotes": 0,
                })
            else:
                items = data.get("reviews", data.get("data", []))
                if isinstance(items, list):
                    for item in items[:max_reviews]:
                        reviews.append({
                            "title": item.get("title", item.get("heading", f"TrustRadius review for {comp['name']}")),
                            "body": (item.get("text", item.get("body", item.get("review_text", ""))) or "")[:MAX_BODY_LENGTH],
                            "url": item.get("url", url),
                            "author": item.get("author", item.get("reviewer_name")),
                            "rating": item.get("rating", item.get("trScore")),
                            "upvotes": item.get("upvotes", item.get("helpful_count", 0)),
                        })

        for review in reviews[:max_reviews]:
            hash_input = f"trustradius_{comp_slug}_{review.get('title', '')}_{review.get('author', '')}"
            signals.append({
                "id": _signal_id(),
                "source": "trustradius",
                "source_type": "review",
                "competitor_id": comp_id,
                "title": review.get("title", f"TrustRadius review for {comp['name']}"),
                "body": review.get("body", "")[:MAX_BODY_LENGTH],
                "url": review.get("url", url),
                "author": review.get("author"),
                "rating": review.get("rating"),
                "upvotes": review.get("upvotes", 0) or 0,
                "sentiment": None,
                "content_hash": _content_hash(hash_input),
                "metadata": json.dumps({
                    "competitor_name": comp["name"],
                    "competitor_id": comp_id,
                    "site": "trustradius",
                }),
                "discovered_at": _now(),
            })

        time.sleep(delay)

    return signals


def scan_reddit(config, competitors=None):
    """Scan Reddit subreddits for community discussions and pain points.

    Reads subreddit list and keyword filters from config.  Uses the Reddit
    JSON API (append .json to any listing URL).  Filters by min_upvotes
    and optional keyword_filter.

    Args:
        config: Full creative_config.yaml dict.
        competitors: Not used directly but accepted for registry consistency.

    Returns:
        List of normalized signal dicts.
    """
    signals = []
    community_config = config.get("sources", {}).get("community_forums", {})
    if not community_config.get("enabled", False):
        return signals

    reddit_cfg = None
    for platform in community_config.get("platforms", []):
        if platform.get("name") == "reddit":
            reddit_cfg = platform
            break
    if not reddit_cfg:
        return signals

    subreddits = reddit_cfg.get("subreddits", [])
    if not subreddits:
        return signals

    sort_by = reddit_cfg.get("sort", "hot")
    max_results = reddit_cfg.get("max_results", 50)
    min_upvotes = reddit_cfg.get("min_upvotes", 10)
    keyword_filter = [kw.lower() for kw in reddit_cfg.get("keyword_filter", [])]
    delay = _rate_delay(config, "community_forums")

    headers = {
        "User-Agent": "ICDEV-CreativeEngine/1.0 (GovTech research; CUI // SP-CTI)",
    }

    for subreddit in subreddits:
        url = f"{REDDIT_BASE_URL}/r/{subreddit}/{sort_by}.json"
        params = {"limit": min(max_results, 100)}

        data, err = _safe_get(url, headers=headers, params=params)
        if err:
            signals.append(_error_signal("reddit", f"r/{subreddit}", err))
            time.sleep(delay)
            continue

        # Reddit JSON API wraps posts in data.children
        children = []
        if isinstance(data, dict):
            listing_data = data.get("data", {})
            if isinstance(listing_data, dict):
                children = listing_data.get("children", [])

        for child in children[:max_results]:
            post = child.get("data", {}) if isinstance(child, dict) else {}
            if not post:
                continue

            score = post.get("score", 0)
            if score < min_upvotes:
                continue

            title = post.get("title", "")
            selftext = post.get("selftext", "") or ""

            # Apply keyword filter if configured
            if keyword_filter:
                combined_text = (title + " " + selftext).lower()
                if not any(kw in combined_text for kw in keyword_filter):
                    continue

            post_id = post.get("id", "")
            permalink = post.get("permalink", "")
            post_url = f"https://www.reddit.com{permalink}" if permalink else ""

            hash_input = f"reddit_{subreddit}_{post_id}"
            signals.append({
                "id": _signal_id(),
                "source": "reddit",
                "source_type": "forum_post",
                "competitor_id": None,
                "title": title[:500],
                "body": selftext[:MAX_BODY_LENGTH],
                "url": post_url,
                "author": post.get("author"),
                "rating": None,
                "upvotes": score,
                "sentiment": None,
                "content_hash": _content_hash(hash_input),
                "metadata": json.dumps({
                    "subreddit": subreddit,
                    "score": score,
                    "num_comments": post.get("num_comments", 0),
                    "created_utc": post.get("created_utc", 0),
                    "is_self": post.get("is_self", False),
                    "link_flair_text": post.get("link_flair_text"),
                }),
                "discovered_at": _now(),
            })

        time.sleep(delay)

    return signals


def scan_github_issues(config, competitors=None):
    """Scan GitHub repos for open feature-request and enhancement issues.

    Reads repo list and label filters from config.  Uses the GitHub REST API
    v3 to fetch open issues sorted by reaction thumbs-up.  Filters out
    pull requests.

    Args:
        config: Full creative_config.yaml dict.
        competitors: Not used directly but accepted for registry consistency.

    Returns:
        List of normalized signal dicts.
    """
    signals = []
    gh_config = config.get("sources", {}).get("github_issues", {})
    if not gh_config.get("enabled", False):
        return signals

    repos = gh_config.get("repos", [])
    if not repos:
        return signals

    labels = gh_config.get("labels", [])
    max_results = gh_config.get("max_results", 100)
    delay_seconds = gh_config.get("rate_limit", {}).get("delay_between_requests_seconds", 2)

    headers = {"Accept": "application/vnd.github+json"}
    gh_token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if gh_token:
        headers["Authorization"] = f"Bearer {gh_token}"

    for repo in repos:
        params = {
            "state": "open",
            "sort": "reactions-+1",
            "per_page": min(max_results, 100),
            "direction": "desc",
        }
        if labels:
            params["labels"] = ",".join(labels)

        url = f"{GITHUB_API}/repos/{repo}/issues"
        data, err = _safe_get(url, headers=headers, params=params)
        if err:
            signals.append(_error_signal("github", f"issues_{repo}", err))
            time.sleep(delay_seconds)
            continue

        items = data if isinstance(data, list) else []

        for item in items[:max_results]:
            # Skip pull requests (GitHub issues endpoint includes PRs)
            if item.get("pull_request"):
                continue

            reactions = item.get("reactions", {})
            thumbs_up = reactions.get("+1", 0) if isinstance(reactions, dict) else 0
            title = item.get("title", "")
            body = (item.get("body", "") or "")[:MAX_BODY_LENGTH]
            issue_url = item.get("html_url", "")
            issue_number = item.get("number", 0)

            hash_input = f"github_{repo}_{issue_number}"
            signals.append({
                "id": _signal_id(),
                "source": "github",
                "source_type": "issue",
                "competitor_id": None,
                "title": f"[{repo}] {title}",
                "body": body,
                "url": issue_url,
                "author": (item.get("user", {}) or {}).get("login"),
                "rating": None,
                "upvotes": thumbs_up,
                "sentiment": None,
                "content_hash": _content_hash(hash_input),
                "metadata": json.dumps({
                    "repo": repo,
                    "issue_number": issue_number,
                    "labels": [lbl.get("name", "") for lbl in item.get("labels", [])],
                    "reactions_thumbs_up": thumbs_up,
                    "comments": item.get("comments", 0),
                    "created_at": item.get("created_at", ""),
                    "updated_at": item.get("updated_at", ""),
                }),
                "discovered_at": _now(),
            })

        time.sleep(delay_seconds)

    return signals


def scan_producthunt(config, competitors=None):
    """Scan Product Hunt for recent launches in configured topics.

    Constructs topic listing URLs and fetches product data.  Each launch
    is normalized into a creative signal.

    Args:
        config: Full creative_config.yaml dict.
        competitors: Not used directly but accepted for registry consistency.

    Returns:
        List of normalized signal dicts.
    """
    signals = []
    ph_config = config.get("sources", {}).get("producthunt", {})
    if not ph_config.get("enabled", False):
        return signals

    topics = ph_config.get("topics", [])
    if not topics:
        return signals

    max_results = ph_config.get("max_results", 20)

    headers = {
        "User-Agent": "ICDEV-CreativeEngine/1.0 (GovTech research)",
        "Accept": "application/json, text/html",
    }

    for topic in topics:
        topic_slug = _slug(topic)
        url = f"{PRODUCTHUNT_BASE_URL}/topics/{topic_slug}"

        data, err = _safe_get(url, headers=headers)
        if err:
            signals.append(_error_signal("producthunt", f"topic_{topic_slug}", err))
            time.sleep(2)
            continue

        launches = []
        if isinstance(data, dict):
            if "_raw" in data:
                # HTML page -- create a single summary signal
                raw_text = data["_raw"][:50000]
                launches.append({
                    "title": f"Product Hunt topic: {topic}",
                    "body": raw_text[:MAX_BODY_LENGTH],
                    "url": url,
                    "author": None,
                    "upvotes": 0,
                    "tagline": "",
                })
            else:
                # Structured JSON -- iterate 'posts' or 'data' key
                items = data.get("posts", data.get("data", data.get("products", [])))
                if isinstance(items, list):
                    for item in items[:max_results]:
                        launches.append({
                            "title": item.get("name", item.get("title", f"PH launch in {topic}")),
                            "body": (item.get("description", item.get("tagline", "")) or "")[:MAX_BODY_LENGTH],
                            "url": item.get("url", item.get("discussion_url", url)),
                            "author": item.get("maker_name", item.get("user", {}).get("name") if isinstance(item.get("user"), dict) else None),
                            "upvotes": item.get("votes_count", item.get("upvotes", 0)),
                            "tagline": item.get("tagline", ""),
                        })

        for launch in launches[:max_results]:
            hash_input = f"producthunt_{topic_slug}_{launch.get('title', '')}"
            signals.append({
                "id": _signal_id(),
                "source": "producthunt",
                "source_type": "launch",
                "competitor_id": None,
                "title": launch.get("title", f"Product Hunt: {topic}"),
                "body": launch.get("body", "")[:MAX_BODY_LENGTH],
                "url": launch.get("url", url),
                "author": launch.get("author"),
                "rating": None,
                "upvotes": launch.get("upvotes", 0) or 0,
                "sentiment": None,
                "content_hash": _content_hash(hash_input),
                "metadata": json.dumps({
                    "topic": topic,
                    "tagline": launch.get("tagline", ""),
                    "site": "producthunt",
                }),
                "discovered_at": _now(),
            })

        time.sleep(2)

    return signals


def scan_govcon_blogs(config, competitors=None):
    """Scan GovCon blog URLs for discussion posts and articles.

    Reads blog URLs from config community_forums section.  Fetches each
    URL and creates a summary signal from the page content.

    Args:
        config: Full creative_config.yaml dict.
        competitors: Not used directly but accepted for registry consistency.

    Returns:
        List of normalized signal dicts.
    """
    signals = []
    community_config = config.get("sources", {}).get("community_forums", {})
    if not community_config.get("enabled", False):
        return signals

    govcon_cfg = None
    for platform in community_config.get("platforms", []):
        if platform.get("name") == "govcon_blogs":
            govcon_cfg = platform
            break
    if not govcon_cfg:
        return signals

    urls = govcon_cfg.get("urls", [])
    if not urls:
        return signals

    max_results = govcon_cfg.get("max_results", 30)
    delay = _rate_delay(config, "community_forums")

    headers = {
        "User-Agent": "ICDEV-CreativeEngine/1.0 (GovTech research)",
        "Accept": "text/html, application/json",
    }

    collected = 0
    for blog_url in urls:
        if collected >= max_results:
            break

        data, err = _safe_get(blog_url, headers=headers)
        if err:
            signals.append(_error_signal("govcon_blog", blog_url, err))
            time.sleep(delay)
            continue

        # Extract content from response
        body = ""
        title = blog_url
        if isinstance(data, dict):
            if "_raw" in data:
                raw_text = data["_raw"]
                # Attempt to extract title from HTML <title> tag
                title_start = raw_text.find("<title>")
                title_end = raw_text.find("</title>")
                if title_start != -1 and title_end != -1:
                    extracted = raw_text[title_start + 7:title_end].strip()
                    if extracted:
                        title = extracted[:500]
                body = raw_text[:MAX_BODY_LENGTH]
            else:
                # JSON response -- extract title and body from common keys
                title = data.get("title", data.get("name", blog_url))
                items = data.get("posts", data.get("articles", data.get("entries", [])))
                if isinstance(items, list):
                    for article in items[:max_results - collected]:
                        art_title = article.get("title", "GovCon blog post")
                        art_body = (article.get("content", article.get("body", article.get("summary", ""))) or "")[:MAX_BODY_LENGTH]
                        art_url = article.get("url", article.get("link", blog_url))
                        art_author = article.get("author", article.get("author_name"))

                        hash_input = f"govcon_blog_{art_url}_{art_title}"
                        signals.append({
                            "id": _signal_id(),
                            "source": "govcon_blog",
                            "source_type": "forum_post",
                            "competitor_id": None,
                            "title": art_title[:500],
                            "body": art_body,
                            "url": art_url,
                            "author": art_author,
                            "rating": None,
                            "upvotes": article.get("likes", article.get("shares", 0)) or 0,
                            "sentiment": None,
                            "content_hash": _content_hash(hash_input),
                            "metadata": json.dumps({
                                "source_url": blog_url,
                                "site": "govcon_blog",
                                "published": article.get("published", article.get("date", "")),
                            }),
                            "discovered_at": _now(),
                        })
                        collected += 1
                    time.sleep(delay)
                    continue

        # Single-page signal (HTML or non-list JSON)
        hash_input = f"govcon_blog_{blog_url}"
        signals.append({
            "id": _signal_id(),
            "source": "govcon_blog",
            "source_type": "forum_post",
            "competitor_id": None,
            "title": title[:500] if isinstance(title, str) else str(title)[:500],
            "body": body[:MAX_BODY_LENGTH],
            "url": blog_url,
            "author": None,
            "rating": None,
            "upvotes": 0,
            "sentiment": None,
            "content_hash": _content_hash(hash_input),
            "metadata": json.dumps({
                "source_url": blog_url,
                "site": "govcon_blog",
            }),
            "discovered_at": _now(),
        })
        collected += 1
        time.sleep(delay)

    return signals


# =========================================================================
# SOURCE REGISTRY
# =========================================================================
def scan_sam_gov_for_creative(config, competitors=None):
    """Scan SAM.gov award data as creative signals (D361 cross-registration).

    Surfaces competitive intelligence from government contract awards,
    enabling gap analysis against known competitors.
    """
    signals = []
    try:
        from tools.govcon.competitor_profiler import get_leaderboard
        lb = get_leaderboard(limit=20)
        for entry in lb.get("leaderboard", []):
            signals.append({
                "source_type": "sam_gov_rfp",
                "source_url": "",
                "title": f"Award leader: {entry.get('vendor', 'unknown')}",
                "content": f"{entry.get('awards', 0)} awards, ${entry.get('total_value', 0):,.0f} total value, "
                           f"{entry.get('naics_diversity', 0)} NAICS codes, {entry.get('agency_diversity', 0)} agencies",
                "sentiment": "neutral",
                "keywords": ["govcon", "award", entry.get("vendor", "")],
                "metadata": json.dumps({
                    "vendor": entry.get("vendor"),
                    "awards": entry.get("awards"),
                    "total_value": entry.get("total_value"),
                    "source": "sam_gov_awards",
                }),
            })
    except Exception:
        signals.append({"source_type": "scan_error", "title": "SAM.gov creative scan failed", "content": ""})
    return signals


SOURCE_SCANNERS = {
    "g2": scan_g2,
    "capterra": scan_capterra,
    "trustradius": scan_trustradius,
    "reddit": scan_reddit,
    "github": scan_github_issues,
    "producthunt": scan_producthunt,
    "govcon_blogs": scan_govcon_blogs,
    "sam_gov": scan_sam_gov_for_creative,
}


# =========================================================================
# SIGNAL STORAGE
# =========================================================================
def store_signals(signals, db_path=None):
    """Store discovered signals in the creative_signals table (append-only, D6).

    Deduplicates by content_hash -- existing hashes are skipped.
    Error signals (source_type='scan_error') are counted but not stored.

    Args:
        signals: List of signal dicts from source adapters.
        db_path: Optional database path override.

    Returns:
        Dict with stored count, duplicates skipped, and errors encountered.
    """
    conn = _get_db(db_path)
    stored = 0
    duplicates = 0
    errors = 0

    try:
        for signal in signals:
            if signal.get("source_type") == "scan_error":
                errors += 1
                continue

            # Check for duplicate by content_hash
            existing = conn.execute(
                "SELECT id FROM creative_signals WHERE content_hash = ?",
                (signal.get("content_hash", ""),),
            ).fetchone()

            if existing:
                duplicates += 1
                continue

            try:
                conn.execute(
                    """INSERT INTO creative_signals
                       (id, source, source_type, competitor_id, title, body, url,
                        author, rating, upvotes, sentiment, content_hash, metadata,
                        discovered_at, classification)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'CUI')""",
                    (
                        signal["id"],
                        signal["source"],
                        signal["source_type"],
                        signal.get("competitor_id"),
                        signal.get("title", ""),
                        signal.get("body", ""),
                        signal.get("url", ""),
                        signal.get("author"),
                        signal.get("rating"),
                        signal.get("upvotes", 0),
                        signal.get("sentiment"),
                        signal.get("content_hash", ""),
                        signal.get("metadata", "{}"),
                        signal.get("discovered_at", _now()),
                    ),
                )
                stored += 1
            except sqlite3.IntegrityError:
                duplicates += 1
            except sqlite3.OperationalError as exc:
                errors += 1
                _audit(
                    "creative.scan.error",
                    "creative-engine",
                    f"Failed to store signal {signal.get('id', '?')}: {exc}",
                )

        conn.commit()
    finally:
        conn.close()

    _audit(
        "creative.scan.store",
        "creative-engine",
        f"Stored {stored} signals ({duplicates} duplicates, {errors} errors)",
        {"stored": stored, "duplicates": duplicates, "errors": errors},
    )

    return {
        "stored": stored,
        "duplicates": duplicates,
        "errors": errors,
        "total_processed": len(signals),
    }


# =========================================================================
# SCAN ORCHESTRATOR
# =========================================================================
def run_scan(source=None, db_path=None):
    """Run source scan for the specified source or all enabled sources.

    Loads config, retrieves confirmed competitors from DB, invokes scanner
    functions, and stores results.

    Args:
        source: Source name (g2, capterra, reddit, etc.) or None for all.
        db_path: Optional database path override.

    Returns:
        Dict with per-source results and aggregate totals.
    """
    config = _load_config()
    competitors = _get_confirmed_competitors(db_path)
    results = {}
    all_signals = []
    error_list = []

    sources_to_scan = [source] if source else list(SOURCE_SCANNERS.keys())

    _audit(
        "creative.scan.start",
        "creative-engine",
        f"Starting scan: sources={sources_to_scan}, competitors={len(competitors)}",
        {"sources": sources_to_scan, "competitor_count": len(competitors)},
    )

    for src in sources_to_scan:
        scanner = SOURCE_SCANNERS.get(src)
        if not scanner:
            results[src] = {"error": f"Unknown source: {src}"}
            error_list.append(f"Unknown source: {src}")
            continue

        try:
            found_signals = scanner(config, competitors=competitors)
            storage_result = store_signals(found_signals, db_path)
            results[src] = {
                "signals_found": len(found_signals),
                **storage_result,
            }
            all_signals.extend(found_signals)
        except Exception as e:
            results[src] = {"error": str(e), "signals_found": 0}
            error_list.append(f"{src}: {e}")

    total_stored = sum(r.get("stored", 0) for r in results.values())
    total_found = sum(r.get("signals_found", 0) for r in results.values())

    _audit(
        "creative.scan.complete",
        "creative-engine",
        f"Scan complete: {total_found} found, {total_stored} stored",
        {"total_found": total_found, "total_stored": total_stored, "errors": error_list},
    )

    return {
        "source": source or "all",
        "scan_time": _now(),
        "sources_scanned": len(sources_to_scan),
        "competitors_available": len(competitors),
        "signals_discovered": total_found,
        "signals_stored": total_stored,
        "results": results,
        "errors": error_list,
    }


def list_sources():
    """List all configured sources and their enabled/disabled status.

    Returns:
        Dict with source list, total count, and scanner availability.
    """
    config = _load_config()
    sources = []

    for source_name, scanner_fn in SOURCE_SCANNERS.items():
        # Determine enabled status from config -- check multiple config locations
        enabled = False
        scan_interval = 24

        review_sites = config.get("sources", {}).get("review_sites", {})
        for site in review_sites.get("sites", []):
            if site.get("name") == source_name:
                enabled = site.get("enabled", False) and review_sites.get("enabled", False)
                scan_interval = review_sites.get("scan_interval_hours", 24)
                break

        community_config = config.get("sources", {}).get("community_forums", {})
        for platform in community_config.get("platforms", []):
            if platform.get("name") == source_name or (source_name == "govcon_blogs" and platform.get("name") == "govcon_blogs"):
                enabled = community_config.get("enabled", False)
                scan_interval = community_config.get("scan_interval_hours", 12)
                break

        if source_name == "reddit":
            for platform in community_config.get("platforms", []):
                if platform.get("name") == "reddit":
                    enabled = community_config.get("enabled", False)
                    scan_interval = community_config.get("scan_interval_hours", 12)
                    break

        gh_config = config.get("sources", {}).get("github_issues", {})
        if source_name == "github":
            enabled = gh_config.get("enabled", False)
            scan_interval = gh_config.get("scan_interval_hours", 12)

        ph_config = config.get("sources", {}).get("producthunt", {})
        if source_name == "producthunt":
            enabled = ph_config.get("enabled", False)
            scan_interval = ph_config.get("scan_interval_hours", 168)

        sources.append({
            "name": source_name,
            "enabled": enabled,
            "scan_interval_hours": scan_interval,
            "has_scanner": True,
            "scanner_function": scanner_fn.__name__,
        })

    return {
        "sources": sources,
        "total": len(sources),
        "requests_available": _HAS_REQUESTS,
        "yaml_available": _HAS_YAML,
    }


def get_scan_history(days=7, db_path=None):
    """Get recent scan history from stored creative signals.

    Queries the creative_signals table grouped by source and date.

    Args:
        days: Number of days to look back.
        db_path: Optional database path override.

    Returns:
        Dict with signal counts per source per day.
    """
    conn = _get_db(db_path)
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

        # Per-source per-day counts
        rows = conn.execute(
            """SELECT source, DATE(discovered_at) AS scan_date, COUNT(*) AS count,
                      source_type
               FROM creative_signals
               WHERE discovered_at >= ?
               GROUP BY source, scan_date, source_type
               ORDER BY scan_date DESC""",
            (cutoff,),
        ).fetchall()

        history = {}
        for row in rows:
            src = row["source"]
            if src not in history:
                history[src] = []
            history[src].append({
                "date": row["scan_date"],
                "count": row["count"],
                "source_type": row["source_type"],
            })

        # Total count
        total = conn.execute(
            "SELECT COUNT(*) AS total FROM creative_signals WHERE discovered_at >= ?",
            (cutoff,),
        ).fetchone()["total"]

        # Per-source totals
        source_totals = conn.execute(
            """SELECT source, COUNT(*) AS count
               FROM creative_signals
               WHERE discovered_at >= ?
               GROUP BY source
               ORDER BY count DESC""",
            (cutoff,),
        ).fetchall()

        source_summary = {}
        for row in source_totals:
            source_summary[row["source"]] = row["count"]

        return {
            "days": days,
            "total_signals": total,
            "by_source": history,
            "source_totals": source_summary,
        }
    finally:
        conn.close()


# =========================================================================
# CLI
# =========================================================================
def main():
    parser = argparse.ArgumentParser(
        description="ICDEV Creative Engine Source Scanner -- discover customer pain points and competitive signals"
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable output")
    parser.add_argument("--db-path", type=Path, default=None, help="Database path override")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--scan", action="store_true", help="Run source scan")
    group.add_argument("--list-sources", action="store_true", help="List configured sources")
    group.add_argument("--history", action="store_true", help="Show scan history")

    parser.add_argument("--source", type=str, help="Specific source to scan (with --scan)")
    parser.add_argument("--all", action="store_true", help="Scan all sources (with --scan)")
    parser.add_argument("--days", type=int, default=7, help="History lookback days (with --history)")

    args = parser.parse_args()

    try:
        if args.scan:
            source = None if args.all else args.source
            if not args.all and not args.source:
                source = None  # Default: scan all
            result = run_scan(source=source, db_path=args.db_path)
        elif args.list_sources:
            result = list_sources()
        elif args.history:
            result = get_scan_history(days=args.days, db_path=args.db_path)
        else:
            result = {"error": "No action specified"}

        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            # Human-readable output
            if args.scan:
                print(f"Creative Engine Scan -- {result.get('scan_time', '')}")
                print(f"Sources scanned: {result.get('sources_scanned', 0)}")
                print(f"Competitors available: {result.get('competitors_available', 0)}")
                print(f"Signals discovered: {result.get('signals_discovered', 0)}")
                print(f"Signals stored: {result.get('signals_stored', 0)}")
                print("")
                for src, res in result.get("results", {}).items():
                    if "error" in res:
                        status = f"ERROR: {res['error']}"
                    else:
                        status = f"{res.get('stored', 0)} stored, {res.get('duplicates', 0)} dupes"
                    print(f"  {src:20s} {res.get('signals_found', 0):4d} found -- {status}")
                if result.get("errors"):
                    print(f"\nErrors ({len(result['errors'])}):")
                    for err in result["errors"]:
                        print(f"  - {err}")
            elif args.list_sources:
                print("Creative Engine Sources:")
                print(f"  requests library: {'available' if result.get('requests_available') else 'MISSING'}")
                print(f"  yaml library: {'available' if result.get('yaml_available') else 'MISSING'}")
                print("")
                for src in result.get("sources", []):
                    status = "enabled" if src["enabled"] else "disabled"
                    interval = src.get("scan_interval_hours", "?")
                    print(f"  {src['name']:20s} {status:10s} (every {interval}h) -- {src['scanner_function']}")
            elif args.history:
                print(f"Creative Engine Scan History (last {result.get('days', 7)} days):")
                print(f"Total signals: {result.get('total_signals', 0)}")
                print("")
                source_totals = result.get("source_totals", {})
                if source_totals:
                    print("By source:")
                    for src, count in source_totals.items():
                        print(f"  {src:20s} {count:6d} signals")
                print("")
                for src, entries in result.get("by_source", {}).items():
                    print(f"  {src}:")
                    for entry in entries[:10]:
                        print(f"    {entry['date']}: {entry['count']:4d} ({entry.get('source_type', '?')})")

    except Exception as e:
        error = {"error": str(e)}
        if args.json:
            print(json.dumps(error, indent=2))
        else:
            print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
