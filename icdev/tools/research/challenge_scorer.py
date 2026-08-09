#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""6-Dimension Composite Challenge Scorer for ICDEV™ Research Engine.

Scores research challenges using a 6-dimension weighted average
(D21 deterministic scoring pattern, D-RES-4):

  1. market_demand          (0.25) -- signal frequency, upvotes, citations
  2. regulatory_pressure    (0.20) -- regulation count, enforcement actions
  3. technical_complexity   (0.15) -- academic paper density, patent activity
  4. competitive_saturation (0.15) -- inverse: fewer solutions = bigger opportunity
  5. icdev_readiness        (0.15) -- ICDEV™ capability coverage score
  6. compliance_alignment   (0.10) -- maps to existing ICDEV™ framework = 1.0

Architecture:
    - Weights loaded from args/research_config.yaml under scoring.weights (D26 pattern)
    - Thresholds: critical >= 0.80, notable >= 0.50, appendix < 0.50
    - Status transitions: new -> scored (after scoring)
    - Composite score + dimension breakdown stored in research_challenges table
    - Signal clustering groups signals by keyword overlap into challenges
    - All scoring is deterministic (D21 -- reproducible, not probabilistic)

Usage:
    # Cluster signals into challenges
    python tools/research/challenge_scorer.py --cluster --session-id "rsess-xxx" --json

    # Score all new (unscored) challenges
    python tools/research/challenge_scorer.py --score --session-id "rsess-xxx" --json

    # Score a single challenge
    python tools/research/challenge_scorer.py --score-one --challenge-id "rchal-xxx" --json

    # Get top challenges by composite score
    python tools/research/challenge_scorer.py --top --session-id "rsess-xxx" --limit 20 --json

    # List all challenges for a session
    python tools/research/challenge_scorer.py --challenges --session-id "rsess-xxx" --json

    # Human-readable output
    python tools/research/challenge_scorer.py --score --session-id "rsess-xxx" --human
    python tools/research/challenge_scorer.py --top --session-id "rsess-xxx" --human
"""

import argparse
import hashlib
import json
import os
import re
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
# Hardcoded English stopwords (~100 common words) -- no NLTK dependency needed
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

_WORD_RE = re.compile(r"\b[a-z][a-z0-9_-]{2,}\b")

# Valid categories matching research_challenges CHECK constraint
VALID_CATEGORIES = (
    "infrastructure",
    "compliance",
    "security",
    "ux",
    "performance",
    "integration",
    "data",
    "cost",
    "scalability",
    "automation",
    "governance",
    "other",
)

# Category keyword banks for classification
CATEGORY_KEYWORDS = {
    "infrastructure": [
        "infrastructure",
        "latency",
        "throughput",
        "architecture",
        "microservice",
        "deployment",
        "hosting",
        "cloud",
        "server",
    ],
    "compliance": [
        "compliance",
        "regulatory",
        "cftc",
        "nfa",
        "sec",
        "audit",
        "reporting",
        "regulation",
        "mandate",
        "requirement",
    ],
    "security": [
        "security",
        "encryption",
        "authentication",
        "vulnerability",
        "breach",
        "cyber",
        "penetration",
        "firewall",
        "access-control",
    ],
    "ux": [
        "interface",
        "ui",
        "ux",
        "dashboard",
        "usability",
        "workflow",
        "user-experience",
        "frontend",
        "display",
    ],
    "performance": [
        "performance",
        "speed",
        "latency",
        "throughput",
        "optimization",
        "response-time",
        "benchmark",
        "fast",
    ],
    "integration": [
        "integration",
        "api",
        "connector",
        "exchange",
        "broker",
        "interoperability",
        "plugin",
        "webhook",
    ],
    "data": [
        "data",
        "feed",
        "market-data",
        "historical",
        "real-time",
        "streaming",
        "analytics",
        "database",
        "etl",
        "pipeline",
    ],
    "cost": [
        "cost",
        "pricing",
        "fee",
        "commission",
        "subscription",
        "budget",
        "expensive",
        "affordable",
        "license",
    ],
    "scalability": [
        "scale",
        "concurrent",
        "volume",
        "enterprise",
        "horizontal",
        "vertical",
        "distributed",
        "cluster",
    ],
    "automation": [
        "automate",
        "automation",
        "algorithm",
        "bot",
        "systematic",
        "scheduled",
        "trigger",
        "orchestration",
        "workflow-engine",
    ],
    "governance": [
        "governance",
        "model",
        "drift",
        "explainability",
        "bias",
        "fairness",
        "oversight",
        "accountability",
        "transparency",
    ],
    "other": [],
}

# =========================================================================
# DEFAULT CONFIGURATION
# =========================================================================
DEFAULT_WEIGHTS = {
    "market_demand": 0.25,
    "regulatory_pressure": 0.20,
    "technical_complexity": 0.15,
    "competitive_saturation": 0.15,
    "icdev_readiness": 0.15,
    "compliance_alignment": 0.10,
}

DEFAULT_THRESHOLDS = {
    "critical": 0.80,
    "notable": 0.50,
    "appendix": 0.00,
}


# =========================================================================
# HELPERS
# =========================================================================
def _get_db(db_path=None):
    """Get database connection with dict-like row access."""
    path = db_path or DB_PATH
    if not Path(str(path)).exists():
        raise FileNotFoundError(f"Database not found: {path}\nRun: python tools/db/init_icdev_db.py")
    conn = get_connection(db_path=str(path))
    return conn


def _now():
    """ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _challenge_id():
    """Generate a research challenge ID with rchal- prefix."""
    return f"rchal-{uuid.uuid4().hex[:12]}"


def _audit(event_type, action, details=None):
    """Write audit trail entry (best-effort, never raises)."""
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
    """Load research config from YAML with fallback defaults."""
    if not _HAS_YAML:
        return {}
    if not CONFIG_PATH.exists():
        return {}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _get_weights(config=None):
    """Extract scoring weights from config, falling back to defaults."""
    if config is None:
        config = _load_config()
    scoring = config.get("scoring", {})
    weights = scoring.get("weights", {})
    result = {}
    for dim, default_val in DEFAULT_WEIGHTS.items():
        result[dim] = float(weights.get(dim, default_val))
    # Normalize weights to sum to 1.0
    total = sum(result.values())
    if total > 0 and abs(total - 1.0) > 0.001:
        result = {k: v / total for k, v in result.items()}
    return result


def _get_thresholds(config=None):
    """Extract scoring thresholds from config, falling back to defaults."""
    if config is None:
        config = _load_config()
    scoring = config.get("scoring", {})
    thresholds = scoring.get("thresholds", {})
    return {
        "critical": float(thresholds.get("critical", DEFAULT_THRESHOLDS["critical"])),
        "notable": float(thresholds.get("notable", DEFAULT_THRESHOLDS["notable"])),
        "appendix": float(thresholds.get("appendix", DEFAULT_THRESHOLDS["appendix"])),
    }


# =========================================================================
# KEYWORD EXTRACTION & CLUSTERING UTILITIES
# =========================================================================
def extract_keywords(text, top_n=10):
    """Extract top-N keywords from text via term-frequency with stopword removal.

    Args:
        text: Raw text to extract keywords from.
        top_n: Maximum number of keywords to return (default 10).

    Returns:
        List of keyword strings, sorted by frequency descending.
    """
    if not text:
        return []
    tokens = _WORD_RE.findall(text.lower())
    filtered = [t for t in tokens if t not in STOPWORDS and len(t) >= 3]
    counts = Counter(filtered)
    return [kw for kw, _ in counts.most_common(top_n)]


def _keyword_fingerprint(keywords):
    """SHA-256 fingerprint of sorted comma-joined keywords, truncated to 32 chars."""
    if not keywords:
        return hashlib.sha256(b"").hexdigest()[:32]
    canonical = ",".join(sorted(keywords))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def _content_hash(text):
    """SHA-256 hash of text, truncated to 32 chars."""
    if not text:
        return hashlib.sha256(b"").hexdigest()[:32]
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:32]


def classify_category(text, keywords):
    """Map text+keywords to a category using keyword bank overlap scoring.

    Args:
        text: Full text to classify.
        keywords: List of extracted keywords.

    Returns:
        Category string from VALID_CATEGORIES.
    """
    combined = (text or "").lower() + " " + " ".join(kw.lower() for kw in keywords)
    scores = {}
    for cat, bank in CATEGORY_KEYWORDS.items():
        if cat == "other":
            continue
        overlap = 0
        for term in bank:
            if term.lower() in combined:
                overlap += 1
        scores[cat] = overlap

    if not scores or max(scores.values()) == 0:
        return "other"

    best_cat = max(scores, key=scores.get)
    return best_cat


# =========================================================================
# DIMENSION SCORERS
# =========================================================================
def _score_market_demand(challenge_data, signals, conn, config):
    """Score market demand dimension.

    Measures signal frequency normalized by max in session, with
    upvote and citation boosts.

    Formula: min(1.0, signal_count / max_signals * 0.6
                      + avg_upvotes / 100 * 0.2
                      + avg_citations / 50 * 0.2)

    Args:
        challenge_data: Dict of challenge row from DB.
        signals: List of signal dicts associated with this challenge.
        conn: Open database connection.
        config: Loaded config dict.

    Returns:
        Float in [0.0, 1.0].
    """
    session_id = challenge_data.get("session_id", "")
    signal_count = len(signals) if signals else int(challenge_data.get("signal_count", 1) or 1)

    # Get max signal count across all challenges in this session
    try:
        max_row = conn.execute(
            "SELECT MAX(signal_count) as max_sc FROM research_challenges WHERE session_id = %s",
            (session_id,),
        ).fetchone()
        max_signals = max_row["max_sc"] if max_row and max_row["max_sc"] else signal_count
    except Exception:
        max_signals = signal_count

    max_signals = max(max_signals, 1)

    # Compute average upvotes and citations from signals
    avg_upvotes = 0.0
    avg_citations = 0.0
    if signals:
        total_upvotes = sum(int(s.get("upvotes", 0) or 0) for s in signals)
        total_citations = sum(int(s.get("citations", 0) or 0) for s in signals)
        avg_upvotes = total_upvotes / max(len(signals), 1)
        avg_citations = total_citations / max(len(signals), 1)

    score = min(
        1.0,
        (signal_count / max_signals) * 0.6 + (avg_upvotes / 100.0) * 0.2 + (avg_citations / 50.0) * 0.2,
    )

    return max(0.0, min(1.0, score))


def _score_regulatory_pressure(challenge_data, signals, conn, config):
    """Score regulatory pressure dimension.

    Counts regulatory signals, enforcement actions get 1.5x boost,
    deadlines get 1.3x boost. Queries research_regulatory_map for the
    challenge.

    Args:
        challenge_data: Dict of challenge row from DB.
        signals: List of signal dicts associated with this challenge.
        conn: Open database connection.
        config: Loaded config dict.

    Returns:
        Float in [0.0, 1.0].
    """
    challenge_id = challenge_data.get("id", "")

    # Get enforcement and deadline boosts from config
    scoring_cfg = config.get("scoring", {})
    enforcement_boost = float(scoring_cfg.get("enforcement_boost", 1.5))
    deadline_boost = float(scoring_cfg.get("deadline_boost", 1.3))

    # Count regulatory signals from the signals list
    reg_signal_count = 0
    if signals:
        for s in signals:
            source = s.get("source", "")
            if source == "regulatory_body":
                reg_signal_count += 1

    # Query research_regulatory_map for this challenge
    total_enforcement = 0
    has_deadline = False
    try:
        reg_rows = conn.execute(
            "SELECT enforcement_actions, deadline FROM research_regulatory_map WHERE challenge_id = %s",
            (challenge_id,),
        ).fetchall()
        for rr in reg_rows:
            reg_signal_count += 1
            ea = int(rr["enforcement_actions"] or 0)
            total_enforcement += ea
            if rr["deadline"]:
                has_deadline = True
    except Exception:
        pass

    if reg_signal_count == 0:
        return 0.0

    # Base score: regulation count normalized (5 regulations = 1.0)
    base = min(reg_signal_count / 5.0, 1.0)

    # Apply boosts
    if total_enforcement > 0:
        base = min(base * enforcement_boost, 1.0)
    if has_deadline:
        base = min(base * deadline_boost, 1.0)

    return max(0.0, min(1.0, base))


def _score_technical_complexity(challenge_data, signals, conn, config):
    """Score technical complexity dimension.

    Academic paper density and patent activity. More papers + patents
    means a bigger moat, which means higher score (harder to replicate).

    Args:
        challenge_data: Dict of challenge row from DB.
        signals: List of signal dicts associated with this challenge.
        conn: Open database connection.
        config: Loaded config dict.

    Returns:
        Float in [0.0, 1.0].
    """
    paper_count = 0
    patent_count = 0

    if signals:
        for s in signals:
            source = s.get("source", "")
            if source == "academic_paper":
                paper_count += 1
            elif source == "patent":
                patent_count += 1

    if paper_count == 0 and patent_count == 0:
        return 0.3  # neutral baseline when no academic/patent data

    # Normalize: 10 papers = 1.0, 5 patents = 1.0
    paper_score = min(paper_count / 10.0, 1.0)
    patent_score = min(patent_count / 5.0, 1.0)

    # Weighted combination: papers 0.6, patents 0.4
    score = paper_score * 0.6 + patent_score * 0.4

    return max(0.0, min(1.0, score))


def _score_competitive_saturation(challenge_data, signals, conn, config):
    """Score competitive saturation dimension (INVERSE).

    Fewer existing solutions = bigger opportunity = higher score.
    Counts open source projects and commercial solutions from signals.

    Args:
        challenge_data: Dict of challenge row from DB.
        signals: List of signal dicts associated with this challenge.
        conn: Open database connection.
        config: Loaded config dict.

    Returns:
        Float in [0.0, 1.0]. Higher = fewer competitors = bigger opportunity.
    """
    oss_count = 0
    commercial_count = 0

    if signals:
        for s in signals:
            source = s.get("source", "")
            if source == "open_source":
                oss_count += 1
            elif source == "saas_commercial":
                commercial_count += 1

    total_solutions = oss_count + commercial_count

    if total_solutions == 0:
        return 0.8  # no known solutions = big opportunity

    # Inverse: more solutions = lower score
    # 10+ solutions = 0.1 (saturated market), 1 solution = 0.9
    score = max(0.1, 1.0 - (total_solutions / 12.0))

    return max(0.0, min(1.0, score))


def _score_icdev_readiness(challenge_data, signals, conn, config):
    """Score ICDEV™ readiness dimension.

    Queries research_capability_map for the challenge and uses coverage_score.
    If no mapping yet, returns 0.5 (neutral).

    Args:
        challenge_data: Dict of challenge row from DB.
        signals: List of signal dicts associated with this challenge.
        conn: Open database connection.
        config: Loaded config dict.

    Returns:
        Float in [0.0, 1.0].
    """
    challenge_id = challenge_data.get("id", "")

    try:
        cap_rows = conn.execute(
            "SELECT coverage_score FROM research_capability_map WHERE challenge_id = %s",
            (challenge_id,),
        ).fetchall()
    except Exception:
        return 0.5

    if not cap_rows:
        return 0.5  # neutral when no mapping exists

    # Average coverage score across all capability mappings
    total = sum(float(r["coverage_score"] or 0.0) for r in cap_rows)
    avg = total / max(len(cap_rows), 1)

    return max(0.0, min(1.0, avg))


def _score_compliance_alignment(challenge_data, signals, conn, config):
    """Score compliance alignment dimension.

    Checks if challenge maps to existing ICDEV™ compliance frameworks.
    Full match = 1.0, crosswalk-able = 0.5, no match = 0.0.

    Uses research_regulatory_map crosswalk_coverage for the challenge.

    Args:
        challenge_data: Dict of challenge row from DB.
        signals: List of signal dicts associated with this challenge.
        conn: Open database connection.
        config: Loaded config dict.

    Returns:
        Float in [0.0, 1.0].
    """
    challenge_id = challenge_data.get("id", "")

    # Get coverage scoring thresholds from config
    reg_cfg = config.get("regulatory_mapping", {})
    coverage_cfg = reg_cfg.get("coverage_scoring", {})
    full_match_val = float(coverage_cfg.get("full_match", 1.0))
    crosswalk_val = float(coverage_cfg.get("crosswalk_match", 0.5))
    no_match_val = float(coverage_cfg.get("no_match", 0.0))

    try:
        reg_rows = conn.execute(
            "SELECT crosswalk_coverage, icdev_frameworks FROM research_regulatory_map WHERE challenge_id = %s",
            (challenge_id,),
        ).fetchall()
    except Exception:
        return no_match_val

    if not reg_rows:
        # Also check category for implicit alignment
        category = challenge_data.get("category", "other")
        if category in ("compliance", "security", "governance"):
            return crosswalk_val
        return no_match_val

    # Average crosswalk coverage across all regulatory mappings
    total_coverage = 0.0
    has_full_match = False
    for rr in reg_rows:
        cov = float(rr["crosswalk_coverage"] or 0.0)
        total_coverage += cov
        # Check if any mapping has ICDEV™ frameworks
        try:
            frameworks = json.loads(rr["icdev_frameworks"] or "[]")
            if isinstance(frameworks, list) and len(frameworks) > 0:
                has_full_match = True
        except (json.JSONDecodeError, TypeError):
            pass

    avg_coverage = total_coverage / max(len(reg_rows), 1)

    if has_full_match and avg_coverage >= 0.7:
        return full_match_val
    elif avg_coverage > 0.0:
        return max(crosswalk_val, avg_coverage)
    else:
        return no_match_val


# =========================================================================
# SIGNAL CLUSTERING
# =========================================================================
def cluster_signals(session_id, db_path=None):
    """Group signals by keyword overlap into challenges.

    Algorithm:
      1. Fetch all signals for session
      2. Extract keywords from each signal (title + body)
      3. Group by category (classify_category using keyword overlap scoring)
      4. Within each category, greedy clustering: pick seed, find all sharing
         >= 3 keywords, form challenge if cluster size >= 2
      5. INSERT new challenges into research_challenges table
      6. Return list of challenge dicts

    Args:
        session_id: Research session ID.
        db_path: Optional database path override.

    Returns:
        List of challenge dicts created.
    """
    conn = _get_db(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM research_signals WHERE session_id = %s ORDER BY discovered_at ASC",
            (session_id,),
        ).fetchall()

        if not rows:
            return []

        # Step 1-2: Extract keywords for each signal
        signal_data = []
        for row in rows:
            d = dict(row)
            text = f"{d.get('title', '')} {d.get('body', '')}"
            kws = extract_keywords(text, top_n=10)
            d["_extracted_keywords"] = kws
            signal_data.append(d)

        # Step 3: Group by category
        by_category = defaultdict(list)
        for sd in signal_data:
            text = f"{sd.get('title', '')} {sd.get('body', '')}"
            cat = classify_category(text, sd["_extracted_keywords"])
            sd["_category"] = cat
            by_category[cat].append(sd)

        challenges = []
        now = _now()

        # Step 4: Greedy clustering within each category
        for cat, cat_signals in by_category.items():
            used = set()

            for i, seed in enumerate(cat_signals):
                if i in used:
                    continue

                seed_kws = set(seed["_extracted_keywords"])
                cluster = [seed]
                cluster_kws = set(seed_kws)
                used.add(i)

                for j, candidate in enumerate(cat_signals):
                    if j in used:
                        continue
                    cand_kws = set(candidate["_extracted_keywords"])
                    overlap = seed_kws & cand_kws
                    if len(overlap) >= 3:
                        cluster.append(candidate)
                        cluster_kws |= cand_kws
                        used.add(j)

                # Form challenge if cluster size >= 2 (or single significant signal)
                if len(cluster) < 2:
                    # Single signals still become challenges if they have enough keywords
                    if len(seed_kws) < 4:
                        continue

                # Build challenge from cluster
                all_keywords = []
                all_signal_ids = []
                all_titles = []
                for sig in cluster:
                    all_keywords.extend(sig["_extracted_keywords"])
                    all_signal_ids.append(sig["id"])
                    all_titles.append(sig.get("title", ""))

                # Deduplicate and rank keywords
                kw_counts = Counter(all_keywords)
                top_keywords = [kw for kw, _ in kw_counts.most_common(15)]

                fingerprint = _keyword_fingerprint(top_keywords)

                # Check if challenge with this fingerprint already exists
                existing = conn.execute(
                    "SELECT id FROM research_challenges WHERE session_id = %s AND keyword_fingerprint = %s",
                    (session_id, fingerprint),
                ).fetchone()
                if existing:
                    continue

                # Build title from most common keywords
                title_parts = [kw for kw, _ in kw_counts.most_common(5)]
                challenge_title = " / ".join(title_parts).title() if title_parts else "Unknown Challenge"

                # Build description from signal titles
                desc_parts = [t for t in all_titles[:5] if t]
                description = "; ".join(desc_parts) if desc_parts else ""

                chal_id = _challenge_id()
                conn.execute(
                    """INSERT INTO research_challenges
                       (id, session_id, title, description, category,
                        signal_ids, signal_count, keyword_fingerprint, keywords,
                        status, first_seen, last_seen, classification)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'new', %s, %s, 'CUI')""",
                    (
                        chal_id,
                        session_id,
                        challenge_title,
                        description,
                        cat,
                        json.dumps(all_signal_ids),
                        len(cluster),
                        fingerprint,
                        json.dumps(top_keywords),
                        now,
                        now,
                    ),
                )

                challenges.append(
                    {
                        "id": chal_id,
                        "session_id": session_id,
                        "title": challenge_title,
                        "description": description,
                        "category": cat,
                        "signal_count": len(cluster),
                        "keyword_fingerprint": fingerprint,
                        "keywords": top_keywords,
                        "status": "new",
                    }
                )

        conn.commit()

        _audit(
            "research.cluster",
            f"Clustered {len(signal_data)} signals into {len(challenges)} challenges for session {session_id}",
            {
                "session_id": session_id,
                "signal_count": len(signal_data),
                "challenge_count": len(challenges),
            },
        )

        return challenges

    finally:
        conn.close()


# =========================================================================
# SCORING FUNCTIONS
# =========================================================================
def score_challenge(challenge_id, db_path=None):
    """Score a single challenge across all 6 dimensions.

    Reads the challenge from DB, fetches associated signals, computes each
    dimension score, calculates the weighted average, and updates the
    challenge row with the scored results.

    Args:
        challenge_id: The challenge ID (e.g., "rchal-abc123def456").
        db_path: Optional database path override.

    Returns:
        Dict with challenge_id, composite_score, breakdown, severity.
    """
    config = _load_config()
    weights = _get_weights(config)
    thresholds = _get_thresholds(config)

    conn = _get_db(db_path)
    try:
        row = conn.execute("SELECT * FROM research_challenges WHERE id = %s", (challenge_id,)).fetchone()
        if not row:
            raise ValueError(f"Challenge not found: {challenge_id}")

        challenge_data = dict(row)

        # Fetch associated signals
        signals = []
        try:
            signal_ids_raw = challenge_data.get("signal_ids", "[]") or "[]"
            signal_ids = json.loads(signal_ids_raw)
            if isinstance(signal_ids, list) and signal_ids:
                placeholders = ",".join("?" for _ in signal_ids)
                sig_rows = conn.execute(
                    f"SELECT * FROM research_signals WHERE id IN ({placeholders})",  # nosec B608 -- table/column names are internal constants, not user input
                    signal_ids,
                ).fetchall()
                signals = [dict(sr) for sr in sig_rows]
        except (json.JSONDecodeError, TypeError):
            pass

        # Compute each dimension
        dimensions = {
            "market_demand": _score_market_demand(challenge_data, signals, conn, config),
            "regulatory_pressure": _score_regulatory_pressure(challenge_data, signals, conn, config),
            "technical_complexity": _score_technical_complexity(challenge_data, signals, conn, config),
            "competitive_saturation": _score_competitive_saturation(challenge_data, signals, conn, config),
            "icdev_readiness": _score_icdev_readiness(challenge_data, signals, conn, config),
            "compliance_alignment": _score_compliance_alignment(challenge_data, signals, conn, config),
        }

        # Weighted average (D21 deterministic pattern)
        composite = sum(dimensions[dim] * weights.get(dim, 0.0) for dim in dimensions)
        composite = round(max(0.0, min(1.0, composite)), 4)

        # Determine severity band from thresholds
        if composite >= thresholds["critical"]:
            severity = "critical"
        elif composite >= thresholds["notable"]:
            severity = "notable"
        else:
            severity = "appendix"

        # Build score breakdown JSON
        score_breakdown = {
            "dimensions": {k: round(v, 4) for k, v in dimensions.items()},
            "weights": weights,
            "composite": composite,
            "severity": severity,
            "scored_at": _now(),
        }

        # UPDATE challenge row with scored results
        conn.execute(
            """UPDATE research_challenges
               SET composite_score = %s,
                   score_breakdown = %s,
                   market_demand = %s,
                   regulatory_pressure = %s,
                   technical_complexity = %s,
                   competitive_saturation = %s,
                   icdev_readiness = %s,
                   compliance_alignment = %s,
                   severity = %s,
                   status = 'scored',
                   last_seen = %s
               WHERE id = %s""",
            (
                composite,
                json.dumps(score_breakdown),
                round(dimensions["market_demand"], 4),
                round(dimensions["regulatory_pressure"], 4),
                round(dimensions["technical_complexity"], 4),
                round(dimensions["competitive_saturation"], 4),
                round(dimensions["icdev_readiness"], 4),
                round(dimensions["compliance_alignment"], 4),
                severity,
                _now(),
                challenge_id,
            ),
        )
        conn.commit()

        _audit(
            "research.score",
            f"Scored challenge {challenge_id}: {composite:.4f} ({severity})",
            {
                "challenge_id": challenge_id,
                "composite_score": composite,
                "severity": severity,
                "dimensions": {k: round(v, 4) for k, v in dimensions.items()},
            },
        )

        return {
            "challenge_id": challenge_id,
            "title": challenge_data.get("title", ""),
            "category": challenge_data.get("category", ""),
            "signal_count": challenge_data.get("signal_count", 0),
            "composite_score": composite,
            "severity": severity,
            "breakdown": {k: round(v, 4) for k, v in dimensions.items()},
            "weights_used": weights,
            "status": "scored",
            "scored_at": score_breakdown["scored_at"],
        }

    finally:
        conn.close()


def score_all_new(session_id=None, db_path=None):
    """Score all challenges with status='new'.

    Optionally scoped to a session. Deduplicates by keyword_fingerprint
    (takes latest by rowid), then scores each unique challenge.

    Args:
        session_id: Optional session ID to scope scoring.
        db_path: Optional database path override.

    Returns:
        Dict with scored count, skipped count, avg_score, top_5.
    """
    conn = _get_db(db_path)
    try:
        if session_id:
            rows = conn.execute(
                """SELECT * FROM research_challenges
                   WHERE status = 'new' AND session_id = %s
                   ORDER BY last_seen ASC""",
                (session_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT * FROM research_challenges
                   WHERE status = 'new'
                   ORDER BY last_seen ASC"""
            ).fetchall()
    finally:
        conn.close()

    if not rows:
        return {
            "scored": 0,
            "skipped": 0,
            "avg_score": 0.0,
            "top_5": [],
            "scored_at": _now(),
        }

    # Deduplicate by keyword_fingerprint (latest row per fingerprint)
    by_fingerprint = {}
    for row in rows:
        d = dict(row)
        fp = d.get("keyword_fingerprint", "")
        by_fingerprint[fp] = d  # last one wins

    unique_challenges = list(by_fingerprint.values())
    scored_count = 0
    skipped_count = 0
    all_scores = []
    results = []

    for ch in unique_challenges:
        try:
            result = score_challenge(ch["id"], db_path=db_path)
            scored_count += 1
            all_scores.append(result["composite_score"])
            results.append(result)
        except Exception:
            skipped_count += 1

    # Compute average score
    avg_score = round(sum(all_scores) / max(len(all_scores), 1), 4) if all_scores else 0.0

    # Top 5 by composite_score
    results.sort(key=lambda r: r.get("composite_score", 0.0), reverse=True)
    top_5 = []
    for r in results[:5]:
        top_5.append(
            {
                "challenge_id": r["challenge_id"],
                "title": r["title"],
                "composite_score": r["composite_score"],
                "severity": r["severity"],
                "category": r["category"],
            }
        )

    _audit(
        "research.score_batch",
        f"Batch scored {scored_count} challenges ({skipped_count} skipped)",
        {
            "session_id": session_id,
            "scored": scored_count,
            "skipped": skipped_count,
            "avg_score": avg_score,
        },
    )

    return {
        "scored": scored_count,
        "skipped": skipped_count,
        "avg_score": avg_score,
        "top_5": top_5,
        "scored_at": _now(),
    }


def get_top_challenges(session_id, limit=20, db_path=None):
    """Get highest-scored challenges for a session.

    Deduplicates by keyword_fingerprint (latest row per fingerprint),
    sorts descending by composite_score.

    Args:
        session_id: Research session ID.
        limit: Maximum number of challenges to return (default 20).
        db_path: Optional database path override.

    Returns:
        List of scored challenge dicts.
    """
    conn = _get_db(db_path)
    try:
        rows = conn.execute(
            """SELECT * FROM research_challenges
               WHERE session_id = %s
               AND composite_score IS NOT NULL
               ORDER BY last_seen ASC""",
            (session_id,),
        ).fetchall()

        # Deduplicate by keyword_fingerprint (latest row wins)
        by_fingerprint = {}
        for row in rows:
            d = dict(row)
            fp = d.get("keyword_fingerprint", "")
            by_fingerprint[fp] = d

        # Sort descending by composite_score
        scored = []
        for ch in by_fingerprint.values():
            score = ch.get("composite_score", 0.0) or 0.0

            # Parse score_breakdown
            try:
                breakdown = json.loads(ch.get("score_breakdown") or "{}")
            except (json.JSONDecodeError, TypeError):
                breakdown = {}

            scored.append(
                {
                    "challenge_id": ch["id"],
                    "title": ch.get("title", ""),
                    "description": ch.get("description", ""),
                    "category": ch.get("category", ""),
                    "signal_count": ch.get("signal_count", 0),
                    "composite_score": score,
                    "severity": ch.get("severity", "appendix"),
                    "breakdown": breakdown.get("dimensions", {}),
                    "keywords": json.loads(ch.get("keywords") or "[]"),
                    "signal_ids": json.loads(ch.get("signal_ids") or "[]"),
                    "first_seen": ch.get("first_seen", ""),
                    "last_seen": ch.get("last_seen", ""),
                }
            )

        scored.sort(key=lambda x: x.get("composite_score", 0.0), reverse=True)
        return scored[:limit]

    finally:
        conn.close()


def list_challenges(session_id, db_path=None):
    """List all challenges for a session.

    Args:
        session_id: Research session ID.
        db_path: Optional database path override.

    Returns:
        List of challenge dicts.
    """
    conn = _get_db(db_path)
    try:
        rows = conn.execute(
            """SELECT * FROM research_challenges
               WHERE session_id = %s
               ORDER BY CASE WHEN composite_score IS NULL THEN 1 ELSE 0 END, composite_score DESC, last_seen ASC""",
            (session_id,),
        ).fetchall()

        # Deduplicate by keyword_fingerprint (latest row wins)
        by_fingerprint = {}
        for row in rows:
            d = dict(row)
            fp = d.get("keyword_fingerprint", "")
            by_fingerprint[fp] = d

        challenges = []
        for ch in by_fingerprint.values():
            challenges.append(
                {
                    "challenge_id": ch["id"],
                    "title": ch.get("title", ""),
                    "description": ch.get("description", ""),
                    "category": ch.get("category", ""),
                    "signal_count": ch.get("signal_count", 0),
                    "composite_score": ch.get("composite_score"),
                    "severity": ch.get("severity", "appendix"),
                    "status": ch.get("status", "new"),
                    "keywords": json.loads(ch.get("keywords") or "[]"),
                    "first_seen": ch.get("first_seen", ""),
                    "last_seen": ch.get("last_seen", ""),
                }
            )

        # Sort: scored first (by score desc), then new (by first_seen)
        challenges.sort(
            key=lambda x: (
                0 if x["status"] == "scored" else 1,
                -(x.get("composite_score") or 0.0),
            )
        )
        return challenges

    finally:
        conn.close()


# =========================================================================
# HUMAN-READABLE OUTPUT
# =========================================================================
def _print_human(args, result):
    """Print human-readable output for each command."""
    print("=" * 70)
    print("  CHALLENGE SCORER -- CUI // SP-CTI")
    print("=" * 70)

    if isinstance(result, dict) and "error" in result:
        print(f"\n  ERROR: {result['error']}\n")
        print("=" * 70)
        return

    if args.cluster:
        if isinstance(result, list):
            print(f"\n  Clustered {len(result)} challenges")
            print()
            if result:
                print(f"  {'#':>3s}  {'Category':>14s}  {'Signals':>7s}  Title")
                print(f"  {'---':>3s}  {'-' * 14:>14s}  {'-------':>7s}  -----")
                for i, ch in enumerate(result, 1):
                    print(f"  {i:3d}  {ch['category']:>14s}  {ch['signal_count']:7d}  {ch['title'][:40]}")
        else:
            print("\n  No signals to cluster.")

    elif args.score_one:
        print(f"\n  Challenge: {result.get('challenge_id', '')}")
        print(f"  Title:     {result.get('title', '')}")
        print(f"  Category:  {result.get('category', '')}")
        print(f"  Signals:   {result.get('signal_count', 0)}")
        print(f"  Score:     {result.get('composite_score', 0):.4f}  [{result.get('severity', '')}]")
        print(f"  Status:    {result.get('status', '')}")
        print()
        print("  Dimensions:")
        for dim, val in result.get("breakdown", {}).items():
            weight = result.get("weights_used", {}).get(dim, 0)
            bar = "#" * int(val * 20)
            print(f"    {dim:25s} {val:.4f} (w={weight:.2f})  |{bar:<20s}|")

    elif args.score:
        print(f"\n  Batch scoring completed at {result.get('scored_at', '')}")
        print(f"    Scored:  {result.get('scored', 0)}")
        print(f"    Skipped: {result.get('skipped', 0)}")
        print(f"    Average: {result.get('avg_score', 0):.4f}")
        if result.get("top_5"):
            print()
            print("  Top 5 Challenges:")
            print(f"    {'#':>3s}  {'Score':>7s}  {'Severity':>10s}  {'Category':>14s}  Title")
            print(f"    {'---':>3s}  {'-------':>7s}  {'----------':>10s}  {'-' * 14:>14s}  -----")
            for i, t in enumerate(result["top_5"], 1):
                print(
                    f"    {i:3d}  {t['composite_score']:7.4f}  "
                    f"{t['severity']:>10s}  {t['category']:>14s}  "
                    f"{t['title'][:40]}"
                )

    elif args.top:
        if isinstance(result, list):
            print(f"\n  Top Scored Challenges ({len(result)} results):")
            print()
            for i, ch in enumerate(result, 1):
                score = ch.get("composite_score", 0)
                sev = ch.get("severity", "")
                print(f"  {i:3d}. [{score:.4f}] {ch.get('title', '')[:60]}")
                print(
                    f"       Category: {ch.get('category', '')}  |  "
                    f"Signals: {ch.get('signal_count', 0)}  |  "
                    f"Severity: {sev}"
                )
                dims = ch.get("breakdown", {})
                if dims:
                    dim_str = "  ".join(f"{k[:12]}={v:.2f}" for k, v in dims.items())
                    print(f"       {dim_str}")
                print()
        else:
            print("\n  No results.")

    elif args.challenges:
        if isinstance(result, list):
            print(f"\n  All Challenges ({len(result)} total):")
            print()
            print(f"  {'#':>3s}  {'Score':>7s}  {'Severity':>10s}  {'Status':>8s}  {'Category':>14s}  Title")
            print(f"  {'---':>3s}  {'-------':>7s}  {'----------':>10s}  {'--------':>8s}  {'-' * 14:>14s}  -----")
            for i, ch in enumerate(result, 1):
                score = ch.get("composite_score")
                score_str = f"{score:7.4f}" if score is not None else "  --   "
                print(
                    f"  {i:3d}  {score_str}  "
                    f"{ch.get('severity', ''):>10s}  "
                    f"{ch.get('status', ''):>8s}  "
                    f"{ch.get('category', ''):>14s}  "
                    f"{ch.get('title', '')[:40]}"
                )
        else:
            print("\n  No challenges found.")

    print()
    print("=" * 70)


# =========================================================================
# CLI
# =========================================================================
def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="ICDEV™ Research Engine Challenge Scorer -- CUI // SP-CTI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  %(prog)s --cluster --session-id rsess-xxx --json\n"
            "  %(prog)s --score --session-id rsess-xxx --json\n"
            "  %(prog)s --score-one --challenge-id rchal-xxx --json\n"
            "  %(prog)s --top --session-id rsess-xxx --limit 10 --json\n"
            "  %(prog)s --challenges --session-id rsess-xxx --json\n"
            "  %(prog)s --top --session-id rsess-xxx --human\n"
        ),
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable output")
    parser.add_argument("--db-path", type=Path, default=None, help="Database path override")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--cluster",
        action="store_true",
        help="Cluster signals into challenges (requires --session-id)",
    )
    group.add_argument(
        "--score",
        action="store_true",
        help="Score all new (unscored) challenges (requires --session-id)",
    )
    group.add_argument(
        "--score-one",
        action="store_true",
        help="Score a single challenge (requires --challenge-id)",
    )
    group.add_argument(
        "--top",
        action="store_true",
        help="Get top challenges by composite score (requires --session-id)",
    )
    group.add_argument(
        "--challenges",
        action="store_true",
        help="List all challenges for a session (requires --session-id)",
    )

    parser.add_argument(
        "--session-id",
        type=str,
        help="Research session ID (required for --cluster, --score, --top, --challenges)",
    )
    parser.add_argument(
        "--challenge-id",
        type=str,
        help="Challenge ID to score (required with --score-one)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Max challenges to return (with --top, default 20)",
    )

    args = parser.parse_args()

    try:
        if args.cluster:
            if not args.session_id:
                parser.error("--cluster requires --session-id")
            result = cluster_signals(args.session_id, db_path=args.db_path)
        elif args.score:
            if not args.session_id:
                parser.error("--score requires --session-id")
            result = score_all_new(session_id=args.session_id, db_path=args.db_path)
        elif args.score_one:
            if not args.challenge_id:
                parser.error("--score-one requires --challenge-id")
            result = score_challenge(args.challenge_id, db_path=args.db_path)
        elif args.top:
            if not args.session_id:
                parser.error("--top requires --session-id")
            result = get_top_challenges(args.session_id, limit=args.limit, db_path=args.db_path)
        elif args.challenges:
            if not args.session_id:
                parser.error("--challenges requires --session-id")
            result = list_challenges(args.session_id, db_path=args.db_path)
        else:
            result = {"error": "No action specified"}

        if args.human:
            _print_human(args, result)
        elif args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            # Default to JSON if neither --human nor --json specified
            print(json.dumps(result, indent=2, default=str))

    except FileNotFoundError as e:
        error = {"error": str(e), "hint": "Run: python tools/db/init_icdev_db.py"}
        if args.human:
            print(f"ERROR: {e}", file=sys.stderr)
        else:
            print(json.dumps(error, indent=2))
        sys.exit(1)
    except ValueError as e:
        error = {"error": str(e)}
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
