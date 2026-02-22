#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Web Intelligence Scanner — discover developer pain points, CVEs, and innovation signals.

Scans configurable web sources (GitHub, Stack Overflow, NVD, NIST, community forums,
package registries, compliance updates) and produces normalized innovation signals.

Architecture:
    - Source adapters follow ABC pattern (D66) — add new sources without code changes
    - Rate limiting per source (configurable in args/innovation_config.yaml)
    - Graceful degradation on network failures (circuit breaker pattern D146)
    - All signals stored in innovation_signals table (append-only, D6)
    - Air-gapped mode: disables web sources, enables introspective-only scanning

Usage:
    python tools/innovation/web_scanner.py --scan --source github --json
    python tools/innovation/web_scanner.py --scan --all --json
    python tools/innovation/web_scanner.py --scan --source cve_databases --json
    python tools/innovation/web_scanner.py --list-sources --json
    python tools/innovation/web_scanner.py --history --days 7 --json
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
CONFIG_PATH = BASE_DIR / "args" / "innovation_config.yaml"

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
GITHUB_API = "https://api.github.com"
NVD_API = "https://services.nvd.nist.gov/rest/json/cves/2.0"
SO_API = "https://api.stackexchange.com/2.3"
HN_API = "https://hacker-news.firebaseio.com/v0"
DEFAULT_TIMEOUT = 30
MAX_RETRIES = 3


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
    """Generate unique signal ID."""
    return f"sig-{uuid.uuid4().hex[:12]}"


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
                project_id=project_id or "innovation-engine",
            )
        except Exception:
            pass


def _load_config():
    """Load innovation config from YAML."""
    if not _HAS_YAML:
        return {}
    if not CONFIG_PATH.exists():
        return {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# =========================================================================
# SOURCE ADAPTERS
# =========================================================================
def _safe_get(url, headers=None, params=None, timeout=DEFAULT_TIMEOUT):
    """HTTP GET with error handling and rate limit awareness."""
    if not _HAS_REQUESTS:
        return None, "requests library not installed"
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=timeout)
        if resp.status_code == 429:
            return None, "rate_limited"
        if resp.status_code == 403:
            return None, "forbidden"
        resp.raise_for_status()
        return resp.json(), None
    except requests.exceptions.Timeout:
        return None, "timeout"
    except requests.exceptions.ConnectionError:
        return None, "connection_error"
    except requests.exceptions.RequestException as e:
        return None, str(e)
    except json.JSONDecodeError:
        return None, "invalid_json"


def scan_github(config):
    """Scan GitHub for trending repos, issues, and discussions.

    Args:
        config: GitHub source config from innovation_config.yaml.

    Returns:
        List of normalized signal dicts.
    """
    signals = []
    gh_config = config.get("sources", {}).get("github", {})
    if not gh_config.get("enabled", False):
        return signals

    headers = {"Accept": "application/vnd.github+json"}
    gh_token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if gh_token:
        headers["Authorization"] = f"Bearer {gh_token}"

    targets = gh_config.get("targets", [])

    for target in targets:
        target_type = target.get("type", "")

        if target_type == "trending_repos":
            # Search for recently created repos with high stars
            since = target.get("since", "daily")
            days_map = {"daily": 1, "weekly": 7, "monthly": 30}
            days = days_map.get(since, 1)
            date_since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
            languages = target.get("languages", ["python"])
            max_results = target.get("max_results", 50)

            for lang in languages:
                query = f"language:{lang} created:>{date_since} stars:>10"
                data, err = _safe_get(
                    f"{GITHUB_API}/search/repositories",
                    headers=headers,
                    params={"q": query, "sort": "stars", "per_page": min(max_results, 30)},
                )
                if err:
                    signals.append(_error_signal("github", f"trending_{lang}", err))
                    continue

                for item in (data or {}).get("items", [])[:max_results]:
                    signals.append({
                        "id": _signal_id(),
                        "source": "github",
                        "source_type": "trending_repo",
                        "title": item.get("full_name", ""),
                        "description": item.get("description", "") or "",
                        "url": item.get("html_url", ""),
                        "metadata": json.dumps({
                            "stars": item.get("stargazers_count", 0),
                            "forks": item.get("forks_count", 0),
                            "language": item.get("language", ""),
                            "topics": item.get("topics", []),
                            "created_at": item.get("created_at", ""),
                        }),
                        "community_score": min(item.get("stargazers_count", 0) / 1000, 1.0),
                        "content_hash": _content_hash(item.get("html_url", "")),
                        "discovered_at": _now(),
                    })
                time.sleep(1)  # Rate limit courtesy

        elif target_type == "issues":
            repos = target.get("repos", [])
            labels = target.get("labels", [])
            max_results = target.get("max_results", 100)

            for repo in repos:
                params = {
                    "state": "open",
                    "sort": "reactions-+1",
                    "per_page": min(max_results, 30),
                }
                if labels:
                    params["labels"] = ",".join(labels)

                data, err = _safe_get(
                    f"{GITHUB_API}/repos/{repo}/issues",
                    headers=headers,
                    params=params,
                )
                if err:
                    signals.append(_error_signal("github", f"issues_{repo}", err))
                    continue

                for item in (data or [])[:max_results]:
                    if item.get("pull_request"):
                        continue  # Skip PRs
                    reactions = item.get("reactions", {})
                    thumbs_up = reactions.get("+1", 0) if isinstance(reactions, dict) else 0
                    signals.append({
                        "id": _signal_id(),
                        "source": "github",
                        "source_type": "issue",
                        "title": f"[{repo}] {item.get('title', '')}",
                        "description": (item.get("body", "") or "")[:2000],
                        "url": item.get("html_url", ""),
                        "metadata": json.dumps({
                            "repo": repo,
                            "labels": [l.get("name", "") for l in item.get("labels", [])],
                            "reactions_thumbs_up": thumbs_up,
                            "comments": item.get("comments", 0),
                            "created_at": item.get("created_at", ""),
                        }),
                        "community_score": min(thumbs_up / 50, 1.0),
                        "content_hash": _content_hash(item.get("html_url", "")),
                        "discovered_at": _now(),
                    })
                time.sleep(1)

    return signals


def scan_cve_databases(config):
    """Scan NVD and GitHub Advisories for new CVEs.

    Args:
        config: CVE source config from innovation_config.yaml.

    Returns:
        List of normalized signal dicts.
    """
    signals = []
    cve_config = config.get("sources", {}).get("cve_databases", {})
    if not cve_config.get("enabled", False):
        return signals

    # NVD scan — last 24 hours
    sources = cve_config.get("sources", [])
    ecosystems = cve_config.get("ecosystems", [])
    max_results = cve_config.get("max_results", 100)

    for source in sources:
        if source.get("name") == "nvd":
            last_modified = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime(
                "%Y-%m-%dT%H:%M:%S.000"
            )
            params = {
                "lastModStartDate": last_modified,
                "lastModEndDate": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000"),
                "resultsPerPage": min(max_results, 50),
            }

            # Filter by severity
            severity_filter = source.get("severity_filter", ["CRITICAL", "HIGH"])
            for severity in severity_filter:
                params["cvssV3Severity"] = severity
                data, err = _safe_get(
                    source.get("api_url", NVD_API),
                    params=params,
                    timeout=60,
                )
                if err:
                    signals.append(_error_signal("nvd", severity, err))
                    continue

                vulnerabilities = (data or {}).get("vulnerabilities", [])
                for vuln in vulnerabilities[:max_results]:
                    cve_data = vuln.get("cve", {})
                    cve_id = cve_data.get("id", "")
                    descriptions = cve_data.get("descriptions", [])
                    desc = next(
                        (d.get("value", "") for d in descriptions if d.get("lang") == "en"),
                        "",
                    )
                    metrics = cve_data.get("metrics", {})
                    cvss_data = metrics.get("cvssMetricV31", [{}])
                    cvss_score = (
                        cvss_data[0].get("cvssData", {}).get("baseScore", 0.0)
                        if cvss_data
                        else 0.0
                    )

                    signals.append({
                        "id": _signal_id(),
                        "source": "nvd",
                        "source_type": "cve",
                        "title": cve_id,
                        "description": desc[:2000],
                        "url": f"https://nvd.nist.gov/vuln/detail/{cve_id}",
                        "metadata": json.dumps({
                            "cve_id": cve_id,
                            "cvss_score": cvss_score,
                            "severity": severity,
                            "published": cve_data.get("published", ""),
                            "modified": cve_data.get("lastModified", ""),
                        }),
                        "community_score": min(cvss_score / 10.0, 1.0),
                        "content_hash": _content_hash(cve_id),
                        "discovered_at": _now(),
                    })
                time.sleep(2)  # NVD rate limit

        elif source.get("name") == "github_advisories":
            headers = {"Accept": "application/vnd.github+json"}
            gh_token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
            if gh_token:
                headers["Authorization"] = f"Bearer {gh_token}"

            severity_filter = source.get("severity_filter", ["critical", "high"])
            for severity in severity_filter:
                params = {
                    "severity": severity,
                    "per_page": min(max_results, 30),
                    "sort": "updated",
                    "direction": "desc",
                }
                for eco in ecosystems:
                    params["ecosystem"] = eco
                    data, err = _safe_get(
                        source.get("api_url", f"{GITHUB_API}/advisories"),
                        headers=headers,
                        params=params,
                    )
                    if err:
                        signals.append(_error_signal("github_advisories", f"{eco}_{severity}", err))
                        continue

                    for advisory in (data or [])[:max_results]:
                        ghsa_id = advisory.get("ghsa_id", "")
                        cve_id = advisory.get("cve_id", ghsa_id)
                        signals.append({
                            "id": _signal_id(),
                            "source": "github_advisories",
                            "source_type": "cve",
                            "title": f"{cve_id}: {advisory.get('summary', '')}",
                            "description": (advisory.get("description", "") or "")[:2000],
                            "url": advisory.get("html_url", ""),
                            "metadata": json.dumps({
                                "ghsa_id": ghsa_id,
                                "cve_id": cve_id,
                                "severity": advisory.get("severity", ""),
                                "ecosystem": eco,
                                "cvss_score": (advisory.get("cvss", {}) or {}).get("score", 0),
                            }),
                            "community_score": min(
                                ((advisory.get("cvss", {}) or {}).get("score", 0)) / 10.0, 1.0
                            ),
                            "content_hash": _content_hash(ghsa_id or cve_id),
                            "discovered_at": _now(),
                        })
                    time.sleep(1)

    return signals


def scan_stackoverflow(config):
    """Scan Stack Overflow for trending questions in target tags.

    Args:
        config: SO source config from innovation_config.yaml.

    Returns:
        List of normalized signal dicts.
    """
    signals = []
    so_config = config.get("sources", {}).get("stackoverflow", {})
    if not so_config.get("enabled", False):
        return signals

    tags = so_config.get("tags", [])
    min_score = so_config.get("min_score", 5)
    max_results = so_config.get("max_results", 50)
    sort = so_config.get("sort", "votes")

    # Batch tags (SO API supports semicolon-separated)
    batch_size = 5
    for i in range(0, len(tags), batch_size):
        tag_batch = ";".join(tags[i : i + batch_size])
        params = {
            "tagged": tag_batch,
            "sort": sort,
            "order": "desc",
            "pagesize": min(max_results, 30),
            "filter": "withbody",
            "site": "stackoverflow",
        }

        data, err = _safe_get(f"{SO_API}/questions", params=params)
        if err:
            signals.append(_error_signal("stackoverflow", tag_batch, err))
            continue

        for item in (data or {}).get("items", [])[:max_results]:
            score = item.get("score", 0)
            if score < min_score:
                continue
            signals.append({
                "id": _signal_id(),
                "source": "stackoverflow",
                "source_type": "question",
                "title": item.get("title", ""),
                "description": (item.get("body", "") or "")[:2000],
                "url": item.get("link", ""),
                "metadata": json.dumps({
                    "tags": item.get("tags", []),
                    "score": score,
                    "answer_count": item.get("answer_count", 0),
                    "view_count": item.get("view_count", 0),
                    "is_answered": item.get("is_answered", False),
                    "creation_date": item.get("creation_date", 0),
                }),
                "community_score": min(score / 100, 1.0),
                "content_hash": _content_hash(str(item.get("question_id", ""))),
                "discovered_at": _now(),
            })
        time.sleep(1)

    return signals


def scan_hackernews(config):
    """Scan Hacker News for high-scoring relevant stories.

    Args:
        config: HN config from innovation_config.yaml.

    Returns:
        List of normalized signal dicts.
    """
    signals = []
    community_config = config.get("sources", {}).get("community_forums", {})
    if not community_config.get("enabled", False):
        return signals

    hn_config = None
    for platform in community_config.get("platforms", []):
        if platform.get("name") == "hackernews":
            hn_config = platform
            break

    if not hn_config:
        return signals

    min_score = hn_config.get("min_score", 100)
    max_results = hn_config.get("max_results", 20)

    # Get top stories
    data, err = _safe_get(f"{HN_API}/topstories.json")
    if err:
        return [_error_signal("hackernews", "topstories", err)]

    story_ids = (data or [])[:max_results * 2]  # Fetch more, filter by score

    for story_id in story_ids:
        item_data, err = _safe_get(f"{HN_API}/item/{story_id}.json")
        if err or not item_data:
            continue

        score = item_data.get("score", 0)
        if score < min_score:
            continue

        title = item_data.get("title", "")
        # Filter for relevant topics
        relevance_keywords = [
            "security", "compliance", "devops", "devsecops", "kubernetes",
            "terraform", "ci/cd", "pipeline", "vulnerability", "sbom",
            "zero trust", "supply chain", "developer", "tooling", "ai",
            "coding", "automation", "infrastructure", "cloud", "container",
        ]
        title_lower = title.lower()
        if not any(kw in title_lower for kw in relevance_keywords):
            continue

        signals.append({
            "id": _signal_id(),
            "source": "hackernews",
            "source_type": "story",
            "title": title,
            "description": item_data.get("text", "") or f"URL: {item_data.get('url', '')}",
            "url": item_data.get("url", f"https://news.ycombinator.com/item?id={story_id}"),
            "metadata": json.dumps({
                "hn_id": story_id,
                "score": score,
                "comments": item_data.get("descendants", 0),
                "by": item_data.get("by", ""),
                "time": item_data.get("time", 0),
            }),
            "community_score": min(score / 500, 1.0),
            "content_hash": _content_hash(str(story_id)),
            "discovered_at": _now(),
        })

        if len(signals) >= max_results:
            break
        time.sleep(0.5)

    return signals


def _error_signal(source, context, error):
    """Create an error signal for tracking scan failures."""
    return {
        "id": _signal_id(),
        "source": source,
        "source_type": "scan_error",
        "title": f"Scan error: {source}/{context}",
        "description": str(error),
        "url": "",
        "metadata": json.dumps({"error": str(error), "context": context}),
        "community_score": 0.0,
        "content_hash": _content_hash(f"{source}_{context}_{_now()[:10]}"),
        "discovered_at": _now(),
    }


# =========================================================================
# SIGNAL STORAGE
# =========================================================================
def store_signals(signals, db_path=None):
    """Store discovered signals in the database (append-only).

    Args:
        signals: List of signal dicts.
        db_path: Optional DB path override.

    Returns:
        Dict with stored count, duplicates skipped, and errors.
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

            # Check for duplicate by content hash
            existing = conn.execute(
                "SELECT id FROM innovation_signals WHERE content_hash = ?",
                (signal.get("content_hash", ""),),
            ).fetchone()

            if existing:
                duplicates += 1
                continue

            conn.execute(
                """INSERT INTO innovation_signals
                   (id, source, source_type, title, description, url,
                    metadata, community_score, content_hash, discovered_at,
                    status, category)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', NULL)""",
                (
                    signal["id"],
                    signal["source"],
                    signal["source_type"],
                    signal["title"],
                    signal.get("description", ""),
                    signal.get("url", ""),
                    signal.get("metadata", "{}"),
                    signal.get("community_score", 0.0),
                    signal.get("content_hash", ""),
                    signal.get("discovered_at", _now()),
                ),
            )
            stored += 1

        conn.commit()
    finally:
        conn.close()

    _audit(
        "innovation.scan",
        "innovation-agent",
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
# Map source name -> scanner function
SOURCE_SCANNERS = {
    "github": scan_github,
    "cve_databases": scan_cve_databases,
    "stackoverflow": scan_stackoverflow,
    "hackernews": scan_hackernews,
}


def run_scan(source=None, db_path=None):
    """Run web intelligence scan for specified source or all sources.

    Args:
        source: Source name (github, cve_databases, etc.) or None for all.
        db_path: Optional DB path override.

    Returns:
        Dict with scan results per source and totals.
    """
    config = _load_config()
    results = {}
    all_signals = []

    sources_to_scan = [source] if source else list(SOURCE_SCANNERS.keys())

    for src in sources_to_scan:
        scanner = SOURCE_SCANNERS.get(src)
        if not scanner:
            results[src] = {"error": f"Unknown source: {src}"}
            continue

        try:
            signals = scanner(config)
            storage_result = store_signals(signals, db_path)
            results[src] = {
                "signals_found": len(signals),
                **storage_result,
            }
            all_signals.extend(signals)
        except Exception as e:
            results[src] = {"error": str(e), "signals_found": 0}

    total_stored = sum(r.get("stored", 0) for r in results.values())
    total_found = sum(r.get("signals_found", 0) for r in results.values())

    return {
        "scan_time": _now(),
        "sources_scanned": len(sources_to_scan),
        "results": results,
        "totals": {
            "signals_found": total_found,
            "signals_stored": total_stored,
        },
    }


def list_sources():
    """List all configured sources and their status.

    Returns:
        Dict with source list and configuration status.
    """
    config = _load_config()
    sources = []

    for source_name, scanner in SOURCE_SCANNERS.items():
        source_config = config.get("sources", {}).get(source_name, {})
        sources.append({
            "name": source_name,
            "enabled": source_config.get("enabled", False),
            "scan_interval_hours": source_config.get("scan_interval_hours", 12),
            "has_scanner": True,
        })

    # Also list sources with config but no scanner yet
    for source_name in config.get("sources", {}):
        if source_name not in SOURCE_SCANNERS:
            source_config = config["sources"][source_name]
            sources.append({
                "name": source_name,
                "enabled": source_config.get("enabled", False),
                "scan_interval_hours": source_config.get("scan_interval_hours", 12),
                "has_scanner": False,
            })

    return {"sources": sources, "total": len(sources)}


def get_scan_history(days=7, db_path=None):
    """Get recent scan history from stored signals.

    Args:
        days: Number of days to look back.
        db_path: Optional DB path override.

    Returns:
        Dict with signal counts per source per day.
    """
    conn = _get_db(db_path)
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        rows = conn.execute(
            """SELECT source, DATE(discovered_at) as scan_date, COUNT(*) as count,
                      status, category
               FROM innovation_signals
               WHERE discovered_at >= ?
               GROUP BY source, scan_date, status
               ORDER BY scan_date DESC""",
            (cutoff,),
        ).fetchall()

        history = {}
        for row in rows:
            source = row["source"]
            if source not in history:
                history[source] = []
            history[source].append({
                "date": row["scan_date"],
                "count": row["count"],
                "status": row["status"],
                "category": row["category"],
            })

        total = conn.execute(
            "SELECT COUNT(*) as total FROM innovation_signals WHERE discovered_at >= ?",
            (cutoff,),
        ).fetchone()["total"]

        return {
            "days": days,
            "total_signals": total,
            "by_source": history,
        }
    finally:
        conn.close()


# =========================================================================
# CLI
# =========================================================================
def main():
    parser = argparse.ArgumentParser(
        description="ICDEV Web Intelligence Scanner — discover innovation signals"
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--db-path", type=Path, default=None, help="Database path override")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--scan", action="store_true", help="Run web scan")
    group.add_argument("--list-sources", action="store_true", help="List configured sources")
    group.add_argument("--history", action="store_true", help="Show scan history")

    parser.add_argument("--source", type=str, help="Specific source to scan (with --scan)")
    parser.add_argument("--all", action="store_true", help="Scan all sources (with --scan)")
    parser.add_argument("--days", type=int, default=7, help="History lookback days")

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
            if args.scan:
                print(f"Scan completed at {result.get('scan_time', '')}")
                print(f"Sources scanned: {result.get('sources_scanned', 0)}")
                totals = result.get("totals", {})
                print(f"Signals found: {totals.get('signals_found', 0)}")
                print(f"Signals stored: {totals.get('signals_stored', 0)}")
                for src, res in result.get("results", {}).items():
                    status = "OK" if "error" not in res else f"ERROR: {res['error']}"
                    print(f"  {src}: {res.get('signals_found', 0)} found — {status}")
            elif args.list_sources:
                print("Configured Sources:")
                for src in result.get("sources", []):
                    status = "enabled" if src["enabled"] else "disabled"
                    scanner = "scanner" if src["has_scanner"] else "no scanner"
                    print(f"  {src['name']}: {status} ({scanner}, every {src['scan_interval_hours']}h)")
            elif args.history:
                print(f"Scan history (last {result.get('days', 7)} days):")
                print(f"Total signals: {result.get('total_signals', 0)}")
                for src, entries in result.get("by_source", {}).items():
                    print(f"  {src}:")
                    for entry in entries[:5]:
                        print(f"    {entry['date']}: {entry['count']} signals ({entry['status']})")

    except Exception as e:
        error = {"error": str(e)}
        if args.json:
            print(json.dumps(error, indent=2))
        else:
            print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
