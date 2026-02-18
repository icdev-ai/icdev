#!/usr/bin/env python3
# ============================================================================
# CUI // SP-CTRLSYS
# Controlled Unclassified Information — IC Development Framework
# Tool: Log Analyzer — ELK/Splunk log analysis and pattern detection
# Classification: CUI — Do not distribute outside authorized channels
# ============================================================================
"""Log Analyzer — queries ELK and Splunk for log data, detects error patterns,
counts occurrences, and records findings in the metric_snapshots table.

Functions:
    analyze_logs(source, query, time_range, db_path)  -> analysis results dict
    search_patterns(log_data, patterns)                -> matched patterns list

CLI:
    python tools/monitor/log_analyzer.py --source elk|splunk --query "error" --time-range 24h
"""

import argparse
import json
import re
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

# Default endpoints — override via CLI args or environment
DEFAULT_ELK_URL = "http://localhost:9200"
DEFAULT_SPLUNK_URL = "https://localhost:8089"

# Common error patterns for automatic detection
DEFAULT_PATTERNS = [
    {"name": "NullPointerException", "regex": r"NullPointerException|NoneType|null reference"},
    {"name": "OutOfMemory", "regex": r"OutOfMemoryError|MemoryError|OOM|memory limit"},
    {"name": "ConnectionRefused", "regex": r"Connection refused|ECONNREFUSED|connection reset"},
    {"name": "Timeout", "regex": r"TimeoutError|timed out|deadline exceeded|ETIMEDOUT"},
    {"name": "AuthFailure", "regex": r"401|403|authentication failed|unauthorized|forbidden"},
    {"name": "DiskFull", "regex": r"No space left on device|disk full|ENOSPC"},
    {"name": "DNSFailure", "regex": r"Name or service not known|NXDOMAIN|DNS resolution"},
    {"name": "CertificateError", "regex": r"certificate verify failed|SSL|TLS handshake"},
    {"name": "RateLimited", "regex": r"429|rate limit|throttl|too many requests"},
    {"name": "DatabaseError", "regex": r"deadlock|lock timeout|connection pool|database is locked"},
]


def _get_db(db_path: Path = None) -> sqlite3.Connection:
    """Open a connection to the ICDEV database."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _parse_time_range(time_range: str) -> dict:
    """Parse a time range string like '24h', '30m', '7d' into components.

    Returns:
        Dict with start/end ISO strings, timedelta, and ELK/Splunk format strings.
    """
    match = re.match(r"^(\d+)([smhd])$", time_range)
    if not match:
        raise ValueError(f"Invalid time range: {time_range}. Use format: 30m, 1h, 24h, 7d")

    value, unit = int(match.group(1)), match.group(2)
    unit_map = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}
    delta = timedelta(**{unit_map[unit]: value})

    now = datetime.utcnow()
    return {
        "start": (now - delta).isoformat() + "Z",
        "end": now.isoformat() + "Z",
        "delta": delta,
        "elk_format": f"now-{value}{unit}",
        "splunk_format": f"-{value}{unit}",
    }


# ---------------------------------------------------------------------------
# Source queries
# ---------------------------------------------------------------------------
def _query_elk(index: str, query_str: str, time_range: str,
               elk_url: str = None, size: int = 500) -> dict:
    """Execute a search query against Elasticsearch.

    Args:
        index: Elasticsearch index pattern (e.g., 'app-logs-*').
        query_str: Query string (Lucene syntax).
        time_range: Time range string (e.g., '24h').
        elk_url: Elasticsearch base URL.
        size: Maximum number of results.

    Returns:
        Dict with source, total_hits, hits, and optional error.
    """
    url = elk_url or DEFAULT_ELK_URL
    tr = _parse_time_range(time_range)

    body = {
        "query": {
            "bool": {
                "must": [{"query_string": {"query": query_str}}] if query_str else [{"match_all": {}}],
                "filter": [
                    {"range": {"@timestamp": {"gte": tr["elk_format"], "lte": "now"}}}
                ],
            }
        },
        "size": size,
        "sort": [{"@timestamp": {"order": "desc"}}],
    }

    endpoint = f"{url}/{index}/_search"
    data = json.dumps(body).encode("utf-8")

    try:
        req = urllib.request.Request(
            endpoint, data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return {
                "source": "elk",
                "index": index,
                "total_hits": result.get("hits", {}).get("total", {}).get("value", 0),
                "hits": [h.get("_source", {}) for h in result.get("hits", {}).get("hits", [])],
                "took_ms": result.get("took", 0),
            }
    except urllib.error.URLError as exc:
        return {"source": "elk", "error": f"Connection failed: {exc}", "index": index,
                "total_hits": 0, "hits": []}
    except Exception as exc:
        return {"source": "elk", "error": str(exc), "index": index,
                "total_hits": 0, "hits": []}


def _query_splunk(search_query: str, time_range: str,
                  splunk_url: str = None, splunk_token: str = None,
                  max_results: int = 500) -> dict:
    """Execute a search against the Splunk REST API.

    Args:
        search_query: Splunk SPL query.
        time_range: Time range string.
        splunk_url: Splunk management URL.
        splunk_token: Bearer token for authentication.
        max_results: Maximum results to return.

    Returns:
        Dict with source, total_hits, hits, and optional error.
    """
    url = splunk_url or DEFAULT_SPLUNK_URL
    tr = _parse_time_range(time_range)

    if not search_query.strip().startswith("search"):
        search_query = f"search {search_query}"

    endpoint = f"{url}/services/search/jobs/export"
    params = {
        "search": search_query,
        "earliest_time": tr["splunk_format"],
        "latest_time": "now",
        "output_mode": "json",
        "count": max_results,
    }
    data = urllib.parse.urlencode(params).encode("utf-8")

    try:
        import ssl
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if splunk_token:
            headers["Authorization"] = f"Bearer {splunk_token}"

        req = urllib.request.Request(endpoint, data=data, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
            raw = resp.read().decode("utf-8")
            results = []
            for line in raw.strip().split("\n"):
                if line.strip():
                    try:
                        obj = json.loads(line)
                        if "result" in obj:
                            results.append(obj["result"])
                    except json.JSONDecodeError:
                        continue
            return {"source": "splunk", "query": search_query,
                    "total_hits": len(results), "hits": results}
    except urllib.error.URLError as exc:
        return {"source": "splunk", "error": f"Connection failed: {exc}",
                "query": search_query, "total_hits": 0, "hits": []}
    except Exception as exc:
        return {"source": "splunk", "error": str(exc),
                "query": search_query, "total_hits": 0, "hits": []}


# ---------------------------------------------------------------------------
# Pattern search
# ---------------------------------------------------------------------------
def search_patterns(log_data: list, patterns: list = None) -> list:
    """Search log entries for known error patterns.

    Args:
        log_data: List of log entry dicts. Each should have a 'message' field.
        patterns: List of pattern dicts with 'name' and 'regex' keys.
                  Defaults to DEFAULT_PATTERNS if not provided.

    Returns:
        List of dicts: [{name, regex, count, sample_messages}].
    """
    if patterns is None:
        patterns = DEFAULT_PATTERNS

    results = []
    for pattern in patterns:
        name = pattern.get("name", "unknown")
        regex = pattern.get("regex", "")
        if not regex:
            continue

        compiled = re.compile(regex, re.IGNORECASE)
        matches = []
        for entry in log_data:
            msg = entry.get("message") or entry.get("msg") or entry.get("_raw") or ""
            if compiled.search(msg):
                matches.append(msg[:200])

        if matches:
            results.append({
                "name": name,
                "regex": regex,
                "count": len(matches),
                "sample_messages": matches[:5],
            })

    # Sort by count descending
    results.sort(key=lambda r: r["count"], reverse=True)
    return results


# ---------------------------------------------------------------------------
# Core analysis
# ---------------------------------------------------------------------------
def analyze_logs(source: str, query: str, time_range: str,
                 db_path: Path = None, project_id: str = None,
                 elk_url: str = None, splunk_url: str = None,
                 splunk_token: str = None) -> dict:
    """Analyze logs from ELK or Splunk.

    Queries the specified source, extracts error patterns, counts occurrences,
    and records findings in the metric_snapshots table.

    Args:
        source: Log source — 'elk', 'splunk', or 'both'.
        query: Search query string.
        time_range: Time range (e.g., '24h', '1h', '7d').
        db_path: Optional override for database path.
        project_id: Project identifier (used as index prefix for ELK).
        elk_url: Elasticsearch URL override.
        splunk_url: Splunk URL override.
        splunk_token: Splunk authentication token.

    Returns:
        Dict with analysis results including error patterns, severity counts,
        anomalies, and top messages.
    """
    all_logs = []
    source_results = {}

    # ---------- Query ELK ----------
    if source in ("elk", "both"):
        index = f"{project_id}-*" if project_id else "*"
        elk_result = _query_elk(index, query, time_range, elk_url)
        all_logs.extend(elk_result.get("hits", []))
        source_results["elk"] = {
            "total_hits": elk_result.get("total_hits", 0),
            "error": elk_result.get("error"),
        }

    # ---------- Query Splunk ----------
    if source in ("splunk", "both"):
        splunk_query = query or (f'index="{project_id}" level=ERROR OR level=WARN'
                                 if project_id else "level=ERROR OR level=WARN")
        splunk_result = _query_splunk(splunk_query, time_range, splunk_url, splunk_token)
        all_logs.extend(splunk_result.get("hits", []))
        source_results["splunk"] = {
            "total_hits": splunk_result.get("total_hits", 0),
            "error": splunk_result.get("error"),
        }

    # ---------- Extract severity counts ----------
    severity_counts = Counter()
    for entry in all_logs:
        level = (entry.get("level") or entry.get("log_level")
                 or entry.get("severity") or entry.get("status") or "unknown")
        if isinstance(level, str):
            level_lower = level.lower()
            if level_lower in ("error", "err", "fatal", "critical", "crit"):
                severity_counts["error"] += 1
            elif level_lower in ("warning", "warn"):
                severity_counts["warning"] += 1
            else:
                severity_counts["info"] += 1
        else:
            severity_counts["unknown"] += 1

    # ---------- Detect error patterns ----------
    matched_patterns = search_patterns(all_logs)

    # ---------- Top repeated messages ----------
    message_counter = Counter()
    for entry in all_logs:
        msg = entry.get("message") or entry.get("msg") or ""
        if msg:
            normalized = re.sub(r"\d+", "N", msg[:120])
            message_counter[normalized] += 1

    top_messages = [
        {"message": msg, "count": count}
        for msg, count in message_counter.most_common(10)
        if count > 1
    ]

    # ---------- Time-bucket anomaly detection (5-minute buckets) ----------
    frequency_anomalies = []
    timestamps = []
    for entry in all_logs:
        ts = entry.get("@timestamp") or entry.get("timestamp") or entry.get("_time")
        if ts and isinstance(ts, str):
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00").replace("+00:00", ""))
                timestamps.append(dt)
            except (ValueError, AttributeError):
                pass

    if timestamps:
        timestamps.sort()
        buckets = defaultdict(int)
        for ts_val in timestamps:
            bucket_key = ts_val.replace(minute=(ts_val.minute // 5) * 5, second=0, microsecond=0)
            buckets[bucket_key] += 1

        if len(buckets) >= 2:
            counts = list(buckets.values())
            mean = sum(counts) / len(counts)
            variance = sum((c - mean) ** 2 for c in counts) / len(counts)
            std_dev = variance ** 0.5

            for bucket_time, count in buckets.items():
                if std_dev > 0 and (count - mean) / std_dev > 2.0:
                    frequency_anomalies.append({
                        "bucket": bucket_time.isoformat(),
                        "count": count,
                        "mean": round(mean, 1),
                        "z_score": round((count - mean) / std_dev, 2),
                    })

    # ---------- Error rate ----------
    total = len(all_logs)
    errors = severity_counts.get("error", 0)
    error_rate = round(errors / total, 4) if total > 0 else 0.0
    is_spike = error_rate > 0.10

    # ---------- Build result ----------
    result = {
        "project_id": project_id,
        "source": source,
        "query": query,
        "time_range": time_range,
        "analyzed_at": datetime.utcnow().isoformat() + "Z",
        "total_logs": total,
        "severity_counts": dict(severity_counts),
        "error_rate": error_rate,
        "error_rate_is_spike": is_spike,
        "matched_patterns": matched_patterns,
        "top_messages": top_messages,
        "frequency_anomalies": frequency_anomalies,
        "source_results": source_results,
    }

    # ---------- Record findings in metric_snapshots ----------
    if project_id and (db_path or DB_PATH.exists()):
        _record_findings(project_id, result, db_path)

    return result


def _record_findings(project_id: str, analysis: dict, db_path: Path = None) -> None:
    """Store analysis findings as metric snapshots in the database."""
    try:
        conn = _get_db(db_path)
        now = datetime.utcnow().isoformat()

        metrics = {
            "log_error_rate": analysis.get("error_rate", 0.0),
            "log_error_count": float(analysis.get("severity_counts", {}).get("error", 0)),
            "log_warning_count": float(analysis.get("severity_counts", {}).get("warning", 0)),
            "log_total_count": float(analysis.get("total_logs", 0)),
            "log_pattern_match_count": float(
                sum(p.get("count", 0) for p in analysis.get("matched_patterns", []))
            ),
            "log_anomaly_count": float(len(analysis.get("frequency_anomalies", []))),
        }

        for metric_name, metric_value in metrics.items():
            conn.execute(
                """INSERT INTO metric_snapshots
                   (project_id, metric_name, metric_value, labels, source, collected_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (project_id, metric_name, metric_value,
                 json.dumps({"query": analysis.get("query", ""), "time_range": analysis.get("time_range", "")}),
                 "log_analyzer", now),
            )
        conn.commit()
        conn.close()
    except Exception as exc:
        print(f"[log-analyzer] Warning: failed to record findings: {exc}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Log Analyzer — queries ELK/Splunk, detects error patterns, records findings"
    )
    parser.add_argument("--source", choices=["elk", "splunk", "both"], default="elk",
                        help="Log source to query (default: elk)")
    parser.add_argument("--query", default="level:ERROR OR level:WARN",
                        help='Search query string (default: "level:ERROR OR level:WARN")')
    parser.add_argument("--time-range", default="24h",
                        help="Time range for analysis (e.g., 30m, 1h, 24h, 7d)")
    parser.add_argument("--project-id", help="Project identifier (used as index prefix for ELK)")
    parser.add_argument("--elk-url", default=DEFAULT_ELK_URL, help="Elasticsearch URL")
    parser.add_argument("--splunk-url", default=DEFAULT_SPLUNK_URL, help="Splunk URL")
    parser.add_argument("--splunk-token", help="Splunk authentication token")
    parser.add_argument("--db-path", help="Override database path")
    parser.add_argument("--format", choices=["json", "text"], default="text",
                        help="Output format (default: text)")
    args = parser.parse_args()

    db_path = Path(args.db_path) if args.db_path else None

    result = analyze_logs(
        source=args.source,
        query=args.query,
        time_range=args.time_range,
        db_path=db_path,
        project_id=args.project_id,
        elk_url=args.elk_url,
        splunk_url=args.splunk_url,
        splunk_token=args.splunk_token,
    )

    if args.format == "json":
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"\n{'=' * 60}")
        print(f"  LOG ANALYSIS — {result.get('project_id', 'all')}")
        print(f"  Source: {result['source']} | Time range: {result['time_range']}")
        print(f"  Total logs: {result['total_logs']} | Error rate: {result['error_rate'] * 100:.1f}%"
              + (" ** SPIKE **" if result.get("error_rate_is_spike") else ""))
        print(f"{'=' * 60}")

        # Severity breakdown
        sev = result.get("severity_counts", {})
        if sev:
            print("\n  Severity breakdown:")
            for level, count in sorted(sev.items(), key=lambda x: x[1], reverse=True):
                print(f"    {level:>10s}: {count}")

        # Matched patterns
        patterns = result.get("matched_patterns", [])
        if patterns:
            print(f"\n  ERROR PATTERNS DETECTED ({len(patterns)}):")
            for p in patterns[:10]:
                print(f"    [{p['count']:>4d}x] {p['name']}")
                for sample in p.get("sample_messages", [])[:2]:
                    print(f"           {sample[:80]}")

        # Frequency anomalies
        anomalies = result.get("frequency_anomalies", [])
        if anomalies:
            print(f"\n  FREQUENCY ANOMALIES ({len(anomalies)} detected):")
            for a in anomalies[:5]:
                print(f"    {a['bucket']}: {a['count']} events (z-score: {a['z_score']})")

        # Top messages
        top = result.get("top_messages", [])
        if top:
            print("\n  TOP REPEATED MESSAGES:")
            for m in top[:5]:
                print(f"    ({m['count']}x) {m['message'][:70]}")

        # Source errors
        for src, info in result.get("source_results", {}).items():
            if info.get("error"):
                print(f"\n  WARNING [{src}]: {info['error']}")

        print(f"\n{'=' * 60}")


if __name__ == "__main__":
    main()
