#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""RFP Requirement Extractor — mine "shall" statements and cluster patterns.

Extracts obligation statements (shall, must, will) from SAM.gov opportunity
descriptions and clusters them into recurring requirement patterns across
multiple RFPs.  Uses deterministic regex extraction (D362) and keyword
fingerprint clustering (D364).

Architecture:
    - Deterministic regex extraction — no LLM needed (D362, D354 pattern)
    - Domain classification via keyword overlap scoring
    - Keyword fingerprint clustering using union-find (D364, pain_extractor pattern)
    - All shall_statements and requirement_patterns append-only (D6)
    - Pattern frequency tracking enables trend analysis (D371)

Usage:
    python tools/govcon/requirement_extractor.py --extract-all --json
    python tools/govcon/requirement_extractor.py --extract --opp-id <id> --json
    python tools/govcon/requirement_extractor.py --patterns --json
    python tools/govcon/requirement_extractor.py --patterns --domain devsecops --json
    python tools/govcon/requirement_extractor.py --patterns --min-frequency 3 --json
    python tools/govcon/requirement_extractor.py --trends --json
"""

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import uuid
from collections import Counter
from datetime import datetime, timezone
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
    from tools.audit.audit_logger import log_event as audit_log_event
    _HAS_AUDIT = True
except ImportError:
    _HAS_AUDIT = False
    def audit_log_event(**kwargs):
        return -1

# =========================================================================
# CONSTANTS
# =========================================================================
# Stopwords for keyword extraction (common words to ignore)
_STOPWORDS = frozenset({
    "the", "and", "for", "that", "with", "this", "from", "are", "not",
    "will", "shall", "must", "have", "has", "been", "being", "were",
    "was", "can", "may", "should", "would", "could", "also", "all",
    "any", "each", "other", "such", "than", "into", "over", "more",
    "its", "their", "they", "which", "when", "where", "who", "how",
    "but", "about", "between", "through", "during", "before", "after",
    "above", "below", "both", "these", "those", "only", "very",
    "provide", "ensure", "support", "include", "including", "required",
    "contractor", "government", "agency", "federal", "services",
})

MIN_KEYWORD_LEN = 3
MAX_KEYWORDS_PER_STATEMENT = 15

# Regex for "shall" statement extraction
# Matches sentences containing obligation verbs
_OBLIGATION_VERBS = [
    r"\bshall\b",
    r"\bmust\b",
    r"\bis\s+required\s+to\b",
    r"\bwill\s+provide\b",
    r"\bwill\s+deliver\b",
    r"\bwill\s+maintain\b",
    r"\bwill\s+ensure\b",
    r"\bwill\s+demonstrate\b",
    r"\bwill\s+comply\b",
    r"\bwill\s+support\b",
]
_OBLIGATION_PATTERN = re.compile("|".join(_OBLIGATION_VERBS), re.IGNORECASE)

# Sentence boundary detection
_SENTENCE_SPLIT = re.compile(r'(?<=[.!?;])\s+|\n{2,}')

# Token pattern for keyword extraction
_TOKEN_PATTERN = re.compile(r'\b[a-z][a-z0-9/_-]{2,}\b')


# =========================================================================
# DATABASE HELPERS
# =========================================================================
def _get_db(db_path=None):
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(f"Database not found: {path}")
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stmt_id():
    return f"shall-{uuid.uuid4().hex[:12]}"


def _pattern_id():
    return f"rpat-{uuid.uuid4().hex[:12]}"


def _content_hash(content):
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _keyword_fingerprint(keywords):
    """Canonical fingerprint from sorted keywords."""
    canonical = "|".join(sorted(set(k.lower() for k in keywords)))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _audit(event_type, actor, action, details=None):
    if _HAS_AUDIT:
        try:
            audit_log_event(
                event_type=event_type, actor=actor, action=action,
                details=json.dumps(details) if details else None,
                project_id="govcon-engine",
            )
        except Exception:
            pass


def _load_config():
    if not _HAS_YAML or not CONFIG_PATH.exists():
        return {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# =========================================================================
# EXTRACTION: "SHALL" STATEMENTS
# =========================================================================
def extract_shall_statements(text, config=None):
    """Extract obligation statements from RFP text.

    Args:
        text: Raw RFP description text.
        config: Optional config with custom shall_patterns.

    Returns:
        List of dicts: {text, type, keywords, domain_category}.
    """
    config = config or _load_config()
    req_config = config.get("requirement_extraction", {})

    if not text or not text.strip():
        return []

    # Split into sentences
    sentences = _SENTENCE_SPLIT.split(text)
    results = []
    seen_hashes = set()

    for sentence in sentences:
        sentence = sentence.strip()
        if len(sentence) < 20:
            continue

        # Check for obligation verbs
        match = _OBLIGATION_PATTERN.search(sentence)
        if not match:
            continue

        # Determine statement type
        matched_text = match.group().lower()
        if "shall" in matched_text:
            stmt_type = "shall"
        elif "must" in matched_text:
            stmt_type = "must"
        elif "required" in matched_text:
            stmt_type = "required"
        else:
            stmt_type = "will"

        # Dedup within same document
        stmt_hash = _content_hash(sentence.lower().strip())
        if stmt_hash in seen_hashes:
            continue
        seen_hashes.add(stmt_hash)

        # Extract keywords
        keywords = _extract_keywords(sentence)

        # Classify domain
        domain = _classify_domain(sentence, keywords, req_config)

        results.append({
            "text": sentence,
            "type": stmt_type,
            "keywords": keywords,
            "domain_category": domain,
            "content_hash": stmt_hash,
        })

    return results


def _extract_keywords(text):
    """Extract meaningful keywords from text using TF-based approach.

    Args:
        text: Input text.

    Returns:
        List of top keywords (max MAX_KEYWORDS_PER_STATEMENT).
    """
    tokens = _TOKEN_PATTERN.findall(text.lower())
    filtered = [t for t in tokens if t not in _STOPWORDS and len(t) >= MIN_KEYWORD_LEN]
    counts = Counter(filtered)
    return [word for word, _ in counts.most_common(MAX_KEYWORDS_PER_STATEMENT)]


def _classify_domain(text, keywords, config):
    """Classify a "shall" statement into a domain category.

    Uses keyword overlap scoring — matches category keywords against
    both extracted keywords (+2 per match) and raw text (+1 per match).

    Args:
        text: The statement text.
        keywords: Extracted keywords from the statement.
        config: Requirement extraction config with domain_categories.

    Returns:
        Best-matching domain category string, or 'other'.
    """
    domain_categories = config.get("domain_categories", {})
    if not domain_categories:
        return "other"

    text_lower = text.lower()
    keyword_set = set(k.lower() for k in keywords)
    best_domain = "other"
    best_score = 0

    for domain, domain_conf in domain_categories.items():
        cat_keywords = domain_conf if isinstance(domain_conf, list) else domain_conf.get("keywords", [])
        score = 0
        for ck in cat_keywords:
            ck_lower = ck.lower()
            # Keyword match (stronger signal)
            if ck_lower in keyword_set:
                score += 2
            # Text mention (weaker signal)
            elif ck_lower in text_lower:
                score += 1

        if score > best_score:
            best_score = score
            best_domain = domain

    return best_domain if best_score > 0 else "other"


# =========================================================================
# STORE EXTRACTED STATEMENTS
# =========================================================================
def extract_and_store(opp_id=None, db_path=None, config=None):
    """Extract "shall" statements from one or all cached opportunities.

    Args:
        opp_id: Optional specific opportunity ID. If None, processes all.
        db_path: Optional DB path override.
        config: Optional config override.

    Returns:
        Dict with extracted_count, new_count, duplicate_count, opportunity_count.
    """
    config = config or _load_config()

    try:
        conn = _get_db(db_path)
    except FileNotFoundError as e:
        return {"error": str(e)}

    # Fetch opportunities
    if opp_id:
        rows = conn.execute(
            "SELECT id, description, title FROM sam_gov_opportunities WHERE id = ?",
            (opp_id,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, description, title FROM sam_gov_opportunities WHERE active = 'true'"
        ).fetchall()

    total_extracted = 0
    new_count = 0
    dup_count = 0

    for row in rows:
        desc = row["description"] or ""
        title = row["title"] or ""
        combined_text = f"{title}. {desc}"

        statements = extract_shall_statements(combined_text, config)

        for stmt in statements:
            # Check for existing by content_hash
            existing = conn.execute(
                "SELECT id FROM rfp_shall_statements WHERE content_hash = ?",
                (stmt["content_hash"],)
            ).fetchone()

            if existing:
                dup_count += 1
                continue

            conn.execute(
                "INSERT INTO rfp_shall_statements "
                "(id, sam_opportunity_id, statement_text, statement_type, "
                "domain_category, keywords, keyword_fingerprint, content_hash, "
                "extracted_at, classification) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (_stmt_id(), row["id"], stmt["text"], stmt["type"],
                 stmt["domain_category"], json.dumps(stmt["keywords"]),
                 _keyword_fingerprint(stmt["keywords"]), stmt["content_hash"],
                 _now(), "CUI")
            )
            new_count += 1
            total_extracted += 1

    conn.commit()
    conn.close()

    _audit("govcon.extract", "requirement-extractor",
           f"Extracted {new_count} new shall statements from {len(rows)} opportunities")

    return {
        "opportunity_count": len(rows),
        "extracted_count": total_extracted,
        "new_count": new_count,
        "duplicate_count": dup_count,
    }


# =========================================================================
# CLUSTERING: REQUIREMENT PATTERNS
# =========================================================================
def cluster_patterns(db_path=None, config=None):
    """Cluster extracted "shall" statements into recurring patterns.

    Uses keyword fingerprint overlap — statements sharing >= min_shared_keywords
    keywords are grouped together.  Existing patterns have their frequency
    updated; new clusters become new patterns.

    Args:
        db_path: Optional DB path override.
        config: Optional config override.

    Returns:
        Dict with new_patterns, updated_patterns, total_patterns.
    """
    config = config or _load_config()
    req_config = config.get("requirement_extraction", {})
    cluster_config = req_config.get("clustering", {})
    min_shared = cluster_config.get("min_shared_keywords", 3)
    min_freq = req_config.get("min_pattern_frequency", 3)

    try:
        conn = _get_db(db_path)
    except FileNotFoundError as e:
        return {"error": str(e)}

    # Fetch all shall statements
    stmts = conn.execute(
        "SELECT id, statement_text, domain_category, keywords, keyword_fingerprint "
        "FROM rfp_shall_statements"
    ).fetchall()

    if not stmts:
        conn.close()
        return {"new_patterns": 0, "updated_patterns": 0, "total_patterns": 0}

    # Group by domain first, then cluster within domain
    domain_groups = {}
    for s in stmts:
        domain = s["domain_category"] or "other"
        domain_groups.setdefault(domain, []).append(dict(s))

    new_patterns = 0
    updated_patterns = 0

    for domain, domain_stmts in domain_groups.items():
        # Build keyword sets
        for s in domain_stmts:
            kw = json.loads(s["keywords"]) if isinstance(s["keywords"], str) else s["keywords"]
            s["_kw_set"] = set(k.lower() for k in kw)

        # Simple greedy clustering by keyword overlap
        assigned = set()
        clusters = []

        for i, s1 in enumerate(domain_stmts):
            if i in assigned:
                continue
            cluster = [s1]
            assigned.add(i)
            cluster_keywords = set(s1["_kw_set"])

            for j, s2 in enumerate(domain_stmts):
                if j in assigned:
                    continue
                overlap = len(cluster_keywords & s2["_kw_set"])
                if overlap >= min_shared:
                    cluster.append(s2)
                    assigned.add(j)
                    cluster_keywords |= s2["_kw_set"]

            clusters.append((cluster, list(cluster_keywords)))

        # Store or update patterns
        for cluster_stmts, cluster_kws in clusters:
            if len(cluster_stmts) < 1:
                continue

            fingerprint = _keyword_fingerprint(cluster_kws)
            stmt_ids = [s["id"] for s in cluster_stmts]
            opp_ids = list(set(
                s.get("sam_opportunity_id", "") for s in cluster_stmts
                if s.get("sam_opportunity_id")
            ))

            # Representative text: longest statement
            representative = max(cluster_stmts, key=lambda s: len(s.get("statement_text", "")))

            # Check if pattern already exists by fingerprint
            existing = conn.execute(
                "SELECT id, shall_statement_ids, sam_opportunity_ids, frequency "
                "FROM rfp_requirement_patterns WHERE keyword_fingerprint = ? AND domain_category = ?",
                (fingerprint, domain)
            ).fetchone()

            if existing:
                # Merge statement IDs
                old_ids = json.loads(existing["shall_statement_ids"]) if existing["shall_statement_ids"] else []
                old_opp_ids = json.loads(existing["sam_opportunity_ids"]) if existing["sam_opportunity_ids"] else []
                merged_ids = list(set(old_ids + stmt_ids))
                merged_opp_ids = list(set(old_opp_ids + opp_ids))
                new_freq = len(merged_opp_ids)  # frequency = unique opportunities

                conn.execute(
                    "UPDATE rfp_requirement_patterns SET "
                    "frequency=?, shall_statement_ids=?, sam_opportunity_ids=?, last_seen=? "
                    "WHERE id=?",
                    (new_freq, json.dumps(merged_ids), json.dumps(merged_opp_ids),
                     _now(), existing["id"])
                )
                updated_patterns += 1
            else:
                # Create new pattern
                pattern_name = _generate_pattern_name(cluster_kws, domain)
                conn.execute(
                    "INSERT INTO rfp_requirement_patterns "
                    "(id, pattern_name, description, domain_category, frequency, "
                    "shall_statement_ids, sam_opportunity_ids, keyword_fingerprint, "
                    "keywords, representative_text, capability_coverage, "
                    "icdev_capability_ids, status, first_seen, last_seen, "
                    "metadata, classification) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (_pattern_id(), pattern_name,
                     representative["statement_text"][:500],
                     domain, len(set(opp_ids)) or len(cluster_stmts),
                     json.dumps(stmt_ids), json.dumps(opp_ids),
                     fingerprint, json.dumps(cluster_kws[:20]),
                     representative["statement_text"],
                     0.0, "[]", "new", _now(), _now(), "{}", "CUI")
                )
                new_patterns += 1

    conn.commit()

    # Count total
    total = conn.execute("SELECT COUNT(*) as c FROM rfp_requirement_patterns").fetchone()
    conn.close()

    _audit("govcon.cluster", "requirement-extractor",
           f"Clustered patterns: {new_patterns} new, {updated_patterns} updated")

    return {
        "new_patterns": new_patterns,
        "updated_patterns": updated_patterns,
        "total_patterns": total["c"] if total else 0,
    }


def _generate_pattern_name(keywords, domain):
    """Generate a human-readable pattern name from keywords and domain."""
    top_kw = [k for k in keywords[:4] if k not in _STOPWORDS]
    if not top_kw:
        top_kw = keywords[:3]
    name_part = " ".join(top_kw).title()
    return f"{domain.upper()}: {name_part}"


# =========================================================================
# QUERY FUNCTIONS
# =========================================================================
def get_patterns(db_path=None, domain=None, min_frequency=1, status=None,
                 limit=100):
    """Get requirement patterns, optionally filtered.

    Returns:
        Dict with patterns list and count.
    """
    try:
        conn = _get_db(db_path)
    except FileNotFoundError as e:
        return {"error": str(e)}

    query = "SELECT * FROM rfp_requirement_patterns WHERE frequency >= ?"
    params = [min_frequency]

    if domain:
        query += " AND domain_category = ?"
        params.append(domain)
    if status:
        query += " AND status = ?"
        params.append(status)

    query += " ORDER BY frequency DESC, last_seen DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    conn.close()

    patterns = [dict(r) for r in rows]
    return {"patterns": patterns, "count": len(patterns)}


def get_trends(db_path=None, top_k=20):
    """Get requirement trend analysis — domains growing fastest.

    Returns:
        Dict with domain_trends (frequency by domain, sorted) and
        top_patterns (most frequent across all domains).
    """
    try:
        conn = _get_db(db_path)
    except FileNotFoundError as e:
        return {"error": str(e)}

    # Domain frequency totals
    domain_trends = conn.execute(
        "SELECT domain_category, SUM(frequency) as total_freq, COUNT(*) as pattern_count "
        "FROM rfp_requirement_patterns "
        "GROUP BY domain_category ORDER BY total_freq DESC"
    ).fetchall()

    # Top patterns overall
    top_patterns = conn.execute(
        "SELECT id, pattern_name, domain_category, frequency, capability_coverage, status "
        "FROM rfp_requirement_patterns "
        "ORDER BY frequency DESC LIMIT ?",
        (top_k,)
    ).fetchall()

    # Gap patterns (high frequency, low coverage)
    gaps = conn.execute(
        "SELECT id, pattern_name, domain_category, frequency, capability_coverage "
        "FROM rfp_requirement_patterns "
        "WHERE capability_coverage < 0.4 AND frequency >= 3 "
        "ORDER BY frequency DESC LIMIT ?",
        (top_k,)
    ).fetchall()

    conn.close()

    return {
        "domain_trends": [dict(r) for r in domain_trends],
        "top_patterns": [dict(r) for r in top_patterns],
        "high_frequency_gaps": [dict(r) for r in gaps],
    }


# =========================================================================
# CLI
# =========================================================================
def main():
    parser = argparse.ArgumentParser(description="RFP Requirement Extractor")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--extract-all", action="store_true",
                       help="Extract shall statements from all cached opportunities")
    group.add_argument("--extract", action="store_true",
                       help="Extract from a specific opportunity (use --opp-id)")
    group.add_argument("--cluster", action="store_true",
                       help="Cluster statements into requirement patterns")
    group.add_argument("--patterns", action="store_true",
                       help="List requirement patterns")
    group.add_argument("--trends", action="store_true",
                       help="Show requirement trend analysis")

    parser.add_argument("--opp-id", help="Specific opportunity ID for extraction")
    parser.add_argument("--domain", help="Filter patterns by domain category")
    parser.add_argument("--min-frequency", type=int, default=1,
                        help="Minimum pattern frequency")
    parser.add_argument("--status", help="Filter patterns by status")
    parser.add_argument("--limit", type=int, default=100, help="Max results")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable output")

    args = parser.parse_args()

    if args.extract_all:
        result = extract_and_store()
        # Auto-cluster after extraction
        cluster_result = cluster_patterns()
        result["clustering"] = cluster_result
    elif args.extract:
        if not args.opp_id:
            result = {"error": "--extract requires --opp-id"}
        else:
            result = extract_and_store(opp_id=args.opp_id)
    elif args.cluster:
        result = cluster_patterns()
    elif args.patterns:
        result = get_patterns(domain=args.domain, min_frequency=args.min_frequency,
                              status=args.status, limit=args.limit)
    elif args.trends:
        result = get_trends()
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

    if args.extract_all or args.extract:
        print(f"\n  Requirement Extraction Complete")
        print(f"  {'='*40}")
        print(f"  Opportunities: {result.get('opportunity_count', 0)}")
        print(f"  Extracted:     {result.get('extracted_count', 0)}")
        print(f"  New:           {result.get('new_count', 0)}")
        print(f"  Duplicates:    {result.get('duplicate_count', 0)}")
        if result.get("clustering"):
            cl = result["clustering"]
            print(f"\n  Clustering:")
            print(f"    New patterns:     {cl.get('new_patterns', 0)}")
            print(f"    Updated patterns: {cl.get('updated_patterns', 0)}")
            print(f"    Total patterns:   {cl.get('total_patterns', 0)}")
    elif args.patterns:
        patterns = result.get("patterns", [])
        print(f"\n  Requirement Patterns ({len(patterns)})")
        print(f"  {'='*60}")
        for p in patterns:
            coverage = p.get("capability_coverage", 0)
            cov_bar = "=" * int(coverage * 10)
            print(f"  [{p.get('domain_category','?'):12s}] {p.get('pattern_name','')[:40]}")
            print(f"      Freq: {p.get('frequency',0):3d} | Coverage: [{cov_bar:<10s}] {coverage:.0%} | Status: {p.get('status','')}")
    elif args.trends:
        print(f"\n  Requirement Trends")
        print(f"  {'='*50}")
        print(f"\n  Domain Distribution:")
        for d in result.get("domain_trends", []):
            bar = "#" * min(d.get("total_freq", 0), 30)
            print(f"    {d['domain_category']:15s} {bar} ({d['total_freq']})")
        gaps = result.get("high_frequency_gaps", [])
        if gaps:
            print(f"\n  High-Frequency Gaps (no ICDEV capability):")
            for g in gaps[:10]:
                print(f"    [{g.get('domain_category','')}] {g.get('pattern_name','')[:40]} (freq={g.get('frequency',0)})")
    print()


if __name__ == "__main__":
    main()
