# CUI // SP-CTI
# ICDEV™ Proposal Genesis — Win Theme & Discriminator Registry (§3.17)
# Manages win themes, discriminators, and ghost competitor strategies as first-class objects.

"""
Win Theme Manager — register, track, and measure theme density across proposal sections.

Manages:
    - Win themes, discriminators, and ghost competitor strategies (pg_win_themes)
    - Theme implementation tracking per section (pg_theme_tracking)
    - Keyword-based theme presence detection (deterministic, no LLM)
    - Theme density scoring (themes per 1000 words)
    - Implementation coverage reports

Tables used:
    - pg_win_themes (CRUD — register/update/archive themes)
    - pg_theme_tracking (append-only — implementation tracking, NIST AU-2)
    - audit_trail (write — NIST AU-2)

Usage:
    python tools/govcon/win_theme_manager.py --opportunity-id "opp-xxx" --register --type win_theme --statement "..." --json
    python tools/govcon/win_theme_manager.py --opportunity-id "opp-xxx" --list --json
    python tools/govcon/win_theme_manager.py --opportunity-id "opp-xxx" --list --type discriminator --json
    python tools/govcon/win_theme_manager.py --opportunity-id "opp-xxx" --report --json
    python tools/govcon/win_theme_manager.py --opportunity-id "opp-xxx" --density --json
    python tools/govcon/win_theme_manager.py --theme-id "wt-xxx" --archive --json
    python tools/govcon/win_theme_manager.py --theme-id "wt-xxx" --track --section-id "sec-xxx" --status woven --json
"""

import argparse
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.db.storage import get_connection  # noqa: E402
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.govcon.win_theme_manager")

_DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(_ROOT / "data" / "icdev.db")))

# Theme types supported
THEME_TYPES = ("win_theme", "discriminator", "ghost_strategy")

# English stopwords for keyword extraction (deterministic, no NLTK dependency)
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "but",
        "in",
        "on",
        "at",
        "to",
        "for",
        "of",
        "with",
        "by",
        "from",
        "as",
        "is",
        "was",
        "are",
        "were",
        "be",
        "been",
        "being",
        "have",
        "has",
        "had",
        "do",
        "does",
        "did",
        "will",
        "would",
        "could",
        "should",
        "may",
        "might",
        "shall",
        "can",
        "this",
        "that",
        "these",
        "those",
        "it",
        "its",
        "we",
        "our",
        "us",
        "they",
        "them",
        "their",
        "he",
        "she",
        "him",
        "her",
        "not",
        "no",
        "all",
        "each",
        "every",
        "both",
        "few",
        "more",
        "most",
        "other",
        "some",
        "such",
        "than",
        "too",
        "very",
        "just",
        "also",
        "any",
        "into",
        "over",
        "after",
        "before",
        "between",
        "through",
        "during",
        "about",
        "up",
        "out",
        "if",
        "then",
        "so",
        "because",
        "while",
        "where",
        "when",
        "which",
        "who",
        "whom",
        "how",
        "what",
        "there",
    }
)

# Minimum keyword match ratio to consider a theme present in text
_THEME_PRESENCE_THRESHOLD = 0.60


# ── Helpers ──────────────────────────────────────────────────────────


def _get_db():
    conn = get_connection()
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _now():
    return datetime.now(timezone.utc).isoformat()


def _uuid():
    return f"wt-{uuid.uuid4().hex[:12]}"


def _tracking_uuid():
    return f"wtt-{uuid.uuid4().hex[:12]}"


def _audit(conn, action, details="", actor="win_theme_manager"):
    try:
        conn.execute(
            "INSERT INTO audit_trail (created_at, event_type, actor, action, details, session_id) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (_now(), "govcon.win_theme", actor, action, details, "proposal_genesis"),
        )
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning("_audit: best-effort INSERT into audit_trail failed (non-blocking): %s", exc)


def _extract_keywords(text):
    """Extract meaningful keywords from text (deterministic, no LLM).

    Lowercases, removes punctuation, filters stopwords, returns unique tokens.
    """
    if not text:
        return []
    cleaned = re.sub(r"[^\w\s]", " ", text.lower())
    tokens = cleaned.split()
    keywords = [t for t in tokens if t not in _STOPWORDS and len(t) > 2]
    # Deduplicate while preserving order
    seen = set()
    result = []
    for kw in keywords:
        if kw not in seen:
            seen.add(kw)
            result.append(kw)
    return result


def _word_count(text):
    """Count words in text."""
    if not text:
        return 0
    return len(text.split())


# ── Theme CRUD ───────────────────────────────────────────────────────


def register_theme(
    opportunity_id,
    theme_type,
    theme_statement,
    supporting_evidence=None,
    target_eval_factor=None,
    ghost_competitor=None,
    priority=1,
):
    """Register a win theme, discriminator, or ghost competitor strategy.

    Args:
        opportunity_id: Parent opportunity UUID.
        theme_type: One of 'win_theme', 'discriminator', 'ghost_strategy'.
        theme_statement: The theme statement text.
        supporting_evidence: Optional evidence supporting the theme.
        target_eval_factor: Optional evaluation factor this theme targets.
        ghost_competitor: Optional competitor name (for ghost_strategy type).
        priority: Priority 1-5 (1=highest).

    Returns:
        Dict with status and theme_id.
    """
    if theme_type not in THEME_TYPES:
        return {
            "status": "error",
            "message": f"Invalid theme_type '{theme_type}'. Must be one of: {THEME_TYPES}",
        }

    if not theme_statement or not theme_statement.strip():
        return {"status": "error", "message": "theme_statement is required"}

    conn = _get_db()
    theme_id = _uuid()
    keywords = _extract_keywords(theme_statement)
    now = _now()

    try:
        conn.execute(
            "INSERT INTO pg_win_themes "
            "(id, opportunity_id, theme_type, theme_statement, supporting_evidence, "
            "target_eval_factor, ghost_competitor, priority, keywords, status, "
            "created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                theme_id,
                opportunity_id,
                theme_type,
                theme_statement.strip(),
                supporting_evidence,
                target_eval_factor,
                ghost_competitor,
                priority,
                json.dumps(keywords),
                "active",
                now,
                now,
            ),
        )
        _audit(conn, "register_theme", f"Registered {theme_type} for {opportunity_id}: {theme_statement[:80]}")
        conn.commit()
    except Exception as exc:
        conn.close()
        return {"status": "error", "message": str(exc)}

    conn.close()
    return {
        "status": "ok",
        "theme_id": theme_id,
        "theme_type": theme_type,
        "keywords_extracted": len(keywords),
        "keywords": keywords,
    }


def list_themes(opportunity_id, theme_type=None):
    """List themes for an opportunity, optionally filtered by type.

    Args:
        opportunity_id: Parent opportunity UUID.
        theme_type: Optional filter — one of THEME_TYPES.

    Returns:
        List of theme dicts.
    """
    conn = _get_db()
    query = "SELECT * FROM pg_win_themes WHERE opportunity_id = ? AND status != 'archived'"
    params = [opportunity_id]

    if theme_type:
        query += " AND theme_type = ?"
        params.append(theme_type)

    query += " ORDER BY priority ASC, created_at ASC"
    rows = conn.execute(query, params).fetchall()
    conn.close()

    themes = []
    for row in rows:
        theme = dict(row)
        # Parse keywords JSON
        try:
            theme["keywords"] = json.loads(theme.get("keywords", "[]"))
        except (json.JSONDecodeError, TypeError):
            theme["keywords"] = []
        themes.append(theme)

    return themes


def update_theme(theme_id, **kwargs):
    """Update theme fields.

    Args:
        theme_id: Theme UUID.
        **kwargs: Fields to update (theme_statement, supporting_evidence,
                  target_eval_factor, ghost_competitor, priority, status).

    Returns:
        Dict with status and updated_fields.
    """
    conn = _get_db()
    row = conn.execute("SELECT * FROM pg_win_themes WHERE id = %s", (theme_id,)).fetchone()
    if not row:
        conn.close()
        return {"status": "error", "message": f"Theme {theme_id} not found"}

    updatable = [
        "theme_statement",
        "supporting_evidence",
        "target_eval_factor",
        "ghost_competitor",
        "priority",
        "status",
    ]
    sets = []
    params = []
    for field in updatable:
        if field in kwargs and kwargs[field] is not None:
            sets.append(f"{field} = ?")
            params.append(kwargs[field])

    if not sets:
        conn.close()
        return {"status": "error", "message": "No updatable fields provided"}

    # Re-extract keywords if theme_statement changed
    if "theme_statement" in kwargs and kwargs["theme_statement"]:
        keywords = _extract_keywords(kwargs["theme_statement"])
        sets.append("keywords = ?")
        params.append(json.dumps(keywords))

    sets.append("updated_at = ?")
    params.append(_now())
    params.append(theme_id)

    conn.execute(f"UPDATE pg_win_themes SET {', '.join(sets)} WHERE id = %s", params)  # nosec B608 -- table/column names are internal constants, not user input
    _audit(conn, "update_theme", f"Updated theme {theme_id}: {list(kwargs.keys())}")
    conn.commit()
    conn.close()

    return {"status": "ok", "theme_id": theme_id, "updated_fields": list(kwargs.keys())}


def archive_theme(theme_id):
    """Archive a theme (soft delete — status set to 'archived').

    Args:
        theme_id: Theme UUID.

    Returns:
        Dict with status.
    """
    conn = _get_db()
    row = conn.execute("SELECT id, status FROM pg_win_themes WHERE id = %s", (theme_id,)).fetchone()
    if not row:
        conn.close()
        return {"status": "error", "message": f"Theme {theme_id} not found"}

    if row["status"] == "archived":
        conn.close()
        return {"status": "ok", "message": "Already archived"}

    conn.execute(
        "UPDATE pg_win_themes SET status = 'archived', updated_at = %s WHERE id = %s",
        (_now(), theme_id),
    )
    _audit(conn, "archive_theme", f"Archived theme {theme_id}")
    conn.commit()
    conn.close()

    return {"status": "ok", "theme_id": theme_id, "archived": True}


# ── Theme Tracking ───────────────────────────────────────────────────


def track_implementation(theme_id, section_id, status, density_score=None, notes=None):
    """Record theme implementation in a proposal section.

    Args:
        theme_id: Theme UUID.
        section_id: Proposal section UUID.
        status: Implementation status — 'planned', 'woven', 'verified', 'missing'.
        density_score: Optional pre-computed density score.
        notes: Optional notes.

    Returns:
        Dict with status and tracking_id.
    """
    valid_statuses = ("planned", "woven", "verified", "missing")
    if status not in valid_statuses:
        return {
            "status": "error",
            "message": f"Invalid status '{status}'. Must be one of: {valid_statuses}",
        }

    conn = _get_db()

    # Verify theme exists
    theme = conn.execute("SELECT id FROM pg_win_themes WHERE id = %s", (theme_id,)).fetchone()
    if not theme:
        conn.close()
        return {"status": "error", "message": f"Theme {theme_id} not found"}

    tracking_id = _tracking_uuid()
    now = _now()

    # Append-only tracking record (NIST AU-2)
    conn.execute(
        "INSERT INTO pg_theme_tracking "
        "(id, theme_id, section_id, status, density_score, notes, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (tracking_id, theme_id, section_id, status, density_score, notes, now),
    )
    _audit(
        conn,
        "track_implementation",
        f"Theme {theme_id} in section {section_id}: status={status}, density={density_score}",
    )
    conn.commit()
    conn.close()

    return {"status": "ok", "tracking_id": tracking_id, "theme_id": theme_id, "section_id": section_id}


# ── Theme Presence Detection ─────────────────────────────────────────


def check_theme_presence(text, themes):
    """Find which themes are present in the given text (deterministic keyword matching).

    Extracts keywords from each theme_statement, checks if >= 60% appear in text.
    Returns list of matched themes with match details.

    Args:
        text: Section text to check.
        themes: List of theme dicts (must have 'id', 'theme_statement', optionally 'keywords').

    Returns:
        List of dicts with theme_id, matched_keywords, match_ratio, density_score.
    """
    if not text or not themes:
        return []

    text_lower = text.lower()
    text_words = set(re.sub(r"[^\w\s]", " ", text_lower).split())
    wc = _word_count(text)

    results = []
    for theme in themes:
        # Use pre-extracted keywords if available, otherwise extract on the fly
        keywords = theme.get("keywords", [])
        if isinstance(keywords, str):
            try:
                keywords = json.loads(keywords)
            except (json.JSONDecodeError, TypeError):
                keywords = []
        if not keywords:
            keywords = _extract_keywords(theme.get("theme_statement", ""))

        if not keywords:
            continue

        matched = [kw for kw in keywords if kw in text_words]
        match_ratio = len(matched) / len(keywords) if keywords else 0.0

        if match_ratio >= _THEME_PRESENCE_THRESHOLD:
            density = (len(matched) / (wc / 1000.0)) if wc > 0 else 0.0
            results.append(
                {
                    "theme_id": theme.get("id", "unknown"),
                    "theme_type": theme.get("theme_type", "unknown"),
                    "theme_statement": theme.get("theme_statement", ""),
                    "total_keywords": len(keywords),
                    "matched_keywords": matched,
                    "match_ratio": round(match_ratio, 3),
                    "density_score": round(density, 2),
                }
            )

    return results


# ── Reports ──────────────────────────────────────────────────────────


def get_implementation_report(opportunity_id):
    """Generate theme coverage report across all sections.

    Args:
        opportunity_id: Parent opportunity UUID.

    Returns:
        Dict with per-theme coverage status.
    """
    conn = _get_db()

    # Get all active themes
    themes = conn.execute(
        "SELECT * FROM pg_win_themes WHERE opportunity_id = %s AND status != 'archived' ORDER BY priority ASC",
        (opportunity_id,),
    ).fetchall()

    if not themes:
        conn.close()
        return {
            "status": "ok",
            "opportunity_id": opportunity_id,
            "message": "No themes registered",
            "themes": [],
            "summary": {"total": 0, "fully_covered": 0, "partially_covered": 0, "uncovered": 0},
        }

    report_themes = []
    total = len(themes)
    fully_covered = 0
    partially_covered = 0
    uncovered = 0

    for theme in themes:
        theme_id = theme["id"]

        # Get latest tracking per section (most recent record per section)
        tracking = conn.execute(
            "SELECT section_id, status, density_score, notes, created_at "
            "FROM pg_theme_tracking "
            "WHERE theme_id = %s "
            "ORDER BY created_at DESC",
            (theme_id,),
        ).fetchall()

        # Deduplicate by section_id (keep latest)
        sections = {}
        for t in tracking:
            sid = t["section_id"]
            if sid not in sections:
                sections[sid] = dict(t)

        woven_count = sum(1 for s in sections.values() if s["status"] in ("woven", "verified"))
        planned_count = sum(1 for s in sections.values() if s["status"] == "planned")
        missing_count = sum(1 for s in sections.values() if s["status"] == "missing")

        if woven_count > 0 and missing_count == 0 and planned_count == 0:
            coverage = "fully_covered"
            fully_covered += 1
        elif woven_count > 0 or planned_count > 0:
            coverage = "partially_covered"
            partially_covered += 1
        else:
            coverage = "uncovered"
            uncovered += 1

        report_themes.append(
            {
                "theme_id": theme_id,
                "theme_type": theme["theme_type"],
                "theme_statement": theme["theme_statement"],
                "priority": theme["priority"],
                "coverage": coverage,
                "sections_woven": woven_count,
                "sections_planned": planned_count,
                "sections_missing": missing_count,
                "total_sections": len(sections),
                "sections": list(sections.values()),
            }
        )

    conn.close()

    return {
        "status": "ok",
        "opportunity_id": opportunity_id,
        "themes": report_themes,
        "summary": {
            "total": total,
            "fully_covered": fully_covered,
            "partially_covered": partially_covered,
            "uncovered": uncovered,
            "coverage_rate": round(fully_covered / total, 3) if total > 0 else 0.0,
        },
    }


def get_density_scores(opportunity_id):
    """Get theme density per section for an opportunity.

    Density = theme_keyword_matches / (word_count / 1000) — themes per 1000 words.

    Args:
        opportunity_id: Parent opportunity UUID.

    Returns:
        Dict with per-section density breakdown.
    """
    conn = _get_db()

    # Get all active themes for this opportunity
    themes = conn.execute(
        "SELECT id, theme_type, theme_statement, keywords, priority "
        "FROM pg_win_themes "
        "WHERE opportunity_id = %s AND status != 'archived'",
        (opportunity_id,),
    ).fetchall()

    if not themes:
        conn.close()
        return {
            "status": "ok",
            "opportunity_id": opportunity_id,
            "message": "No themes registered",
            "sections": [],
        }

    # Get tracking records with density scores
    all_tracking = conn.execute(
        "SELECT t.section_id, t.theme_id, t.status, t.density_score, t.created_at "
        "FROM pg_theme_tracking t "
        "INNER JOIN pg_win_themes w ON t.theme_id = w.id "
        "WHERE w.opportunity_id = %s "
        "ORDER BY t.section_id, t.created_at DESC",
        (opportunity_id,),
    ).fetchall()

    conn.close()

    # Aggregate by section
    sections = {}
    for t in all_tracking:
        sid = t["section_id"]
        if sid not in sections:
            sections[sid] = {"section_id": sid, "themes": {}, "avg_density": 0.0}
        tid = t["theme_id"]
        # Keep latest per theme per section
        if tid not in sections[sid]["themes"]:
            sections[sid]["themes"][tid] = {
                "theme_id": tid,
                "status": t["status"],
                "density_score": t["density_score"],
            }

    # Compute averages
    section_list = []
    for sid, sec in sections.items():
        densities = [th["density_score"] for th in sec["themes"].values() if th["density_score"] is not None]
        avg = sum(densities) / len(densities) if densities else 0.0
        section_list.append(
            {
                "section_id": sid,
                "theme_count": len(sec["themes"]),
                "themes": list(sec["themes"].values()),
                "avg_density": round(avg, 2),
                "density_rated": densities,
            }
        )

    # Overall average
    all_densities = [s["avg_density"] for s in section_list if s["avg_density"] > 0]
    overall_avg = sum(all_densities) / len(all_densities) if all_densities else 0.0

    return {
        "status": "ok",
        "opportunity_id": opportunity_id,
        "total_themes": len(themes),
        "sections": section_list,
        "overall_avg_density": round(overall_avg, 2),
    }


# ── CLI ──────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="ICDEV™ Proposal Genesis — Win Theme & Discriminator Registry (§3.17)")

    # Actions
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--register", action="store_true", help="Register a new theme")
    group.add_argument("--list", action="store_true", help="List themes for opportunity")
    group.add_argument("--update", action="store_true", help="Update a theme")
    group.add_argument("--archive", action="store_true", help="Archive a theme")
    group.add_argument("--track", action="store_true", help="Track theme implementation in section")
    group.add_argument("--report", action="store_true", help="Theme implementation report")
    group.add_argument("--density", action="store_true", help="Theme density scores per section")

    # Identifiers
    parser.add_argument("--opportunity-id", help="Opportunity UUID")
    parser.add_argument("--theme-id", help="Theme UUID")
    parser.add_argument("--section-id", help="Section UUID (for --track)")

    # Theme fields
    parser.add_argument("--type", dest="theme_type", choices=THEME_TYPES, help="Theme type")
    parser.add_argument("--statement", help="Theme statement text")
    parser.add_argument("--evidence", help="Supporting evidence")
    parser.add_argument("--eval-factor", help="Target evaluation factor")
    parser.add_argument("--ghost-competitor", help="Ghost competitor name")
    parser.add_argument("--priority", type=int, default=1, help="Priority 1-5 (1=highest)")

    # Tracking fields
    parser.add_argument("--status", help="Implementation status (planned/woven/verified/missing)")
    parser.add_argument("--density-score", type=float, help="Pre-computed density score")
    parser.add_argument("--notes", help="Notes")

    # Output format
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable output")

    args = parser.parse_args()

    result = None

    if args.register:
        if not args.opportunity_id or not args.theme_type or not args.statement:
            print("Error: --opportunity-id, --type, and --statement required for --register", file=sys.stderr)
            sys.exit(1)
        result = register_theme(
            opportunity_id=args.opportunity_id,
            theme_type=args.theme_type,
            theme_statement=args.statement,
            supporting_evidence=args.evidence,
            target_eval_factor=args.eval_factor,
            ghost_competitor=args.ghost_competitor,
            priority=args.priority,
        )

    elif args.list:
        if not args.opportunity_id:
            print("Error: --opportunity-id required for --list", file=sys.stderr)
            sys.exit(1)
        themes = list_themes(args.opportunity_id, theme_type=args.theme_type)
        result = {"status": "ok", "opportunity_id": args.opportunity_id, "themes": themes, "count": len(themes)}

    elif args.update:
        if not args.theme_id:
            print("Error: --theme-id required for --update", file=sys.stderr)
            sys.exit(1)
        kwargs = {}
        if args.statement:
            kwargs["theme_statement"] = args.statement
        if args.evidence:
            kwargs["supporting_evidence"] = args.evidence
        if args.eval_factor:
            kwargs["target_eval_factor"] = args.eval_factor
        if args.ghost_competitor:
            kwargs["ghost_competitor"] = args.ghost_competitor
        if args.priority:
            kwargs["priority"] = args.priority
        if args.status:
            kwargs["status"] = args.status
        result = update_theme(args.theme_id, **kwargs)

    elif args.archive:
        if not args.theme_id:
            print("Error: --theme-id required for --archive", file=sys.stderr)
            sys.exit(1)
        result = archive_theme(args.theme_id)

    elif args.track:
        if not args.theme_id or not args.section_id or not args.status:
            print("Error: --theme-id, --section-id, and --status required for --track", file=sys.stderr)
            sys.exit(1)
        result = track_implementation(
            theme_id=args.theme_id,
            section_id=args.section_id,
            status=args.status,
            density_score=args.density_score,
            notes=args.notes,
        )

    elif args.report:
        if not args.opportunity_id:
            print("Error: --opportunity-id required for --report", file=sys.stderr)
            sys.exit(1)
        result = get_implementation_report(args.opportunity_id)

    elif args.density:
        if not args.opportunity_id:
            print("Error: --opportunity-id required for --density", file=sys.stderr)
            sys.exit(1)
        result = get_density_scores(args.opportunity_id)

    # Output
    if args.json or not args.human:
        print(json.dumps(result, indent=2, default=str))
    elif args.human:
        print(f"\n{'=' * 60}")
        print(f"  Win Theme Manager — {args.opportunity_id or args.theme_id or '?'}")
        print(f"{'=' * 60}")
        if "themes" in result and isinstance(result["themes"], list):
            for t in result["themes"]:
                prio = t.get("priority", "?")
                ttype = t.get("theme_type", "unknown")
                stmt = t.get("theme_statement", "")[:60]
                cov = t.get("coverage", "")
                print(f"  [P{prio}] ({ttype}) {stmt}... {cov}")
        if "summary" in result:
            s = result["summary"]
            print(
                f"\n  Total: {s.get('total', 0)} | "
                f"Covered: {s.get('fully_covered', 0)} | "
                f"Partial: {s.get('partially_covered', 0)} | "
                f"Uncovered: {s.get('uncovered', 0)}"
            )
            if "coverage_rate" in s:
                print(f"  Coverage Rate: {s['coverage_rate']:.0%}")
        print()


if __name__ == "__main__":
    main()
