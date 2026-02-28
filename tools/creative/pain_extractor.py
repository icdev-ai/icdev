#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Pain Point Extraction for ICDEV Creative Engine — extract pain points from creative signals.

Analyzes creative_signals entries using deterministic keyword matching and sentiment
analysis to identify, classify, and cluster customer pain points. No LLM required.
All analysis is air-gap safe, using only Python stdlib and simple regex patterns.

Architecture:
    - Reads from creative_signals table (populated by source adapters)
    - Extracts keywords via term-frequency with stopword removal (trend_detector pattern)
    - Sentiment classification via positive/negative indicator word counting (D354)
    - Category assignment by keyword overlap against config-defined categories
    - Severity estimation via indicator phrase matching
    - Pain points clustered by keyword fingerprint overlap (union-find)
    - Stores results in creative_pain_points table (append-only, D6)
    - When fingerprint exists, INSERTs new row with merged state (latest wins on query)

Usage:
    python tools/creative/pain_extractor.py --extract-all --json
    python tools/creative/pain_extractor.py --extract --signal-id "csig-xxx" --json
    python tools/creative/pain_extractor.py --list --json
    python tools/creative/pain_extractor.py --list --category ux --severity high --json
    python tools/creative/pain_extractor.py --list --limit 50 --json
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
from datetime import datetime, timezone
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
# Top N keywords to extract per signal
TOP_KEYWORDS_PER_SIGNAL = 10

# Valid categories matching creative_pain_points CHECK constraint
VALID_CATEGORIES = (
    "ux", "performance", "integration", "pricing", "compliance",
    "security", "reporting", "customization", "support", "scalability",
    "documentation", "onboarding", "api", "automation", "other",
)

# Severity indicator phrases — maps indicator text to severity level
SEVERITY_MAP = {
    # Critical indicators
    "deal breaker": "critical",
    "dealbreaker": "critical",
    "showstopper": "critical",
    "show stopper": "critical",
    "blocking": "critical",
    "unusable": "critical",
    "critical": "critical",
    "completely broken": "critical",
    "data loss": "critical",
    "security hole": "critical",
    # High indicators
    "major": "high",
    "significant": "high",
    "serious": "high",
    "broken": "high",
    "failing": "high",
    "severe": "high",
    "unacceptable": "high",
    "terrible": "high",
    "awful": "high",
    "horrible": "high",
    # Low indicators
    "minor": "low",
    "nice to have": "low",
    "nice-to-have": "low",
    "cosmetic": "low",
    "small": "low",
    "nitpick": "low",
    "polish": "low",
    "trivial": "low",
}

# Negative indicator phrases — signal that text contains a pain point
_NEGATIVE_INDICATORS = [
    "frustrating", "annoying", "painful", "difficult", "confusing",
    "slow", "broken", "buggy", "missing", "lack", "lacking", "fails",
    "failure", "error", "crash", "unusable", "terrible", "awful",
    "horrible", "poor", "worst", "hate", "impossible", "complicated",
    "unintuitive", "unreliable", "inconsistent", "clunky", "bloated",
    "outdated", "deprecated", "workaround", "hack", "kludge",
    "deal breaker", "showstopper", "blocking",
]

# Feature request indicators — signal that text requests missing capability
_FEATURE_REQUEST_INDICATORS = [
    "wish", "would be nice", "should have", "need", "needs",
    "missing feature", "feature request", "please add", "want",
    "looking for", "hoping for", "expected", "supposed to",
    "why can't", "why doesn't", "no way to", "cannot",
    "doesn't support", "does not support", "not supported",
    "no support for", "unable to", "limitation",
]

# Positive words for sentiment analysis
_POSITIVE_WORDS = [
    "great", "excellent", "love", "amazing", "perfect", "easy",
    "intuitive", "powerful", "best", "recommend", "helpful",
]

# Category keyword mapping for classification
_CATEGORY_KEYWORDS = {
    "ux": ["ui", "ux", "interface", "design", "layout", "navigation",
           "confusing", "unintuitive", "user experience", "usability",
           "workflow", "dashboard", "click", "button", "screen"],
    "performance": ["slow", "timeout", "latency", "memory", "cpu",
                    "bottleneck", "throughput", "scalability", "speed",
                    "lag", "freeze", "hang", "resource", "load time"],
    "integration": ["integrate", "integration", "connect", "connector",
                    "webhook", "sync", "import", "export", "third-party",
                    "plugin", "extension", "interop", "compatibility"],
    "pricing": ["price", "pricing", "cost", "expensive", "cheap",
                "subscription", "license", "tier", "plan", "billing",
                "free", "paid", "premium", "enterprise"],
    "compliance": ["compliance", "nist", "fedramp", "cmmc", "stig",
                   "ato", "audit", "regulation", "certification",
                   "standard", "framework", "control", "accreditation"],
    "security": ["security", "vulnerability", "exploit", "injection",
                 "authentication", "authorization", "encryption", "tls",
                 "certificate", "permission", "access control", "cve"],
    "reporting": ["report", "reporting", "dashboard", "analytics",
                  "metrics", "chart", "graph", "visualization", "data",
                  "insight", "export", "csv", "pdf"],
    "customization": ["customize", "customization", "configuration",
                      "config", "template", "theme", "personalize",
                      "flexible", "configurable", "settings", "options"],
    "support": ["support", "documentation", "help", "customer service",
                "response time", "ticket", "issue", "contact",
                "knowledge base", "community", "forum"],
    "scalability": ["scale", "scalability", "growth", "enterprise",
                    "large", "volume", "concurrent", "cluster",
                    "distributed", "horizontal", "vertical", "capacity"],
    "documentation": ["documentation", "docs", "readme", "guide",
                      "tutorial", "example", "sample", "reference",
                      "api docs", "changelog", "wiki"],
    "onboarding": ["onboarding", "getting started", "setup", "install",
                   "installation", "first time", "learning curve",
                   "tutorial", "quickstart", "beginner"],
    "api": ["api", "endpoint", "rest", "graphql", "sdk", "client",
            "library", "rate limit", "pagination", "versioning",
            "swagger", "openapi", "grpc"],
    "automation": ["automation", "automate", "pipeline", "ci", "cd",
                   "workflow", "schedule", "cron", "batch", "trigger",
                   "script", "cli", "command line"],
}

# Regex for keyword tokenization
_WORD_RE = re.compile(r"\b[a-z][a-z0-9_-]{2,}\b")


# =========================================================================
# DATABASE HELPERS
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


def _pp_id():
    """Generate unique pain point ID with pp- prefix."""
    return f"pp-{uuid.uuid4().hex[:12]}"


def _content_hash(text):
    """SHA-256 content hash for deduplication."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:32]


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
    """Load innovation config from YAML.

    Returns the full config dict. Category keywords for pain point
    classification are defined in _CATEGORY_KEYWORDS constant, but
    signal_categories from config are also used for cross-referencing.
    """
    if not _HAS_YAML:
        return {}
    if not CONFIG_PATH.exists():
        return {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# =========================================================================
# SENTIMENT ANALYSIS (D354 — deterministic, air-gap safe)
# =========================================================================
def extract_sentiment(text):
    """Deterministic sentiment classification via indicator word counting.

    Counts negative indicator matches vs positive word matches in the text.
    Classification rules:
        - If both negative and positive counts > 0: "mixed"
        - If negative > positive: "negative"
        - If positive > negative: "positive"
        - Otherwise: "neutral"

    Args:
        text: Input text to classify.

    Returns:
        One of: 'positive', 'negative', 'neutral', 'mixed'.
    """
    if not text:
        return "neutral"

    text_lower = text.lower()
    neg_count = sum(1 for ind in _NEGATIVE_INDICATORS if ind in text_lower)
    pos_count = sum(1 for word in _POSITIVE_WORDS if word in text_lower)

    if neg_count > 0 and pos_count > 0:
        return "mixed"
    if neg_count > pos_count:
        return "negative"
    if pos_count > neg_count:
        return "positive"
    return "neutral"


# =========================================================================
# KEYWORD EXTRACTION
# =========================================================================
def extract_keywords(text, top_n=TOP_KEYWORDS_PER_SIGNAL):
    """Extract top-N keywords from text using simple term-frequency.

    Approach:
        1. Lowercase and split into tokens via regex
        2. Remove stopwords and short tokens
        3. Count frequencies
        4. Return top_n most common as list of strings

    Args:
        text: Input text (title + body).
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
# CATEGORY CLASSIFICATION
# =========================================================================
def classify_category(text, keywords, config_categories=None):
    """Classify a pain point into one of the 15 valid categories.

    For each category in _CATEGORY_KEYWORDS, counts how many of the
    category's keywords appear in the extracted keywords or text. Returns
    the category with the highest overlap count. Ties broken alphabetically.
    Falls back to 'other' if no category matches.

    Args:
        text: Full text (title + body) for secondary matching.
        keywords: Extracted keywords list from the signal.
        config_categories: Optional config signal_categories for boost (unused
            in pain extraction but accepted for interface consistency).

    Returns:
        Category string from VALID_CATEGORIES.
    """
    if not text and not keywords:
        return "other"

    text_lower = (text or "").lower()
    kw_set = set(keywords) if keywords else set()
    best_category = "other"
    best_score = 0

    for category, cat_keywords in _CATEGORY_KEYWORDS.items():
        score = 0
        for ck in cat_keywords:
            ck_lower = ck.lower()
            # Check in extracted keywords (exact match)
            if ck_lower in kw_set:
                score += 2
            # Check in full text (substring match)
            elif ck_lower in text_lower:
                score += 1

        if score > best_score:
            best_score = score
            best_category = category
        elif score == best_score and score > 0:
            # Tie-break: alphabetical
            if category < best_category:
                best_category = category

    return best_category


# =========================================================================
# SEVERITY ESTIMATION
# =========================================================================
def estimate_severity(text):
    """Estimate pain point severity from indicator phrases.

    Checks text against SEVERITY_MAP phrases in priority order:
    critical > high > low. Default is 'medium'.

    Args:
        text: Input text to analyze.

    Returns:
        One of: 'critical', 'high', 'medium', 'low'.
    """
    if not text:
        return "medium"

    text_lower = text.lower()

    # Check in priority order: critical first, then high, then low
    found_critical = False
    found_high = False
    found_low = False

    for indicator, severity in SEVERITY_MAP.items():
        if indicator in text_lower:
            if severity == "critical":
                found_critical = True
            elif severity == "high":
                found_high = True
            elif severity == "low":
                found_low = True

    if found_critical:
        return "critical"
    if found_high:
        return "high"
    if found_low:
        return "low"
    return "medium"


# =========================================================================
# SINGLE SIGNAL EXTRACTION
# =========================================================================
def extract_pain_points_from_signal(signal, config=None):
    """Extract pain points from a single creative signal.

    Skips signals with purely positive sentiment. Checks for negative
    or feature-request indicators in the signal body. If no indicators
    match, returns an empty list.

    Args:
        signal: Dict with keys: id, title, body, sentiment (optional),
                competitor_id (optional).
        config: Optional config dict (currently unused, reserved).

    Returns:
        List of 0 or 1 pain point dicts. Each dict contains:
            title, description, category, keywords, keyword_fingerprint,
            severity, signal_id, competitor_id.
    """
    title = signal.get("title", "") or ""
    body = signal.get("body", "") or ""
    signal_id = signal.get("id", "")
    competitor_id = signal.get("competitor_id")
    full_text = f"{title} {body}"

    # Check existing sentiment on the signal, or compute it
    sentiment = signal.get("sentiment")
    if not sentiment:
        sentiment = extract_sentiment(full_text)

    # Skip purely positive signals — no pain point to extract
    if sentiment == "positive":
        return []

    # Check for negative or feature-request indicators
    text_lower = full_text.lower()
    has_negative = any(ind in text_lower for ind in _NEGATIVE_INDICATORS)
    has_feature_request = any(ind in text_lower for ind in _FEATURE_REQUEST_INDICATORS)

    if not has_negative and not has_feature_request:
        return []

    # Extract keywords from combined title + body
    keywords = extract_keywords(full_text, TOP_KEYWORDS_PER_SIGNAL)
    if not keywords:
        return []

    # Classify category
    category = classify_category(full_text, keywords)

    # Estimate severity
    severity = estimate_severity(full_text)

    # Build the pain point dict
    pp_title = title[:80].strip() if title else body[:80].strip()
    pp_description = body[:500].strip() if body else title[:500].strip()
    fingerprint = _keyword_fingerprint(keywords)

    pain_point = {
        "title": pp_title,
        "description": pp_description,
        "category": category,
        "keywords": keywords,
        "keyword_fingerprint": fingerprint,
        "severity": severity,
        "signal_id": signal_id,
        "competitor_id": competitor_id,
    }

    return [pain_point]


# =========================================================================
# CLUSTERING
# =========================================================================
def cluster_pain_points(pain_points, min_shared_keywords=3):
    """Cluster pain points by keyword overlap using greedy union-find.

    Builds keyword overlap between all pain point pairs. Groups those
    sharing >= min_shared_keywords into the same cluster. Each cluster
    is consolidated into one representative pain point.

    Args:
        pain_points: List of pain point dicts from extract_pain_points_from_signal.
        min_shared_keywords: Minimum shared keywords to merge two pain points.

    Returns:
        List of consolidated pain point dicts with:
            title, description, category, keywords, keyword_fingerprint,
            severity, signal_ids, competitor_ids, frequency.
    """
    if not pain_points:
        return []

    n = len(pain_points)
    if n == 1:
        pp = pain_points[0]
        return [{
            "title": pp["title"],
            "description": pp["description"],
            "category": pp["category"],
            "keywords": pp["keywords"],
            "keyword_fingerprint": pp["keyword_fingerprint"],
            "severity": pp["severity"],
            "signal_ids": [pp["signal_id"]] if pp.get("signal_id") else [],
            "competitor_ids": [pp["competitor_id"]] if pp.get("competitor_id") else [],
            "frequency": 1,
        }]

    # Union-find data structure
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        rx, ry = find(x), find(y)
        if rx != ry:
            parent[ry] = rx

    # Build keyword sets for each pain point
    kw_sets = []
    for pp in pain_points:
        kw_sets.append(set(pp.get("keywords", [])))

    # Find pairs with sufficient overlap and merge
    for i in range(n):
        for j in range(i + 1, n):
            shared = kw_sets[i] & kw_sets[j]
            if len(shared) >= min_shared_keywords:
                union(i, j)

    # Group by cluster root
    clusters = defaultdict(list)
    for i in range(n):
        root = find(i)
        clusters[root].append(i)

    # Severity priority for taking the highest severity in a cluster
    severity_rank = {"critical": 4, "high": 3, "medium": 2, "low": 1}

    # Consolidate each cluster into one pain point
    consolidated = []
    for indices in clusters.values():
        cluster_pps = [pain_points[i] for i in indices]

        # Title: most frequent title
        title_counts = Counter(pp["title"] for pp in cluster_pps)
        best_title = title_counts.most_common(1)[0][0]

        # Description: longest description (most detail)
        best_description = max(
            (pp.get("description", "") for pp in cluster_pps),
            key=len,
        )

        # Keywords: union of all keywords
        all_keywords = set()
        for pp in cluster_pps:
            all_keywords.update(pp.get("keywords", []))
        all_keywords_sorted = sorted(all_keywords)

        # Signal IDs: union
        signal_ids = []
        for pp in cluster_pps:
            sid = pp.get("signal_id")
            if sid and sid not in signal_ids:
                signal_ids.append(sid)

        # Competitor IDs: union
        competitor_ids = []
        for pp in cluster_pps:
            cid = pp.get("competitor_id")
            if cid and cid not in competitor_ids:
                competitor_ids.append(cid)

        # Category: most common
        cat_counts = Counter(pp["category"] for pp in cluster_pps)
        best_category = cat_counts.most_common(1)[0][0]

        # Severity: highest in cluster
        best_severity = max(
            (pp.get("severity", "medium") for pp in cluster_pps),
            key=lambda s: severity_rank.get(s, 2),
        )

        # Fingerprint: hash of sorted union keywords
        fingerprint = _keyword_fingerprint(all_keywords_sorted)

        consolidated.append({
            "title": best_title,
            "description": best_description,
            "category": best_category,
            "keywords": all_keywords_sorted,
            "keyword_fingerprint": fingerprint,
            "severity": best_severity,
            "signal_ids": signal_ids,
            "competitor_ids": competitor_ids,
            "frequency": len(cluster_pps),
        })

    return consolidated


# =========================================================================
# DATABASE MERGE (append-only, D6)
# =========================================================================
def merge_with_existing(new_pain_points, db_path=None):
    """Merge new pain points into creative_pain_points table.

    Append-only strategy (D6): never UPDATE or DELETE existing rows.
    For each new pain point:
        - If keyword_fingerprint does NOT exist: INSERT new row.
        - If keyword_fingerprint DOES exist: INSERT new row with merged
          signal_ids and incremented frequency. The latest row (by rowid)
          supersedes on query.

    Args:
        new_pain_points: List of consolidated pain point dicts from cluster_pain_points.
        db_path: Optional database path override.

    Returns:
        Dict with {new_count, merged_count, total_stored}.
    """
    if not new_pain_points:
        return {"new_count": 0, "merged_count": 0, "total_stored": 0}

    conn = _get_db(db_path)
    now = _now()
    new_count = 0
    merged_count = 0

    try:
        for pp in new_pain_points:
            fingerprint = pp["keyword_fingerprint"]

            # Check if this fingerprint already exists (take latest row)
            existing = conn.execute(
                """SELECT id, frequency, signal_ids, competitor_ids,
                          first_seen, keywords
                   FROM creative_pain_points
                   WHERE keyword_fingerprint = ?
                   ORDER BY rowid DESC LIMIT 1""",
                (fingerprint,),
            ).fetchone()

            if existing:
                # Merge: combine signal_ids, increment frequency
                existing_signal_ids = json.loads(existing["signal_ids"] or "[]")
                existing_competitor_ids = json.loads(
                    existing["competitor_ids"] if existing["competitor_ids"] else "[]"
                )
                existing_keywords = json.loads(existing["keywords"] or "[]")

                # Union signal IDs
                merged_signal_ids = list(set(existing_signal_ids + pp["signal_ids"]))
                merged_competitor_ids = list(
                    set(existing_competitor_ids + pp.get("competitor_ids", []))
                )
                merged_keywords = sorted(set(existing_keywords + pp["keywords"]))
                merged_frequency = existing["frequency"] + pp["frequency"]
                first_seen = existing["first_seen"]

                # Recompute fingerprint from merged keywords
                merged_fingerprint = _keyword_fingerprint(merged_keywords)

                conn.execute(
                    """INSERT INTO creative_pain_points
                       (id, title, description, category, frequency, signal_ids,
                        competitor_ids, keyword_fingerprint, keywords, severity,
                        status, first_seen, last_seen, classification)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?, ?, 'CUI')""",
                    (
                        _pp_id(),
                        pp["title"],
                        pp.get("description", ""),
                        pp["category"],
                        merged_frequency,
                        json.dumps(merged_signal_ids),
                        json.dumps(merged_competitor_ids),
                        merged_fingerprint,
                        json.dumps(merged_keywords),
                        pp["severity"],
                        first_seen,
                        now,
                    ),
                )
                merged_count += 1
            else:
                # Brand new pain point
                conn.execute(
                    """INSERT INTO creative_pain_points
                       (id, title, description, category, frequency, signal_ids,
                        competitor_ids, keyword_fingerprint, keywords, severity,
                        status, first_seen, last_seen, classification)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?, ?, 'CUI')""",
                    (
                        _pp_id(),
                        pp["title"],
                        pp.get("description", ""),
                        pp["category"],
                        pp["frequency"],
                        json.dumps(pp["signal_ids"]),
                        json.dumps(pp.get("competitor_ids", [])),
                        fingerprint,
                        json.dumps(pp["keywords"]),
                        pp["severity"],
                        now,
                        now,
                    ),
                )
                new_count += 1

        conn.commit()

        # Count total stored (unique fingerprints, latest row each)
        total_row = conn.execute(
            "SELECT COUNT(DISTINCT keyword_fingerprint) as cnt FROM creative_pain_points"
        ).fetchone()
        total_stored = total_row["cnt"] if total_row else 0

        return {
            "new_count": new_count,
            "merged_count": merged_count,
            "total_stored": total_stored,
        }
    finally:
        conn.close()


# =========================================================================
# EXTRACT ALL NEW SIGNALS
# =========================================================================
def extract_all_new(db_path=None):
    """Extract pain points from all unprocessed creative signals.

    Queries creative_signals whose IDs do not appear in any existing
    creative_pain_points.signal_ids JSON arrays. For each unprocessed
    signal, extracts pain points, clusters them, and merges into the DB.

    Args:
        db_path: Optional database path override.

    Returns:
        Dict with {signals_processed, pain_points_extracted,
        clusters_formed, merge_result}.
    """
    config = _load_config()
    conn = _get_db(db_path)

    try:
        # Get all signal IDs already referenced in pain points
        existing_rows = conn.execute(
            "SELECT signal_ids FROM creative_pain_points"
        ).fetchall()

        processed_ids = set()
        for row in existing_rows:
            try:
                ids = json.loads(row["signal_ids"] or "[]")
                processed_ids.update(ids)
            except (json.JSONDecodeError, TypeError):
                pass

        # Fetch all creative signals
        all_signals = conn.execute(
            """SELECT id, title, body, sentiment, competitor_id,
                      source, source_type, rating, upvotes, discovered_at
               FROM creative_signals
               ORDER BY discovered_at DESC"""
        ).fetchall()

        if not all_signals:
            return {
                "signals_processed": 0,
                "pain_points_extracted": 0,
                "clusters_formed": 0,
                "merge_result": {"new_count": 0, "merged_count": 0, "total_stored": 0},
            }

        # Filter to unprocessed signals
        unprocessed = [
            dict(row) for row in all_signals if row["id"] not in processed_ids
        ]

        if not unprocessed:
            # Count total stored for the response
            total_row = conn.execute(
                "SELECT COUNT(DISTINCT keyword_fingerprint) as cnt "
                "FROM creative_pain_points"
            ).fetchone()
            total_stored = total_row["cnt"] if total_row else 0
            return {
                "signals_processed": 0,
                "pain_points_extracted": 0,
                "clusters_formed": 0,
                "merge_result": {
                    "new_count": 0,
                    "merged_count": 0,
                    "total_stored": total_stored,
                },
                "note": "All signals already processed.",
            }

    finally:
        conn.close()

    # Extract pain points from each unprocessed signal
    all_pain_points = []
    for signal in unprocessed:
        extracted = extract_pain_points_from_signal(signal, config)
        all_pain_points.extend(extracted)

    signals_processed = len(unprocessed)
    pain_points_extracted = len(all_pain_points)

    # Cluster the extracted pain points
    clustered = cluster_pain_points(all_pain_points, min_shared_keywords=3)
    clusters_formed = len(clustered)

    # Merge with existing DB
    merge_result = merge_with_existing(clustered, db_path=db_path)

    _audit(
        "creative.pain_extraction",
        "creative-engine",
        f"Extracted {pain_points_extracted} pain points from "
        f"{signals_processed} signals, formed {clusters_formed} clusters",
        {
            "signals_processed": signals_processed,
            "pain_points_extracted": pain_points_extracted,
            "clusters_formed": clusters_formed,
            "new_count": merge_result["new_count"],
            "merged_count": merge_result["merged_count"],
        },
    )

    return {
        "signals_processed": signals_processed,
        "pain_points_extracted": pain_points_extracted,
        "clusters_formed": clusters_formed,
        "merge_result": merge_result,
    }


# =========================================================================
# EXTRACT SINGLE SIGNAL
# =========================================================================
def extract_from_signal(signal_id, db_path=None):
    """Extract pain points from a single creative signal by ID.

    Fetches the signal from the database, runs extraction, clustering
    (with itself only), and merges into creative_pain_points.

    Args:
        signal_id: The creative signal ID (e.g., "csig-xxx").
        db_path: Optional database path override.

    Returns:
        Dict with extraction result for the single signal.
    """
    conn = _get_db(db_path)
    try:
        row = conn.execute(
            """SELECT id, title, body, sentiment, competitor_id,
                      source, source_type, rating, upvotes, discovered_at
               FROM creative_signals WHERE id = ?""",
            (signal_id,),
        ).fetchone()

        if not row:
            return {"error": f"Signal not found: {signal_id}"}

        signal = dict(row)
    finally:
        conn.close()

    config = _load_config()
    extracted = extract_pain_points_from_signal(signal, config)

    if not extracted:
        return {
            "signal_id": signal_id,
            "pain_points_extracted": 0,
            "note": "No pain point indicators found in this signal.",
        }

    # Cluster (single signal, so clustering is trivial)
    clustered = cluster_pain_points(extracted, min_shared_keywords=3)
    merge_result = merge_with_existing(clustered, db_path=db_path)

    _audit(
        "creative.pain_extraction",
        "creative-engine",
        f"Extracted pain point from signal {signal_id}",
        {"signal_id": signal_id, "clusters": len(clustered)},
    )

    return {
        "signal_id": signal_id,
        "pain_points_extracted": len(extracted),
        "clusters_formed": len(clustered),
        "merge_result": merge_result,
        "pain_points": clustered,
    }


# =========================================================================
# LIST PAIN POINTS
# =========================================================================
def list_pain_points(category=None, severity=None, limit=50, db_path=None):
    """List stored pain points with optional filtering.

    Queries creative_pain_points, deduplicating by keyword_fingerprint
    (takes the latest row per fingerprint). Supports filtering by
    category and severity.

    Args:
        category: Optional category filter (must be in VALID_CATEGORIES).
        severity: Optional severity filter ('critical', 'high', 'medium', 'low').
        limit: Maximum number of results (default 50).
        db_path: Optional database path override.

    Returns:
        Dict with pain_points list and summary statistics.
    """
    conn = _get_db(db_path)
    try:
        # Build query — use subquery to get latest row per fingerprint
        query = """
            SELECT cp.*
            FROM creative_pain_points cp
            INNER JOIN (
                SELECT keyword_fingerprint, MAX(rowid) as max_rowid
                FROM creative_pain_points
                GROUP BY keyword_fingerprint
            ) latest ON cp.keyword_fingerprint = latest.keyword_fingerprint
                     AND cp.rowid = latest.max_rowid
        """
        conditions = []
        params = []

        if category:
            if category not in VALID_CATEGORIES:
                return {"error": f"Invalid category: {category}. "
                        f"Valid: {', '.join(VALID_CATEGORIES)}"}
            conditions.append("cp.category = ?")
            params.append(category)

        if severity:
            valid_severities = ("critical", "high", "medium", "low")
            if severity not in valid_severities:
                return {"error": f"Invalid severity: {severity}. "
                        f"Valid: {', '.join(valid_severities)}"}
            conditions.append("cp.severity = ?")
            params.append(severity)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY cp.frequency DESC, cp.last_seen DESC"
        query += f" LIMIT {int(limit)}"

        rows = conn.execute(query, params).fetchall()

        pain_points = []
        for row in rows:
            pain_points.append({
                "id": row["id"],
                "title": row["title"],
                "description": row["description"],
                "category": row["category"],
                "frequency": row["frequency"],
                "severity": row["severity"],
                "status": row["status"],
                "keywords": json.loads(row["keywords"]) if row["keywords"] else [],
                "signal_ids": json.loads(row["signal_ids"]) if row["signal_ids"] else [],
                "competitor_ids": (
                    json.loads(row["competitor_ids"])
                    if row["competitor_ids"]
                    else []
                ),
                "composite_score": row["composite_score"],
                "first_seen": row["first_seen"],
                "last_seen": row["last_seen"],
            })

        # Summary stats
        total_row = conn.execute(
            "SELECT COUNT(DISTINCT keyword_fingerprint) as cnt "
            "FROM creative_pain_points"
        ).fetchone()
        total_unique = total_row["cnt"] if total_row else 0

        # Category distribution
        cat_rows = conn.execute(
            """SELECT category, COUNT(DISTINCT keyword_fingerprint) as cnt
               FROM creative_pain_points
               GROUP BY category ORDER BY cnt DESC"""
        ).fetchall()
        category_dist = {row["category"]: row["cnt"] for row in cat_rows}

        # Severity distribution
        sev_rows = conn.execute(
            """SELECT severity, COUNT(DISTINCT keyword_fingerprint) as cnt
               FROM creative_pain_points
               GROUP BY severity ORDER BY cnt DESC"""
        ).fetchall()
        severity_dist = {row["severity"]: row["cnt"] for row in sev_rows}

        return {
            "generated_at": _now(),
            "total_unique": total_unique,
            "returned": len(pain_points),
            "filters": {
                "category": category,
                "severity": severity,
                "limit": limit,
            },
            "category_distribution": category_dist,
            "severity_distribution": severity_dist,
            "pain_points": pain_points,
        }
    finally:
        conn.close()


# =========================================================================
# CLI
# =========================================================================
def main():
    parser = argparse.ArgumentParser(
        description="ICDEV Pain Point Extractor — extract pain points "
        "from creative signals using deterministic keyword/sentiment analysis"
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable output")
    parser.add_argument(
        "--db-path", type=Path, default=None, help="Database path override"
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--extract-all",
        action="store_true",
        help="Extract pain points from all unprocessed creative signals",
    )
    group.add_argument(
        "--extract",
        action="store_true",
        help="Extract pain point from a single signal (requires --signal-id)",
    )
    group.add_argument(
        "--list",
        action="store_true",
        help="List stored pain points with optional filters",
    )

    parser.add_argument(
        "--signal-id", type=str, default=None,
        help="Signal ID for --extract command (e.g., 'csig-xxx')",
    )
    parser.add_argument(
        "--category", type=str, default=None,
        help="Filter by category (e.g., 'ux', 'performance', 'security')",
    )
    parser.add_argument(
        "--severity", type=str, default=None,
        help="Filter by severity ('critical', 'high', 'medium', 'low')",
    )
    parser.add_argument(
        "--limit", type=int, default=50,
        help="Maximum results for --list (default: 50)",
    )

    args = parser.parse_args()

    try:
        if args.extract_all:
            result = extract_all_new(db_path=args.db_path)
        elif args.extract:
            if not args.signal_id:
                print(
                    "ERROR: --extract requires --signal-id", file=sys.stderr
                )
                sys.exit(1)
            result = extract_from_signal(
                signal_id=args.signal_id, db_path=args.db_path
            )
        elif args.list:
            result = list_pain_points(
                category=args.category,
                severity=args.severity,
                limit=args.limit,
                db_path=args.db_path,
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

    if args.extract_all:
        print("Pain Point Extraction — All Signals")
        print(f"  Signals processed:     {result.get('signals_processed', 0)}")
        print(f"  Pain points extracted:  {result.get('pain_points_extracted', 0)}")
        print(f"  Clusters formed:        {result.get('clusters_formed', 0)}")
        mr = result.get("merge_result", {})
        print(f"  New pain points:        {mr.get('new_count', 0)}")
        print(f"  Merged with existing:   {mr.get('merged_count', 0)}")
        print(f"  Total stored (unique):  {mr.get('total_stored', 0)}")
        if result.get("note"):
            print(f"  Note: {result['note']}")

    elif args.extract:
        print(f"Pain Point Extraction — Signal {result.get('signal_id', '')}")
        print(f"  Pain points extracted: {result.get('pain_points_extracted', 0)}")
        print(f"  Clusters formed:       {result.get('clusters_formed', 0)}")
        if result.get("note"):
            print(f"  Note: {result['note']}")
        for pp in result.get("pain_points", []):
            print(f"\n  [{pp.get('severity', 'medium').upper()}] {pp['title']}")
            print(f"    Category: {pp['category']}")
            print(f"    Keywords: {', '.join(pp.get('keywords', [])[:8])}")
            print(f"    Signals:  {len(pp.get('signal_ids', []))}")

    elif args.list:
        print(f"Pain Points — {result.get('generated_at', '')}")
        print(f"  Total unique: {result.get('total_unique', 0)}")
        print(f"  Returned:     {result.get('returned', 0)}")
        filters = result.get("filters", {})
        active_filters = [
            f"{k}={v}" for k, v in filters.items() if v is not None
        ]
        if active_filters:
            print(f"  Filters:      {', '.join(active_filters)}")

        # Category distribution
        cat_dist = result.get("category_distribution", {})
        if cat_dist:
            print("\n  Category Distribution:")
            for cat, cnt in sorted(cat_dist.items(), key=lambda x: -x[1]):
                bar = "#" * min(cnt, 40)
                print(f"    {cat:16s} {cnt:4d} {bar}")

        # Severity distribution
        sev_dist = result.get("severity_distribution", {})
        if sev_dist:
            print("\n  Severity Distribution:")
            sev_order = ["critical", "high", "medium", "low"]
            for sev in sev_order:
                cnt = sev_dist.get(sev, 0)
                if cnt > 0:
                    icon = {"critical": "[!!!]", "high": "[!! ]",
                            "medium": "[ ! ]", "low": "[   ]"}.get(sev, "[   ]")
                    print(f"    {icon} {sev:10s} {cnt:4d}")

        # Pain points
        print()
        for pp in result.get("pain_points", []):
            sev_icon = {
                "critical": "[CRT]",
                "high": "[HGH]",
                "medium": "[MED]",
                "low": "[LOW]",
            }.get(pp.get("severity", "medium"), "[???]")
            freq = pp.get("frequency", 1)
            print(
                f"  {sev_icon} {pp['title'][:60]}"
                f"  ({pp['category']}, freq={freq})"
            )
            kws = ", ".join(pp.get("keywords", [])[:5])
            print(f"         keywords: {kws}")


if __name__ == "__main__":
    main()
