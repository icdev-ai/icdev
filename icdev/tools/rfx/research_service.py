#!/usr/bin/env python3
# CUI // SP-PROPIN
"""Research service: web and government source search with SQLite TTL cache.

Searches:
  - General web (via requests + basic scraping)
  - SAM.gov contract opportunities
  - USASpending.gov (FPDS-NG contract awards)
  - FAR/DFARS regulations (acquisition.gov)

Results cached in rfx_research_cache with 24-hour TTL to avoid redundant
requests. Cache keyed by SHA-256(query + cache_type).

No API keys required for basic web search (uses requests with user-agent).
SAM.gov uses the public API (no key required for basic search).
"""

import hashlib
import json
import os
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus, urlencode

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get(
    "GOVPROPOSAL_DB_PATH", str(BASE_DIR / "data" / "govproposal.db")
))

CACHE_TTL_HOURS = 24
REQUEST_TIMEOUT = 15
_HEADERS = {
    "User-Agent": "GovProposal-Research/1.0 (government proposal research tool)"
}


def _conn():
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def _query_hash(query: str, cache_type: str) -> str:
    raw = f"{cache_type}:{query.lower().strip()}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _get_cached(query_hash: str) -> Optional[list[dict]]:
    """Return cached results if fresh, else None."""
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT results FROM rfx_research_cache "
            "WHERE query_hash = ? AND expires_at > datetime('now')",
            (query_hash,)
        ).fetchone()
        if row:
            return json.loads(row["results"])
        return None
    finally:
        conn.close()


def _store_cache(query: str, query_hash: str, cache_type: str,
                 results: list[dict],
                 proposal_id: Optional[str] = None) -> None:
    """Persist results to rfx_research_cache."""
    now = datetime.now(timezone.utc)
    expires = (now + timedelta(hours=CACHE_TTL_HOURS)).isoformat()
    conn = _conn()
    try:
        conn.execute("""
            INSERT OR REPLACE INTO rfx_research_cache
                (id, proposal_id, query, query_hash, cache_type,
                 results, source_count, expires_at, created_at)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (
            str(uuid.uuid4()), proposal_id, query, query_hash,
            cache_type, json.dumps(results), len(results),
            expires, now.isoformat(),
        ))
        conn.commit()
    finally:
        conn.close()


# ── SAM.gov search ─────────────────────────────────────────────────────────────

def search_sam_gov(query: str, limit: int = 10) -> list[dict]:
    """Search SAM.gov public opportunity API (no API key required)."""
    try:
        import requests
    except ImportError:
        return [{"error": "requests library not available"}]

    qhash = _query_hash(query, "gov_sources_sam")
    cached = _get_cached(qhash)
    if cached is not None:
        return cached

    try:
        params = {
            "q": query,
            "size": limit,
            "index": "opp",
        }
        url = "https://sam.gov/api/prod/opportunities/v2/search"
        resp = requests.get(url, params=params, headers=_HEADERS,
                            timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()

        results = []
        for opp in data.get("opportunityData", [])[:limit]:
            results.append({
                "source": "sam.gov",
                "title": opp.get("title", ""),
                "url": f"https://sam.gov/opp/{opp.get('opportunityId', '')}",
                "agency": opp.get("organizationHierarchy", [{}])[-1].get("name", ""),
                "deadline": opp.get("responseDeadLine", ""),
                "naics": opp.get("naicsCode", ""),
                "set_aside": opp.get("typeOfSetAsideDescription", ""),
                "description": opp.get("description", "")[:500],
                "notice_type": opp.get("type", ""),
            })

        _store_cache(query, qhash, "gov_sources", results)
        return results

    except Exception as e:
        return [{"error": f"SAM.gov search failed: {e}", "source": "sam.gov"}]


# ── USASpending.gov search ─────────────────────────────────────────────────────

def search_usaspending(query: str, limit: int = 10) -> list[dict]:
    """Search USASpending.gov for past contract awards (FPDS-NG)."""
    try:
        import requests
    except ImportError:
        return []

    qhash = _query_hash(query, "gov_sources_usaspend")
    cached = _get_cached(qhash)
    if cached is not None:
        return cached

    try:
        payload = {
            "filters": {
                "keywords": [query],
                "award_type_codes": ["A", "B", "C", "D"],
            },
            "fields": ["Award ID", "Recipient Name", "Award Amount",
                       "Awarding Agency", "Awarding Sub Agency",
                       "Award Date", "Description", "NAICS Code",
                       "Period of Performance Start Date",
                       "Period of Performance Current End Date"],
            "page": 1,
            "limit": limit,
            "sort": "Award Amount",
            "order": "desc",
        }
        url = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
        resp = requests.post(url, json=payload, headers=_HEADERS,
                             timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()

        results = []
        for award in data.get("results", []):
            results.append({
                "source": "usaspending.gov",
                "award_id": award.get("Award ID", ""),
                "recipient": award.get("Recipient Name", ""),
                "amount": award.get("Award Amount", 0),
                "agency": award.get("Awarding Agency", ""),
                "sub_agency": award.get("Awarding Sub Agency", ""),
                "award_date": award.get("Award Date", ""),
                "description": str(award.get("Description", ""))[:500],
                "naics": award.get("NAICS Code", ""),
                "pop_start": award.get("Period of Performance Start Date", ""),
                "pop_end": award.get("Period of Performance Current End Date", ""),
            })

        _store_cache(query, qhash, "gov_sources", results)
        return results

    except Exception as e:
        return [{"error": f"USASpending search failed: {e}"}]


# ── web search ─────────────────────────────────────────────────────────────────

def search_web(query: str, limit: int = 8,
               proposal_id: Optional[str] = None) -> list[dict]:
    """Lightweight web search using DuckDuckGo's instant answer API.

    No API key required. Returns titles, URLs, and snippets.
    """
    try:
        import requests
    except ImportError:
        return []

    qhash = _query_hash(query, "web_search")
    cached = _get_cached(qhash)
    if cached is not None:
        return cached

    try:
        params = {
            "q": query,
            "format": "json",
            "no_redirect": "1",
            "no_html": "1",
        }
        url = "https://api.duckduckgo.com/"
        resp = requests.get(url, params=params, headers=_HEADERS,
                            timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()

        results = []
        # RelatedTopics contain the actual results
        for item in data.get("RelatedTopics", [])[:limit]:
            if isinstance(item, dict) and item.get("Text"):
                results.append({
                    "source": "web",
                    "title": item.get("Text", "")[:120],
                    "url": item.get("FirstURL", ""),
                    "snippet": item.get("Text", "")[:400],
                })

        _store_cache(query, qhash, "web_search", results, proposal_id)
        return results

    except Exception as e:
        return [{"error": f"Web search failed: {e}"}]


# ── deep research ──────────────────────────────────────────────────────────────

def deep_research(query: str, proposal_id: Optional[str] = None) -> dict:
    """Aggregate web + SAM.gov + USASpending into a single research report.

    Returns merged results grouped by source, with deduplication.
    Results are cached with 24h TTL.
    """
    qhash = _query_hash(query, "deep_research")
    cached = _get_cached(qhash)
    if cached is not None:
        return {"query": query, "cached": True,
                "results": cached, "source_count": len(cached)}

    web = search_web(query, limit=6, proposal_id=proposal_id)
    sam = search_sam_gov(query, limit=5)
    fpds = search_usaspending(query, limit=5)

    all_results = []
    for r in web:
        r["category"] = "web"
        all_results.append(r)
    for r in sam:
        r["category"] = "opportunities"
        all_results.append(r)
    for r in fpds:
        r["category"] = "awards"
        all_results.append(r)

    _store_cache(query, qhash, "deep_research", all_results, proposal_id)

    return {
        "query": query,
        "cached": False,
        "source_count": len(all_results),
        "web_results": len(web),
        "sam_results": len(sam),
        "fpds_results": len(fpds),
        "results": all_results,
    }


# ── cache management ───────────────────────────────────────────────────────────

def clear_expired_cache() -> int:
    """Delete expired cache entries. Returns count deleted."""
    conn = _conn()
    try:
        conn.execute(
            "DELETE FROM rfx_research_cache WHERE expires_at <= datetime('now')"
        )
        deleted = conn.total_changes
        conn.commit()
        return deleted
    finally:
        conn.close()


def get_cached_research(proposal_id: str) -> list[dict]:
    """Retrieve all cached research for a proposal."""
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT query, cache_type, source_count, expires_at, created_at "
            "FROM rfx_research_cache WHERE proposal_id = ? "
            "ORDER BY created_at DESC",
            (proposal_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
