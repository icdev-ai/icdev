#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""Cross-Session Trend Detection for ICDEV™ Research Engine (D-RES-11).

Analyzes research_challenges entries across sessions and verticals to identify
emerging, active, declining, and stale trends by clustering challenges with
keyword co-occurrence. All analysis is deterministic (no LLM required), using
keyword co-occurrence clustering and time-series velocity. Air-gap safe.

Architecture:
    - Reads from research_challenges table (populated by challenge scorers)
    - Deduplicates by keyword_fingerprint (latest row per fingerprint)
    - Groups challenges by category
    - Within each category, clusters by keyword overlap (>= 3 shared keywords)
    - Clusters with >= min_signals become named trends
    - Velocity = signals_per_day over window; acceleration = velocity delta vs previous
    - Stores trends in research_trends table (append-only new rows per detection cycle, D6)
    - Trend lifecycle: emerging -> active -> declining -> stale
    - Cross-session (D-RES-11): when session_id is omitted, detects trends across ALL
      sessions and verticals, collecting vertical_ids and session_ids from clustered
      challenges

Trend Detection Approach (deterministic, mirrors tools/creative/trend_tracker.py):
    1. Extract top-10 keywords per challenge (TF with stopword removal)
    2. Group challenges by category
    3. Within each category, build keyword co-occurrence matrix
    4. Cluster challenges sharing >= 3 common keywords
    5. Clusters with >= min_signals become named trends
    6. Track velocity and lifecycle transitions over time

Usage:
    python tools/research/trend_detector.py --detect --json
    python tools/research/trend_detector.py --detect --session-id rsess-xxx --json
    python tools/research/trend_detector.py --trends --json
    python tools/research/trend_detector.py --trends --status active --json
    python tools/research/trend_detector.py --report --json
    python tools/research/trend_detector.py --detect --human
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
STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "been",
        "by",
        "but",
        "can",
        "could",
        "did",
        "do",
        "does",
        "for",
        "from",
        "had",
        "has",
        "have",
        "he",
        "her",
        "him",
        "his",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "just",
        "may",
        "me",
        "might",
        "more",
        "most",
        "must",
        "my",
        "no",
        "nor",
        "not",
        "of",
        "on",
        "or",
        "our",
        "out",
        "own",
        "re",
        "s",
        "she",
        "should",
        "so",
        "some",
        "such",
        "t",
        "than",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "these",
        "they",
        "this",
        "those",
        "through",
        "to",
        "too",
        "up",
        "us",
        "very",
        "was",
        "we",
        "were",
        "what",
        "when",
        "where",
        "which",
        "while",
        "who",
        "whom",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
        "about",
        "above",
        "after",
        "again",
        "all",
        "also",
        "am",
        "any",
        "because",
        "before",
        "being",
        "between",
        "both",
        "during",
        "each",
        "few",
        "further",
        "get",
        "got",
        "here",
        "how",
        "itself",
        "let",
        "like",
        "make",
        "many",
        "much",
        "new",
        "now",
        "off",
        "old",
        "one",
        "only",
        "other",
        "over",
        "same",
        "set",
        "since",
        "still",
        "take",
        "two",
        "under",
        "use",
        "used",
        "using",
        "way",
        "well",
    }
)

# Minimum keyword length to consider
MIN_KEYWORD_LEN = 3
# Top N keywords to extract per challenge
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
    if not Path(str(path)).exists():
        raise FileNotFoundError(f"Database not found: {path}")
    conn = get_connection(db_path=str(path))
    return conn


def _now():
    """ISO-8601 timestamp."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _trend_id():
    """Generate unique trend ID with rtrend- prefix."""
    return f"rtrend-{uuid.uuid4().hex[:12]}"


def _audit(event_type, action, details=None):
    """Write audit trail entry."""
    if _HAS_AUDIT:
        try:
            audit_log_event(
                event_type=event_type,
                actor="research-engine",
                action=action,
                details=json.dumps(details) if details else None,
                project_id="research-engine",
            )
        except Exception:
            pass


def _load_config():
    """Load research config from YAML.

    Returns:
        Dict with full research_config.yaml contents, or empty dict.
    """
    if not _HAS_YAML:
        return {}
    if not CONFIG_PATH.exists():
        return {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


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
        List of keyword strings sorted by frequency descending.
    """
    if not text:
        return []
    tokens = _WORD_RE.findall(text.lower())
    filtered = [t for t in tokens if t not in STOPWORDS and len(t) >= MIN_KEYWORD_LEN]
    counts = Counter(filtered)
    return [kw for kw, _ in counts.most_common(top_n)]


def _keyword_fingerprint(keywords):
    """SHA-256 fingerprint of sorted keyword list for deduplication."""
    canonical = ",".join(sorted(keywords))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


# =========================================================================
# TREND DETECTION
# =========================================================================
def detect_trends(session_id=None, db_path=None):
    """Detect trends by clustering challenges with keyword co-occurrence.

    When session_id is provided, detects trends within that single session.
    When session_id is None, detects cross-session trends across ALL sessions
    and verticals (D-RES-11 cross-vertical trend detection).

    Algorithm:
        1. Load config for detection parameters
        2. Query research_challenges from last N days (detection window)
        3. Deduplicate by keyword_fingerprint (latest row per fingerprint)
        4. Build enriched challenge data with keyword sets
        5. Group challenges by category
        6. Within each category, cluster by keyword overlap (>= 3 shared keywords)
        7. For each cluster, find common keywords (present in majority)
        8. Compute velocity = challenge_count / detection_window_days
        9. Compute acceleration = velocity - old_velocity (from DB lookup)
        10. Determine lifecycle status via _determine_lifecycle()
        11. Store trends in research_trends (append-only, D6)

    Args:
        session_id: Optional session ID to scope detection. If None, detects
                    cross-session trends across all sessions (D-RES-11).
        db_path: Optional database path override.

    Returns:
        Dict with trends_detected, new_trends, updated, by_status counts.
    """
    config = _load_config()
    trends_cfg = config.get("trends", {})
    detection_window = trends_cfg.get("detection_window_days", 60)
    min_sigs = trends_cfg.get("min_signals_for_trend", 5)
    stale_after = trends_cfg.get("stale_after_days", 90)

    conn = _get_db(db_path)

    now = datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=detection_window)).strftime("%Y-%m-%dT%H:%M:%SZ")

    try:
        # -----------------------------------------------------------------
        # Step 1: Fetch challenges within the time window
        # -----------------------------------------------------------------
        if session_id:
            rows = conn.execute(
                """SELECT c.id, c.session_id, c.title, c.description, c.category,
                          c.keyword_fingerprint, c.keywords, c.signal_count,
                          c.composite_score, c.severity,
                          c.first_seen, c.last_seen,
                          s.vertical_id
                   FROM research_challenges c
                   JOIN research_sessions s ON c.session_id = s.id
                   WHERE c.session_id = %s AND c.last_seen >= %s
                   ORDER BY c.last_seen DESC""",
                (session_id, cutoff),
            ).fetchall()
        else:
            # Cross-session: query ALL sessions (D-RES-11)
            rows = conn.execute(
                """SELECT c.id, c.session_id, c.title, c.description, c.category,
                          c.keyword_fingerprint, c.keywords, c.signal_count,
                          c.composite_score, c.severity,
                          c.first_seen, c.last_seen,
                          s.vertical_id
                   FROM research_challenges c
                   JOIN research_sessions s ON c.session_id = s.id
                   WHERE c.last_seen >= %s
                   ORDER BY c.last_seen DESC""",
                (cutoff,),
            ).fetchall()

        scope_label = f"session {session_id}" if session_id else "cross-session"

        if not rows:
            return {
                "detected_at": _now(),
                "scope": scope_label,
                "time_window_days": detection_window,
                "min_signals": min_sigs,
                "challenges_analyzed": 0,
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
        # Step 3: Build enriched challenge data with keyword sets
        # -----------------------------------------------------------------
        challenge_data = []
        for row in deduped:
            title = row["title"] or ""
            desc = row["description"] or ""
            # Use stored keywords if available, else re-extract
            stored_kw = row["keywords"]
            if stored_kw and stored_kw != "[]":
                try:
                    kw_list = json.loads(stored_kw)
                except (json.JSONDecodeError, TypeError):
                    kw_list = extract_keywords(f"{title} {desc}")
            else:
                kw_list = extract_keywords(f"{title} {desc}")

            challenge_data.append(
                {
                    "id": row["id"],
                    "session_id": row["session_id"],
                    "vertical_id": row["vertical_id"],
                    "title": title,
                    "description": desc,
                    "category": row["category"],
                    "signal_count": row["signal_count"] or 1,
                    "keywords": frozenset(kw_list),
                    "severity": row["severity"] or "notable",
                    "composite_score": row["composite_score"] or 0.0,
                    "first_seen": row["first_seen"],
                    "last_seen": row["last_seen"],
                }
            )

        # -----------------------------------------------------------------
        # Step 4: Group by category
        # -----------------------------------------------------------------
        by_category = defaultdict(list)
        for ch in challenge_data:
            by_category[ch["category"]].append(ch)

        # -----------------------------------------------------------------
        # Step 5: Cluster challenges within each category by keyword overlap
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
                for ch in cluster_points:
                    for kw in ch["keywords"]:
                        kw_counter[kw] += 1
                # Keywords appearing in majority of challenges
                majority = max(len(cluster_points) // 2, 2)
                common_keywords = sorted(
                    [kw for kw, cnt in kw_counter.items() if cnt >= majority],
                    key=lambda k: -kw_counter[k],
                )[:10]

                if len(common_keywords) < MIN_SHARED_KEYWORDS:
                    continue

                fingerprint = _keyword_fingerprint(common_keywords)
                challenge_ids = [ch["id"] for ch in cluster_points]
                signal_count = sum(ch["signal_count"] for ch in cluster_points)

                # Collect cross-session metadata
                vertical_ids = sorted(set(ch["vertical_id"] for ch in cluster_points if ch["vertical_id"]))
                session_ids = sorted(set(ch["session_id"] for ch in cluster_points if ch["session_id"]))

                # Dates
                dates = sorted(ch["last_seen"] for ch in cluster_points if ch["last_seen"])
                first_dates = sorted(ch["first_seen"] for ch in cluster_points if ch["first_seen"])
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
                       FROM research_trends
                       WHERE keyword_fingerprint = %s
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
                    velocity,
                    acceleration,
                    last_seen,
                    now,
                    stale_after,
                )

                # Severity distribution for metadata
                severity_dist = Counter(ch["severity"] for ch in cluster_points)

                trend = {
                    "id": _trend_id(),
                    "name": name,
                    "vertical_ids": vertical_ids,
                    "session_ids": session_ids,
                    "challenge_ids": challenge_ids,
                    "signal_count": signal_count,
                    "keyword_fingerprint": fingerprint,
                    "keywords": common_keywords,
                    "velocity": round(velocity, 4),
                    "acceleration": round(acceleration, 4),
                    "status": status,
                    "first_seen": first_seen,
                    "last_seen": last_seen,
                    "metadata": {
                        "category": category,
                        "severity_distribution": dict(severity_dist),
                        "challenge_count": len(cluster_points),
                        "common_keyword_count": len(common_keywords),
                        "cross_vertical": len(vertical_ids) > 1,
                        "cross_session": len(session_ids) > 1,
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
        # Step 9: Mark stale trends — no new challenges in stale_after_days
        # -----------------------------------------------------------------
        stale_cutoff = (now - timedelta(days=stale_after)).strftime("%Y-%m-%dT%H:%M:%SZ")
        stale_candidates = conn.execute(
            """SELECT DISTINCT keyword_fingerprint
               FROM research_trends
               WHERE status IN ('emerging', 'active')
                 AND last_seen < %s""",
            (stale_cutoff,),
        ).fetchall()
        for row in stale_candidates:
            fp = row["keyword_fingerprint"]
            # Only mark stale if no recent detection refreshed it
            already_fresh = any(t["keyword_fingerprint"] == fp for t in detected_trends)
            if not already_fresh:
                # Insert a stale row to preserve history (append-only)
                latest = conn.execute(
                    """SELECT * FROM research_trends
                       WHERE keyword_fingerprint = %s
                       ORDER BY detected_at DESC LIMIT 1""",
                    (fp,),
                ).fetchone()
                if latest and latest["status"] != "stale":
                    stale_trend = {
                        "id": _trend_id(),
                        "name": latest["name"],
                        "vertical_ids": json.loads(latest["vertical_ids"]) if latest["vertical_ids"] else [],
                        "session_ids": json.loads(latest["session_ids"]) if latest["session_ids"] else [],
                        "challenge_ids": json.loads(latest["challenge_ids"]) if latest["challenge_ids"] else [],
                        "signal_count": latest["signal_count"],
                        "keyword_fingerprint": fp,
                        "keywords": json.loads(latest["keywords"]) if latest["keywords"] else [],
                        "velocity": 0.0,
                        "acceleration": 0.0 - (latest["velocity"] or 0.0),
                        "status": "stale",
                        "first_seen": latest["first_seen"],
                        "last_seen": latest["last_seen"],
                        "metadata": {"marked_stale": True},
                        "is_new": False,
                    }
                    _store_trend(conn, stale_trend)

        # Cross-register high-scoring trends to innovation_signals
        _cross_register_to_innovation(conn, detected_trends)

        # Build status summary
        status_counts = Counter(t["status"] for t in detected_trends)

        _audit(
            "research.trend_detection",
            f"Detected {len(detected_trends)} trends from {len(deduped)} challenges ({scope_label})",
            {
                "trends_detected": len(detected_trends),
                "challenges_analyzed": len(deduped),
                "new_trends": new_count,
                "updated": updated_count,
                "time_window_days": detection_window,
                "min_signals": min_sigs,
                "scope": scope_label,
            },
        )

        return {
            "detected_at": _now(),
            "scope": scope_label,
            "time_window_days": detection_window,
            "min_signals": min_sigs,
            "challenges_analyzed": len(deduped),
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
        - days since last_seen >= stale_after: "stale"
        - velocity > 0.5: "active"
        - velocity > 0.1: "emerging" (or "declining" if negative acceleration)
        - velocity > 0 and declining (negative acceleration): "declining"
        - velocity > 0: "emerging"
        - otherwise: "stale"

    Args:
        velocity: Signals per day in current window.
        acceleration: Change in velocity vs previous detection cycle.
        last_seen: ISO timestamp of the most recent challenge.
        now: Current datetime (UTC).
        stale_after: Number of days after which a trend is considered stale.

    Returns:
        Status string: 'emerging', 'active', 'declining', or 'stale'.
    """
    # Check for stale — no new challenges in stale_after days
    try:
        last_dt = datetime.strptime(last_seen[:19], "%Y-%m-%dT%H:%M:%S")
        last_dt = last_dt.replace(tzinfo=timezone.utc)
        days_since = (now - last_dt).days
    except (ValueError, TypeError):
        days_since = 999

    if days_since >= stale_after:
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
    """Store a trend as a new row in research_trends (append-only, D6).

    Each detection cycle inserts a NEW row. Historical rows are preserved
    for trend-over-time analysis.

    Args:
        conn: SQLite connection.
        trend: Trend dict with all fields.
    """
    conn.execute(
        """INSERT INTO research_trends
           (id, name, vertical_ids, session_ids, challenge_ids,
            keyword_fingerprint, keywords, signal_count, velocity, acceleration,
            status, first_seen, last_seen, metadata, detected_at,
            classification)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            trend["id"],
            trend["name"],
            json.dumps(trend["vertical_ids"]),
            json.dumps(trend["session_ids"]),
            json.dumps(trend["challenge_ids"]),
            trend["keyword_fingerprint"],
            json.dumps(trend["keywords"]),
            trend["signal_count"],
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
# CROSS-ENGINE REGISTRATION (D-RES-11)
# =========================================================================
def _cross_register_to_innovation(conn, detected_trends):
    """Cross-register high-scoring trends to innovation_signals.

    Trends with velocity above a configurable threshold are registered as
    innovation signals with source label 'research_engine'. This enables
    the Innovation Engine to detect cross-cutting trends from research
    discoveries.

    Args:
        conn: SQLite connection (already open).
        detected_trends: List of trend dicts from current detection cycle.
    """
    config = _load_config()
    bridge = config.get("innovation_bridge", {})
    if not bridge.get("enabled", True):
        return

    min_score = bridge.get("min_score_for_innovation", 0.70)
    source_label = bridge.get("source_label", "research_engine")

    # Check if innovation_signals table exists (graceful degradation)
    try:
        conn.execute("SELECT 1 FROM innovation_signals LIMIT 0")
    except sqlite3.OperationalError:
        return

    # Gather already cross-registered research-trend ids. Portable: parse the
    # metadata JSON in Python rather than json_extract() per-trend — runtime SQL
    # is authored for PG; the translator is only a SQLite init-fallback.
    # See PGP / pgp-tx-02.
    registered_trend_ids = set()
    for sig in conn.execute("SELECT metadata FROM innovation_signals").fetchall():
        meta = json.loads(sig["metadata"] or "{}")
        rt_id = meta.get("research_trend_id")
        if rt_id is not None:
            registered_trend_ids.add(rt_id)

    registered = 0
    for trend in detected_trends:
        # Use velocity as a proxy score (normalized against threshold)
        if trend["velocity"] < min_score:
            continue

        trend_id = trend["id"]

        # Check for existing cross-registration
        if trend_id in registered_trend_ids:
            continue

        sig_id = f"isig-{uuid.uuid4().hex[:12]}"
        now = _now()
        metadata = json.dumps(
            {
                "research_trend_id": trend_id,
                "cross_registered_from": "research_engine",
                "trend_name": trend["name"],
                "trend_status": trend["status"],
                "velocity": trend["velocity"],
                "vertical_ids": trend["vertical_ids"],
                "session_ids": trend["session_ids"],
            }
        )

        try:
            conn.execute(
                """INSERT INTO innovation_signals
                   (id, source, source_type, title, description, url,
                    category, innovation_score, metadata, content_hash,
                    discovered_at, classification)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    sig_id,
                    source_label,
                    "external_framework_analysis",
                    f"Research trend: {trend['name']}",
                    f"Cross-session research trend with velocity {trend['velocity']:.4f} "
                    f"across {len(trend.get('session_ids', []))} sessions",
                    "",
                    "research_trend",
                    trend["velocity"],
                    metadata,
                    trend["keyword_fingerprint"],
                    now,
                    "CUI",
                ),
            )
            registered += 1
        except sqlite3.IntegrityError:
            pass

    if registered > 0:
        conn.commit()
        _audit(
            "research.innovation_bridge",
            f"Cross-registered {registered} trends to innovation_signals",
            {"registered": registered, "source": source_label},
        )


# =========================================================================
# GET TRENDS
# =========================================================================
def get_trends(status=None, db_path=None):
    """Get research trends, deduplicated by fingerprint (latest wins).

    Args:
        status: Optional status filter ('emerging', 'active', 'declining', 'stale').
        db_path: Optional database path override.

    Returns:
        Dict with trends list and total count.
    """
    conn = _get_db(db_path)

    try:
        rows = conn.execute(
            """SELECT id, name, vertical_ids, session_ids, challenge_ids,
                      keyword_fingerprint, keywords, signal_count,
                      velocity, acceleration, status,
                      first_seen, last_seen, metadata, detected_at
               FROM research_trends
               ORDER BY detected_at DESC"""
        ).fetchall()

        if not rows:
            return {
                "generated_at": _now(),
                "trends": [],
                "total": 0,
            }

        # Deduplicate by keyword_fingerprint (latest detected_at wins)
        seen_fps = {}
        deduped = []
        for row in rows:
            fp = row["keyword_fingerprint"]
            if fp not in seen_fps:
                seen_fps[fp] = True
                trend = _row_to_trend(row)
                deduped.append(trend)

        # Optional status filter
        if status:
            deduped = [t for t in deduped if t["status"] == status]

        return {
            "generated_at": _now(),
            "trends": [_trend_summary(t) for t in deduped],
            "total": len(deduped),
        }
    finally:
        conn.close()


# =========================================================================
# TREND REPORT
# =========================================================================
def get_trend_report(db_path=None):
    """Generate a summary report of all tracked research trends.

    Reads from research_trends table, deduplicates by keyword_fingerprint
    (keeps latest row per fingerprint), groups by status and category,
    includes top trending by velocity.

    Args:
        db_path: Optional database path override.

    Returns:
        Dict with total, by_status, top_trending, by_category.
    """
    conn = _get_db(db_path)

    try:
        rows = conn.execute(
            """SELECT id, name, vertical_ids, session_ids, challenge_ids,
                      keyword_fingerprint, keywords, signal_count,
                      velocity, acceleration, status,
                      first_seen, last_seen, metadata, detected_at
               FROM research_trends
               ORDER BY detected_at DESC"""
        ).fetchall()

        if not rows:
            return {
                "generated_at": _now(),
                "total": 0,
                "by_status": {},
                "top_trending": [],
                "by_vertical": {},
                "summary": "No trends detected yet. Run --detect first.",
            }

        # Deduplicate by keyword_fingerprint (latest row wins)
        seen_fps = {}
        deduped = []
        for row in rows:
            fp = row["keyword_fingerprint"]
            if fp not in seen_fps:
                seen_fps[fp] = True
                trend = _row_to_trend(row)
                deduped.append(trend)

        # Group by status
        by_status = defaultdict(list)
        by_vertical = defaultdict(list)
        for trend in deduped:
            by_status[trend["status"]].append(_trend_summary(trend))
            # Group by verticals (a trend may span multiple)
            for vid in trend.get("vertical_ids", []):
                by_vertical[vid].append(_trend_summary(trend))

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
        avg_velocity = sum(t["velocity"] for t in deduped) / total if total else 0
        total_signals = sum(t["signal_count"] for t in deduped)

        # Count cross-vertical trends
        cross_vertical_count = sum(1 for t in deduped if len(t.get("vertical_ids", [])) > 1)

        # Summary text
        parts = []
        for s, c in status_counts.items():
            if c > 0:
                parts.append(f"{c} {s}")
        summary_text = f"{total} trends tracked: {', '.join(parts)}." if parts else "No trends."
        if cross_vertical_count > 0:
            summary_text += f" {cross_vertical_count} cross-vertical."

        _audit(
            "research.trend_report",
            f"Generated trend report: {total} trends",
            {"total": total, "status_counts": status_counts},
        )

        return {
            "generated_at": _now(),
            "total": total,
            "by_status": dict(by_status),
            "top_trending": [_trend_summary(t) for t in top_trending],
            "by_vertical": dict(by_vertical),
            "statistics": {
                "total_signals_in_trends": total_signals,
                "average_velocity": round(avg_velocity, 4),
                "verticals_with_trends": len(by_vertical),
                "cross_vertical_trends": cross_vertical_count,
                "status_counts": status_counts,
            },
            "summary": summary_text,
        }
    finally:
        conn.close()


def _row_to_trend(row):
    """Convert a sqlite3.Row to a trend dict with parsed JSON fields."""
    return {
        "id": row["id"],
        "name": row["name"],
        "vertical_ids": json.loads(row["vertical_ids"]) if row["vertical_ids"] else [],
        "session_ids": json.loads(row["session_ids"]) if row["session_ids"] else [],
        "challenge_ids": json.loads(row["challenge_ids"]) if row["challenge_ids"] else [],
        "signal_count": row["signal_count"],
        "keywords": json.loads(row["keywords"]) if row["keywords"] else [],
        "velocity": row["velocity"],
        "acceleration": row["acceleration"],
        "status": row["status"],
        "first_seen": row["first_seen"],
        "last_seen": row["last_seen"],
        "metadata": json.loads(row["metadata"]) if row["metadata"] else {},
        "detected_at": row["detected_at"],
    }


def _trend_summary(trend):
    """Create a compact summary dict from a trend for report output."""
    meta = trend.get("metadata", {})
    return {
        "id": trend["id"],
        "name": trend["name"],
        "vertical_ids": trend.get("vertical_ids", []),
        "session_ids": trend.get("session_ids", []),
        "signal_count": trend["signal_count"],
        "velocity": trend["velocity"],
        "acceleration": trend["acceleration"],
        "status": trend["status"],
        "keywords": trend.get("keywords", [])[:5],
        "category": meta.get("category", ""),
        "cross_vertical": meta.get("cross_vertical", False),
        "first_seen": trend.get("first_seen", ""),
        "last_seen": trend.get("last_seen", ""),
    }


# =========================================================================
# CLI — HUMAN OUTPUT
# =========================================================================
_STATUS_ICONS = {
    "emerging": "[NEW]",
    "active": "[ACT]",
    "declining": "[DEC]",
    "stale": "[OLD]",
}


def _print_human(args, result):
    """Print human-readable output for CLI."""
    if "error" in result:
        print(f"ERROR: {result['error']}")
        return

    if args.detect:
        print("CUI // SP-CTI")
        print(f"Research Trend Detection -- {result.get('detected_at', '')}")
        print(f"  Scope:               {result.get('scope', '')}")
        print(f"  Challenges analyzed: {result.get('challenges_analyzed', 0)}")
        print(f"  Trends detected:     {result.get('trends_detected', 0)}")
        print(f"  New trends:          {result.get('new_trends', 0)}")
        print(f"  Updated trends:      {result.get('updated', 0)}")
        print(f"  Time window:         {result.get('time_window_days', 0)} days")
        print(f"  Min signals:         {result.get('min_signals', 0)}")
        print()
        by_status = result.get("by_status", {})
        if by_status:
            parts = [f"{s}={c}" for s, c in sorted(by_status.items())]
            print(f"  Status breakdown: {', '.join(parts)}")
            print()
        for trend in result.get("trends", []):
            icon = _STATUS_ICONS.get(trend["status"], "[???]")
            meta = trend.get("metadata", {})
            category = meta.get("category", "")
            cross = " [CROSS-VERTICAL]" if meta.get("cross_vertical") else ""
            print(
                f"  {icon} {trend['name']}"
                f"  ({category}, {trend['signal_count']} signals,"
                f" v={trend['velocity']:.4f}/day){cross}"
            )
            kws = ", ".join(trend.get("keywords", [])[:5])
            print(f"         keywords: {kws}")
            verts = trend.get("vertical_ids", [])
            if len(verts) > 1:
                print(f"         verticals: {', '.join(verts)}")
            sessions = trend.get("session_ids", [])
            if len(sessions) > 1:
                print(f"         sessions: {len(sessions)} sessions")

    elif args.trends:
        print("CUI // SP-CTI")
        print(f"Research Trends -- {result.get('generated_at', '')}")
        print(f"  Total: {result.get('total', 0)}")
        if args.status:
            print(f"  Filter: status={args.status}")
        print()
        for trend in result.get("trends", []):
            icon = _STATUS_ICONS.get(trend["status"], "[???]")
            cross = " [CROSS]" if trend.get("cross_vertical") else ""
            print(f"  {icon} {trend['name']}  -- {trend['signal_count']} signals, v={trend['velocity']:.4f}/day{cross}")
            kws = ", ".join(trend.get("keywords", [])[:5])
            print(f"         keywords: {kws}")

    elif args.report:
        print("CUI // SP-CTI")
        print(f"Research Trend Report -- {result.get('generated_at', '')}")
        print(f"  {result.get('summary', '')}")
        stats = result.get("statistics", {})
        print(f"  Total signals in trends: {stats.get('total_signals_in_trends', 0)}")
        print(f"  Average velocity:        {stats.get('average_velocity', 0):.4f} sig/day")
        print(f"  Verticals with trends:   {stats.get('verticals_with_trends', 0)}")
        print(f"  Cross-vertical trends:   {stats.get('cross_vertical_trends', 0)}")
        print()
        print("  Top Trending:")
        for t in result.get("top_trending", [])[:10]:
            icon = _STATUS_ICONS.get(t["status"], "[???]")
            cross = " [CROSS]" if t.get("cross_vertical") else ""
            print(f"    {icon} {t['name']}  -- {t['signal_count']} signals, v={t['velocity']:.4f}/day{cross}")
        print()
        print("  By Vertical:")
        for vid, trends in result.get("by_vertical", {}).items():
            print(f"    {vid}: {len(trends)} trends")


# =========================================================================
# CLI
# =========================================================================
def main():
    parser = argparse.ArgumentParser(
        description="ICDEV™ Research Trend Detector -- cross-session trend detection (D-RES-11)"
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable output")
    parser.add_argument("--db-path", type=Path, default=None, help="Database path override")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--detect", action="store_true", help="Detect trends from challenges (omit --session-id for cross-session)"
    )
    group.add_argument("--trends", action="store_true", help="Get trends (optional --status filter)")
    group.add_argument("--report", action="store_true", help="Generate a full trend report")

    parser.add_argument(
        "--session-id",
        type=str,
        default=None,
        help="Optional session ID for single-session detection (omit for cross-session)",
    )
    parser.add_argument(
        "--status", type=str, default=None, help="Filter by status for --trends (emerging, active, declining, stale)"
    )

    args = parser.parse_args()

    try:
        if args.detect:
            result = detect_trends(
                session_id=args.session_id,
                db_path=args.db_path,
            )
        elif args.trends:
            result = get_trends(
                status=args.status,
                db_path=args.db_path,
            )
        elif args.report:
            result = get_trend_report(db_path=args.db_path)
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
