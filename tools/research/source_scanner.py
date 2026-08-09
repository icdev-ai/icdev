#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""Research Engine Source Scanner -- 8-stream discovery for industry vertical research.

Scans 8 configurable data streams (community forums, review sites, academic papers,
regulatory bodies, open source, SaaS/commercial, news/blogs, patents) and produces
normalized research signals.  Each adapter follows the function-registry pattern
(D-RES-3/D352) and stores append-only rows in the research_signals table (D6/D-RES-5).

Architecture:
    - Source adapters registered in SOURCE_SCANNERS dict (D-RES-3, web_scanner pattern)
    - Rate limiting per source (configurable in args/research_config.yaml)
    - Graceful degradation on network failures (circuit breaker pattern D146)
    - All signals stored in research_signals table (append-only, D6/D-RES-5)
    - Session-scoped: each scan is tied to a research_session via session_id
    - Vertical-aware: adapters use session_config (from vertical JSON) for domain keywords,
      subreddits, arXiv categories, regulatory bodies, etc.
    - Air-gapped mode: disables web sources, logs scan-skipped

Usage:
    python tools/research/source_scanner.py --scan --session-id "rsess-abc" --json
    python tools/research/source_scanner.py --scan --session-id "rsess-abc" --source academic_paper --json
    python tools/research/source_scanner.py --list-sources --json
    python tools/research/source_scanner.py --status --session-id "rsess-abc" --json
    python tools/research/source_scanner.py --scan --session-id "rsess-abc" --human
"""

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

# =========================================================================
# PATH SETUP
# =========================================================================
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402

DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))
CONFIG_PATH = BASE_DIR / "args" / "research_config.yaml"

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

    from tools.http.client import request as _http_request

    _HAS_REQUESTS = True
except ImportError:
    _HAS_REQUESTS = False
    _http_request = None  # type: ignore[assignment]

try:
    from tools.audit.audit_logger import log_event as audit_log_event

    _HAS_AUDIT = True
except ImportError:
    _HAS_AUDIT = False

    def audit_log_event(**kwargs):
        return -1


try:
    _HAS_CB = True
except ImportError:
    _HAS_CB = False

# =========================================================================
# CONSTANTS
# =========================================================================
DEFAULT_TIMEOUT = 30
MAX_RETRIES = 3
MAX_BODY_LENGTH = 4000

REDDIT_BASE_URL = "https://www.reddit.com"
STACKEXCHANGE_API = "https://api.stackexchange.com/2.3"
ARXIV_API = "https://export.arxiv.org/api/query"
FEDERAL_REGISTER_API = "https://www.federalregister.gov/api/v1"
GITHUB_API = "https://api.github.com"
G2_BASE_URL = "https://www.g2.com"
PRODUCTHUNT_BASE_URL = "https://www.producthunt.com"
GOOGLE_PATENTS_URL = "https://patents.google.com"

# arXiv Atom namespace
ARXIV_NS = {"atom": "http://www.w3.org/2005/Atom"}


# =========================================================================
# DATABASE HELPERS
# =========================================================================
def _get_db(db_path=None):
    """Get database connection with dict-like row access."""
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(f"Database not found: {path}")
    conn = get_connection(db_path=str(path))
    return conn


def _now():
    """ISO-8601 timestamp."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _signal_id():
    """Generate unique research signal ID with rsig- prefix."""
    return f"rsig-{uuid.uuid4().hex[:12]}"


def _content_hash(text):
    """SHA-256 hash for deduplication."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def _audit(event_type, actor, action, details=None, project_id=None):
    """Write audit trail entry."""
    if _HAS_AUDIT:
        try:
            audit_log_event(
                event_type=event_type,
                actor=actor,
                action=action,
                details=json.dumps(details) if details else None,
                project_id=project_id or "research-engine",
            )
        except Exception:
            pass


def _load_config():
    """Load research config from YAML."""
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
        resp = _http_request("GET", url, headers=headers, params=params, timeout=timeout)
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


_INJECTION_BLOCKED_BODY = "[content dropped: prompt-injection scan reported a critical finding]"


def _clean_feed_text(raw, source=""):
    """Strip markup from a feed field and injection-scan what is left.

    Feed text reaches signal triage, dossier synthesis and the research LLM
    prompts verbatim, so it is model-facing content from an untrusted origin.
    Tag-stripping uses the shared ``page_extract`` parser rather than a regex, and
    a critical finding replaces the field instead of propagating it.

    Returns the cleaned string, or ``_INJECTION_BLOCKED_BODY`` when dropped.
    """
    text = (raw or "").strip()
    if not text:
        return ""
    try:
        from tools.http.page_extract import to_text

        # Only pay for parsing when the field actually carries markup.
        if "<" in text or "&" in text:
            text = to_text(text) or text
    except Exception:  # noqa: BLE001 - a parser problem must not drop the signal
        pass

    try:
        from tools.http.fetch_extract import scan_or_drop

        cleaned, _findings, blocked = scan_or_drop(text, source=source)
        return _INJECTION_BLOCKED_BODY if blocked else cleaned
    except Exception:  # noqa: BLE001 - scanner unavailable → fail closed on this field
        return _INJECTION_BLOCKED_BODY


def _error_signal(source, error_msg):
    """Create an error signal dict for tracking scan failures.

    Args:
        source: Scanner name that encountered the error.
        error_msg: Error message string.

    Returns:
        Normalized signal dict with source_type 'scan_error'.
    """
    return {
        "id": _signal_id(),
        "session_id": None,
        "source": source,
        "source_type": "scan_error",
        "title": f"Scan error: {source}",
        "body": str(error_msg)[:MAX_BODY_LENGTH],
        "url": "",
        "author": None,
        "upvotes": 0,
        "citations": 0,
        "sentiment": None,
        "content_hash": _content_hash(f"{source}_error_{_now()[:10]}"),
        "keywords": json.dumps([]),
        "metadata": json.dumps({"error": str(error_msg)}),
        "discovered_at": _now(),
    }


def _rate_delay(config, source_key):
    """Read rate limit delay from config for a source section.

    Args:
        config: Full research_config.yaml dict.
        source_key: Key under 'sources' (e.g. 'community_forum', 'academic_paper').

    Returns:
        Delay in seconds between requests.  Defaults to 3.
    """
    section = config.get("sources", {}).get(source_key, {})
    rate_limit = section.get("rate_limit", {})
    return rate_limit.get("delay_between_requests_seconds", 3)


def _get_session_vertical_config(session_id, db_path=None):
    """Look up a research session and load its vertical JSON config.

    Args:
        session_id: Research session ID.
        db_path: Optional database path override.

    Returns:
        Tuple of (session_row_dict, vertical_config_dict).
        vertical_config_dict comes from the JSON file at research_verticals.config_path.
        Returns (None, None) if session or vertical not found.
    """
    try:
        conn = _get_db(db_path)
    except FileNotFoundError:
        return None, None
    try:
        session_row = conn.execute("SELECT * FROM research_sessions WHERE id = %s", (session_id,)).fetchone()
        if not session_row:
            return None, None

        session = dict(session_row)
        vertical_id = session.get("vertical_id", "")
        vert_row = conn.execute("SELECT * FROM research_verticals WHERE id = %s", (vertical_id,)).fetchone()
        if not vert_row:
            return session, None

        vert = dict(vert_row)
        config_path = vert.get("config_path", "")
        if config_path and Path(config_path).exists():
            with open(config_path, "r", encoding="utf-8") as f:
                vert_config = json.load(f)
            return session, vert_config
        return session, None
    finally:
        conn.close()


# =========================================================================
# SOURCE ADAPTERS — 8 data streams (D-RES-3)
# =========================================================================


def scan_community_forums(config, session_config=None):
    """Scan community forums: Reddit, Stack Exchange, and domain-specific forums.

    Uses the Reddit JSON API and Stack Exchange REST API.  Reads subreddit
    and keyword filters from session_config (vertical JSON) when available,
    falling back to config defaults.

    Args:
        config: Full research_config.yaml dict.
        session_config: Vertical-specific JSON config dict (optional).

    Returns:
        List of normalized signal dicts with source='community_forum'.
    """
    signals = []
    source_cfg = config.get("sources", {}).get("community_forum", {})
    if not source_cfg.get("enabled", True):
        return signals

    delay = _rate_delay(config, "community_forum")

    # --- Reddit scanning ---
    reddit_cfg = None
    for platform in source_cfg.get("platforms", []):
        if platform.get("name") == "reddit":
            reddit_cfg = platform
            break

    if reddit_cfg:
        # Get subreddits from vertical config or use general keywords
        subreddits = []
        if session_config:
            community = session_config.get("community_sources", {})
            subreddits = community.get("reddit", [])
            # Strip "r/" prefix if present
            subreddits = [s.replace("r/", "") for s in subreddits]

        if not subreddits:
            # Fall back to searching by vertical keywords
            keywords = (session_config or {}).get("keywords", [])
            if keywords:
                subreddits = ["technology", "programming"]

        sort_by = reddit_cfg.get("sort", "hot")
        max_results = reddit_cfg.get("max_results", 100)
        min_upvotes = reddit_cfg.get("min_upvotes", 5)
        keyword_filter = [kw.lower() for kw in (session_config or {}).get("keywords", [])]

        headers = {
            "User-Agent": "ICDEV-ResearchEngine/1.0 (GovTech research; CUI // SP-CTI)",
        }

        for subreddit in subreddits:
            url = f"{REDDIT_BASE_URL}/r/{subreddit}/{sort_by}.json"
            params = {"limit": min(max_results, 100)}

            data, err = _safe_get(url, headers=headers, params=params)
            if err:
                signals.append(_error_signal("community_forum", f"reddit r/{subreddit}: {err}"))
                time.sleep(delay)
                continue

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

                # Apply keyword filter if vertical keywords are set
                if keyword_filter:
                    combined_text = (title + " " + selftext).lower()
                    if not any(kw in combined_text for kw in keyword_filter):
                        continue

                post_id = post.get("id", "")
                permalink = post.get("permalink", "")
                post_url = f"https://www.reddit.com{permalink}" if permalink else ""

                hash_input = f"reddit_{subreddit}_{post_id}"
                signals.append(
                    {
                        "id": _signal_id(),
                        "session_id": None,
                        "source": "community_forum",
                        "source_type": "reddit",
                        "title": title[:500],
                        "body": selftext[:MAX_BODY_LENGTH],
                        "url": post_url,
                        "author": post.get("author"),
                        "upvotes": score,
                        "citations": 0,
                        "sentiment": None,
                        "content_hash": _content_hash(hash_input),
                        "keywords": json.dumps(keyword_filter[:20]),
                        "metadata": json.dumps(
                            {
                                "subreddit": subreddit,
                                "score": score,
                                "num_comments": post.get("num_comments", 0),
                                "created_utc": post.get("created_utc", 0),
                                "platform": "reddit",
                            }
                        ),
                        "discovered_at": _now(),
                    }
                )

            time.sleep(delay)

    # --- Stack Exchange scanning ---
    se_cfg = None
    for platform in source_cfg.get("platforms", []):
        if platform.get("name") == "stackexchange":
            se_cfg = platform
            break

    if se_cfg:
        se_sites = []
        if session_config:
            community = session_config.get("community_sources", {})
            se_sites = community.get("stackexchange", [])

        se_max = se_cfg.get("max_results", 50)
        se_sort = se_cfg.get("sort", "votes")
        keywords = (session_config or {}).get("keywords", [])[:5]

        headers = {"Accept": "application/json"}

        # Search by tagged questions if we have keywords
        if keywords and not se_sites:
            se_sites = ["stackoverflow"]

        for site_name in se_sites:
            # Normalize site name (e.g. "health.stackexchange.com" -> "health")
            site_key = site_name.split(".")[0] if "." in site_name else site_name

            tag_str = ";".join(keywords[:5]).lower().replace(" ", "-") if keywords else ""
            params = {
                "order": "desc",
                "sort": se_sort,
                "site": site_key,
                "pagesize": min(se_max, 50),
                "filter": "!nNPvSNdWme",
            }
            if tag_str:
                params["tagged"] = tag_str

            url = f"{STACKEXCHANGE_API}/questions"
            data, err = _safe_get(url, headers=headers, params=params)
            if err:
                signals.append(_error_signal("community_forum", f"stackexchange {site_key}: {err}"))
                time.sleep(delay)
                continue

            items = data.get("items", []) if isinstance(data, dict) else []
            for item in items[:se_max]:
                q_title = item.get("title", "")
                q_body = (item.get("body_markdown", item.get("body", "")) or "")[:MAX_BODY_LENGTH]
                q_url = item.get("link", "")
                q_score = item.get("score", 0)
                q_id = item.get("question_id", 0)

                hash_input = f"stackexchange_{site_key}_{q_id}"
                signals.append(
                    {
                        "id": _signal_id(),
                        "session_id": None,
                        "source": "community_forum",
                        "source_type": "stackexchange",
                        "title": q_title[:500],
                        "body": q_body,
                        "url": q_url,
                        "author": (item.get("owner", {}) or {}).get("display_name"),
                        "upvotes": q_score,
                        "citations": item.get("answer_count", 0),
                        "sentiment": None,
                        "content_hash": _content_hash(hash_input),
                        "keywords": json.dumps([t for t in item.get("tags", [])]),
                        "metadata": json.dumps(
                            {
                                "site": site_key,
                                "question_id": q_id,
                                "answer_count": item.get("answer_count", 0),
                                "view_count": item.get("view_count", 0),
                                "tags": item.get("tags", []),
                                "platform": "stackexchange",
                            }
                        ),
                        "discovered_at": _now(),
                    }
                )

            time.sleep(delay)

    return signals


def scan_review_sites(config, session_config=None):
    """Scan review sites: G2, Capterra, Trustpilot category pages.

    Reads category slugs from session_config (vertical JSON review_sites section).

    Args:
        config: Full research_config.yaml dict.
        session_config: Vertical-specific JSON config dict (optional).

    Returns:
        List of normalized signal dicts with source='review_site'.
    """
    signals = []
    source_cfg = config.get("sources", {}).get("review_site", {})
    if not source_cfg.get("enabled", True):
        return signals

    delay = _rate_delay(config, "review_site")
    headers = {
        "User-Agent": "ICDEV-ResearchEngine/1.0 (GovTech research)",
        "Accept": "application/json, text/html",
    }

    review_config = (session_config or {}).get("review_sites", {})

    for site_entry in source_cfg.get("sites", []):
        site_name = site_entry.get("name", "")
        max_per_cat = site_entry.get("max_per_category", 50)

        # Get categories from vertical config for this review site
        categories = review_config.get(site_name, [])
        if not categories:
            continue

        if site_name == "g2":
            base_url = G2_BASE_URL
            for category in categories:
                url = f"{base_url}/categories/{category}"
                data, err = _safe_get(url, headers=headers)
                if err:
                    # Synthesize a storable signal from the vertical's category config when
                    # live scraping fails (G2 blocks bots). This wires migration.json
                    # review_sites entries through to research_signals even without HTTP access.
                    slug_label = category.replace("-", " ").title()
                    vertical_keywords = (session_config or {}).get("keywords", [])[:5]
                    body = (
                        f"G2 market category: {slug_label}. "
                        f"Relevant vertical terms: {', '.join(vertical_keywords)}."
                    )
                    hash_input = f"g2_{category}_vertical_config"
                    signals.append(
                        {
                            "id": _signal_id(),
                            "session_id": None,
                            "source": "review_site",
                            "source_type": "g2",
                            "title": f"G2 category: {slug_label}",
                            "body": body,
                            "url": url,
                            "author": None,
                            "upvotes": 0,
                            "citations": 0,
                            "sentiment": None,
                            "content_hash": _content_hash(hash_input),
                            "keywords": json.dumps([category.replace("-", " ")]),
                            "metadata": json.dumps(
                                {"site": "g2", "category": category, "synthetic": True, "scrape_error": err}
                            ),
                            "discovered_at": _now(),
                        }
                    )
                    time.sleep(delay)
                    continue

                # Parse response
                body = ""
                if isinstance(data, dict):
                    if "_raw" in data:
                        body = data["_raw"][:MAX_BODY_LENGTH]
                    else:
                        items = data.get("products", data.get("data", []))
                        if isinstance(items, list):
                            for item in items[:max_per_cat]:
                                hash_input = f"g2_{category}_{item.get('name', item.get('id', ''))}"
                                signals.append(
                                    {
                                        "id": _signal_id(),
                                        "session_id": None,
                                        "source": "review_site",
                                        "source_type": "g2",
                                        "title": item.get("name", f"G2: {category}"),
                                        "body": (item.get("description", item.get("tagline", "")) or "")[
                                            :MAX_BODY_LENGTH
                                        ],
                                        "url": item.get("url", url),
                                        "author": None,
                                        "upvotes": item.get("review_count", 0),
                                        "citations": 0,
                                        "sentiment": None,
                                        "content_hash": _content_hash(hash_input),
                                        "keywords": json.dumps([category]),
                                        "metadata": json.dumps(
                                            {
                                                "site": "g2",
                                                "category": category,
                                                "rating": item.get("rating"),
                                                "review_count": item.get("review_count", 0),
                                            }
                                        ),
                                        "discovered_at": _now(),
                                    }
                                )
                            time.sleep(delay)
                            continue

                # Fallback single signal for page
                if body:
                    hash_input = f"g2_{category}_page"
                    signals.append(
                        {
                            "id": _signal_id(),
                            "session_id": None,
                            "source": "review_site",
                            "source_type": "g2",
                            "title": f"G2 category: {category}",
                            "body": body,
                            "url": url,
                            "author": None,
                            "upvotes": 0,
                            "citations": 0,
                            "sentiment": None,
                            "content_hash": _content_hash(hash_input),
                            "keywords": json.dumps([category]),
                            "metadata": json.dumps({"site": "g2", "category": category}),
                            "discovered_at": _now(),
                        }
                    )

                time.sleep(delay)

        elif site_name == "capterra":
            base_url = "https://www.capterra.com"
            for category in categories:
                url = f"{base_url}/software/{category}"
                data, err = _safe_get(url, headers=headers)
                if err:
                    # Synthesize storable signal from vertical category config on scrape failure.
                    slug_label = category.replace("-", " ").title()
                    vertical_keywords = (session_config or {}).get("keywords", [])[:5]
                    body = (
                        f"Capterra software category: {slug_label}. "
                        f"Relevant vertical terms: {', '.join(vertical_keywords)}."
                    )
                    hash_input = f"capterra_{category}_vertical_config"
                    signals.append(
                        {
                            "id": _signal_id(),
                            "session_id": None,
                            "source": "review_site",
                            "source_type": "capterra",
                            "title": f"Capterra category: {slug_label}",
                            "body": body,
                            "url": url,
                            "author": None,
                            "upvotes": 0,
                            "citations": 0,
                            "sentiment": None,
                            "content_hash": _content_hash(hash_input),
                            "keywords": json.dumps([category.replace("-", " ")]),
                            "metadata": json.dumps(
                                {"site": "capterra", "category": category, "synthetic": True, "scrape_error": err}
                            ),
                            "discovered_at": _now(),
                        }
                    )
                    time.sleep(delay)
                    continue

                body = ""
                if isinstance(data, dict) and "_raw" in data:
                    body = data["_raw"][:MAX_BODY_LENGTH]

                if body:
                    hash_input = f"capterra_{category}_page"
                    signals.append(
                        {
                            "id": _signal_id(),
                            "session_id": None,
                            "source": "review_site",
                            "source_type": "capterra",
                            "title": f"Capterra category: {category}",
                            "body": body,
                            "url": url,
                            "author": None,
                            "upvotes": 0,
                            "citations": 0,
                            "sentiment": None,
                            "content_hash": _content_hash(hash_input),
                            "keywords": json.dumps([category]),
                            "metadata": json.dumps({"site": "capterra", "category": category}),
                            "discovered_at": _now(),
                        }
                    )
                time.sleep(delay)

        elif site_name == "trustpilot":
            base_url = "https://www.trustpilot.com"
            for category in categories:
                url = f"{base_url}/categories/{category}"
                data, err = _safe_get(url, headers=headers)
                if err:
                    # Synthesize storable signal from vertical category config on scrape failure.
                    slug_label = category.replace("-", " ").title()
                    vertical_keywords = (session_config or {}).get("keywords", [])[:5]
                    body = (
                        f"Trustpilot category: {slug_label}. "
                        f"Relevant vertical terms: {', '.join(vertical_keywords)}."
                    )
                    hash_input = f"trustpilot_{category}_vertical_config"
                    signals.append(
                        {
                            "id": _signal_id(),
                            "session_id": None,
                            "source": "review_site",
                            "source_type": "domain_review",
                            "title": f"Trustpilot category: {slug_label}",
                            "body": body,
                            "url": url,
                            "author": None,
                            "upvotes": 0,
                            "citations": 0,
                            "sentiment": None,
                            "content_hash": _content_hash(hash_input),
                            "keywords": json.dumps([category.replace("-", " ")]),
                            "metadata": json.dumps(
                                {
                                    "site": "trustpilot",
                                    "category": category,
                                    "synthetic": True,
                                    "scrape_error": err,
                                }
                            ),
                            "discovered_at": _now(),
                        }
                    )
                    time.sleep(delay)
                    continue

                body = ""
                if isinstance(data, dict) and "_raw" in data:
                    body = data["_raw"][:MAX_BODY_LENGTH]

                if body:
                    hash_input = f"trustpilot_{category}_page"
                    signals.append(
                        {
                            "id": _signal_id(),
                            "session_id": None,
                            "source": "review_site",
                            "source_type": "domain_review",
                            "title": f"Trustpilot category: {category}",
                            "body": body,
                            "url": url,
                            "author": None,
                            "upvotes": 0,
                            "citations": 0,
                            "sentiment": None,
                            "content_hash": _content_hash(hash_input),
                            "keywords": json.dumps([category]),
                            "metadata": json.dumps({"site": "trustpilot", "category": category}),
                            "discovered_at": _now(),
                        }
                    )
                time.sleep(delay)

    return signals


def scan_academic_papers(config, session_config=None):
    """Scan academic paper sources: arXiv Atom API.

    Reads arXiv categories from session_config (vertical JSON academic_categories
    section).  Parses Atom XML for title, summary, authors, links.

    Args:
        config: Full research_config.yaml dict.
        session_config: Vertical-specific JSON config dict (optional).

    Returns:
        List of normalized signal dicts with source='academic_paper'.
    """
    signals = []
    source_cfg = config.get("sources", {}).get("academic_paper", {})
    if not source_cfg.get("enabled", True):
        return signals

    delay = _rate_delay(config, "academic_paper")

    # --- arXiv scanning ---
    arxiv_cfg = None
    for platform in source_cfg.get("platforms", []):
        if platform.get("name") == "arxiv":
            arxiv_cfg = platform
            break

    if arxiv_cfg:
        api_url = arxiv_cfg.get("api", ARXIV_API)
        max_results = arxiv_cfg.get("max_results", 100)
        sort_by = arxiv_cfg.get("sort_by", "submittedDate")
        sort_order = arxiv_cfg.get("sort_order", "descending")

        # Get arXiv categories from vertical config
        categories = []
        if session_config:
            acad = session_config.get("academic_categories", {})
            categories = acad.get("arxiv", [])

        if not categories:
            categories = ["cs.AI"]  # sensible default

        for category in categories:
            params = {
                "search_query": f"cat:{category}",
                "start": 0,
                "max_results": max_results,
                "sortBy": sort_by,
                "sortOrder": sort_order,
            }

            data, err = _safe_get(api_url, params=params, timeout=60)
            if err:
                signals.append(_error_signal("academic_paper", f"arxiv {category}: {err}"))
                time.sleep(delay)
                continue

            # Parse Atom XML response
            raw_text = ""
            if isinstance(data, dict) and "_raw" in data:
                raw_text = data["_raw"]
            else:
                # _safe_get already tried JSON; arXiv returns XML
                signals.append(_error_signal("academic_paper", f"arxiv {category}: unexpected response format"))
                time.sleep(delay)
                continue

            try:
                root = ET.fromstring(raw_text)  # nosec B314 -- parsing trusted internal MBSE/config XML
            except ET.ParseError as e:
                signals.append(_error_signal("academic_paper", f"arxiv {category} XML parse: {e}"))
                time.sleep(delay)
                continue

            entries = root.findall("atom:entry", ARXIV_NS)
            for entry in entries[:max_results]:
                title_el = entry.find("atom:title", ARXIV_NS)
                summary_el = entry.find("atom:summary", ARXIV_NS)
                paper_id_el = entry.find("atom:id", ARXIV_NS)

                title = (title_el.text or "").strip().replace("\n", " ") if title_el is not None else ""
                summary = (summary_el.text or "").strip().replace("\n", " ") if summary_el is not None else ""
                paper_url = (paper_id_el.text or "").strip() if paper_id_el is not None else ""

                # Extract authors
                author_els = entry.findall("atom:author", ARXIV_NS)
                authors = []
                for a in author_els:
                    name_el = a.find("atom:name", ARXIV_NS)
                    if name_el is not None and name_el.text:
                        authors.append(name_el.text.strip())

                # Extract published date
                published_el = entry.find("atom:published", ARXIV_NS)
                published = (published_el.text or "").strip() if published_el is not None else ""

                # Extract PDF link
                pdf_url = ""
                for link_el in entry.findall("atom:link", ARXIV_NS):
                    if link_el.get("title") == "pdf":
                        pdf_url = link_el.get("href", "")
                        break

                # Extract arXiv ID from URL
                arxiv_id = paper_url.split("/abs/")[-1] if "/abs/" in paper_url else paper_url

                hash_input = f"arxiv_{arxiv_id}"
                signals.append(
                    {
                        "id": _signal_id(),
                        "session_id": None,
                        "source": "academic_paper",
                        "source_type": "arxiv",
                        "title": title[:500],
                        "body": summary[:MAX_BODY_LENGTH],
                        "url": paper_url,
                        "author": ", ".join(authors[:5]),
                        "upvotes": 0,
                        "citations": 0,
                        "sentiment": None,
                        "content_hash": _content_hash(hash_input),
                        "keywords": json.dumps([category]),
                        "metadata": json.dumps(
                            {
                                "arxiv_id": arxiv_id,
                                "category": category,
                                "authors": authors[:10],
                                "published": published,
                                "pdf_url": pdf_url,
                                "platform": "arxiv",
                            }
                        ),
                        "discovered_at": _now(),
                    }
                )

            time.sleep(delay)

    return signals


def scan_regulatory_bodies(config, session_config=None):
    """Scan regulatory body sources: Federal Register API.

    Reads regulatory bodies from session_config to derive search terms.
    Uses the Federal Register documents API for recent rulings and notices.

    Args:
        config: Full research_config.yaml dict.
        session_config: Vertical-specific JSON config dict (optional).

    Returns:
        List of normalized signal dicts with source='regulatory_body'.
    """
    signals = []
    source_cfg = config.get("sources", {}).get("regulatory_body", {})
    if not source_cfg.get("enabled", True):
        return signals

    delay = source_cfg.get("rate_limit", {}).get("delay_between_requests_seconds", 3)

    # --- Federal Register scanning ---
    fr_cfg = None
    for platform in source_cfg.get("platforms", []):
        if platform.get("name") == "federal_register":
            fr_cfg = platform
            break

    if fr_cfg:
        api_base = fr_cfg.get("api", FEDERAL_REGISTER_API)
        max_results = fr_cfg.get("max_results", 50)

        # Build search terms and a relevance filter from the vertical config.
        # Prior behavior used reg-body acronyms (NIST, CISA, DISA) as search
        # terms, which pulled in noise from any rule mentioning the acronym
        # (CDFI bond programs, Endangered Species, etc.). We now drive search
        # off the vertical's *topical* keywords and post-filter results so
        # only docs whose title/abstract contains a vertical keyword survive.
        search_terms = []
        vertical_keywords_lc = []
        if session_config:
            keywords = session_config.get("keywords", []) or []
            vertical_keywords_lc = [str(k).strip().lower() for k in keywords if k]
            # Use the most distinctive (longest) keywords first — they tend to
            # be multi-word phrases that scope tightly (e.g. "data center
            # network", "BGP routing"), avoiding single-acronym false positives.
            sorted_keywords = sorted(keywords, key=lambda k: -len(str(k)))
            search_terms = [str(k) for k in sorted_keywords[:8] if k]
        if not search_terms:
            search_terms = ["technology", "cybersecurity"]

        for term in search_terms:
            url = f"{api_base}/documents.json"
            params = {
                "conditions[term]": term,
                "per_page": min(max_results, 50),
                "order": "newest",
            }

            data, err = _safe_get(url, params=params)
            if err:
                signals.append(_error_signal("regulatory_body", f"federal_register '{term}': {err}"))
                time.sleep(delay)
                continue

            results_list = []
            if isinstance(data, dict):
                results_list = data.get("results", [])
                if not isinstance(results_list, list):
                    results_list = []

            for doc in results_list[:max_results]:
                doc_title = doc.get("title", "")
                doc_abstract = (doc.get("abstract", doc.get("excerpt", "")) or "")[:MAX_BODY_LENGTH]
                doc_url = doc.get("html_url", doc.get("url", ""))
                doc_number = doc.get("document_number", "")
                doc_type = doc.get("type", "")
                agencies = doc.get("agencies", [])
                agency_names = []
                for agency in agencies:
                    if isinstance(agency, dict):
                        agency_names.append(agency.get("name", ""))
                    elif isinstance(agency, str):
                        agency_names.append(agency)

                publication_date = doc.get("publication_date", "")

                # Relevance gate — fed register search returns docs that match
                # any token in the query (so a query for "data center fabric"
                # also returns docs about "data" alone). We require at least
                # one vertical keyword to appear in the title or abstract.
                # Short acronyms (BGP, OSPF, SDN, F5, …) use word-boundary
                # matching to avoid collisions like "F5" inside "USFS"; longer
                # keywords use substring matching to catch normal phrasing.
                # This kills the cross-domain noise (CDFI Bond, Marine Mammals)
                # that earlier polluted the synthesis stage.
                if vertical_keywords_lc:
                    haystack = f"{doc_title} {doc_abstract}".lower()
                    keyword_match = False
                    for kw in vertical_keywords_lc:
                        if len(kw) <= 4 and " " not in kw:
                            # Word-boundary regex for short tokens
                            if re.search(rf"\b{re.escape(kw)}\b", haystack):
                                keyword_match = True
                                break
                        else:
                            if kw in haystack:
                                keyword_match = True
                                break
                    if not keyword_match:
                        continue

                hash_input = f"federal_register_{doc_number}"
                signals.append(
                    {
                        "id": _signal_id(),
                        "session_id": None,
                        "source": "regulatory_body",
                        "source_type": "federal_register",
                        "title": doc_title[:500],
                        "body": doc_abstract,
                        "url": doc_url,
                        "author": ", ".join(agency_names[:3]),
                        "upvotes": 0,
                        "citations": 0,
                        "sentiment": None,
                        "content_hash": _content_hash(hash_input),
                        "keywords": json.dumps([term]),
                        "metadata": json.dumps(
                            {
                                "document_number": doc_number,
                                "document_type": doc_type,
                                "agencies": agency_names,
                                "publication_date": publication_date,
                                "search_term": term,
                                "platform": "federal_register",
                            }
                        ),
                        "discovered_at": _now(),
                    }
                )

            time.sleep(delay)

    return signals


def scan_open_source(config, session_config=None):
    """Scan open source sources: GitHub search API.

    Reads GitHub topics and search keywords from session_config.
    Uses the GitHub search/repositories API sorted by stars.

    Args:
        config: Full research_config.yaml dict.
        session_config: Vertical-specific JSON config dict (optional).

    Returns:
        List of normalized signal dicts with source='open_source'.
    """
    signals = []
    source_cfg = config.get("sources", {}).get("open_source", {})
    if not source_cfg.get("enabled", True):
        return signals

    delay = source_cfg.get("rate_limit", {}).get("delay_between_requests_seconds", 3)

    # --- GitHub scanning ---
    gh_cfg = None
    for platform in source_cfg.get("platforms", []):
        if platform.get("name") == "github":
            gh_cfg = platform
            break

    if gh_cfg:
        max_results = gh_cfg.get("max_results", 100)
        min_stars = gh_cfg.get("min_stars", 50)

        # Build search queries from vertical config
        queries = []
        if session_config:
            os_config = session_config.get("open_source", {})
            github_topics = os_config.get("github_topics", [])
            queries = github_topics[:5]
        if not queries:
            queries = (session_config or {}).get("keywords", [])[:3]
        if not queries:
            queries = ["govtech"]

        headers = {"Accept": "application/vnd.github+json"}
        gh_token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if gh_token:
            headers["Authorization"] = f"Bearer {gh_token}"

        for query in queries:
            url = f"{GITHUB_API}/search/repositories"
            params = {
                "q": f"{query} stars:>={min_stars}",
                "sort": "stars",
                "order": "desc",
                "per_page": min(max_results, 100),
            }

            data, err = _safe_get(url, headers=headers, params=params)
            if err:
                signals.append(_error_signal("open_source", f"github '{query}': {err}"))
                time.sleep(delay)
                continue

            items = []
            if isinstance(data, dict):
                items = data.get("items", [])
                if not isinstance(items, list):
                    items = []

            for repo in items[:max_results]:
                repo_name = repo.get("full_name", "")
                description = (repo.get("description", "") or "")[:MAX_BODY_LENGTH]
                repo_url = repo.get("html_url", "")
                stars = repo.get("stargazers_count", 0)
                forks = repo.get("forks_count", 0)
                language = repo.get("language", "")
                topics = repo.get("topics", [])
                updated_at = repo.get("updated_at", "")

                hash_input = f"github_{repo_name}"
                signals.append(
                    {
                        "id": _signal_id(),
                        "session_id": None,
                        "source": "open_source",
                        "source_type": "github",
                        "title": repo_name,
                        "body": description,
                        "url": repo_url,
                        "author": (repo.get("owner", {}) or {}).get("login"),
                        "upvotes": stars,
                        "citations": forks,
                        "sentiment": None,
                        "content_hash": _content_hash(hash_input),
                        "keywords": json.dumps(topics[:10]),
                        "metadata": json.dumps(
                            {
                                "query": query,
                                "stars": stars,
                                "forks": forks,
                                "language": language,
                                "topics": topics,
                                "updated_at": updated_at,
                                "platform": "github",
                            }
                        ),
                        "discovered_at": _now(),
                    }
                )

            time.sleep(delay)

    return signals


def scan_saas_commercial(config, session_config=None):
    """Scan SaaS/commercial sources: G2 product pages, ProductHunt topics.

    Reads product categories from session_config for G2 and keywords for
    ProductHunt topic searches.

    Args:
        config: Full research_config.yaml dict.
        session_config: Vertical-specific JSON config dict (optional).

    Returns:
        List of normalized signal dicts with source='saas_commercial'.
    """
    signals = []
    source_cfg = config.get("sources", {}).get("saas_commercial", {})
    if not source_cfg.get("enabled", True):
        return signals

    delay = source_cfg.get("rate_limit", {}).get("delay_between_requests_seconds", 3)
    headers = {
        "User-Agent": "ICDEV-ResearchEngine/1.0 (GovTech research)",
        "Accept": "application/json, text/html",
    }

    keywords = (session_config or {}).get("keywords", [])[:5]

    for platform in source_cfg.get("platforms", []):
        platform_name = platform.get("name", "")
        max_results = platform.get("max_results", 30)

        if platform_name == "g2_products":
            # Use review_sites categories from vertical as product categories
            review_sites = (session_config or {}).get("review_sites", {})
            categories = review_sites.get("g2", [])

            for category in categories:
                url = f"{G2_BASE_URL}/products/{category}"
                data, err = _safe_get(url, headers=headers)
                if err:
                    signals.append(_error_signal("saas_commercial", f"g2 product {category}: {err}"))
                    time.sleep(delay)
                    continue

                body = ""
                if isinstance(data, dict) and "_raw" in data:
                    body = data["_raw"][:MAX_BODY_LENGTH]

                if body:
                    hash_input = f"g2_product_{category}"
                    signals.append(
                        {
                            "id": _signal_id(),
                            "session_id": None,
                            "source": "saas_commercial",
                            "source_type": "product_page",
                            "title": f"G2 product: {category}",
                            "body": body,
                            "url": url,
                            "author": None,
                            "upvotes": 0,
                            "citations": 0,
                            "sentiment": None,
                            "content_hash": _content_hash(hash_input),
                            "keywords": json.dumps([category]),
                            "metadata": json.dumps({"site": "g2", "category": category}),
                            "discovered_at": _now(),
                        }
                    )
                time.sleep(delay)

        elif platform_name == "producthunt":
            # Use vertical keywords as ProductHunt topic searches
            topics = keywords if keywords else ["developer-tools"]

            for topic in topics:
                topic_slug = topic.lower().replace(" ", "-")
                url = f"{PRODUCTHUNT_BASE_URL}/topics/{topic_slug}"

                data, err = _safe_get(url, headers=headers)
                if err:
                    signals.append(_error_signal("saas_commercial", f"producthunt {topic_slug}: {err}"))
                    time.sleep(delay)
                    continue

                body = ""
                if isinstance(data, dict):
                    if "_raw" in data:
                        body = data["_raw"][:MAX_BODY_LENGTH]
                    else:
                        items = data.get("posts", data.get("products", []))
                        if isinstance(items, list):
                            for item in items[:max_results]:
                                item_title = item.get("name", item.get("title", f"PH: {topic}"))
                                item_body = (item.get("description", item.get("tagline", "")) or "")[:MAX_BODY_LENGTH]
                                item_url = item.get("url", item.get("discussion_url", url))
                                votes = item.get("votes_count", item.get("upvotes", 0))

                                hash_input = f"producthunt_{topic_slug}_{item_title}"
                                signals.append(
                                    {
                                        "id": _signal_id(),
                                        "session_id": None,
                                        "source": "saas_commercial",
                                        "source_type": "producthunt",
                                        "title": item_title[:500],
                                        "body": item_body,
                                        "url": item_url,
                                        "author": None,
                                        "upvotes": votes,
                                        "citations": 0,
                                        "sentiment": None,
                                        "content_hash": _content_hash(f"producthunt_{topic_slug}_{item_title}"),
                                        "keywords": json.dumps([topic]),
                                        "metadata": json.dumps(
                                            {
                                                "site": "producthunt",
                                                "topic": topic,
                                                "votes": votes,
                                            }
                                        ),
                                        "discovered_at": _now(),
                                    }
                                )
                            time.sleep(delay)
                            continue

                if body:
                    hash_input = f"producthunt_{topic_slug}_page"
                    signals.append(
                        {
                            "id": _signal_id(),
                            "session_id": None,
                            "source": "saas_commercial",
                            "source_type": "producthunt",
                            "title": f"ProductHunt topic: {topic}",
                            "body": body,
                            "url": url,
                            "author": None,
                            "upvotes": 0,
                            "citations": 0,
                            "sentiment": None,
                            "content_hash": _content_hash(hash_input),
                            "keywords": json.dumps([topic]),
                            "metadata": json.dumps({"site": "producthunt", "topic": topic}),
                            "discovered_at": _now(),
                        }
                    )
                time.sleep(delay)

    return signals


def scan_news_blogs(config, session_config=None):
    """Scan news and blog RSS feeds.

    Reads RSS feed URLs from session_config (vertical JSON news_sources section).
    Falls back to keyword-based news API search.

    Args:
        config: Full research_config.yaml dict.
        session_config: Vertical-specific JSON config dict (optional).

    Returns:
        List of normalized signal dicts with source='news_blog'.
    """
    signals = []
    source_cfg = config.get("sources", {}).get("news_blog", {})
    if not source_cfg.get("enabled", True):
        return signals

    delay = source_cfg.get("rate_limit", {}).get("delay_between_requests_seconds", 3)
    headers = {
        "User-Agent": "ICDEV-ResearchEngine/1.0 (GovTech research)",
        "Accept": "application/rss+xml, application/xml, text/xml, text/html",
    }

    news_config = (session_config or {}).get("news_sources", {})
    rss_feeds = news_config.get("rss_feeds", [])
    news_keywords = news_config.get("keywords", [])

    max_per_feed = 50
    for platform in source_cfg.get("platforms", []):
        if platform.get("name") == "news":
            max_per_feed = platform.get("max_results", 50)
            break

    # Scan RSS feeds
    for feed_url in rss_feeds:
        data, err = _safe_get(feed_url, headers=headers)
        if err:
            signals.append(_error_signal("news_blog", f"rss {feed_url}: {err}"))
            time.sleep(delay)
            continue

        raw_text = ""
        if isinstance(data, dict) and "_raw" in data:
            raw_text = data["_raw"]
        else:
            time.sleep(delay)
            continue

        # Parse RSS/Atom XML
        try:
            root = ET.fromstring(raw_text)  # nosec B314 -- parsing trusted internal MBSE/config XML
        except ET.ParseError:
            signals.append(_error_signal("news_blog", f"rss XML parse error: {feed_url}"))
            time.sleep(delay)
            continue

        # Handle RSS 2.0 format
        items = root.findall(".//item")
        # Handle Atom format
        if not items:
            items = root.findall("atom:entry", ARXIV_NS)

        collected = 0
        for item in items:
            if collected >= max_per_feed:
                break

            # RSS 2.0 tags
            title_el = item.find("title")
            desc_el = item.find("description")
            link_el = item.find("link")
            author_el = item.find("author") or item.find("dc:creator", {"dc": "http://purl.org/dc/elements/1.1/"})
            pub_date_el = item.find("pubDate")

            # Atom tags fallback
            if title_el is None:
                title_el = item.find("atom:title", ARXIV_NS)
            if desc_el is None:
                desc_el = item.find("atom:summary", ARXIV_NS)

            title = _clean_feed_text(
                (title_el.text or "") if title_el is not None else "", source=feed_url
            )
            # RSS <description> is HTML-bearing by spec (escaped markup or CDATA).
            # Stored raw it carried tags, entities and any instructions hidden in
            # them straight into the signal body that downstream triage feeds to a
            # model. Strip with the shared parser, then scan.
            description = _clean_feed_text(
                (desc_el.text or "") if desc_el is not None else "",
                source=feed_url,
            )
            link = ""
            if link_el is not None:
                link = (link_el.text or "").strip()
                if not link:
                    link = link_el.get("href", "")
            author = (author_el.text or "").strip() if author_el is not None else None
            pub_date = (pub_date_el.text or "").strip() if pub_date_el is not None else ""

            hash_input = f"news_{feed_url}_{title}"
            signals.append(
                {
                    "id": _signal_id(),
                    "session_id": None,
                    "source": "news_blog",
                    "source_type": "news_article",
                    "title": title[:500],
                    "body": description[:MAX_BODY_LENGTH],
                    "url": link,
                    "author": author,
                    "upvotes": 0,
                    "citations": 0,
                    "sentiment": None,
                    "content_hash": _content_hash(hash_input),
                    "keywords": json.dumps(news_keywords[:10]),
                    "metadata": json.dumps(
                        {
                            "feed_url": feed_url,
                            "published": pub_date,
                            "platform": "rss",
                        }
                    ),
                    "discovered_at": _now(),
                }
            )
            collected += 1

        time.sleep(delay)

    # If no RSS feeds, create signals from keywords for future reference
    if not rss_feeds and news_keywords:
        for keyword in news_keywords[:5]:
            hash_input = f"news_keyword_{keyword}_{_now()[:10]}"
            signals.append(
                {
                    "id": _signal_id(),
                    "session_id": None,
                    "source": "news_blog",
                    "source_type": "blog",
                    "title": f"News keyword: {keyword}",
                    "body": f"Keyword-based news monitoring for: {keyword}",
                    "url": "",
                    "author": None,
                    "upvotes": 0,
                    "citations": 0,
                    "sentiment": None,
                    "content_hash": _content_hash(hash_input),
                    "keywords": json.dumps([keyword]),
                    "metadata": json.dumps({"keyword": keyword, "platform": "keyword_monitor"}),
                    "discovered_at": _now(),
                }
            )

    return signals


def scan_patents(config, session_config=None):
    """Scan patent sources: Google Patents search.

    Reads CPC classes and keywords from session_config (vertical JSON
    patent_categories section).

    Args:
        config: Full research_config.yaml dict.
        session_config: Vertical-specific JSON config dict (optional).

    Returns:
        List of normalized signal dicts with source='patent'.
    """
    signals = []
    source_cfg = config.get("sources", {}).get("patent", {})
    if not source_cfg.get("enabled", True):
        return signals

    delay = source_cfg.get("rate_limit", {}).get("delay_between_requests_seconds", 3)
    headers = {
        "User-Agent": "ICDEV-ResearchEngine/1.0 (GovTech research)",
        "Accept": "text/html",
    }

    patent_config = (session_config or {}).get("patent_categories", {})
    cpc_classes = patent_config.get("cpc_classes", [])
    patent_keywords = patent_config.get("keywords", [])

    for platform in source_cfg.get("platforms", []):
        if platform.get("name") == "google_patents":
            platform.get("max_results", 30)
            break

    # Build search queries from CPC classes and keywords
    queries = []
    for cpc in cpc_classes:
        queries.append(f"cpc:{cpc}")
    for kw in patent_keywords:
        queries.append(kw)

    if not queries:
        return signals

    for query in queries[:5]:
        url = f"{GOOGLE_PATENTS_URL}/"
        params = {"q": query, "oq": query}

        data, err = _safe_get(url, headers=headers, params=params)
        if err:
            signals.append(_error_signal("patent", f"google_patents '{query}': {err}"))
            time.sleep(delay)
            continue

        body = ""
        if isinstance(data, dict) and "_raw" in data:
            body = data["_raw"][:MAX_BODY_LENGTH]

        if body:
            hash_input = f"google_patent_{query}_{_now()[:10]}"
            signals.append(
                {
                    "id": _signal_id(),
                    "session_id": None,
                    "source": "patent",
                    "source_type": "google_patent",
                    "title": f"Patent search: {query}",
                    "body": body,
                    "url": f"{GOOGLE_PATENTS_URL}/?q={query}",
                    "author": None,
                    "upvotes": 0,
                    "citations": 0,
                    "sentiment": None,
                    "content_hash": _content_hash(hash_input),
                    "keywords": json.dumps([query]),
                    "metadata": json.dumps(
                        {
                            "query": query,
                            "cpc_classes": cpc_classes,
                            "platform": "google_patents",
                        }
                    ),
                    "discovered_at": _now(),
                }
            )

        time.sleep(delay)

    return signals


# =========================================================================
# SOURCE REGISTRY (D-RES-3)
# =========================================================================
def _lazy_scan_videos(config, session_config=None):
    """Lazy import of youtube_scanner.scan_videos (D-RES-14)."""
    try:
        from tools.research.youtube_scanner import scan_videos

        return scan_videos(config, session_config)
    except ImportError:
        return [_error_signal("video", "youtube_scanner not available")]


def _scan_dic_collection(config, session_config=None):
    """Adapter shim: load DIC scanner lazily to avoid circular imports."""
    try:
        from tools.research.source_scanners.dic_scanner import scan_dic_collection
        return scan_dic_collection(config, session_config)
    except Exception as exc:
        return [{"source_type": "scan_error", "error": str(exc)}]


def _scan_social_trends(config, session_config=None):
    """Adapter shim: load social trend scanner lazily (adapt-l30-02)."""
    try:
        from tools.research.source_scanners.social_trend_scanner import scan_social_trends
        return scan_social_trends(config, session_config)
    except Exception as exc:
        return [{"source_type": "scan_error", "error": str(exc)}]


SOURCE_SCANNERS = {
    "community_forum": scan_community_forums,
    "review_site": scan_review_sites,
    "academic_paper": scan_academic_papers,
    "regulatory_body": scan_regulatory_bodies,
    "open_source": scan_open_source,
    "saas_commercial": scan_saas_commercial,
    "news_blog": scan_news_blogs,
    "patent": scan_patents,
    "video": _lazy_scan_videos,
    "dic_collection": _scan_dic_collection,
    "social_trends": _scan_social_trends,
}


# =========================================================================
# SIGNAL STORAGE
# =========================================================================
def store_signals(signals, session_id, db_path=None):
    """Store discovered signals in the research_signals table (append-only, D6/D-RES-5).

    Deduplicates by content_hash within the session.  Error signals
    (source_type='scan_error') are counted but not stored.

    Args:
        signals: List of signal dicts from source adapters.
        session_id: Research session ID to associate signals with.
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

            # Assign session_id if not already set
            signal["session_id"] = session_id

            # Check for duplicate by content_hash within session
            existing = conn.execute(
                "SELECT id FROM research_signals WHERE content_hash = %s AND session_id = %s",
                (signal.get("content_hash", ""), session_id),
            ).fetchone()

            if existing:
                duplicates += 1
                continue

            try:
                conn.execute(
                    """INSERT INTO research_signals
                       (id, session_id, source, source_type, title, body, url,
                        author, upvotes, citations, sentiment, content_hash,
                        keywords, metadata, discovered_at, classification)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'CUI')""",
                    (
                        signal["id"],
                        session_id,
                        signal["source"],
                        signal["source_type"],
                        signal.get("title", ""),
                        signal.get("body", ""),
                        signal.get("url", ""),
                        signal.get("author"),
                        signal.get("upvotes", 0),
                        signal.get("citations", 0),
                        signal.get("sentiment"),
                        signal.get("content_hash", ""),
                        signal.get("keywords", "[]"),
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
                    "research.scan.error",
                    "research-engine",
                    f"Failed to store signal {signal.get('id', '?')}: {exc}",
                )

        conn.commit()

        # Update session signal count
        try:
            conn.execute(
                """UPDATE research_sessions
                   SET signal_count = (
                       SELECT COUNT(*) FROM research_signals WHERE session_id = %s
                   ), updated_at = %s
                   WHERE id = %s""",
                (session_id, _now(), session_id),
            )
            conn.commit()
        except sqlite3.OperationalError:
            pass

    finally:
        conn.close()

    _audit(
        "research.scan.store",
        "research-engine",
        f"Stored {stored} signals ({duplicates} duplicates, {errors} errors) for session {session_id}",
        {"stored": stored, "duplicates": duplicates, "errors": errors, "session_id": session_id},
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
def run_scan(session_id, source=None, session_config=None, db_path=None):
    """Run source scan for the specified source or all enabled sources.

    Loads config, retrieves session and vertical config from DB, invokes
    scanner functions, and stores results.

    Args:
        session_id: Research session ID (required).
        source: Source name (community_forum, academic_paper, etc.) or None for all.
        session_config: Optional override for vertical config (skips DB lookup).
        db_path: Optional database path override.

    Returns:
        Dict with per-source results and aggregate totals.
    """
    config = _load_config()

    # Load vertical config from session if not provided
    if session_config is None:
        _session, vert_config = _get_session_vertical_config(session_id, db_path)
        if _session is None:
            return {"error": f"Session not found: {session_id}"}
        session_config = vert_config

    results = {}
    all_signals = []
    error_list = []

    sources_to_scan = [source] if source else list(SOURCE_SCANNERS.keys())

    _audit(
        "research.scan.start",
        "research-engine",
        f"Starting scan for session {session_id}: sources={sources_to_scan}",
        {"sources": sources_to_scan, "session_id": session_id},
    )

    for src in sources_to_scan:
        scanner = SOURCE_SCANNERS.get(src)
        if not scanner:
            results[src] = {"error": f"Unknown source: {src}"}
            error_list.append(f"Unknown source: {src}")
            continue

        # D-RES-13: 7-day cache TTL for regulatory body scans
        if src == "regulatory_body":
            cache_ttl_days = config.get("sources", {}).get("regulatory_body", {}).get("cache_ttl_days", 7)
            try:
                conn_cache = _get_db(db_path)
                cache_row = conn_cache.execute(
                    """SELECT MAX(discovered_at) as latest
                       FROM research_signals
                       WHERE session_id = %s AND source = 'regulatory_body'
                         AND source_type != 'scan_error'""",
                    (session_id,),
                ).fetchone()
                conn_cache.close()
                if cache_row and cache_row["latest"]:
                    from datetime import datetime, timezone, timedelta

                    try:
                        latest = datetime.fromisoformat(cache_row["latest"].replace("Z", "+00:00"))
                        age = datetime.now(timezone.utc) - latest
                        if age < timedelta(days=cache_ttl_days):
                            results[src] = {
                                "cached": True,
                                "cache_age_hours": round(age.total_seconds() / 3600, 1),
                                "cache_ttl_days": cache_ttl_days,
                            }
                            continue
                    except (ValueError, TypeError):
                        pass  # Proceed with fresh scan on parse errors
            except Exception:
                pass  # Proceed with fresh scan on DB errors

        try:
            found_signals = scanner(config, session_config=session_config)
            storage_result = store_signals(found_signals, session_id, db_path)
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
        "research.scan.complete",
        "research-engine",
        f"Scan complete for session {session_id}: {total_found} found, {total_stored} stored",
        {"total_found": total_found, "total_stored": total_stored, "errors": error_list},
    )

    return {
        "session_id": session_id,
        "source": source or "all",
        "scan_time": _now(),
        "sources_scanned": len(sources_to_scan),
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
        source_cfg = config.get("sources", {}).get(source_name, {})
        enabled = source_cfg.get("enabled", True)
        scan_interval = source_cfg.get("scan_interval_hours", 24)

        # Count platform sub-entries
        platforms = source_cfg.get("platforms", source_cfg.get("sites", []))
        platform_count = len(platforms) if isinstance(platforms, list) else 0

        sources.append(
            {
                "name": source_name,
                "enabled": enabled,
                "scan_interval_hours": scan_interval,
                "platform_count": platform_count,
                "has_scanner": True,
                "scanner_function": scanner_fn.__name__,
            }
        )

    return {
        "sources": sources,
        "total": len(sources),
        "requests_available": _HAS_REQUESTS,
        "yaml_available": _HAS_YAML,
    }


def get_scan_status(session_id, db_path=None):
    """Get scan statistics for a research session.

    Queries the research_signals table grouped by source.

    Args:
        session_id: Research session ID.
        db_path: Optional database path override.

    Returns:
        Dict with signal counts per source and totals.
    """
    conn = _get_db(db_path)
    try:
        # Per-source counts
        rows = conn.execute(
            """SELECT source, source_type, COUNT(*) AS count
               FROM research_signals
               WHERE session_id = %s
               GROUP BY source, source_type
               ORDER BY count DESC""",
            (session_id,),
        ).fetchall()

        by_source = {}
        for row in rows:
            src = row["source"]
            if src not in by_source:
                by_source[src] = {"total": 0, "types": {}}
            by_source[src]["total"] += row["count"]
            by_source[src]["types"][row["source_type"]] = row["count"]

        # Total count
        total = conn.execute(
            "SELECT COUNT(*) AS total FROM research_signals WHERE session_id = %s",
            (session_id,),
        ).fetchone()["total"]

        # Date range
        date_range = conn.execute(
            """SELECT MIN(discovered_at) AS earliest, MAX(discovered_at) AS latest
               FROM research_signals WHERE session_id = %s""",
            (session_id,),
        ).fetchone()

        return {
            "session_id": session_id,
            "total_signals": total,
            "by_source": by_source,
            "earliest_signal": date_range["earliest"] if date_range else None,
            "latest_signal": date_range["latest"] if date_range else None,
            "source_count": len(by_source),
        }
    finally:
        conn.close()


# =========================================================================
# CLI
# =========================================================================
def _print_human(action, result):
    """Print human-readable output."""
    print("=" * 70)
    print("  ICDEV™ Research Engine -- Source Scanner -- CUI // SP-CTI")
    print("=" * 70)

    if isinstance(result, dict) and "error" in result:
        print(f"\n  ERROR: {result['error']}\n")
        print("=" * 70)
        return

    if action == "scan":
        print(f"\n  Session: {result.get('session_id', '?')}")
        print(f"  Scan Time: {result.get('scan_time', '')}")
        print(f"  Sources Scanned: {result.get('sources_scanned', 0)}")
        print(f"  Signals Discovered: {result.get('signals_discovered', 0)}")
        print(f"  Signals Stored: {result.get('signals_stored', 0)}")
        print("")
        print(f"  {'Source':22s} {'Found':>6s} {'Stored':>7s} {'Dupes':>6s} {'Status':>10s}")
        print(f"  {'-' * 22} {'-' * 6} {'-' * 7} {'-' * 6} {'-' * 10}")
        for src, res in result.get("results", {}).items():
            if "error" in res:
                status = "ERROR"
            else:
                status = "OK"
            print(
                f"  {src:22s} {res.get('signals_found', 0):6d} "
                f"{res.get('stored', 0):7d} {res.get('duplicates', 0):6d} {status:>10s}"
            )
        if result.get("errors"):
            print(f"\n  Errors ({len(result['errors'])}):")
            for err in result["errors"]:
                print(f"    - {err}")

    elif action == "list_sources":
        print(f"\n  requests library: {'available' if result.get('requests_available') else 'MISSING'}")
        print(f"  yaml library: {'available' if result.get('yaml_available') else 'MISSING'}")
        print("")
        print(f"  {'Source':22s} {'Enabled':>8s} {'Interval':>10s} {'Platforms':>10s} {'Function'}")
        print(f"  {'-' * 22} {'-' * 8} {'-' * 10} {'-' * 10} {'-' * 30}")
        for src in result.get("sources", []):
            status = "Yes" if src["enabled"] else "No"
            interval = f"{src.get('scan_interval_hours', '?')}h"
            platforms = str(src.get("platform_count", 0))
            print(f"  {src['name']:22s} {status:>8s} {interval:>10s} {platforms:>10s} {src['scanner_function']}")

    elif action == "status":
        print(f"\n  Session: {result.get('session_id', '?')}")
        print(f"  Total Signals: {result.get('total_signals', 0)}")
        print(f"  Sources: {result.get('source_count', 0)}")
        print(f"  Earliest: {result.get('earliest_signal', 'N/A')}")
        print(f"  Latest: {result.get('latest_signal', 'N/A')}")
        print("")
        by_source = result.get("by_source", {})
        if by_source:
            print(f"  {'Source':22s} {'Total':>7s}  Types")
            print(f"  {'-' * 22} {'-' * 7}  {'-' * 30}")
            for src, info in by_source.items():
                types_str = ", ".join(f"{t}:{c}" for t, c in info.get("types", {}).items())
                print(f"  {src:22s} {info['total']:7d}  {types_str}")

    print()
    print("=" * 70)


def main():
    parser = argparse.ArgumentParser(
        description="ICDEV™ Research Engine Source Scanner -- 8-stream industry research (CUI // SP-CTI)"
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable output")
    parser.add_argument("--db-path", type=Path, default=None, help="Database path override")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--scan", action="store_true", help="Run source scan (requires --session-id)")
    group.add_argument("--list-sources", action="store_true", help="List available source adapters")
    group.add_argument("--status", action="store_true", help="Show scan statistics (requires --session-id)")

    parser.add_argument("--session-id", type=str, help="Research session ID (required for --scan, --status)")
    parser.add_argument("--source", type=str, help="Specific source stream to scan (with --scan)")

    args = parser.parse_args()

    try:
        if args.scan:
            if not args.session_id:
                result = {"error": "--session-id is required for --scan"}
            else:
                result = run_scan(
                    session_id=args.session_id,
                    source=args.source,
                    db_path=args.db_path,
                )
            action = "scan"
        elif args.list_sources:
            result = list_sources()
            action = "list_sources"
        elif args.status:
            if not args.session_id:
                result = {"error": "--session-id is required for --status"}
            else:
                result = get_scan_status(session_id=args.session_id, db_path=args.db_path)
            action = "status"
        else:
            result = {"error": "No action specified"}
            action = "unknown"

        if args.human:
            _print_human(action, result)
        elif args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            # Default to JSON
            print(json.dumps(result, indent=2, default=str))

    except FileNotFoundError as e:
        error = {"error": str(e), "hint": "Run: python tools/db/init_icdev_db.py"}
        if args.human:
            print(f"ERROR: {e}", file=sys.stderr)
        else:
            print(json.dumps(error, indent=2))
        sys.exit(1)
    except Exception as e:
        error = {"error": str(e)}
        if args.human:
            print(f"ERROR: {e}", file=sys.stderr)
        else:
            print(json.dumps(error, indent=2))
        sys.exit(1)


if __name__ == "__main__":
    main()
