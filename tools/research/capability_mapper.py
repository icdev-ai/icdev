#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""
Capability Mapper — maps research challenges to ICDEV™ capability catalog (D-RES-7).

Uses a keyword-overlap algorithm (reuses the approach from ICDEV's own
tools/govcon/capability_mapper.py internal module) to match
research challenges against the ICDEV™ capability catalog. For each challenge, extracts
keywords from title + description, computes overlap against each capability's keyword
set, and stores mappings in research_capability_map (append-only, D6/D-RES-5).

Architecture:
    - Reads from research_challenges table (populated by challenge_scorer)
    - Loads ICDEV™ capability catalog from context/agentic/icdev_capability_catalog.json
      with a built-in DEFAULT_CATALOG fallback (D-RES-7)
    - Keyword overlap: overlap = len(challenge_kw & cap_kw) / max(1, len(cap_kw))
    - Mapping threshold: min_overlap_score (default 0.3 from research_config.yaml)
    - Enhancement detection: coverage < enhancement_threshold (default 0.6)
    - Stores results in research_capability_map table (append-only, D6)
    - All scoring is deterministic (D21 -- reproducible, not probabilistic)

Usage:
    # Map all challenges in a session to capabilities
    python tools/research/capability_mapper.py --map --session-id "rsess-xxx" --json

    # Map a single challenge
    python tools/research/capability_mapper.py --map-one --challenge-id "rchal-xxx" --session-id "rsess-xxx" --json

    # Show session capability coverage
    python tools/research/capability_mapper.py --coverage --session-id "rsess-xxx" --json

    # Show capabilities for a specific challenge
    python tools/research/capability_mapper.py --challenge-caps --challenge-id "rchal-xxx" --json

    # Human-readable output
    python tools/research/capability_mapper.py --map --session-id "rsess-xxx" --human
"""

import argparse
import json
import os
import sys
import uuid
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
CATALOG_PATH = BASE_DIR / "context" / "agentic" / "icdev_capability_catalog.json"

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
# DEFAULT CATALOG (fallback when catalog JSON file does not exist)
# =========================================================================
DEFAULT_CATALOG = [
    {"id": "cap-tdd", "name": "TDD/BDD Testing", "keywords": ["testing", "tdd", "bdd", "pytest", "behave"]},
    {
        "id": "cap-compliance",
        "name": "Compliance Framework",
        "keywords": ["compliance", "nist", "fedramp", "cmmc", "stig"],
    },
    {
        "id": "cap-security",
        "name": "Security Scanning",
        "keywords": ["security", "sast", "vulnerability", "secret", "container"],
    },
    {
        "id": "cap-infra",
        "name": "Infrastructure as Code",
        "keywords": ["terraform", "ansible", "kubernetes", "docker", "deployment"],
    },
    {
        "id": "cap-monitoring",
        "name": "Monitoring & Observability",
        "keywords": ["monitoring", "logging", "metrics", "tracing", "alerting"],
    },
    {"id": "cap-ai", "name": "AI/ML Integration", "keywords": ["ai", "ml", "model", "llm", "embedding", "inference"]},
    {"id": "cap-data", "name": "Data Pipeline", "keywords": ["data", "pipeline", "etl", "streaming", "database"]},
    {"id": "cap-api", "name": "API Development", "keywords": ["api", "rest", "graphql", "endpoint", "gateway"]},
    {
        "id": "cap-auth",
        "name": "Authentication & Authorization",
        "keywords": ["auth", "identity", "rbac", "oauth", "cac"],
    },
    {"id": "cap-audit", "name": "Audit Trail", "keywords": ["audit", "logging", "trail", "immutable", "append"]},
]


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


def _capmap_id():
    """Generate a capability mapping ID with rcapmap- prefix."""
    return f"rcapmap-{uuid.uuid4().hex[:12]}"


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


def _get_mapping_config(config=None):
    """Extract capability_mapping section from config with defaults."""
    if config is None:
        config = _load_config()
    mapping = config.get("capability_mapping", {})
    return {
        "min_overlap_score": float(mapping.get("min_overlap_score", 0.3)),
        "enhancement_threshold": float(mapping.get("enhancement_threshold", 0.6)),
    }


# =========================================================================
# CATALOG LOADING
# =========================================================================
def load_capability_catalog():
    """Load the ICDEV™ capability catalog from JSON file.

    Falls back to DEFAULT_CATALOG if the file does not exist or is invalid.

    Returns:
        list[dict]: List of capability dictionaries with id, name, keywords.
    """
    if not CATALOG_PATH.exists():
        return list(DEFAULT_CATALOG)

    try:
        with open(CATALOG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        capabilities = data.get("capabilities", data if isinstance(data, list) else [])
        if not capabilities:
            return list(DEFAULT_CATALOG)
        return capabilities
    except (json.JSONDecodeError, IOError, TypeError):
        return list(DEFAULT_CATALOG)


# =========================================================================
# KEYWORD EXTRACTION
# =========================================================================
# Hardcoded English stopwords -- no NLTK dependency needed
_STOPWORDS = frozenset(
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
        "shall",
        "she",
        "should",
        "so",
        "some",
        "such",
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
        "will",
        "with",
        "would",
        "you",
        "your",
    }
)


def _extract_keywords(text):
    """Extract meaningful keywords from text.

    Lowercases, splits on non-alphanumeric characters, removes stopwords
    and short tokens (< 3 chars).

    Returns:
        set[str]: Unique lowercase keywords.
    """
    if not text:
        return set()
    import re

    tokens = re.split(r"[^a-zA-Z0-9]+", text.lower())
    return {t for t in tokens if t and len(t) >= 3 and t not in _STOPWORDS}


# =========================================================================
# CORE ALGORITHM
# =========================================================================
def compute_coverage_score(challenge_keywords, capability_keywords):
    """Compute keyword overlap score between challenge and capability.

    Score = len(challenge_keywords & capability_keywords) / max(1, len(capability_keywords))

    Args:
        challenge_keywords: set of lowercase keywords from the challenge.
        capability_keywords: set of lowercase keywords from the capability.

    Returns:
        float: Overlap score in [0.0, 1.0].
    """
    if not challenge_keywords or not capability_keywords:
        return 0.0

    overlap = challenge_keywords & capability_keywords
    score = len(overlap) / max(1, len(capability_keywords))
    return min(round(score, 4), 1.0)


def map_challenge_capabilities(challenge_id, session_id, db_path=None):
    """Map a single challenge to ICDEV™ capabilities via keyword overlap.

    Loads the challenge from DB, extracts keywords from title + description,
    computes overlap against each catalog capability, and INSERTs matches
    into research_capability_map (append-only).

    Args:
        challenge_id: ID of the research_challenges row.
        session_id: Session ID for the mapping.
        db_path: Optional database path override.

    Returns:
        dict: Mapping result with status, challenge_id, and list of mappings.
    """
    config = _load_config()
    mapping_cfg = _get_mapping_config(config)
    min_overlap = mapping_cfg["min_overlap_score"]
    enhancement_threshold = mapping_cfg["enhancement_threshold"]

    conn = _get_db(db_path)
    try:
        # Load the challenge
        row = conn.execute("SELECT * FROM research_challenges WHERE id = %s", (challenge_id,)).fetchone()

        if not row:
            return {"status": "error", "message": f"Challenge not found: {challenge_id}"}

        challenge = dict(row)

        # Extract keywords from title + description + stored keywords
        text = f"{challenge.get('title', '')} {challenge.get('description', '')}"
        challenge_keywords = _extract_keywords(text)

        # Also incorporate stored keywords JSON array
        try:
            stored_kw = json.loads(challenge.get("keywords", "[]") or "[]")
            if isinstance(stored_kw, list):
                challenge_keywords.update(k.lower().strip() for k in stored_kw if k)
        except (json.JSONDecodeError, TypeError):
            pass

        # Load catalog
        catalog = load_capability_catalog()

        mappings = []
        now = _now()

        for cap in catalog:
            cap_keywords = {k.lower().strip() for k in cap.get("keywords", []) if k}
            score = compute_coverage_score(challenge_keywords, cap_keywords)

            if score >= min_overlap:
                overlap_kw = sorted(challenge_keywords & cap_keywords)
                enhancement_needed = 1 if score < enhancement_threshold else 0

                gap_desc = None
                if enhancement_needed:
                    missing = sorted(cap_keywords - challenge_keywords)
                    gap_desc = f"Coverage {score:.0%} below enhancement threshold {enhancement_threshold:.0%}. Missing capability keywords: {', '.join(missing[:10])}"

                mapping_id = _capmap_id()
                conn.execute(
                    """INSERT INTO research_capability_map
                    (id, session_id, challenge_id, capability_id, capability_name, coverage_score,
                     keyword_overlap, gap_description, enhancement_needed, metadata, mapped_at, classification)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'CUI')""",
                    (
                        mapping_id,
                        session_id,
                        challenge_id,
                        cap["id"],
                        cap["name"],
                        score,
                        json.dumps(overlap_kw),
                        gap_desc,
                        enhancement_needed,
                        json.dumps(
                            {"catalog_keywords": list(cap_keywords), "challenge_keywords": sorted(challenge_keywords)}
                        ),
                        now,
                    ),
                )

                mappings.append(
                    {
                        "id": mapping_id,
                        "capability_id": cap["id"],
                        "capability_name": cap["name"],
                        "coverage_score": score,
                        "keyword_overlap": overlap_kw,
                        "enhancement_needed": bool(enhancement_needed),
                        "gap_description": gap_desc,
                    }
                )

        # Update challenge status to 'mapped' if it was 'scored' or 'new'
        if challenge.get("status") in ("new", "scored"):
            conn.execute(
                "UPDATE research_challenges SET status = 'mapped' WHERE id = %s",
                (challenge_id,),
            )

        conn.commit()

        _audit(
            "research.capability_map",
            f"Mapped challenge {challenge_id} to {len(mappings)} capabilities",
            {"challenge_id": challenge_id, "session_id": session_id, "mappings_count": len(mappings)},
        )

        return {
            "status": "ok",
            "challenge_id": challenge_id,
            "challenge_title": challenge.get("title", ""),
            "mappings_count": len(mappings),
            "mappings": mappings,
        }
    finally:
        conn.close()


def map_all_challenges(session_id, db_path=None):
    """Map all challenges in a session to ICDEV™ capabilities.

    Iterates over all challenges in the given session and calls
    map_challenge_capabilities for each.

    Args:
        session_id: Research session ID.
        db_path: Optional database path override.

    Returns:
        dict: Summary with total challenges, mappings count, and per-challenge results.
    """
    conn = _get_db(db_path)
    try:
        challenges = conn.execute(
            "SELECT id FROM research_challenges WHERE session_id = %s ORDER BY composite_score DESC",
            (session_id,),
        ).fetchall()
    finally:
        conn.close()

    if not challenges:
        return {
            "status": "ok",
            "session_id": session_id,
            "challenges_mapped": 0,
            "total_mappings": 0,
            "message": "No challenges found for session",
            "results": [],
        }

    results = []
    total_mappings = 0

    for ch in challenges:
        result = map_challenge_capabilities(ch["id"], session_id, db_path=db_path)
        results.append(result)
        if result.get("status") == "ok":
            total_mappings += result.get("mappings_count", 0)

    _audit(
        "research.capability_map.batch",
        f"Mapped {len(challenges)} challenges in session {session_id}",
        {"session_id": session_id, "challenges": len(challenges), "total_mappings": total_mappings},
    )

    return {
        "status": "ok",
        "session_id": session_id,
        "challenges_mapped": len(challenges),
        "total_mappings": total_mappings,
        "results": results,
    }


def get_session_coverage(session_id, db_path=None):
    """Aggregate capability coverage across all challenges in a session.

    Returns:
        dict: Coverage summary including:
            - total_challenges_mapped: number of challenges with mappings
            - average_coverage: mean coverage score across all mappings
            - capabilities_highest: capabilities with highest average coverage
            - capabilities_lowest: capabilities with lowest average coverage
            - enhancement_needed_count: number of mappings requiring enhancement
            - gap_capabilities: catalog capabilities with no coverage at all
    """
    conn = _get_db(db_path)
    try:
        # Get all mappings for this session
        mappings = conn.execute(
            "SELECT * FROM research_capability_map WHERE session_id = %s",
            (session_id,),
        ).fetchall()

        if not mappings:
            return {
                "status": "ok",
                "session_id": session_id,
                "total_challenges_mapped": 0,
                "average_coverage": 0.0,
                "capabilities_highest": [],
                "capabilities_lowest": [],
                "enhancement_needed_count": 0,
                "gap_capabilities": [],
                "message": "No mappings found. Run --map first.",
            }

        # Aggregate by capability
        cap_scores = {}  # capability_id -> list of scores
        cap_names = {}  # capability_id -> name
        challenge_ids = set()
        enhancement_count = 0

        for m in mappings:
            m_dict = dict(m)
            cap_id = m_dict["capability_id"]
            cap_name = m_dict["capability_name"]
            score = m_dict["coverage_score"] or 0.0

            cap_names[cap_id] = cap_name
            if cap_id not in cap_scores:
                cap_scores[cap_id] = []
            cap_scores[cap_id].append(score)

            challenge_ids.add(m_dict["challenge_id"])
            if m_dict.get("enhancement_needed"):
                enhancement_count += 1

        # Compute averages per capability
        cap_averages = []
        for cap_id, scores in cap_scores.items():
            avg = sum(scores) / len(scores) if scores else 0.0
            cap_averages.append(
                {
                    "capability_id": cap_id,
                    "capability_name": cap_names[cap_id],
                    "average_coverage": round(avg, 4),
                    "mapping_count": len(scores),
                }
            )

        cap_averages.sort(key=lambda x: x["average_coverage"], reverse=True)

        # Overall average
        all_scores = [m["coverage_score"] or 0.0 for m in mappings]
        overall_avg = sum(all_scores) / len(all_scores) if all_scores else 0.0

        # Find gap capabilities (in catalog but not mapped)
        catalog = load_capability_catalog()
        mapped_cap_ids = set(cap_scores.keys())
        gap_capabilities = [
            {"capability_id": c["id"], "capability_name": c["name"]} for c in catalog if c["id"] not in mapped_cap_ids
        ]

        return {
            "status": "ok",
            "session_id": session_id,
            "total_challenges_mapped": len(challenge_ids),
            "total_mappings": len(mappings),
            "average_coverage": round(overall_avg, 4),
            "capabilities_highest": cap_averages[:5],
            "capabilities_lowest": list(reversed(cap_averages[-5:]))
            if len(cap_averages) > 5
            else list(reversed(cap_averages)),
            "enhancement_needed_count": enhancement_count,
            "gap_capabilities": gap_capabilities,
            "gap_capability_count": len(gap_capabilities),
        }
    finally:
        conn.close()


def get_challenge_capabilities(challenge_id, db_path=None):
    """Get all capability mappings for a specific challenge.

    Args:
        challenge_id: The research challenge ID.
        db_path: Optional database path override.

    Returns:
        dict: Challenge info and its capability mappings.
    """
    conn = _get_db(db_path)
    try:
        # Get challenge info
        challenge = conn.execute(
            "SELECT id, title, category, composite_score, status FROM research_challenges WHERE id = %s",
            (challenge_id,),
        ).fetchone()

        if not challenge:
            return {"status": "error", "message": f"Challenge not found: {challenge_id}"}

        ch_dict = dict(challenge)

        # Get mappings
        mappings = conn.execute(
            "SELECT * FROM research_capability_map WHERE challenge_id = %s ORDER BY coverage_score DESC",
            (challenge_id,),
        ).fetchall()

        mapping_list = []
        for m in mappings:
            m_dict = dict(m)
            # Parse keyword_overlap JSON
            try:
                kw_overlap = json.loads(m_dict.get("keyword_overlap", "[]") or "[]")
            except (json.JSONDecodeError, TypeError):
                kw_overlap = []

            mapping_list.append(
                {
                    "id": m_dict["id"],
                    "capability_id": m_dict["capability_id"],
                    "capability_name": m_dict["capability_name"],
                    "coverage_score": m_dict["coverage_score"],
                    "keyword_overlap": kw_overlap,
                    "enhancement_needed": bool(m_dict.get("enhancement_needed")),
                    "gap_description": m_dict.get("gap_description"),
                    "mapped_at": m_dict.get("mapped_at"),
                }
            )

        return {
            "status": "ok",
            "challenge_id": ch_dict["id"],
            "challenge_title": ch_dict["title"],
            "challenge_category": ch_dict["category"],
            "challenge_score": ch_dict.get("composite_score"),
            "challenge_status": ch_dict["status"],
            "mappings_count": len(mapping_list),
            "mappings": mapping_list,
        }
    finally:
        conn.close()


# =========================================================================
# CLI OUTPUT
# =========================================================================
def _print_human(result, action):
    """Human-readable output."""
    status = result.get("status", "unknown")
    print(f"\n{'=' * 70}")
    print(f"  ICDEV™ Research Capability Mapper -- {status.upper()} -- CUI // SP-CTI")
    print(f"{'=' * 70}")

    if status == "error":
        print(f"\n  ERROR: {result.get('message', 'Unknown error')}")
        print(f"\n{'=' * 70}")
        return

    if action == "map":
        print(f"\n  Session: {result.get('session_id', '')}")
        print(f"  Challenges mapped: {result.get('challenges_mapped', 0)}")
        print(f"  Total mappings: {result.get('total_mappings', 0)}")
        for r in result.get("results", []):
            title = r.get("challenge_title", "")[:45]
            r.get("mappings_count", 0)
            print(f"\n    [{r.get('challenge_id', '')[:20]}] {title}")
            for m in r.get("mappings", []):
                enh = " (enhancement needed)" if m.get("enhancement_needed") else ""
                print(f"      -> {m['capability_name']:35s} score={m['coverage_score']:.2f}{enh}")

    elif action == "map-one":
        print(f"\n  Challenge: {result.get('challenge_title', '')}")
        print(f"  ID: {result.get('challenge_id', '')}")
        print(f"  Mappings: {result.get('mappings_count', 0)}")
        for m in result.get("mappings", []):
            enh = " (enhancement needed)" if m.get("enhancement_needed") else ""
            overlap = ", ".join(m.get("keyword_overlap", [])[:5])
            print(f"    -> {m['capability_name']:35s} score={m['coverage_score']:.2f}{enh}")
            if overlap:
                print(f"       keywords: {overlap}")

    elif action == "coverage":
        print(f"\n  Session: {result.get('session_id', '')}")
        print(f"  Challenges mapped: {result.get('total_challenges_mapped', 0)}")
        print(f"  Total mappings: {result.get('total_mappings', 0)}")
        print(f"  Average coverage: {result.get('average_coverage', 0):.2%}")
        print(f"  Enhancements needed: {result.get('enhancement_needed_count', 0)}")
        print(f"  Gap capabilities: {result.get('gap_capability_count', 0)}")

        highest = result.get("capabilities_highest", [])
        if highest:
            print("\n  Highest Coverage:")
            for c in highest:
                print(
                    f"    {c['capability_name']:35s} avg={c['average_coverage']:.2f}  ({c['mapping_count']} mappings)"
                )

        lowest = result.get("capabilities_lowest", [])
        if lowest:
            print("\n  Lowest Coverage:")
            for c in lowest:
                print(
                    f"    {c['capability_name']:35s} avg={c['average_coverage']:.2f}  ({c['mapping_count']} mappings)"
                )

        gaps = result.get("gap_capabilities", [])
        if gaps:
            print("\n  Gap Capabilities (no coverage):")
            for g in gaps:
                print(f"    - {g['capability_name']}")

    elif action == "challenge-caps":
        print(f"\n  Challenge: {result.get('challenge_title', '')}")
        print(f"  Category: {result.get('challenge_category', '')}")
        print(f"  Score: {result.get('challenge_score', 'N/A')}")
        print(f"  Status: {result.get('challenge_status', '')}")
        print(f"  Mappings: {result.get('mappings_count', 0)}")
        for m in result.get("mappings", []):
            enh = " (enhancement needed)" if m.get("enhancement_needed") else ""
            print(f"\n    [{m['capability_id']}] {m['capability_name']}")
            print(f"      Coverage: {m['coverage_score']:.2f}{enh}")
            overlap = ", ".join(m.get("keyword_overlap", [])[:8])
            if overlap:
                print(f"      Overlap: {overlap}")
            if m.get("gap_description"):
                print(f"      Gap: {m['gap_description'][:80]}")

    print(f"\n{'=' * 70}\n")


# =========================================================================
# CLI
# =========================================================================
def main():
    parser = argparse.ArgumentParser(
        description="ICDEV™ Research Capability Mapper (D-RES-7) -- CUI // SP-CTI",
    )
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable output")
    parser.add_argument("--db-path", type=Path, default=None, help="Database path override")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--map", action="store_true", help="Map all challenges in session to capabilities")
    group.add_argument("--map-one", action="store_true", help="Map single challenge to capabilities")
    group.add_argument("--coverage", action="store_true", help="Show session capability coverage")
    group.add_argument("--challenge-caps", action="store_true", help="Show capabilities for a challenge")

    parser.add_argument("--session-id", type=str, help="Research session ID (required for --map, --coverage)")
    parser.add_argument("--challenge-id", type=str, help="Challenge ID (required for --map-one, --challenge-caps)")

    args = parser.parse_args()

    try:
        if args.map:
            if not args.session_id:
                print("Error: --session-id required for --map", file=sys.stderr)
                sys.exit(1)
            result = map_all_challenges(args.session_id, db_path=args.db_path)
            action = "map"
        elif args.map_one:
            if not args.challenge_id or not args.session_id:
                print("Error: --challenge-id and --session-id required for --map-one", file=sys.stderr)
                sys.exit(1)
            result = map_challenge_capabilities(args.challenge_id, args.session_id, db_path=args.db_path)
            action = "map-one"
        elif args.coverage:
            if not args.session_id:
                print("Error: --session-id required for --coverage", file=sys.stderr)
                sys.exit(1)
            result = get_session_coverage(args.session_id, db_path=args.db_path)
            action = "coverage"
        elif args.challenge_caps:
            if not args.challenge_id:
                print("Error: --challenge-id required for --challenge-caps", file=sys.stderr)
                sys.exit(1)
            result = get_challenge_capabilities(args.challenge_id, db_path=args.db_path)
            action = "challenge-caps"
        else:
            result = {"status": "error", "message": "No action specified"}
            action = "unknown"

        if args.human:
            _print_human(result, action)
        else:
            print(json.dumps(result, indent=2, default=str))

    except FileNotFoundError as e:
        error = {"status": "error", "error": str(e), "hint": "Run: python tools/db/init_icdev_db.py"}
        if args.human:
            print(f"ERROR: {e}", file=sys.stderr)
        else:
            print(json.dumps(error, indent=2))
        sys.exit(1)
    except Exception as e:
        error = {"status": "error", "error": str(e)}
        if args.human:
            print(f"ERROR: {e}", file=sys.stderr)
        else:
            print(json.dumps(error, indent=2))
        sys.exit(1)


if __name__ == "__main__":
    main()
