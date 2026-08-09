#!/usr/bin/env python3
# CUI // SP-CTI
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
    _ai_extract_log_patterns(messages)                 -> emergent NLP categories | None
    _detect_frequency_anomalies(buckets, cfg)          -> frequency anomaly list
    _detect_silence_gaps(buckets, cfg)                 -> ingestion-silence run list

The regex catalog (DEFAULT_PATTERNS) only catches known error families; novel
or free-text failures fall into an "unknown" bucket. An optional NLP extractor
(nlp_extractor paradigm) clusters those unmatched lines into emergent error
categories. It is opt-in (use_ai_extraction / --ai-extract) and degrades
gracefully — the deterministic regex result is always authoritative.

Frequency-spike and error-rate detection (anomaly_detection paradigm) previously
used thresholds hardcoded inline (z-score > 2.0; error_rate > 0.10). Those now
load from args/monitoring_config.yaml (anomaly_detection block) and default to
the legacy values when unset. A robust median-based method ("mad") is available
that is not skewed by the very spike it is detecting. The same paradigm also
covers the inverse failure mode — ingestion-silence gaps (contiguous empty/near-
zero buckets between periods of activity, i.e. a "missing partition"), surfaced
under ``silence_gaps`` when ``anomaly_detection.gaps.enabled`` is set.

CLI:
    python tools/monitor/log_analyzer.py --source elk|splunk --query "error" --time-range 24h
    python tools/monitor/log_analyzer.py --source elk --ai-extract           # add NLP extraction
    python tools/monitor/log_analyzer.py --source elk --anomaly-method mad    # robust detection
"""

import argparse
import json
import re
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
import sys
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.db.storage import get_connection
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

# Default endpoints — override via CLI args or environment
DEFAULT_ELK_URL = "http://localhost:9200"
DEFAULT_SPLUNK_URL = "https://localhost:8089"

# ---------------------------------------------------------------------------
# AI pattern extraction (nlp_extractor paradigm) — tuning knobs
# ---------------------------------------------------------------------------
# The regex catalog below only catches *known* error families. Novel or
# free-text failures land in the "unknown" bucket and stay invisible. The
# optional NLP extractor (``_ai_extract_log_patterns``) clusters those
# unmatched messages into named, emergent categories. It degrades to ``None``
# whenever the LLM is unavailable, so the deterministic regex result is always
# authoritative and shipped unchanged.
_AI_EXTRACT_SYSTEM_PROMPT = (
    "You are a site-reliability log triage assistant. You are given log lines "
    "that did NOT match any known error-pattern regex. Cluster them into a small "
    "set of emergent error categories (at most 8). Respond with ONLY a JSON array; "
    "each element is an object with keys: name (short PascalCase label), "
    "description (one sentence), count (integer, how many of the provided lines "
    "fall in this category), sample_messages (array of up to 3 representative "
    "lines, each truncated to 200 chars). Do not invent categories for lines that "
    "are plainly informational; omit them. Return [] if nothing is noteworthy."
)
_AI_EXTRACT_MAX_TOKENS = 1024
_AI_EXTRACT_TEMPERATURE = 0.0
# Bound the prompt so a noisy log window can't blow up cost / context.
_AI_EXTRACT_MAX_SAMPLES = 60

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
    """Open a connection to the ICDEV™ database."""
    path = db_path or DB_PATH
    conn = get_connection(db_path=str(path))
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

    now = datetime.now(timezone.utc)
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
def _query_elk(index: str, query_str: str, time_range: str, elk_url: str = None, size: int = 500) -> dict:
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
                "filter": [{"range": {"@timestamp": {"gte": tr["elk_format"], "lte": "now"}}}],
            }
        },
        "size": size,
        "sort": [{"@timestamp": {"order": "desc"}}],
    }

    endpoint = f"{url}/{index}/_search"
    data = json.dumps(body).encode("utf-8")

    try:
        req = urllib.request.Request(
            endpoint,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=30) as resp:  # nosec B310 -- URL scheme validated; internal/configured endpoints only
            result = json.loads(resp.read().decode("utf-8"))
            return {
                "source": "elk",
                "index": index,
                "total_hits": result.get("hits", {}).get("total", {}).get("value", 0),
                "hits": [h.get("_source", {}) for h in result.get("hits", {}).get("hits", [])],
                "took_ms": result.get("took", 0),
            }
    except urllib.error.URLError as exc:
        return {"source": "elk", "error": f"Connection failed: {exc}", "index": index, "total_hits": 0, "hits": []}
    except Exception as exc:
        return {"source": "elk", "error": str(exc), "index": index, "total_hits": 0, "hits": []}


def _query_splunk(
    search_query: str, time_range: str, splunk_url: str = None, splunk_token: str = None, max_results: int = 500
) -> dict:
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
        with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:  # nosec B310 -- URL scheme validated; internal/configured endpoints only
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
            return {"source": "splunk", "query": search_query, "total_hits": len(results), "hits": results}
    except urllib.error.URLError as exc:
        return {
            "source": "splunk",
            "error": f"Connection failed: {exc}",
            "query": search_query,
            "total_hits": 0,
            "hits": [],
        }
    except Exception as exc:
        return {"source": "splunk", "error": str(exc), "query": search_query, "total_hits": 0, "hits": []}


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
            results.append(
                {
                    "name": name,
                    "regex": regex,
                    "count": len(matches),
                    "sample_messages": matches[:5],
                }
            )

    # Sort by count descending
    results.sort(key=lambda r: r["count"], reverse=True)
    return results


def _unmatched_messages(log_data: list, patterns: list = None) -> list:
    """Return log messages that matched none of the regex patterns.

    These are the candidates the NLP extractor reasons over — the "unknown"
    bucket that the deterministic regex catalog leaves uncategorized.

    Args:
        log_data: List of log entry dicts.
        patterns: Pattern dicts (defaults to DEFAULT_PATTERNS).

    Returns:
        List of message strings (deduplicated, order-preserving) that no
        pattern's regex matched.
    """
    if patterns is None:
        patterns = DEFAULT_PATTERNS

    compiled = [re.compile(p["regex"], re.IGNORECASE) for p in patterns if p.get("regex")]
    seen = set()
    unmatched = []
    for entry in log_data:
        msg = entry.get("message") or entry.get("msg") or entry.get("_raw") or ""
        if not msg or msg in seen:
            continue
        if not any(c.search(msg) for c in compiled):
            seen.add(msg)
            unmatched.append(msg[:200])
    return unmatched


def _ai_extract_log_patterns(messages: list) -> list | None:
    """Cluster unmatched log messages into emergent error categories via NLP.

    This is the ``nlp_extractor`` augmentation of the regex-only
    ``search_patterns`` path: it surfaces failure families the static regex
    catalog cannot anticipate (novel stack traces, free-text operator notes,
    third-party error strings).

    Args:
        messages: Free-text log lines that matched no known regex pattern.
            Only the first ``_AI_EXTRACT_MAX_SAMPLES`` are sent to bound cost.

    Returns:
        A list of ``{name, description, count, sample_messages, source}`` dicts
        sorted by count descending, or ``None`` if extraction is unavailable or
        fails for any reason. Callers MUST treat ``None`` as "no AI patterns"
        and ship the deterministic regex result unchanged.
    """
    if not messages:
        return None

    sample = messages[:_AI_EXTRACT_MAX_SAMPLES]
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        numbered = "\n".join(f"{i + 1}. {m}" for i, m in enumerate(sample))
        req = LLMRequest(
            messages=[{"role": "user", "content": f"Unmatched log lines:\n{numbered}"}],
            system_prompt=_AI_EXTRACT_SYSTEM_PROMPT,
            max_tokens=_AI_EXTRACT_MAX_TOKENS,
            temperature=_AI_EXTRACT_TEMPERATURE,
            skip_injection_scan=False,  # log lines are untrusted content — keep the scanner on
            classification="CUI",
        )
        # Reuse the structured-extraction route; the router falls back to the
        # default provider when this logical function is unconfigured.
        resp = LLMRouter().invoke("requirement_extraction", req)
        if not resp or not resp.content:
            return None
        parsed = _parse_ai_patterns(resp.content)
    except Exception:
        return None  # Graceful degradation — deterministic regex result is authoritative.

    if not parsed:
        return None
    parsed.sort(key=lambda r: r.get("count", 0), reverse=True)
    return parsed


def _parse_ai_patterns(raw: str) -> list:
    """Parse and sanitize the LLM's JSON pattern array.

    Tolerates markdown code fences and stray prose around the JSON. Drops any
    element that lacks a usable name. Returns ``[]`` on any parse failure so the
    caller can degrade cleanly.
    """
    text = raw.strip()
    if text.startswith("```"):
        # Strip a leading ```json / ``` fence and trailing fence.
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text).strip()
    # Isolate the outermost JSON array if the model added prose.
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1 or end < start:
        return []
    try:
        items = json.loads(text[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        return []
    if not isinstance(items, list):
        return []

    cleaned = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        samples = item.get("sample_messages") or []
        if not isinstance(samples, list):
            samples = []
        try:
            count = int(item.get("count", 0))
        except (TypeError, ValueError):
            count = 0
        cleaned.append(
            {
                "name": name,
                "description": str(item.get("description", "")).strip(),
                "count": count,
                "sample_messages": [str(s)[:200] for s in samples[:3]],
                "source": "ai",
            }
        )
    return cleaned


# ---------------------------------------------------------------------------
# Anomaly detection (anomaly_detection paradigm) — config-driven thresholds
# ---------------------------------------------------------------------------
# Frequency-spike and error-rate detection previously used thresholds hardcoded
# inline (z-score > 2.0; error_rate > 0.10). Those constants now live in
# ``args/monitoring_config.yaml`` under ``anomaly_detection`` and degrade to the
# original values whenever the file, PyYAML, or a key is missing — so default
# behavior is unchanged. The robust ``mad`` method (median absolute deviation)
# is offered as an alternative that is not skewed by the very spike it detects.
# ``_detect_silence_gaps`` adds the inverse: contiguous empty/near-zero buckets
# between activity (an ingestion outage / missing partition), opt-in via the
# ``gaps`` sub-block (off by default so output is unchanged).
_ANOMALY_CONFIG_PATH = BASE_DIR / "args" / "monitoring_config.yaml"
_DEFAULT_ANOMALY_CFG = {
    "frequency": {
        "method": "zscore",
        "z_threshold": 2.0,
        "mad_threshold": 3.5,
        "min_buckets": 2,
        "bucket_window_minutes": 5,
    },
    "error_rate": {"spike_threshold": 0.10},
    "gaps": {"enabled": False, "min_active_buckets": 3, "drop_ratio": 0.2},
}


def _median(values: list) -> float:
    """Return the median of a non-empty numeric list (0.0 for an empty list)."""
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return float(s[mid]) if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def _floor_to_window(dt: datetime, window_minutes: int) -> datetime:
    """Floor a datetime to the start of its ``window_minutes`` time bucket.

    Buckets are anchored at midnight by minutes-since-midnight, so a window that
    divides evenly into 60 (1/5/15/30) keeps the legacy within-the-hour
    alignment, while larger or non-dividing windows floor correctly and recompute
    the hour. The window is clamped to ``>= 1`` minute; the default 5 preserves
    the historical 5-minute bucketing exactly.
    """
    window = window_minutes if isinstance(window_minutes, int) and window_minutes >= 1 else 5
    minutes_since_midnight = dt.hour * 60 + dt.minute
    floored = (minutes_since_midnight // window) * window
    return dt.replace(hour=floored // 60, minute=floored % 60, second=0, microsecond=0)


def _load_anomaly_cfg(config_path: Path = None) -> dict:
    """Load the ``anomaly_detection`` tuning block from monitoring_config.yaml.

    Returns defaults deep-merged with any configured overrides, so a missing
    file, absent PyYAML, or absent key always yields the legacy hardcoded
    behavior (z-score > 2.0, error-rate spike at 0.10). Any failure degrades to
    defaults rather than raising — log analysis must never break on config.
    """
    cfg = {
        "frequency": dict(_DEFAULT_ANOMALY_CFG["frequency"]),
        "error_rate": dict(_DEFAULT_ANOMALY_CFG["error_rate"]),
        "gaps": dict(_DEFAULT_ANOMALY_CFG["gaps"]),
    }
    path = config_path or _ANOMALY_CONFIG_PATH
    try:
        import yaml

        with open(path, encoding="utf-8") as fh:
            loaded = yaml.safe_load(fh) or {}
        section = loaded.get("anomaly_detection") or {}
        for group in ("frequency", "error_rate", "gaps"):
            overrides = section.get(group) or {}
            for key in cfg[group]:
                if overrides.get(key) is not None:
                    cfg[group][key] = overrides[key]
    except Exception:
        pass  # Any failure → legacy defaults.

    # bucket_window_minutes must be a positive int; a non-int / non-positive
    # override falls back to the legacy 5-minute window rather than raising.
    try:
        bw = int(cfg["frequency"].get("bucket_window_minutes", 5))
        cfg["frequency"]["bucket_window_minutes"] = bw if bw >= 1 else 5
    except (TypeError, ValueError):
        cfg["frequency"]["bucket_window_minutes"] = 5
    return cfg


def _detect_frequency_anomalies(buckets: dict, cfg: dict) -> list:
    """Flag time-buckets whose event count is anomalously high.

    Args:
        buckets: Mapping of bucket-start datetime -> event count.
        cfg: Anomaly config (see ``_load_anomaly_cfg``); ``cfg["frequency"]``
            selects the method and threshold.

    Methods (``frequency.method``):
        * ``zscore`` — classic mean/std-dev z-score: flag where
          (count - mean) / std > ``z_threshold``. Simple, but the spike itself
          inflates std-dev and can mask smaller co-occurring anomalies.
        * ``mad`` — robust modified z-score (Iglewicz-Hoaglin):
          0.6745 * (count - median) / MAD > ``mad_threshold``, where MAD is the
          median absolute deviation. Resistant to the outliers being detected.

    Returns:
        Anomaly dicts ordered by bucket time; empty when there are too few
        buckets (``min_buckets``) or none clears the threshold.
    """
    freq = cfg.get("frequency", {})
    method = str(freq.get("method", "zscore")).lower()
    min_buckets = int(freq.get("min_buckets", 2) or 2)
    if len(buckets) < max(2, min_buckets):
        return []

    items = sorted(buckets.items())  # deterministic order by bucket time
    counts = [c for _, c in items]
    anomalies = []

    if method == "mad":
        med = _median(counts)
        mad = _median([abs(c - med) for c in counts])
        threshold = float(freq.get("mad_threshold", 3.5))
        for bucket_time, count in items:
            if mad > 0:
                mod_z = 0.6745 * (count - med) / mad
            else:
                # Degenerate spread (≥ half the buckets identical): flag only
                # buckets strictly above the median.
                mod_z = float("inf") if count > med else 0.0
            if mod_z > threshold:
                anomalies.append(
                    {
                        "bucket": bucket_time.isoformat(),
                        "count": count,
                        "median": round(med, 1),
                        "mod_z_score": None if mod_z == float("inf") else round(mod_z, 2),
                        "method": "mad",
                    }
                )
    else:  # zscore (default / legacy)
        mean = sum(counts) / len(counts)
        variance = sum((c - mean) ** 2 for c in counts) / len(counts)
        std_dev = variance**0.5
        threshold = float(freq.get("z_threshold", 2.0))
        for bucket_time, count in items:
            if std_dev > 0 and (count - mean) / std_dev > threshold:
                anomalies.append(
                    {
                        "bucket": bucket_time.isoformat(),
                        "count": count,
                        "mean": round(mean, 1),
                        "z_score": round((count - mean) / std_dev, 2),
                        "method": "zscore",
                    }
                )
    return anomalies


def _detect_silence_gaps(buckets: dict, cfg: dict) -> list:
    """Flag contiguous interior time-buckets that fall (near-)silent.

    This is the data-presence ("missing partition") counterpart to
    ``_detect_frequency_anomalies``: where that flags buckets that are
    anomalously *high*, this flags runs of buckets that are anomalously *low* or
    empty **between** periods of normal activity — the signature of an ingestion
    outage or a log pipeline that silently stopped writing (e.g. a date
    partition that never received data). One-sided spike detection cannot see
    this, because an absent bucket simply isn't in the ``buckets`` mapping.

    The full bucket grid is reconstructed at the frequency
    ``bucket_window_minutes`` cadence so absent buckets count as 0. A bucket is
    "silent" when its count is below ``median(active) * drop_ratio``. Only runs
    bounded by activity on BOTH sides are reported (a trailing quiet tail at the
    edge of the query window is the window ending, not an outage).

    Args:
        buckets: Mapping of bucket-start datetime -> event count (only populated
            buckets are present; reconstructed gaps are treated as count 0).
        cfg: Anomaly config (see ``_load_anomaly_cfg``); ``cfg["gaps"]`` selects
            the enable flag and thresholds, ``cfg["frequency"]`` the cadence.

    Returns:
        Run dicts ordered by start time, each describing one contiguous silence
        interval; empty when gap detection is disabled (default), the active
        baseline is too thin (``min_active_buckets``), or nothing drops below
        the silence floor.
    """
    gaps = cfg.get("gaps", {})
    if not gaps.get("enabled") or not buckets:
        return []

    min_active = int(gaps.get("min_active_buckets", 3) or 3)
    drop_ratio = float(gaps.get("drop_ratio", 0.2))
    window = int(cfg.get("frequency", {}).get("bucket_window_minutes", 5) or 5)

    active_counts = [c for c in buckets.values() if c > 0]
    if len(active_counts) < max(2, min_active):
        return []
    baseline = _median(active_counts)
    if baseline <= 0:
        return []
    floor = baseline * drop_ratio

    times = sorted(buckets)
    step = timedelta(minutes=window)
    grid = []
    t = times[0]
    end = times[-1]
    while t <= end:
        grid.append((t, buckets.get(t, 0)))
        t += step

    runs = []
    i, n = 0, len(grid)
    while i < n:
        if grid[i][1] < floor:
            j = i
            while j < n and grid[j][1] < floor:
                j += 1
            # Interior only: real activity must bound the run on both sides.
            # grid[0] is always a populated bucket, so i > 0 ⇒ a leading active
            # bucket exists; j < n ⇒ a trailing active bucket exists.
            if i > 0 and j < n:
                run = grid[i:j]
                runs.append(
                    {
                        "start": run[0][0].isoformat(),
                        "end": run[-1][0].isoformat(),
                        "buckets": len(run),
                        "max_count": max(c for _, c in run),
                        "baseline": round(baseline, 1),
                        "method": "silence_gap",
                    }
                )
            i = j + 1
        else:
            i += 1
    return runs


# ---------------------------------------------------------------------------
# Core analysis
# ---------------------------------------------------------------------------
def analyze_logs(
    source: str,
    query: str,
    time_range: str,
    db_path: Path = None,
    project_id: str = None,
    elk_url: str = None,
    splunk_url: str = None,
    splunk_token: str = None,
    use_ai_extraction: bool = False,
    anomaly_config: dict = None,
) -> dict:
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
        use_ai_extraction: When True, run the optional NLP extractor over log
            lines that matched no known regex pattern and add the resulting
            emergent categories under ``ai_extracted_patterns``. Off by default
            so behavior stays fully deterministic unless explicitly requested;
            degrades silently to ``[]`` if the LLM is unavailable.
        anomaly_config: Optional pre-loaded anomaly-detection config (see
            ``_load_anomaly_cfg``). Defaults to loading
            ``args/monitoring_config.yaml``; pass a dict to override the
            frequency method / thresholds without touching the config file.

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
        splunk_query = query or (
            f'index="{project_id}" level=ERROR OR level=WARN' if project_id else "level=ERROR OR level=WARN"
        )
        splunk_result = _query_splunk(splunk_query, time_range, splunk_url, splunk_token)
        all_logs.extend(splunk_result.get("hits", []))
        source_results["splunk"] = {
            "total_hits": splunk_result.get("total_hits", 0),
            "error": splunk_result.get("error"),
        }

    # ---------- Extract severity counts ----------
    severity_counts = Counter()
    for entry in all_logs:
        level = (
            entry.get("level") or entry.get("log_level") or entry.get("severity") or entry.get("status") or "unknown"
        )
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

    # ---------- NLP extraction over the unmatched "unknown" bucket ----------
    ai_extracted_patterns = []
    if use_ai_extraction:
        unmatched = _unmatched_messages(all_logs)
        ai_result = _ai_extract_log_patterns(unmatched)
        if ai_result:
            ai_extracted_patterns = ai_result

    # ---------- Top repeated messages ----------
    message_counter = Counter()
    for entry in all_logs:
        msg = entry.get("message") or entry.get("msg") or ""
        if msg:
            normalized = re.sub(r"\d+", "N", msg[:120])
            message_counter[normalized] += 1

    top_messages = [{"message": msg, "count": count} for msg, count in message_counter.most_common(10) if count > 1]

    # ---------- Time-bucket anomaly detection (config-driven window) ----------
    # The bucket window (default 5 minutes) is the time-partition granularity for
    # frequency-spike detection — too wide masks short spikes, too narrow adds
    # noise — so it loads from anomaly_detection.frequency.bucket_window_minutes.
    anomaly_cfg = anomaly_config if anomaly_config is not None else _load_anomaly_cfg()
    bucket_window = int(anomaly_cfg.get("frequency", {}).get("bucket_window_minutes", 5) or 5)
    frequency_anomalies = []
    silence_gaps = []
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
        buckets = defaultdict(int)
        for ts_val in timestamps:
            bucket_key = _floor_to_window(ts_val, bucket_window)
            buckets[bucket_key] += 1
        frequency_anomalies = _detect_frequency_anomalies(buckets, anomaly_cfg)
        silence_gaps = _detect_silence_gaps(buckets, anomaly_cfg)

    # ---------- Error rate ----------
    total = len(all_logs)
    errors = severity_counts.get("error", 0)
    error_rate = round(errors / total, 4) if total > 0 else 0.0
    spike_threshold = float(anomaly_cfg.get("error_rate", {}).get("spike_threshold", 0.10))
    is_spike = error_rate > spike_threshold

    # ---------- Build result ----------
    result = {
        "project_id": project_id,
        "source": source,
        "query": query,
        "time_range": time_range,
        "analyzed_at": datetime.now(timezone.utc).isoformat() + "Z",
        "total_logs": total,
        "severity_counts": dict(severity_counts),
        "error_rate": error_rate,
        "error_rate_is_spike": is_spike,
        "matched_patterns": matched_patterns,
        "ai_extracted_patterns": ai_extracted_patterns,
        "top_messages": top_messages,
        "frequency_anomalies": frequency_anomalies,
        "silence_gaps": silence_gaps,
        "source_results": source_results,
    }

    # ---------- Record findings in metric_snapshots ----------
    # Gated on project_id alone. The previous guard also required the SQLite
    # file data/icdev.db to exist on disk, which silently dropped every write
    # on a PostgreSQL-primary stack — where that file is absent by design and
    # the real target is PG. The row count is surfaced so a caller can tell
    # persistence apart from a no-op.
    if project_id:
        result["metrics_recorded"] = _record_findings(project_id, result, db_path)

    return result


def _record_findings(project_id: str, analysis: dict, db_path: Path = None) -> int:
    """Store analysis findings as metric snapshots in the database.

    Returns the number of rows written; 0 if the write failed. Never raises —
    a metrics-recording failure must not discard the analysis itself.
    """
    conn = None
    try:
        conn = _get_db(db_path)
        now = datetime.now(timezone.utc).isoformat()

        metrics = {
            "log_error_rate": analysis.get("error_rate", 0.0),
            "log_error_count": float(analysis.get("severity_counts", {}).get("error", 0)),
            "log_warning_count": float(analysis.get("severity_counts", {}).get("warning", 0)),
            "log_total_count": float(analysis.get("total_logs", 0)),
            "log_pattern_match_count": float(sum(p.get("count", 0) for p in analysis.get("matched_patterns", []))),
            "log_anomaly_count": float(len(analysis.get("frequency_anomalies", []))),
            "log_silence_gap_count": float(len(analysis.get("silence_gaps", []))),
        }

        for metric_name, metric_value in metrics.items():
            conn.execute(
                """INSERT INTO metric_snapshots
                   (project_id, metric_name, metric_value, labels, source, collected_at)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (
                    project_id,
                    metric_name,
                    metric_value,
                    json.dumps({"query": analysis.get("query", ""), "time_range": analysis.get("time_range", "")}),
                    "log_analyzer",
                    now,
                ),
            )
        conn.commit()
        return len(metrics)
    except Exception as exc:
        print(f"[log-analyzer] Warning: failed to record findings: {exc}")
        return 0
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="Log Analyzer — queries ELK/Splunk, detects error patterns, records findings"
    )
    parser.add_argument(
        "--source", choices=["elk", "splunk", "both"], default="elk", help="Log source to query (default: elk)"
    )
    parser.add_argument(
        "--query",
        default="level:ERROR OR level:WARN",
        help='Search query string (default: "level:ERROR OR level:WARN")',
    )
    parser.add_argument("--time-range", default="24h", help="Time range for analysis (e.g., 30m, 1h, 24h, 7d)")
    parser.add_argument("--project-id", help="Project identifier (used as index prefix for ELK)")
    parser.add_argument("--elk-url", default=DEFAULT_ELK_URL, help="Elasticsearch URL")
    parser.add_argument("--splunk-url", default=DEFAULT_SPLUNK_URL, help="Splunk URL")
    parser.add_argument("--splunk-token", help="Splunk authentication token")
    parser.add_argument("--db-path", help="Override database path")
    parser.add_argument(
        "--ai-extract",
        action="store_true",
        dest="use_ai_extraction",
        help="Run the NLP extractor over unmatched log lines to surface emergent error categories",
    )
    parser.add_argument(
        "--anomaly-method",
        choices=["zscore", "mad"],
        help="Override the frequency anomaly method from monitoring_config.yaml "
        "(zscore=mean/std-dev, mad=robust median-based)",
    )
    parser.add_argument("--format", choices=["json", "text"], default="text", help="Output format (default: text)")
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    db_path = Path(args.db_path) if args.db_path else None

    anomaly_config = None
    if args.anomaly_method:
        anomaly_config = _load_anomaly_cfg()
        anomaly_config["frequency"]["method"] = args.anomaly_method

    result = analyze_logs(
        source=args.source,
        query=args.query,
        time_range=args.time_range,
        db_path=db_path,
        project_id=args.project_id,
        elk_url=args.elk_url,
        splunk_url=args.splunk_url,
        splunk_token=args.splunk_token,
        use_ai_extraction=args.use_ai_extraction,
        anomaly_config=anomaly_config,
    )

    if args.format == "json":
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"\n{'=' * 60}")
        print(f"  LOG ANALYSIS — {result.get('project_id', 'all')}")
        print(f"  Source: {result['source']} | Time range: {result['time_range']}")
        print(
            f"  Total logs: {result['total_logs']} | Error rate: {result['error_rate'] * 100:.1f}%"
            + (" ** SPIKE **" if result.get("error_rate_is_spike") else "")
        )
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

        # AI-extracted (emergent) patterns
        ai_patterns = result.get("ai_extracted_patterns", [])
        if ai_patterns:
            print(f"\n  AI-EXTRACTED PATTERNS ({len(ai_patterns)} emergent):")
            for p in ai_patterns[:10]:
                print(f"    [{p.get('count', 0):>4d}x] {p['name']}")
                if p.get("description"):
                    print(f"           {p['description'][:80]}")

        # Frequency anomalies
        anomalies = result.get("frequency_anomalies", [])
        if anomalies:
            print(f"\n  FREQUENCY ANOMALIES ({len(anomalies)} detected):")
            for a in anomalies[:5]:
                print(f"    {a['bucket']}: {a['count']} events (z-score: {a['z_score']})")

        # Silence gaps (ingestion outages / missing partitions)
        gaps = result.get("silence_gaps", [])
        if gaps:
            print(f"\n  SILENCE GAPS ({len(gaps)} detected):")
            for g in gaps[:5]:
                print(
                    f"    {g['start']} -> {g['end']}: {g['buckets']} quiet bucket(s) "
                    f"(baseline {g['baseline']}/bucket)"
                )

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
