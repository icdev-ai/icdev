#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""SAM.gov Opportunity Scanner — discover federal contracting opportunities.

Polls the SAM.gov Opportunities API v2 for solicitations, pre-solicitations,
RFIs, and award notices matching configured NAICS codes.  Returns normalized
signals compatible with both the Innovation Engine and Creative Engine.

Architecture:
    - Follows SOURCE_SCANNERS function registry pattern (D352)
    - SAM.gov API key from SAM_GOV_API_KEY env var (D366)
    - Rate limiting: 10 req/sec, 10K/day (D370)
    - Signals stored in sam_gov_opportunities table (allows UPDATE for sync)
    - Cross-registers to innovation_signals and creative_signals (D361)
    - Air-gapped mode: disables scanning, serves cached data only
    - All extracted "shall" statements stored append-only (D6)

Usage:
    python tools/govcon/sam_scanner.py --scan --json
    python tools/govcon/sam_scanner.py --scan --naics 541512 --json
    python tools/govcon/sam_scanner.py --scan --notice-type r --json
    python tools/govcon/sam_scanner.py --list-cached --json
    python tools/govcon/sam_scanner.py --history --days 30 --json
    python tools/govcon/sam_scanner.py --stats --json
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
CONFIG_PATH = BASE_DIR / "args" / "govcon_config.yaml"

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
MAX_DESCRIPTION_LENGTH = 10000
SAM_GOV_API_DEFAULT = "https://api.sam.gov/opportunities/v2/search"


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


def _opp_id():
    """Generate unique opportunity ID."""
    return f"sam-{uuid.uuid4().hex[:12]}"


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
                project_id=project_id or "govcon-engine",
            )
        except Exception:
            pass


def _load_config():
    """Load govcon config from YAML."""
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

    Returns:
        Tuple of (data, error).  On success error is None.
    """
    if not _HAS_REQUESTS:
        return None, "requests library not installed"
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=timeout)
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
    except requests.exceptions.Timeout:
        return None, "timeout"
    except requests.exceptions.ConnectionError:
        return None, "connection_error"
    except requests.exceptions.RequestException as e:
        return None, str(e)


# =========================================================================
# SAM.GOV API SCANNER
# =========================================================================
def scan_sam_gov(config=None, naics_filter=None, notice_type_filter=None,
                 db_path=None):
    """Scan SAM.gov Opportunities API for new opportunities.

    Args:
        config: Full govcon config dict (loaded from govcon_config.yaml).
        naics_filter: Optional single NAICS code to filter by.
        notice_type_filter: Optional single notice type to filter by.
        db_path: Optional database path override.

    Returns:
        Dict with keys: opportunities (list), new_count, updated_count,
        skipped_count, errors (list), scan_duration_seconds.
    """
    config = config or _load_config()
    sam_config = config.get("sam_gov", {})
    api_url = sam_config.get("api_url", SAM_GOV_API_DEFAULT)
    api_key = os.environ.get(sam_config.get("api_key_env", "SAM_GOV_API_KEY"), "")
    if not api_key:
        return {"error": "SAM_GOV_API_KEY not set in environment", "opportunities": []}

    rate_config = sam_config.get("rate_limit", {})
    delay = rate_config.get("delay_between_requests", 0.15)
    lookback_days = sam_config.get("lookback_days", 30)
    max_per_poll = sam_config.get("max_per_poll", 100)
    max_desc = sam_config.get("description_max_chars", MAX_DESCRIPTION_LENGTH)

    # Build NAICS list
    naics_codes = [naics_filter] if naics_filter else sam_config.get("naics_codes", [])
    notice_types = [notice_type_filter] if notice_type_filter else sam_config.get("notice_types", [])

    # Date range
    posted_from = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%m/%d/%Y")
    posted_to = datetime.now(timezone.utc).strftime("%m/%d/%Y")

    start_time = time.time()
    all_opportunities = []
    errors = []
    new_count = 0
    updated_count = 0
    skipped_count = 0

    try:
        conn = _get_db(db_path)
    except FileNotFoundError as e:
        return {"error": str(e), "opportunities": []}

    for naics in (naics_codes or [""]):
        for ntype in (notice_types or [""]):
            params = {
                "api_key": api_key,
                "postedFrom": posted_from,
                "postedTo": posted_to,
                "limit": min(max_per_poll, 1000),
                "offset": 0,
            }
            if naics:
                params["ncode"] = naics
            if ntype:
                params["ptype"] = ntype

            data, err = _safe_get(api_url, params=params)
            if err:
                errors.append({"naics": naics, "notice_type": ntype, "error": err})
                time.sleep(delay)
                continue

            opportunities = []
            if isinstance(data, dict):
                opportunities = data.get("opportunitiesData", [])
                if not opportunities:
                    opportunities = data.get("opportunities", [])

            for opp in opportunities:
                normalized = _normalize_opportunity(opp, max_desc)
                if not normalized:
                    continue

                # Dedup by content_hash
                existing = conn.execute(
                    "SELECT id, content_hash FROM sam_gov_opportunities WHERE id = ?",
                    (normalized["id"],)
                ).fetchone()

                if existing:
                    if existing["content_hash"] != normalized["content_hash"]:
                        # Updated opportunity
                        conn.execute(
                            "UPDATE sam_gov_opportunities SET title=?, description=?, "
                            "response_deadline=?, content_hash=?, last_synced=?, "
                            "metadata=?, active=? WHERE id=?",
                            (normalized["title"], normalized["description"],
                             normalized["response_deadline"], normalized["content_hash"],
                             _now(), normalized["metadata"], "true", normalized["id"])
                        )
                        updated_count += 1
                    else:
                        skipped_count += 1
                else:
                    # New opportunity
                    conn.execute(
                        "INSERT INTO sam_gov_opportunities "
                        "(id, solicitation_number, title, agency, agency_hierarchy, "
                        "naics_code, classification_code, notice_type, posted_date, "
                        "response_deadline, description, point_of_contact, set_aside_type, "
                        "place_of_performance, attachment_urls, active, content_hash, "
                        "metadata, first_seen, last_synced, classification) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (normalized["id"], normalized["solicitation_number"],
                         normalized["title"], normalized["agency"],
                         normalized["agency_hierarchy"], normalized["naics_code"],
                         normalized["classification_code"], normalized["notice_type"],
                         normalized["posted_date"], normalized["response_deadline"],
                         normalized["description"], normalized["point_of_contact"],
                         normalized["set_aside_type"], normalized["place_of_performance"],
                         json.dumps(normalized.get("attachment_urls", [])),
                         "true", normalized["content_hash"],
                         normalized["metadata"], _now(), _now(), "CUI")
                    )
                    new_count += 1

                all_opportunities.append(normalized)

            conn.commit()
            time.sleep(delay)

    conn.close()
    duration = round(time.time() - start_time, 2)

    _audit("govcon.scan", "govcon-scanner", f"Scanned SAM.gov: {new_count} new, {updated_count} updated",
           details={"new": new_count, "updated": updated_count, "skipped": skipped_count, "errors": len(errors)})

    return {
        "opportunities": all_opportunities,
        "new_count": new_count,
        "updated_count": updated_count,
        "skipped_count": skipped_count,
        "total_fetched": len(all_opportunities),
        "errors": errors,
        "scan_duration_seconds": duration,
    }


def _normalize_opportunity(raw, max_desc=MAX_DESCRIPTION_LENGTH):
    """Normalize a raw SAM.gov opportunity to our schema.

    Args:
        raw: Raw opportunity dict from SAM.gov API.
        max_desc: Maximum description length to store.

    Returns:
        Normalized dict or None if unparseable.
    """
    if not isinstance(raw, dict):
        return None

    notice_id = raw.get("noticeId") or raw.get("id", "")
    if not notice_id:
        return None

    title = raw.get("title", "").strip()
    if not title:
        title = raw.get("subject", "Untitled")

    description = raw.get("description", "") or ""
    if len(description) > max_desc:
        description = description[:max_desc]

    agency = raw.get("fullParentPathName", "") or raw.get("departmentName", "") or ""
    agency_short = agency.split(".")[0].strip() if agency else ""

    # Extract point of contact
    poc = raw.get("pointOfContact", [])
    poc_str = ""
    if isinstance(poc, list) and poc:
        first_poc = poc[0] if isinstance(poc[0], dict) else {}
        poc_str = f"{first_poc.get('fullName', '')} ({first_poc.get('email', '')})"
    elif isinstance(poc, str):
        poc_str = poc

    # Attachments
    resource_links = raw.get("resourceLinks", []) or []
    attachment_urls = resource_links if isinstance(resource_links, list) else []

    # Content hash for dedup
    hash_content = f"{notice_id}|{title}|{description[:500]}|{raw.get('responseDeadLine', '')}"
    content = _content_hash(hash_content)

    return {
        "id": notice_id,
        "solicitation_number": raw.get("solicitationNumber", ""),
        "title": title,
        "agency": agency_short or agency,
        "agency_hierarchy": agency,
        "naics_code": raw.get("naicsCode") or raw.get("naics", ""),
        "classification_code": raw.get("classificationCode", ""),
        "notice_type": raw.get("type", "o"),
        "posted_date": raw.get("postedDate", ""),
        "response_deadline": raw.get("responseDeadLine") or raw.get("responseDateLine", ""),
        "description": description,
        "point_of_contact": poc_str,
        "set_aside_type": raw.get("typeOfSetAside") or raw.get("typeOfSetAsideDescription", ""),
        "place_of_performance": json.dumps(raw.get("placeOfPerformance", {})),
        "attachment_urls": attachment_urls,
        "content_hash": content,
        "metadata": json.dumps({
            "award_amount": raw.get("award", {}).get("amount") if isinstance(raw.get("award"), dict) else None,
            "awardee": raw.get("award", {}).get("awardee", {}).get("name") if isinstance(raw.get("award"), dict) else None,
            "archive_type": raw.get("archiveType", ""),
            "archive_date": raw.get("archiveDate", ""),
            "ui_link": raw.get("uiLink", ""),
        }),
    }


# =========================================================================
# QUERY FUNCTIONS
# =========================================================================
def list_cached(db_path=None, naics_filter=None, notice_type_filter=None,
                active_only=True, limit=100):
    """List cached SAM.gov opportunities from local database.

    Returns:
        Dict with keys: opportunities (list), count.
    """
    try:
        conn = _get_db(db_path)
    except FileNotFoundError as e:
        return {"error": str(e), "opportunities": []}

    query = "SELECT * FROM sam_gov_opportunities WHERE 1=1"
    params = []
    if active_only:
        query += " AND active = 'true'"
    if naics_filter:
        query += " AND naics_code = ?"
        params.append(naics_filter)
    if notice_type_filter:
        query += " AND notice_type = ?"
        params.append(notice_type_filter)
    query += " ORDER BY response_deadline DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    conn.close()

    opps = [dict(r) for r in rows]
    return {"opportunities": opps, "count": len(opps)}


def get_history(db_path=None, days=30):
    """Get scan history and statistics over a time period.

    Returns:
        Dict with scan history by day, totals by notice type and NAICS.
    """
    try:
        conn = _get_db(db_path)
    except FileNotFoundError as e:
        return {"error": str(e)}

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Count by notice type
    type_counts = conn.execute(
        "SELECT notice_type, COUNT(*) as count FROM sam_gov_opportunities "
        "WHERE first_seen >= ? GROUP BY notice_type ORDER BY count DESC",
        (cutoff,)
    ).fetchall()

    # Count by NAICS
    naics_counts = conn.execute(
        "SELECT naics_code, COUNT(*) as count FROM sam_gov_opportunities "
        "WHERE first_seen >= ? GROUP BY naics_code ORDER BY count DESC",
        (cutoff,)
    ).fetchall()

    # Count by agency
    agency_counts = conn.execute(
        "SELECT agency, COUNT(*) as count FROM sam_gov_opportunities "
        "WHERE first_seen >= ? GROUP BY agency ORDER BY count DESC LIMIT 20",
        (cutoff,)
    ).fetchall()

    # Daily counts
    daily = conn.execute(
        "SELECT DATE(first_seen) as day, COUNT(*) as count FROM sam_gov_opportunities "
        "WHERE first_seen >= ? GROUP BY DATE(first_seen) ORDER BY day",
        (cutoff,)
    ).fetchall()

    total = conn.execute(
        "SELECT COUNT(*) as total FROM sam_gov_opportunities WHERE first_seen >= ?",
        (cutoff,)
    ).fetchone()

    conn.close()

    return {
        "period_days": days,
        "total_opportunities": total["total"] if total else 0,
        "by_notice_type": [dict(r) for r in type_counts],
        "by_naics": [dict(r) for r in naics_counts],
        "by_agency": [dict(r) for r in agency_counts],
        "daily_counts": [dict(r) for r in daily],
    }


def get_stats(db_path=None):
    """Get overall SAM.gov scanner statistics.

    Returns:
        Dict with total counts, latest scan time, response deadline distribution.
    """
    try:
        conn = _get_db(db_path)
    except FileNotFoundError as e:
        return {"error": str(e)}

    total = conn.execute("SELECT COUNT(*) as c FROM sam_gov_opportunities").fetchone()
    active = conn.execute("SELECT COUNT(*) as c FROM sam_gov_opportunities WHERE active='true'").fetchone()
    latest = conn.execute(
        "SELECT MAX(last_synced) as latest FROM sam_gov_opportunities"
    ).fetchone()

    # Upcoming deadlines (next 30 days)
    now = _now()
    future = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    upcoming = conn.execute(
        "SELECT COUNT(*) as c FROM sam_gov_opportunities "
        "WHERE response_deadline >= ? AND response_deadline <= ? AND active='true'",
        (now, future)
    ).fetchone()

    conn.close()

    return {
        "total_opportunities": total["c"] if total else 0,
        "active_opportunities": active["c"] if active else 0,
        "upcoming_deadlines_30d": upcoming["c"] if upcoming else 0,
        "latest_scan": latest["latest"] if latest else None,
    }


# =========================================================================
# CROSS-REGISTRATION (D361)
# =========================================================================
def cross_register_to_innovation(opportunities, config=None, db_path=None):
    """Register SAM.gov opportunities as innovation signals.

    High-value opportunities with DevSecOps/AI/cloud requirements are
    registered in innovation_signals for trend analysis.

    Args:
        opportunities: List of normalized opportunity dicts.
        config: Optional config override.
        db_path: Optional DB path override.

    Returns:
        Dict with registered_count.
    """
    config = config or _load_config()
    cross_config = config.get("cross_registration", {}).get("innovation_engine", {})
    if not cross_config.get("enabled", True):
        return {"registered_count": 0, "skipped": "cross-registration disabled"}

    try:
        conn = _get_db(db_path)
    except FileNotFoundError:
        return {"registered_count": 0, "error": "db not found"}

    registered = 0
    for opp in opportunities:
        sig_id = f"sig-sam-{opp['id'][:12]}"
        # Check for existing
        existing = conn.execute(
            "SELECT id FROM innovation_signals WHERE id = ?", (sig_id,)
        ).fetchone()
        if existing:
            continue

        desc = opp.get("description", "")
        title = opp.get("title", "")
        combined = f"{title} {desc}".lower()

        # Only register if it mentions key capability areas
        capability_keywords = ["devsecops", "ci/cd", "ai", "machine learning", "cato",
                               "fedramp", "cloud", "zero trust", "ato", "rmf", "nist"]
        if not any(kw in combined for kw in capability_keywords):
            continue

        try:
            conn.execute(
                "INSERT INTO innovation_signals "
                "(id, source, source_type, title, description, url, metadata, "
                "community_score, content_hash, discovered_at, created_at, "
                "status, category) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (sig_id, "sam_gov", "govcon_opportunity", title,
                 desc[:2000], opp.get("metadata", "{}"),
                 json.dumps({"naics": opp.get("naics_code"), "agency": opp.get("agency"),
                             "deadline": opp.get("response_deadline")}),
                 0.5, opp.get("content_hash", ""),
                 _now(), _now(), "new", "govcon_opportunity")
            )
            registered += 1
        except sqlite3.IntegrityError:
            pass

    conn.commit()
    conn.close()
    return {"registered_count": registered}


def cross_register_to_creative(opportunities, config=None, db_path=None):
    """Register SAM.gov opportunities as creative signals.

    Provides competitive intelligence on what agencies are procuring.

    Args:
        opportunities: List of normalized opportunity dicts.
        config: Optional config override.
        db_path: Optional DB path override.

    Returns:
        Dict with registered_count.
    """
    config = config or _load_config()
    cross_config = config.get("cross_registration", {}).get("creative_engine", {})
    if not cross_config.get("enabled", True):
        return {"registered_count": 0, "skipped": "cross-registration disabled"}

    try:
        conn = _get_db(db_path)
    except FileNotFoundError:
        return {"registered_count": 0, "error": "db not found"}

    registered = 0
    for opp in opportunities:
        sig_id = f"csig-sam-{opp['id'][:10]}"
        existing = conn.execute(
            "SELECT id FROM creative_signals WHERE id = ?", (sig_id,)
        ).fetchone()
        if existing:
            continue

        try:
            conn.execute(
                "INSERT INTO creative_signals "
                "(id, source, source_type, competitor_id, title, body, url, "
                "author, rating, upvotes, sentiment, content_hash, metadata, "
                "discovered_at, classification) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (sig_id, "sam_gov", "rfp_opportunity", None,
                 opp.get("title", ""), opp.get("description", "")[:4000],
                 "", opp.get("agency", ""), None, 0, "neutral",
                 opp.get("content_hash", ""),
                 json.dumps({"naics": opp.get("naics_code"),
                             "deadline": opp.get("response_deadline"),
                             "set_aside": opp.get("set_aside_type")}),
                 _now(), "CUI")
            )
            registered += 1
        except sqlite3.IntegrityError:
            pass

    conn.commit()
    conn.close()
    return {"registered_count": registered}


# =========================================================================
# CLI
# =========================================================================
def main():
    parser = argparse.ArgumentParser(description="SAM.gov Opportunity Scanner")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--scan", action="store_true", help="Scan SAM.gov for new opportunities")
    group.add_argument("--list-cached", action="store_true", help="List cached opportunities")
    group.add_argument("--history", action="store_true", help="Show scan history")
    group.add_argument("--stats", action="store_true", help="Show scanner statistics")

    parser.add_argument("--naics", help="Filter by NAICS code")
    parser.add_argument("--notice-type", help="Filter by notice type (o/p/r/k/a)")
    parser.add_argument("--days", type=int, default=30, help="History lookback days")
    parser.add_argument("--limit", type=int, default=100, help="Max results for list")
    parser.add_argument("--cross-register", action="store_true", default=True,
                        help="Cross-register to Innovation/Creative engines")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable output")

    args = parser.parse_args()

    if args.scan:
        result = scan_sam_gov(naics_filter=args.naics, notice_type_filter=args.notice_type)
        if args.cross_register and result.get("opportunities"):
            inno = cross_register_to_innovation(result["opportunities"])
            creative = cross_register_to_creative(result["opportunities"])
            result["cross_registration"] = {
                "innovation_signals": inno.get("registered_count", 0),
                "creative_signals": creative.get("registered_count", 0),
            }
    elif args.list_cached:
        result = list_cached(naics_filter=args.naics, notice_type_filter=args.notice_type,
                             limit=args.limit)
    elif args.history:
        result = get_history(days=args.days)
    elif args.stats:
        result = get_stats()
    else:
        result = {"error": "No action specified"}

    if args.json or not args.human:
        print(json.dumps(result, indent=2, default=str))
    else:
        _print_human(result, args)


def _print_human(result, args):
    """Print human-readable output."""
    if "error" in result:
        print(f"\n  ERROR: {result['error']}\n")
        return

    if args.scan:
        print(f"\n  SAM.gov Scan Complete")
        print(f"  {'='*40}")
        print(f"  New:     {result.get('new_count', 0)}")
        print(f"  Updated: {result.get('updated_count', 0)}")
        print(f"  Skipped: {result.get('skipped_count', 0)}")
        print(f"  Errors:  {len(result.get('errors', []))}")
        print(f"  Duration: {result.get('scan_duration_seconds', 0)}s")
        if result.get("cross_registration"):
            cr = result["cross_registration"]
            print(f"\n  Cross-Registration:")
            print(f"    Innovation signals: {cr.get('innovation_signals', 0)}")
            print(f"    Creative signals:   {cr.get('creative_signals', 0)}")
    elif args.stats:
        print(f"\n  SAM.gov Scanner Stats")
        print(f"  {'='*40}")
        print(f"  Total:    {result.get('total_opportunities', 0)}")
        print(f"  Active:   {result.get('active_opportunities', 0)}")
        print(f"  Upcoming: {result.get('upcoming_deadlines_30d', 0)} (30d)")
        print(f"  Last Scan: {result.get('latest_scan', 'never')}")
    elif args.list_cached:
        opps = result.get("opportunities", [])
        print(f"\n  Cached Opportunities ({len(opps)})")
        print(f"  {'='*60}")
        for o in opps[:20]:
            deadline = o.get("response_deadline", "N/A")
            print(f"  [{o.get('notice_type','?')}] {o.get('title','')[:50]}")
            print(f"      NAICS: {o.get('naics_code','')} | Agency: {o.get('agency','')[:30]} | Due: {deadline}")
    print()


if __name__ == "__main__":
    main()
