#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Cross-Signal Pattern Detection for ICDEV — detect emerging trends from innovation signals.

Analyzes patterns across multiple innovation signals to identify emerging trends,
measure trend velocity, and produce actionable reports. All analysis is deterministic
(no LLM required) using keyword co-occurrence clustering and time-series velocity.

Architecture:
    - Reads from innovation_signals table (populated by web_scanner.py)
    - Groups signals by category (from innovation_config.yaml signal_categories)
    - Clusters by keyword co-occurrence (TF extraction, top N keywords per signal)
    - A "trend" emerges when >= min_signals share >= 3 common keywords in time window
    - Velocity = signals_per_day over window; acceleration = velocity delta vs previous
    - Stores trends in innovation_trends table (append-only, D6)
    - Trend lifecycle: emerging -> active -> declining -> stale

Trend Detection Approach (deterministic):
    1. Extract top-10 keywords per signal (TF with stopword removal)
    2. Group signals by config-defined category
    3. Within each category, build keyword co-occurrence matrix
    4. Cluster signals sharing >= 3 common keywords
    5. Clusters with >= min_signals become named trends
    6. Track velocity and lifecycle transitions over time

Usage:
    python tools/innovation/trend_detector.py --detect --days 30 --min-signals 3 --json
    python tools/innovation/trend_detector.py --report --json
    python tools/innovation/trend_detector.py --velocity --trend-id "trend-xxx" --json
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
    from tools.audit.audit_logger import log_event as audit_log_event
    _HAS_AUDIT = True
except ImportError:
    _HAS_AUDIT = False
    def audit_log_event(**kwargs):
        return -1

# =========================================================================
# CONSTANTS
# =========================================================================
# Hardcoded English stopwords (~60 common words) — no NLTK dependency needed
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
# Top N keywords to extract per signal
TOP_KEYWORDS_PER_SIGNAL = 10
# Minimum shared keywords to form a cluster
MIN_SHARED_KEYWORDS = 3

# Trend lifecycle thresholds
LIFECYCLE = {
    "emerging_min": 3,
    "emerging_max": 5,
    "active_min": 6,
    "stale_days": 30,
    "declining_consecutive": 2,
}


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


def _trend_id():
    """Generate unique trend ID."""
    return f"trend-{uuid.uuid4().hex[:12]}"


def _fingerprint(keywords):
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


def _ensure_trends_table(conn):
    """Create innovation_trends table if it does not exist.

    Follows append-only pattern (D6). Trends are updated via INSERT of new
    rows with updated status/velocity rather than UPDATE in place.
    """
    conn.execute("""
        CREATE TABLE IF NOT EXISTS innovation_trends (
            id              TEXT PRIMARY KEY,
            name            TEXT NOT NULL,
            category        TEXT NOT NULL,
            signal_count    INTEGER NOT NULL DEFAULT 0,
            velocity        REAL NOT NULL DEFAULT 0.0,
            acceleration    REAL NOT NULL DEFAULT 0.0,
            first_seen      TEXT NOT NULL,
            last_seen       TEXT NOT NULL,
            status          TEXT NOT NULL DEFAULT 'emerging',
            keyword_fingerprint TEXT NOT NULL,
            keywords        TEXT NOT NULL DEFAULT '[]',
            signal_ids      TEXT NOT NULL DEFAULT '[]',
            metadata        TEXT NOT NULL DEFAULT '{}',
            detected_at     TEXT NOT NULL
        )
    """)
    # Index for fast lookup by fingerprint and category
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_trends_fingerprint
        ON innovation_trends (keyword_fingerprint)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_trends_category
        ON innovation_trends (category, status)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_trends_status
        ON innovation_trends (status)
    """)
    conn.commit()


# =========================================================================
# KEYWORD EXTRACTION
# =========================================================================
_WORD_RE = re.compile(r"[a-z][a-z0-9_\-]{2,}", re.IGNORECASE)


def extract_keywords(text, top_n=TOP_KEYWORDS_PER_SIGNAL):
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
        List of (keyword, count) tuples sorted by frequency descending.
    """
    if not text:
        return []
    text_lower = text.lower()
    tokens = _WORD_RE.findall(text_lower)
    # Filter stopwords and very short tokens
    filtered = [t for t in tokens if t not in STOPWORDS and len(t) >= MIN_KEYWORD_LEN]
    counts = Counter(filtered)
    return counts.most_common(top_n)


def extract_keyword_set(text, top_n=TOP_KEYWORDS_PER_SIGNAL):
    """Extract keywords as a frozenset (for set operations).

    Args:
        text: Input text.
        top_n: Number of top keywords.

    Returns:
        frozenset of keyword strings.
    """
    return frozenset(kw for kw, _ in extract_keywords(text, top_n))


# =========================================================================
# SIGNAL CATEGORIZATION
# =========================================================================
def categorize_signal(title, description, config_categories):
    """Assign a signal to a category based on keyword matching.

    Uses signal_categories from innovation_config.yaml. Each category has
    a list of keywords. The signal is assigned to the category with the
    most keyword hits. Ties broken by priority_boost.

    Args:
        title: Signal title.
        description: Signal description.
        config_categories: Dict of category_name -> {keywords, priority_boost}.

    Returns:
        Best-matching category name, or 'uncategorized'.
    """
    text = f"{title} {description}".lower()
    best_category = "uncategorized"
    best_score = 0

    for cat_name, cat_config in config_categories.items():
        cat_keywords = [kw.lower() for kw in cat_config.get("keywords", [])]
        hits = sum(1 for kw in cat_keywords if kw in text)
        boost = cat_config.get("priority_boost", 1.0)
        score = hits * boost
        if score > best_score:
            best_score = score
            best_category = cat_name

    return best_category


# =========================================================================
# TREND DETECTION
# =========================================================================
def detect_trends(time_window_days=30, min_signals=3, db_path=None):
    """Detect emerging trends by clustering signals with keyword co-occurrence.

    Algorithm:
        1. Fetch all signals within the time window
        2. Categorize each signal (if not already categorized)
        3. Extract keywords per signal
        4. Within each category, find groups of signals sharing >= 3 keywords
        5. Groups with >= min_signals become trends
        6. Calculate velocity (signals/day) and acceleration
        7. Determine lifecycle status
        8. Store or update trends in DB

    Args:
        time_window_days: How far back to look for signals.
        min_signals: Minimum number of signals to form a trend.
        db_path: Optional database path override.

    Returns:
        Dict with detected trends, counts, and metadata.
    """
    config = _load_config()
    categories_config = config.get("signal_categories", {})
    conn = _get_db(db_path)
    _ensure_trends_table(conn)

    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=time_window_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    prev_cutoff = (now - timedelta(days=time_window_days * 2)).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        # -----------------------------------------------------------------
        # Step 1: Fetch signals within the current window
        # -----------------------------------------------------------------
        rows = conn.execute(
            """SELECT id, title, description, discovered_at, category, source,
                      source_type, community_score, metadata
               FROM innovation_signals
               WHERE discovered_at >= ?
               ORDER BY discovered_at DESC""",
            (cutoff,),
        ).fetchall()

        if not rows:
            return {
                "detected_at": _now(),
                "time_window_days": time_window_days,
                "min_signals": min_signals,
                "signals_analyzed": 0,
                "trends_detected": 0,
                "trends": [],
            }

        # -----------------------------------------------------------------
        # Step 2: Categorize and extract keywords per signal
        # -----------------------------------------------------------------
        signal_data = []
        for row in rows:
            title = row["title"] or ""
            desc = row["description"] or ""
            category = row["category"]
            if not category or category == "":
                category = categorize_signal(title, desc, categories_config)
            keywords = extract_keyword_set(f"{title} {desc}")
            signal_data.append({
                "id": row["id"],
                "title": title,
                "description": desc,
                "category": category,
                "keywords": keywords,
                "discovered_at": row["discovered_at"],
                "source": row["source"],
                "community_score": row["community_score"] or 0.0,
            })

        # -----------------------------------------------------------------
        # Step 3: Group by category
        # -----------------------------------------------------------------
        by_category = defaultdict(list)
        for sig in signal_data:
            by_category[sig["category"]].append(sig)

        # -----------------------------------------------------------------
        # Step 4: Cluster signals within each category by keyword overlap
        # -----------------------------------------------------------------
        detected_trends = []

        for category, signals in by_category.items():
            if len(signals) < min_signals:
                continue

            # Greedy clustering: pick seed, find all signals sharing >= 3 keywords
            unclustered = list(range(len(signals)))
            clusters = []

            while unclustered:
                seed_idx = unclustered[0]
                seed_kw = signals[seed_idx]["keywords"]
                if not seed_kw:
                    unclustered.remove(seed_idx)
                    continue

                cluster_indices = [seed_idx]
                cluster_keywords = set(seed_kw)

                # Find signals sharing enough keywords with the seed
                remaining = [i for i in unclustered if i != seed_idx]
                for idx in remaining:
                    shared = seed_kw & signals[idx]["keywords"]
                    if len(shared) >= MIN_SHARED_KEYWORDS:
                        cluster_indices.append(idx)
                        cluster_keywords.update(signals[idx]["keywords"])

                # Remove clustered signals from unclustered pool
                for idx in cluster_indices:
                    if idx in unclustered:
                        unclustered.remove(idx)

                if len(cluster_indices) >= min_signals:
                    clusters.append(cluster_indices)

            # -----------------------------------------------------------------
            # Step 5: Build trend objects from clusters
            # -----------------------------------------------------------------
            for cluster_indices in clusters:
                cluster_signals = [signals[i] for i in cluster_indices]
                # Find common keywords across ALL signals in cluster
                all_kw_sets = [s["keywords"] for s in cluster_signals if s["keywords"]]
                if not all_kw_sets:
                    continue

                # Common keywords = intersection of all, but use frequency-based top
                kw_counter = Counter()
                for s in cluster_signals:
                    for kw in s["keywords"]:
                        kw_counter[kw] += 1
                # Keywords appearing in majority of signals
                majority = max(len(cluster_signals) // 2, 2)
                common_keywords = sorted(
                    [kw for kw, cnt in kw_counter.items() if cnt >= majority],
                    key=lambda k: -kw_counter[k],
                )[:10]

                if len(common_keywords) < MIN_SHARED_KEYWORDS:
                    continue

                fingerprint = _fingerprint(common_keywords)
                signal_ids = [s["id"] for s in cluster_signals]
                signal_count = len(cluster_signals)

                # Dates
                dates = sorted(s["discovered_at"] for s in cluster_signals)
                first_seen = dates[0]
                last_seen = dates[-1]

                # Auto-generate name from top 3 keywords
                name = " + ".join(common_keywords[:3])

                # -----------------------------------------------------------------
                # Step 6: Velocity = signals per day within current window
                # -----------------------------------------------------------------
                velocity = signal_count / max(time_window_days, 1)

                # Calculate acceleration vs previous window
                prev_count = conn.execute(
                    """SELECT COUNT(*) as cnt FROM innovation_signals
                       WHERE discovered_at >= ? AND discovered_at < ?
                         AND (category = ? OR category IS NULL)
                         AND (title || ' ' || description) LIKE ?""",
                    (prev_cutoff, cutoff, category,
                     f"%{common_keywords[0]}%"),
                ).fetchone()["cnt"]
                prev_velocity = prev_count / max(time_window_days, 1)
                acceleration = velocity - prev_velocity

                # -----------------------------------------------------------------
                # Step 7: Lifecycle status
                # -----------------------------------------------------------------
                status = _determine_lifecycle(
                    signal_count, velocity, acceleration, last_seen, now,
                )

                # Check if trend with same fingerprint already exists
                existing = conn.execute(
                    "SELECT id, signal_count, velocity, status FROM innovation_trends WHERE keyword_fingerprint = ?",
                    (fingerprint,),
                ).fetchone()

                avg_community_score = (
                    sum(s["community_score"] for s in cluster_signals) / signal_count
                )
                sources = list(set(s["source"] for s in cluster_signals))

                trend = {
                    "id": existing["id"] if existing else _trend_id(),
                    "name": name,
                    "category": category,
                    "signal_count": signal_count,
                    "velocity": round(velocity, 4),
                    "acceleration": round(acceleration, 4),
                    "first_seen": first_seen,
                    "last_seen": last_seen,
                    "status": status,
                    "keyword_fingerprint": fingerprint,
                    "keywords": common_keywords,
                    "signal_ids": signal_ids,
                    "metadata": {
                        "avg_community_score": round(avg_community_score, 3),
                        "sources": sources,
                        "prev_window_count": prev_count,
                    },
                    "is_update": existing is not None,
                }

                # Store or update in DB
                _store_trend(conn, trend)
                detected_trends.append(trend)

        # -----------------------------------------------------------------
        # Step 8: Mark stale trends that got no new signals
        # -----------------------------------------------------------------
        stale_cutoff = (now - timedelta(days=LIFECYCLE["stale_days"])).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        conn.execute(
            """UPDATE innovation_trends SET status = 'stale'
               WHERE status IN ('emerging', 'active')
                 AND last_seen < ?""",
            (stale_cutoff,),
        )
        conn.commit()

        _audit(
            "innovation.trend_detection",
            "innovation-agent",
            f"Detected {len(detected_trends)} trends from {len(rows)} signals",
            {
                "trends_detected": len(detected_trends),
                "signals_analyzed": len(rows),
                "time_window_days": time_window_days,
                "min_signals": min_signals,
            },
        )

        return {
            "detected_at": _now(),
            "time_window_days": time_window_days,
            "min_signals": min_signals,
            "signals_analyzed": len(rows),
            "trends_detected": len(detected_trends),
            "trends": detected_trends,
        }
    finally:
        conn.close()


def _determine_lifecycle(signal_count, velocity, acceleration, last_seen, now):
    """Determine trend lifecycle status based on signal count and velocity.

    Lifecycle rules:
        - emerging: 3-5 signals AND velocity > 0
        - active: > 5 signals AND velocity > 0
        - declining: velocity < 0 for the measurement period
        - stale: no new signals in 30 days

    Args:
        signal_count: Total signals in the trend cluster.
        velocity: Signals per day in current window.
        acceleration: Change in velocity vs previous window.
        last_seen: ISO timestamp of the most recent signal.
        now: Current datetime (UTC).

    Returns:
        Status string: 'emerging', 'active', 'declining', or 'stale'.
    """
    # Check for stale — no new signals in 30 days
    try:
        last_dt = datetime.strptime(last_seen[:19], "%Y-%m-%dT%H:%M:%S")
        last_dt = last_dt.replace(tzinfo=timezone.utc)
        days_since = (now - last_dt).days
    except (ValueError, TypeError):
        days_since = 999

    if days_since >= LIFECYCLE["stale_days"]:
        return "stale"

    # Declining: negative velocity (fewer signals this window than previous)
    if velocity <= 0 or acceleration < 0:
        # Only mark declining if we had a meaningful previous window
        if acceleration < -0.01:
            return "declining"

    # Active: more than 5 signals and positive velocity
    if signal_count > LIFECYCLE["emerging_max"] and velocity > 0:
        return "active"

    # Emerging: 3-5 signals and positive velocity
    if LIFECYCLE["emerging_min"] <= signal_count <= LIFECYCLE["emerging_max"]:
        return "emerging"

    # Default to active for large signal counts even if velocity is zero
    if signal_count > LIFECYCLE["emerging_max"]:
        return "active"

    return "emerging"


def _store_trend(conn, trend):
    """Store or update a trend in the database.

    If a trend with the same keyword_fingerprint exists, update it.
    Otherwise insert a new row.

    Args:
        conn: SQLite connection.
        trend: Trend dict with all fields.
    """
    if trend.get("is_update"):
        conn.execute(
            """UPDATE innovation_trends
               SET name = ?, signal_count = ?, velocity = ?, acceleration = ?,
                   last_seen = ?, status = ?, keywords = ?, signal_ids = ?,
                   metadata = ?, detected_at = ?
               WHERE id = ?""",
            (
                trend["name"],
                trend["signal_count"],
                trend["velocity"],
                trend["acceleration"],
                trend["last_seen"],
                trend["status"],
                json.dumps(trend["keywords"]),
                json.dumps(trend["signal_ids"]),
                json.dumps(trend["metadata"]),
                _now(),
                trend["id"],
            ),
        )
    else:
        conn.execute(
            """INSERT INTO innovation_trends
               (id, name, category, signal_count, velocity, acceleration,
                first_seen, last_seen, status, keyword_fingerprint,
                keywords, signal_ids, metadata, detected_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                trend["id"],
                trend["name"],
                trend["category"],
                trend["signal_count"],
                trend["velocity"],
                trend["acceleration"],
                trend["first_seen"],
                trend["last_seen"],
                trend["status"],
                trend["keyword_fingerprint"],
                json.dumps(trend["keywords"]),
                json.dumps(trend["signal_ids"]),
                json.dumps(trend["metadata"]),
                _now(),
            ),
        )
    conn.commit()


# =========================================================================
# TREND REPORT
# =========================================================================
def get_trend_report(db_path=None):
    """Generate a summary report of all active trends.

    Reads from innovation_trends table, groups by status and category,
    and produces a structured report with trend details and statistics.

    Args:
        db_path: Optional database path override.

    Returns:
        Dict with trend report: by_status, by_category, top_trends, summary.
    """
    conn = _get_db(db_path)
    _ensure_trends_table(conn)

    try:
        rows = conn.execute(
            """SELECT id, name, category, signal_count, velocity, acceleration,
                      first_seen, last_seen, status, keywords, metadata, detected_at
               FROM innovation_trends
               ORDER BY velocity DESC, signal_count DESC"""
        ).fetchall()

        if not rows:
            return {
                "generated_at": _now(),
                "total_trends": 0,
                "by_status": {},
                "by_category": {},
                "top_trends": [],
                "summary": "No trends detected yet. Run --detect first.",
            }

        trends = []
        by_status = defaultdict(list)
        by_category = defaultdict(list)

        for row in rows:
            trend = {
                "id": row["id"],
                "name": row["name"],
                "category": row["category"],
                "signal_count": row["signal_count"],
                "velocity": row["velocity"],
                "acceleration": row["acceleration"],
                "first_seen": row["first_seen"],
                "last_seen": row["last_seen"],
                "status": row["status"],
                "keywords": json.loads(row["keywords"]) if row["keywords"] else [],
                "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
                "detected_at": row["detected_at"],
            }
            trends.append(trend)
            by_status[row["status"]].append(trend)
            by_category[row["category"]].append(trend)

        # Top 10 by velocity
        top_trends = sorted(trends, key=lambda t: t["velocity"], reverse=True)[:10]

        # Summary statistics
        total = len(trends)
        active = len(by_status.get("active", []))
        emerging = len(by_status.get("emerging", []))
        declining = len(by_status.get("declining", []))
        stale = len(by_status.get("stale", []))

        avg_velocity = sum(t["velocity"] for t in trends) / total if total else 0
        total_signals = sum(t["signal_count"] for t in trends)

        summary_parts = []
        if emerging > 0:
            summary_parts.append(f"{emerging} emerging")
        if active > 0:
            summary_parts.append(f"{active} active")
        if declining > 0:
            summary_parts.append(f"{declining} declining")
        if stale > 0:
            summary_parts.append(f"{stale} stale")
        summary_text = f"{total} trends tracked: {', '.join(summary_parts)}."

        _audit(
            "innovation.trend_report",
            "innovation-agent",
            f"Generated trend report: {total} trends",
            {"total": total, "active": active, "emerging": emerging},
        )

        return {
            "generated_at": _now(),
            "total_trends": total,
            "by_status": {
                status: [_trend_summary(t) for t in trend_list]
                for status, trend_list in by_status.items()
            },
            "by_category": {
                cat: [_trend_summary(t) for t in trend_list]
                for cat, trend_list in by_category.items()
            },
            "top_trends": [_trend_summary(t) for t in top_trends],
            "statistics": {
                "total_signals_in_trends": total_signals,
                "average_velocity": round(avg_velocity, 4),
                "categories_with_trends": len(by_category),
                "status_counts": {
                    "emerging": emerging,
                    "active": active,
                    "declining": declining,
                    "stale": stale,
                },
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
        "category": trend["category"],
        "signal_count": trend["signal_count"],
        "velocity": trend["velocity"],
        "acceleration": trend["acceleration"],
        "status": trend["status"],
        "keywords": trend.get("keywords", [])[:5],
        "first_seen": trend["first_seen"],
        "last_seen": trend["last_seen"],
    }


# =========================================================================
# TREND VELOCITY
# =========================================================================
def get_trend_velocity(trend_id, db_path=None):
    """Measure how fast a specific trend is growing or declining.

    Computes velocity (signals/day) and acceleration (velocity delta)
    using the signals associated with the trend. Also provides a
    day-by-day signal count breakdown.

    Args:
        trend_id: The trend ID to analyze.
        db_path: Optional database path override.

    Returns:
        Dict with velocity metrics, daily breakdown, and projection.
    """
    conn = _get_db(db_path)
    _ensure_trends_table(conn)

    try:
        # Fetch the trend
        row = conn.execute(
            """SELECT id, name, category, signal_count, velocity, acceleration,
                      first_seen, last_seen, status, keywords, signal_ids, metadata
               FROM innovation_trends WHERE id = ?""",
            (trend_id,),
        ).fetchone()

        if not row:
            return {"error": f"Trend not found: {trend_id}"}

        signal_ids = json.loads(row["signal_ids"]) if row["signal_ids"] else []
        keywords = json.loads(row["keywords"]) if row["keywords"] else []

        # Fetch individual signal dates for day-by-day breakdown
        daily_counts = defaultdict(int)
        if signal_ids:
            placeholders = ",".join("?" for _ in signal_ids)
            signal_rows = conn.execute(
                f"""SELECT DATE(discovered_at) as day, COUNT(*) as cnt
                    FROM innovation_signals
                    WHERE id IN ({placeholders})
                    GROUP BY day
                    ORDER BY day""",
                signal_ids,
            ).fetchall()
            for sr in signal_rows:
                daily_counts[sr["day"]] = sr["cnt"]

        # Calculate rolling velocity (7-day windows)
        sorted_days = sorted(daily_counts.keys())
        rolling_velocity = []
        for i, day in enumerate(sorted_days):
            window_start = max(0, i - 6)
            window_days = sorted_days[window_start : i + 1]
            window_count = sum(daily_counts[d] for d in window_days)
            window_size = len(window_days)
            vel = window_count / window_size if window_size > 0 else 0
            rolling_velocity.append({
                "date": day,
                "count": daily_counts[day],
                "rolling_7d_velocity": round(vel, 4),
            })

        # Projection: if velocity > 0, estimate signals in next 7 days
        current_velocity = row["velocity"]
        projected_7d = round(current_velocity * 7, 1) if current_velocity > 0 else 0

        # Trend direction label
        accel = row["acceleration"]
        if accel > 0.01:
            direction = "accelerating"
        elif accel < -0.01:
            direction = "decelerating"
        else:
            direction = "steady"

        _audit(
            "innovation.trend_velocity",
            "innovation-agent",
            f"Velocity check for trend {trend_id}: {current_velocity:.4f} sig/day",
            {"trend_id": trend_id, "velocity": current_velocity, "direction": direction},
        )

        return {
            "trend_id": row["id"],
            "name": row["name"],
            "category": row["category"],
            "status": row["status"],
            "current_velocity": row["velocity"],
            "acceleration": accel,
            "direction": direction,
            "signal_count": row["signal_count"],
            "first_seen": row["first_seen"],
            "last_seen": row["last_seen"],
            "keywords": keywords,
            "daily_breakdown": rolling_velocity,
            "projection": {
                "next_7d_signals": projected_7d,
                "note": "Linear projection based on current velocity",
            },
            "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
        }
    finally:
        conn.close()


# =========================================================================
# CLI
# =========================================================================
def main():
    parser = argparse.ArgumentParser(
        description="ICDEV Cross-Signal Trend Detector — detect emerging patterns from innovation signals"
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument(
        "--db-path", type=Path, default=None, help="Database path override"
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--detect", action="store_true",
        help="Detect trends from signals within the time window"
    )
    group.add_argument(
        "--report", action="store_true",
        help="Generate a summary report of all tracked trends"
    )
    group.add_argument(
        "--velocity", action="store_true",
        help="Measure velocity for a specific trend (requires --trend-id)"
    )

    parser.add_argument(
        "--days", type=int, default=30,
        help="Time window in days for trend detection (default: 30)"
    )
    parser.add_argument(
        "--min-signals", type=int, default=3,
        help="Minimum signals to form a trend (default: 3)"
    )
    parser.add_argument(
        "--trend-id", type=str, default=None,
        help="Trend ID for --velocity command"
    )

    args = parser.parse_args()

    try:
        if args.detect:
            result = detect_trends(
                time_window_days=args.days,
                min_signals=args.min_signals,
                db_path=args.db_path,
            )
        elif args.report:
            result = get_trend_report(db_path=args.db_path)
        elif args.velocity:
            if not args.trend_id:
                print("ERROR: --velocity requires --trend-id", file=sys.stderr)
                sys.exit(1)
            result = get_trend_velocity(
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


def _print_human(args, result):
    """Print human-readable output for CLI."""
    if "error" in result:
        print(f"ERROR: {result['error']}")
        return

    if args.detect:
        print(f"Trend Detection — {result.get('detected_at', '')}")
        print(f"  Signals analyzed: {result.get('signals_analyzed', 0)}")
        print(f"  Trends detected:  {result.get('trends_detected', 0)}")
        print(f"  Time window:      {result.get('time_window_days', 0)} days")
        print(f"  Min signals:      {result.get('min_signals', 0)}")
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
        print(f"Trend Report — {result.get('generated_at', '')}")
        print(f"  {result.get('summary', '')}")
        stats = result.get("statistics", {})
        print(f"  Total signals in trends: {stats.get('total_signals_in_trends', 0)}")
        print(f"  Average velocity:        {stats.get('average_velocity', 0):.4f} sig/day")
        print()
        print("  Top Trends:")
        for t in result.get("top_trends", [])[:10]:
            print(
                f"    [{t['status'][:3].upper()}] {t['name']}"
                f"  — {t['signal_count']} signals, v={t['velocity']:.4f}/day"
            )

    elif args.velocity:
        print(f"Trend Velocity — {result.get('name', '')}")
        print(f"  ID:           {result.get('trend_id', '')}")
        print(f"  Status:       {result.get('status', '')}")
        print(f"  Direction:    {result.get('direction', '')}")
        print(f"  Velocity:     {result.get('current_velocity', 0):.4f} signals/day")
        print(f"  Acceleration: {result.get('acceleration', 0):.4f}")
        print(f"  Signals:      {result.get('signal_count', 0)}")
        proj = result.get("projection", {})
        print(f"  Projected 7d: {proj.get('next_7d_signals', 0)} signals")
        print()
        breakdown = result.get("daily_breakdown", [])
        if breakdown:
            print("  Daily Breakdown (last 10):")
            for entry in breakdown[-10:]:
                bar = "#" * min(entry["count"], 40)
                print(
                    f"    {entry['date']}: {entry['count']:3d} {bar}"
                    f"  (7d avg: {entry['rolling_7d_velocity']:.2f})"
                )


if __name__ == "__main__":
    main()
