#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Pain Point Trend Detection for ICDEV Creative Engine — detect trends over time.

Analyzes creative_pain_points entries to identify emerging, active, declining, and
stale trends by clustering pain points with keyword co-occurrence. All analysis is
deterministic (no LLM required), using keyword co-occurrence clustering and
time-series velocity. Air-gap safe.

Architecture:
    - Reads from creative_pain_points table (populated by pain_extractor.py)
    - Deduplicates by keyword_fingerprint (latest row per fingerprint)
    - Groups pain points by category
    - Within each category, clusters by keyword overlap (>= 3 shared keywords)
    - Clusters with >= min_signals become named trends
    - Velocity = signals_per_day over window; acceleration = velocity delta vs previous
    - Stores trends in creative_trends table (append-only new rows per detection cycle, D6)
    - Trend lifecycle: emerging -> active -> declining -> stale

Trend Detection Approach (deterministic, mirrors tools/innovation/trend_detector.py):
    1. Extract top-10 keywords per pain point (TF with stopword removal)
    2. Group pain points by category
    3. Within each category, build keyword co-occurrence matrix
    4. Cluster pain points sharing >= 3 common keywords
    5. Clusters with >= min_signals become named trends
    6. Track velocity and lifecycle transitions over time

Usage:
    python tools/creative/trend_tracker.py --detect --days 30 --min-signals 3 --json
    python tools/creative/trend_tracker.py --report --json
    python tools/creative/trend_tracker.py --velocity --trend-id "ctrend-xxx" --json
    python tools/creative/trend_tracker.py --detect --human
"""

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import uuid
from collections import Counter, defaultdict
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
    from tools.audit.audit_logger import log_event as audit_log_event
    _HAS_AUDIT = True
except ImportError:
    _HAS_AUDIT = False

    def audit_log_event(**kwargs):
        return -1

# =========================================================================
# CONSTANTS
# =========================================================================
# Hardcoded English stopwords (~100 common words) — no NLTK dependency needed
STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "been", "by", "but",
    "can", "could", "did", "do", "does", "for", "from", "had", "has",
    "have", "he", "her", "him", "his", "how", "i", "if", "in", "into",
    "is", "it", "its", "just", "may", "me", "might", "more", "most",
    "must", "my", "no", "nor", "not", "of", "on", "or", "our", "out",
    "own", "re", "s", "she", "should", "so", "some", "such", "t",
    "than", "that", "the", "their", "them", "then", "there", "these",
    "they", "this", "those", "through", "to", "too", "up", "us",
    "very", "was", "we", "were", "what", "when", "where", "which",
    "while", "who", "whom", "why", "will", "with", "would", "you",
    "your", "about", "above", "after", "again", "all", "also", "am",
    "any", "because", "before", "being", "between", "both", "during",
    "each", "few", "further", "get", "got", "here", "how", "itself",
    "let", "like", "make", "many", "much", "new", "now", "off", "old",
    "one", "only", "other", "over", "same", "set", "since", "still",
    "take", "two", "under", "use", "used", "using", "way", "well",
})

# Minimum keyword length to consider
MIN_KEYWORD_LEN = 3
# Top N keywords to extract per pain point
TOP_KEYWORDS_PER_SIGNAL = 10
# Minimum shared keywords to form a cluster
MIN_SHARED_KEYWORDS = 3

# Trend lifecycle velocity thresholds
VELOCITY_ACTIVE = 0.5
VELOCITY_EMERGING = 0.1


# =========================================================================
# HELPERS
# =========================================================================
def _get_db(db_path=None):
    """Get database connection with dict-like row access."""
    path = db_path or DB_PATH
    if not Path(path).exists():
        raise FileNotFoundError(f"Database not found: {path}")
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _now():
    """ISO-8601 timestamp."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _trend_id():
    """Generate unique trend ID with ctrend- prefix."""
    return f"ctrend-{uuid.uuid4().hex[:12]}"


def _keyword_fingerprint(keywords):
    """SHA-256 fingerprint of sorted keyword list for deduplication."""
    canonical = ",".join(sorted(keywords))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


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
    """Load creative config from YAML.

    Returns:
        Dict with full creative_config.yaml contents, or empty dict.
    """
    if not _HAS_YAML:
        return {}
    if not CONFIG_PATH.exists():
        return {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _ensure_trends_table(conn):
    """Create creative_trends table if it does not exist.

    Follows append-only pattern (D6). Trends are inserted as new rows
    per detection cycle to maintain full history.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS creative_trends (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            category TEXT,
            pain_point_ids TEXT NOT NULL DEFAULT '[]',
            signal_count INTEGER NOT NULL DEFAULT 0,
            keyword_fingerprint TEXT NOT NULL,
            keywords TEXT NOT NULL DEFAULT '[]',
            velocity REAL DEFAULT 0.0,
            acceleration REAL DEFAULT 0.0,
            status TEXT NOT NULL DEFAULT 'emerging'
                CHECK(status IN ('emerging','active','declining','stale')),
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            metadata TEXT DEFAULT '{}',
            detected_at TEXT NOT NULL DEFAULT (datetime('now')),
            classification TEXT DEFAULT 'CUI'
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_ctrend_status
        ON creative_trends (status)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_ctrend_velocity
        ON creative_trends (velocity)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_ctrend_fingerprint
        ON creative_trends (keyword_fingerprint)
    """)
    conn.commit()


# =========================================================================
# KEYWORD EXTRACTION
# =========================================================================
_WORD_RE = re.compile(r"[a-z][a-z0-9_\-]{2,}", re.IGNORECASE)


def _extract_keywords(text, top_n=TOP_KEYWORDS_PER_SIGNAL):
    """Extract top-N keywords from text using simple term-frequency.

    Approach:
        1. Lowercase and split into tokens via regex
        2. Remove stopwords and short tokens
        3. Count frequencies
        4. Return top-N by frequency

    Args:
        text: Input text (title + description).
        top_n: Number of top keywords to return.

    Returns:
        List of keyword strings sorted by frequency descending.
    """
    if not text:
        return []
    text_lower = text.lower()
    tokens = _WORD_RE.findall(text_lower)
    filtered = [t for t in tokens if t not in STOPWORDS and len(t) >= MIN_KEYWORD_LEN]
    counts = Counter(filtered)
    return [kw for kw, _ in counts.most_common(top_n)]


# =========================================================================
# TREND DETECTION
# =========================================================================
def detect_trends(days=30, min_signals=3, db_path=None):
    """Detect emerging trends by clustering pain points with keyword co-occurrence.

    Algorithm:
        1. Load config for detection parameters
        2. Query creative_pain_points from last `days` days
        3. Deduplicate by keyword_fingerprint (latest row per fingerprint)
        4. Group pain points by category
        5. Within each category, cluster by keyword overlap (>= 3 shared keywords)
        6. Clusters with >= min_signals become trends
        7. Calculate velocity (signals/day) and acceleration
        8. Determine lifecycle status
        9. INSERT into creative_trends (append-only, new row per detection cycle)

    Args:
        days: How far back to look for pain points.
        min_signals: Minimum number of pain points to form a trend.
        db_path: Optional database path override.

    Returns:
        Dict with trends_detected, new_trends, updated, by_status counts.
    """
    config = _load_config()
    trends_cfg = config.get("trends", {})
    detection_window = trends_cfg.get("detection_window_days", days)
    min_sigs = trends_cfg.get("min_signals_for_trend", min_signals)
    velocity_window = trends_cfg.get("velocity_window_days", 7)
    stale_after = trends_cfg.get("stale_after_days", 60)

    # CLI args override config defaults
    if days != 30:
        detection_window = days
    if min_signals != 3:
        min_sigs = min_signals

    conn = _get_db(db_path)
    _ensure_trends_table(conn)

    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=detection_window)).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        # -----------------------------------------------------------------
        # Step 1: Fetch pain points within the time window
        # -----------------------------------------------------------------
        rows = conn.execute(
            """SELECT id, title, description, category, frequency,
                      keyword_fingerprint, keywords, severity,
                      first_seen, last_seen
               FROM creative_pain_points
               WHERE last_seen >= ?
               ORDER BY last_seen DESC""",
            (cutoff,),
        ).fetchall()

        if not rows:
            return {
                "detected_at": _now(),
                "time_window_days": detection_window,
                "min_signals": min_sigs,
                "pain_points_analyzed": 0,
                "trends_detected": 0,
                "new_trends": 0,
                "updated": 0,
                "by_status": {},
                "trends": [],
            }

        # -----------------------------------------------------------------
        # Step 2: Deduplicate by keyword_fingerprint (keep latest)
        # -----------------------------------------------------------------
        seen_fps = {}
        deduped = []
        for row in rows:
            fp = row["keyword_fingerprint"]
            if fp not in seen_fps:
                seen_fps[fp] = True
                deduped.append(row)

        # -----------------------------------------------------------------
        # Step 3: Build enriched pain point data with keyword sets
        # -----------------------------------------------------------------
        pain_data = []
        for row in deduped:
            title = row["title"] or ""
            desc = row["description"] or ""
            # Use stored keywords if available, else re-extract
            stored_kw = row["keywords"]
            if stored_kw and stored_kw != "[]":
                try:
                    kw_list = json.loads(stored_kw)
                except (json.JSONDecodeError, TypeError):
                    kw_list = _extract_keywords(f"{title} {desc}")
            else:
                kw_list = _extract_keywords(f"{title} {desc}")

            pain_data.append({
                "id": row["id"],
                "title": title,
                "description": desc,
                "category": row["category"],
                "frequency": row["frequency"] or 1,
                "keywords": frozenset(kw_list),
                "severity": row["severity"] or "medium",
                "first_seen": row["first_seen"],
                "last_seen": row["last_seen"],
            })

        # -----------------------------------------------------------------
        # Step 4: Group by category
        # -----------------------------------------------------------------
        by_category = defaultdict(list)
        for pp in pain_data:
            by_category[pp["category"]].append(pp)

        # -----------------------------------------------------------------
        # Step 5: Cluster pain points within each category by keyword overlap
        # -----------------------------------------------------------------
        detected_trends = []
        new_count = 0
        updated_count = 0

        for category, points in by_category.items():
            if len(points) < min_sigs:
                continue

            # Greedy clustering: pick seed, find all sharing >= 3 keywords
            unclustered = list(range(len(points)))
            clusters = []

            while unclustered:
                seed_idx = unclustered[0]
                seed_kw = points[seed_idx]["keywords"]
                if not seed_kw:
                    unclustered.remove(seed_idx)
                    continue

                cluster_indices = [seed_idx]

                # Find points sharing enough keywords with the seed
                remaining = [i for i in unclustered if i != seed_idx]
                for idx in remaining:
                    shared = seed_kw & points[idx]["keywords"]
                    if len(shared) >= MIN_SHARED_KEYWORDS:
                        cluster_indices.append(idx)

                # Remove clustered points from unclustered pool
                for idx in cluster_indices:
                    if idx in unclustered:
                        unclustered.remove(idx)

                if len(cluster_indices) >= min_sigs:
                    clusters.append(cluster_indices)

            # -----------------------------------------------------------------
            # Step 6: Build trend objects from clusters
            # -----------------------------------------------------------------
            for cluster_indices in clusters:
                cluster_points = [points[i] for i in cluster_indices]

                # Find common keywords using frequency-based top selection
                kw_counter = Counter()
                for pp in cluster_points:
                    for kw in pp["keywords"]:
                        kw_counter[kw] += 1
                # Keywords appearing in majority of pain points
                majority = max(len(cluster_points) // 2, 2)
                common_keywords = sorted(
                    [kw for kw, cnt in kw_counter.items() if cnt >= majority],
                    key=lambda k: -kw_counter[k],
                )[:10]

                if len(common_keywords) < MIN_SHARED_KEYWORDS:
                    continue

                fingerprint = _keyword_fingerprint(common_keywords)
                pain_point_ids = [pp["id"] for pp in cluster_points]
                signal_count = sum(pp["frequency"] for pp in cluster_points)

                # Dates
                dates = sorted(pp["last_seen"] for pp in cluster_points if pp["last_seen"])
                first_dates = sorted(pp["first_seen"] for pp in cluster_points if pp["first_seen"])
                first_seen = first_dates[0] if first_dates else _now()
                last_seen = dates[-1] if dates else _now()

                # Auto-generate name from top 3 keywords
                name = " + ".join(common_keywords[:3])

                # -----------------------------------------------------------------
                # Step 7: Velocity and acceleration
                # -----------------------------------------------------------------
                velocity = signal_count / max(detection_window, 1)

                # Check existing trend by keyword_fingerprint for acceleration
                existing = conn.execute(
                    """SELECT id, velocity, status, signal_count
                       FROM creative_trends
                       WHERE keyword_fingerprint = ?
                       ORDER BY detected_at DESC
                       LIMIT 1""",
                    (fingerprint,),
                ).fetchone()

                if existing:
                    old_velocity = existing["velocity"] or 0.0
                    acceleration = velocity - old_velocity
                else:
                    acceleration = 0.0

                # -----------------------------------------------------------------
                # Step 8: Lifecycle status based on velocity
                # -----------------------------------------------------------------
                status = _determine_lifecycle(
                    velocity, acceleration, last_seen, now, stale_after,
                )

                # Severity distribution for metadata
                severity_dist = Counter(pp["severity"] for pp in cluster_points)

                trend = {
                    "id": _trend_id(),
                    "name": name,
                    "category": category,
                    "pain_point_ids": pain_point_ids,
                    "signal_count": signal_count,
                    "keyword_fingerprint": fingerprint,
                    "keywords": common_keywords,
                    "velocity": round(velocity, 4),
                    "acceleration": round(acceleration, 4),
                    "status": status,
                    "first_seen": first_seen,
                    "last_seen": last_seen,
                    "metadata": {
                        "severity_distribution": dict(severity_dist),
                        "pain_point_count": len(cluster_points),
                        "common_keyword_count": len(common_keywords),
                    },
                    "is_new": existing is None,
                }

                # INSERT new row (append-only, D6)
                _store_trend(conn, trend)
                detected_trends.append(trend)

                if existing is None:
                    new_count += 1
                else:
                    updated_count += 1

        # -----------------------------------------------------------------
        # Step 9: Mark stale trends — no new pain points in stale_after_days
        # -----------------------------------------------------------------
        stale_cutoff = (now - timedelta(days=stale_after)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        # Find stale trends (latest row per fingerprint that hasn't been updated)
        stale_candidates = conn.execute(
            """SELECT DISTINCT keyword_fingerprint
               FROM creative_trends
               WHERE status IN ('emerging', 'active')
                 AND last_seen < ?""",
            (stale_cutoff,),
        ).fetchall()
        for row in stale_candidates:
            fp = row["keyword_fingerprint"]
            # Only mark stale if no recent detection refreshed it
            already_fresh = any(
                t["keyword_fingerprint"] == fp for t in detected_trends
            )
            if not already_fresh:
                # Insert a stale row to preserve history (append-only)
                latest = conn.execute(
                    """SELECT * FROM creative_trends
                       WHERE keyword_fingerprint = ?
                       ORDER BY detected_at DESC LIMIT 1""",
                    (fp,),
                ).fetchone()
                if latest and latest["status"] != "stale":
                    stale_trend = {
                        "id": _trend_id(),
                        "name": latest["name"],
                        "category": latest["category"],
                        "pain_point_ids": json.loads(latest["pain_point_ids"])
                            if latest["pain_point_ids"] else [],
                        "signal_count": latest["signal_count"],
                        "keyword_fingerprint": fp,
                        "keywords": json.loads(latest["keywords"])
                            if latest["keywords"] else [],
                        "velocity": 0.0,
                        "acceleration": 0.0 - (latest["velocity"] or 0.0),
                        "status": "stale",
                        "first_seen": latest["first_seen"],
                        "last_seen": latest["last_seen"],
                        "metadata": {"marked_stale": True},
                        "is_new": False,
                    }
                    _store_trend(conn, stale_trend)

        # Build status summary
        status_counts = Counter(t["status"] for t in detected_trends)

        _audit(
            "creative.trend_detection",
            "creative-engine",
            f"Detected {len(detected_trends)} trends from {len(deduped)} pain points",
            {
                "trends_detected": len(detected_trends),
                "pain_points_analyzed": len(deduped),
                "new_trends": new_count,
                "updated": updated_count,
                "time_window_days": detection_window,
                "min_signals": min_sigs,
            },
        )

        return {
            "detected_at": _now(),
            "time_window_days": detection_window,
            "min_signals": min_sigs,
            "pain_points_analyzed": len(deduped),
            "trends_detected": len(detected_trends),
            "new_trends": new_count,
            "updated": updated_count,
            "by_status": dict(status_counts),
            "trends": detected_trends,
        }
    finally:
        conn.close()


def _determine_lifecycle(velocity, acceleration, last_seen, now, stale_after):
    """Determine trend lifecycle status based on velocity thresholds.

    Lifecycle rules (from config thresholds):
        - velocity > 0.5: "active"
        - velocity > 0.1: "emerging"
        - velocity > 0 and declining (negative acceleration): "declining"
        - velocity == 0 for > stale_after_days: "stale"

    Args:
        velocity: Signals per day in current window.
        acceleration: Change in velocity vs previous detection cycle.
        last_seen: ISO timestamp of the most recent pain point.
        now: Current datetime (UTC).
        stale_after: Number of days after which a trend is considered stale.

    Returns:
        Status string: 'emerging', 'active', 'declining', or 'stale'.
    """
    # Check for stale — no new pain points in stale_after days
    try:
        last_dt = datetime.strptime(last_seen[:19], "%Y-%m-%dT%H:%M:%S")
        last_dt = last_dt.replace(tzinfo=timezone.utc)
        days_since = (now - last_dt).days
    except (ValueError, TypeError):
        days_since = 999

    if days_since >= stale_after:
        return "stale"

    # Velocity == 0 and old enough
    if velocity <= 0 and days_since >= stale_after:
        return "stale"

    # Active: high velocity
    if velocity > VELOCITY_ACTIVE:
        return "active"

    # Emerging: moderate velocity
    if velocity > VELOCITY_EMERGING:
        # If declining acceleration, mark as declining instead
        if acceleration < -0.01:
            return "declining"
        return "emerging"

    # Low velocity with negative acceleration = declining
    if velocity > 0 and acceleration < -0.01:
        return "declining"

    # Low but positive velocity
    if velocity > 0:
        return "emerging"

    return "stale"


def _store_trend(conn, trend):
    """Store a trend as a new row in creative_trends (append-only, D6).

    Each detection cycle inserts a NEW row. Historical rows are preserved
    for trend-over-time analysis.

    Args:
        conn: SQLite connection.
        trend: Trend dict with all fields.
    """
    conn.execute(
        """INSERT INTO creative_trends
           (id, name, category, pain_point_ids, signal_count,
            keyword_fingerprint, keywords, velocity, acceleration,
            status, first_seen, last_seen, metadata, detected_at,
            classification)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            trend["id"],
            trend["name"],
            trend["category"],
            json.dumps(trend["pain_point_ids"]),
            trend["signal_count"],
            trend["keyword_fingerprint"],
            json.dumps(trend["keywords"]),
            trend["velocity"],
            trend["acceleration"],
            trend["status"],
            trend["first_seen"],
            trend["last_seen"],
            json.dumps(trend["metadata"]),
            _now(),
            "CUI",
        ),
    )
    conn.commit()


# =========================================================================
# TREND REPORT
# =========================================================================
def get_trend_report(db_path=None):
    """Generate a summary report of all tracked trends.

    Reads from creative_trends table, deduplicates by keyword_fingerprint
    (keeps latest row per fingerprint), groups by status and category,
    includes top trending by velocity.

    Args:
        db_path: Optional database path override.

    Returns:
        Dict with total, by_status, top_trending, by_category.
    """
    conn = _get_db(db_path)
    _ensure_trends_table(conn)

    try:
        rows = conn.execute(
            """SELECT id, name, category, pain_point_ids, signal_count,
                      keyword_fingerprint, keywords, velocity, acceleration,
                      status, first_seen, last_seen, metadata, detected_at
               FROM creative_trends
               ORDER BY detected_at DESC"""
        ).fetchall()

        if not rows:
            return {
                "generated_at": _now(),
                "total": 0,
                "by_status": {},
                "top_trending": [],
                "by_category": {},
                "summary": "No trends detected yet. Run --detect first.",
            }

        # Deduplicate by keyword_fingerprint (latest row wins)
        seen_fps = {}
        deduped = []
        for row in rows:
            fp = row["keyword_fingerprint"]
            if fp not in seen_fps:
                seen_fps[fp] = True
                trend = {
                    "id": row["id"],
                    "name": row["name"],
                    "category": row["category"],
                    "pain_point_ids": json.loads(row["pain_point_ids"])
                        if row["pain_point_ids"] else [],
                    "signal_count": row["signal_count"],
                    "keywords": json.loads(row["keywords"])
                        if row["keywords"] else [],
                    "velocity": row["velocity"],
                    "acceleration": row["acceleration"],
                    "status": row["status"],
                    "first_seen": row["first_seen"],
                    "last_seen": row["last_seen"],
                    "metadata": json.loads(row["metadata"])
                        if row["metadata"] else {},
                    "detected_at": row["detected_at"],
                }
                deduped.append(trend)

        # Group by status
        by_status = defaultdict(list)
        by_category = defaultdict(list)
        for trend in deduped:
            by_status[trend["status"]].append(_trend_summary(trend))
            cat = trend["category"] or "uncategorized"
            by_category[cat].append(_trend_summary(trend))

        # Top trending (by velocity, descending)
        top_trending = sorted(deduped, key=lambda t: t["velocity"], reverse=True)[:10]

        # Status counts
        status_counts = {
            "emerging": len(by_status.get("emerging", [])),
            "active": len(by_status.get("active", [])),
            "declining": len(by_status.get("declining", [])),
            "stale": len(by_status.get("stale", [])),
        }

        total = len(deduped)
        avg_velocity = (
            sum(t["velocity"] for t in deduped) / total if total else 0
        )
        total_signals = sum(t["signal_count"] for t in deduped)

        # Summary text
        parts = []
        for s, c in status_counts.items():
            if c > 0:
                parts.append(f"{c} {s}")
        summary_text = f"{total} trends tracked: {', '.join(parts)}." if parts else "No trends."

        _audit(
            "creative.trend_report",
            "creative-engine",
            f"Generated trend report: {total} trends",
            {"total": total, "status_counts": status_counts},
        )

        return {
            "generated_at": _now(),
            "total": total,
            "by_status": dict(by_status),
            "top_trending": [_trend_summary(t) for t in top_trending],
            "by_category": dict(by_category),
            "statistics": {
                "total_signals_in_trends": total_signals,
                "average_velocity": round(avg_velocity, 4),
                "categories_with_trends": len(by_category),
                "status_counts": status_counts,
            },
            "summary": summary_text,
        }
    finally:
        conn.close()


def _trend_summary(trend):
    """Create a compact summary dict from a trend for report output."""
    return {
        "id": trend["id"],
        "name": trend["name"],
        "category": trend.get("category", ""),
        "signal_count": trend["signal_count"],
        "velocity": trend["velocity"],
        "acceleration": trend["acceleration"],
        "status": trend["status"],
        "keywords": trend.get("keywords", [])[:5],
        "first_seen": trend.get("first_seen", ""),
        "last_seen": trend.get("last_seen", ""),
    }


# =========================================================================
# TREND VELOCITY
# =========================================================================
def get_velocity(trend_id, db_path=None):
    """Get velocity metrics for a specific trend.

    Retrieves the trend from creative_trends and returns its velocity,
    acceleration, status, signal count, and timestamps.

    Args:
        trend_id: The trend ID (ctrend-xxx) to look up.
        db_path: Optional database path override.

    Returns:
        Dict with trend_id, name, velocity, acceleration, status,
        signal_count, last_seen, and direction label.
    """
    conn = _get_db(db_path)
    _ensure_trends_table(conn)

    try:
        row = conn.execute(
            """SELECT id, name, category, pain_point_ids, signal_count,
                      keyword_fingerprint, keywords, velocity, acceleration,
                      status, first_seen, last_seen, metadata, detected_at
               FROM creative_trends
               WHERE id = ?""",
            (trend_id,),
        ).fetchone()

        if not row:
            return {"error": f"Trend not found: {trend_id}"}

        keywords = json.loads(row["keywords"]) if row["keywords"] else []
        pain_point_ids = json.loads(row["pain_point_ids"]) if row["pain_point_ids"] else []
        metadata = json.loads(row["metadata"]) if row["metadata"] else {}

        # Direction label based on acceleration
        accel = row["acceleration"] or 0.0
        if accel > 0.01:
            direction = "accelerating"
        elif accel < -0.01:
            direction = "decelerating"
        else:
            direction = "steady"

        # Projection: if velocity > 0, estimate signals in next 7 days
        current_velocity = row["velocity"] or 0.0
        projected_7d = round(current_velocity * 7, 1) if current_velocity > 0 else 0

        # Historical velocity data (all rows for this fingerprint)
        fp = row["keyword_fingerprint"]
        history_rows = conn.execute(
            """SELECT velocity, acceleration, status, signal_count, detected_at
               FROM creative_trends
               WHERE keyword_fingerprint = ?
               ORDER BY detected_at ASC""",
            (fp,),
        ).fetchall()

        velocity_history = [
            {
                "velocity": hr["velocity"],
                "acceleration": hr["acceleration"],
                "status": hr["status"],
                "signal_count": hr["signal_count"],
                "detected_at": hr["detected_at"],
            }
            for hr in history_rows
        ]

        _audit(
            "creative.trend_velocity",
            "creative-engine",
            f"Velocity check for trend {trend_id}: {current_velocity:.4f} sig/day",
            {"trend_id": trend_id, "velocity": current_velocity, "direction": direction},
        )

        return {
            "trend_id": row["id"],
            "name": row["name"],
            "category": row["category"],
            "velocity": current_velocity,
            "acceleration": accel,
            "direction": direction,
            "status": row["status"],
            "signal_count": row["signal_count"],
            "first_seen": row["first_seen"],
            "last_seen": row["last_seen"],
            "keywords": keywords,
            "pain_point_ids": pain_point_ids,
            "projection": {
                "next_7d_signals": projected_7d,
                "note": "Linear projection based on current velocity",
            },
            "velocity_history": velocity_history,
            "metadata": metadata,
        }
    finally:
        conn.close()


# =========================================================================
# CLI — HUMAN OUTPUT
# =========================================================================
def _print_human(args, result):
    """Print human-readable output for CLI."""
    if "error" in result:
        print(f"ERROR: {result['error']}")
        return

    if args.detect:
        print(f"CUI // SP-CTI")
        print(f"Creative Trend Detection -- {result.get('detected_at', '')}")
        print(f"  Pain points analyzed: {result.get('pain_points_analyzed', 0)}")
        print(f"  Trends detected:      {result.get('trends_detected', 0)}")
        print(f"  New trends:           {result.get('new_trends', 0)}")
        print(f"  Updated trends:       {result.get('updated', 0)}")
        print(f"  Time window:          {result.get('time_window_days', 0)} days")
        print(f"  Min signals:          {result.get('min_signals', 0)}")
        print()
        by_status = result.get("by_status", {})
        if by_status:
            parts = [f"{s}={c}" for s, c in sorted(by_status.items())]
            print(f"  Status breakdown: {', '.join(parts)}")
            print()
        for trend in result.get("trends", []):
            status_icon = {
                "emerging": "[NEW]",
                "active": "[ACT]",
                "declining": "[DEC]",
                "stale": "[OLD]",
            }.get(trend["status"], "[???]")
            print(
                f"  {status_icon} {trend['name']}"
                f"  ({trend['category']}, {trend['signal_count']} signals,"
                f" v={trend['velocity']:.4f}/day)"
            )
            kws = ", ".join(trend.get("keywords", [])[:5])
            print(f"         keywords: {kws}")

    elif args.report:
        print(f"CUI // SP-CTI")
        print(f"Creative Trend Report -- {result.get('generated_at', '')}")
        print(f"  {result.get('summary', '')}")
        stats = result.get("statistics", {})
        print(f"  Total signals in trends: {stats.get('total_signals_in_trends', 0)}")
        print(f"  Average velocity:        {stats.get('average_velocity', 0):.4f} sig/day")
        print(f"  Categories with trends:  {stats.get('categories_with_trends', 0)}")
        print()
        print("  Top Trending:")
        for t in result.get("top_trending", [])[:10]:
            print(
                f"    [{t['status'][:3].upper()}] {t['name']}"
                f"  -- {t['signal_count']} signals, v={t['velocity']:.4f}/day"
            )
        print()
        print("  By Category:")
        for cat, trends in result.get("by_category", {}).items():
            print(f"    {cat}: {len(trends)} trends")

    elif args.velocity:
        print(f"CUI // SP-CTI")
        print(f"Trend Velocity -- {result.get('name', '')}")
        print(f"  ID:           {result.get('trend_id', '')}")
        print(f"  Category:     {result.get('category', '')}")
        print(f"  Status:       {result.get('status', '')}")
        print(f"  Direction:    {result.get('direction', '')}")
        print(f"  Velocity:     {result.get('velocity', 0):.4f} signals/day")
        print(f"  Acceleration: {result.get('acceleration', 0):.4f}")
        print(f"  Signals:      {result.get('signal_count', 0)}")
        print(f"  First seen:   {result.get('first_seen', '')}")
        print(f"  Last seen:    {result.get('last_seen', '')}")
        proj = result.get("projection", {})
        print(f"  Projected 7d: {proj.get('next_7d_signals', 0)} signals")
        print()
        kws = ", ".join(result.get("keywords", [])[:8])
        print(f"  Keywords: {kws}")
        print()
        history = result.get("velocity_history", [])
        if history:
            print("  Velocity History:")
            for entry in history[-10:]:
                print(
                    f"    {entry['detected_at']}: v={entry['velocity']:.4f}"
                    f"  a={entry['acceleration']:.4f}  [{entry['status']}]"
                    f"  ({entry['signal_count']} signals)"
                )


# =========================================================================
# CLI
# =========================================================================
def main():
    parser = argparse.ArgumentParser(
        description="ICDEV Creative Trend Tracker -- detect pain point trends over time"
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable output")
    parser.add_argument(
        "--db-path", type=Path, default=None, help="Database path override"
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--detect", action="store_true",
        help="Detect trends from pain points within the time window"
    )
    group.add_argument(
        "--report", action="store_true",
        help="Generate a summary report of all tracked trends"
    )
    group.add_argument(
        "--velocity", action="store_true",
        help="Get velocity metrics for a specific trend (requires --trend-id)"
    )

    parser.add_argument(
        "--days", type=int, default=30,
        help="Time window in days for trend detection (default: 30)"
    )
    parser.add_argument(
        "--min-signals", type=int, default=3,
        help="Minimum pain points to form a trend (default: 3)"
    )
    parser.add_argument(
        "--trend-id", type=str, default=None,
        help="Trend ID for --velocity command"
    )

    args = parser.parse_args()

    try:
        if args.detect:
            result = detect_trends(
                days=args.days,
                min_signals=args.min_signals,
                db_path=args.db_path,
            )
        elif args.report:
            result = get_trend_report(db_path=args.db_path)
        elif args.velocity:
            if not args.trend_id:
                print("ERROR: --velocity requires --trend-id", file=sys.stderr)
                sys.exit(1)
            result = get_velocity(
                trend_id=args.trend_id, db_path=args.db_path
            )
        else:
            result = {"error": "No action specified"}

        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            _print_human(args, result)

    except FileNotFoundError as e:
        error = {"error": str(e)}
        if args.json:
            print(json.dumps(error, indent=2))
        else:
            print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        error = {"error": str(e)}
        if args.json:
            print(json.dumps(error, indent=2))
        else:
            print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
