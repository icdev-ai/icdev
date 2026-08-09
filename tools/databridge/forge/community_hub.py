#!/usr/bin/env python3
# CUI // SP-CTI
"""Connector Forge Community Hub — browsable connector gallery with ratings and trust scoring.

Provides browse, detail, rate, trust-score, featured, and search operations
against forge connectors.  Trust scoring uses a 5-factor weighted model:
validation_pass_rate (0.30), avg_rating_normalized (0.25),
download_count_normalized (0.20), age_factor (0.15), author_reputation (0.10).
"""

import argparse
import json
import math
import sqlite3
import sys
from pathlib import Path

# kax-conflict-05: run by path, sys.path[0] is this file's own directory — never
# the import root. Bootstrap it before the first first-party import below.
# parents[N] is whatever holds this file's `tools` package: the repo root in
# tools/, and <repo>/icdev in the icdev/ mirror (which is what a wheel ships).
_REPO_ROOT = Path(__file__).resolve().parents[3]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.db.storage import get_connection
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

try:
    from tools.compat.db_utils import get_db_connection
except ImportError:
    get_db_connection = None


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------


def _get_connection(db_path=None):
    """Get a database connection with row factory."""
    if get_db_connection:
        return get_db_connection(db_path or DB_PATH, validate=True)
    path = db_path or DB_PATH
    if not Path(path).exists():
        raise FileNotFoundError(f"Database not found: {path}\nRun: python tools/db/init_icdev_db.py")
    conn = get_connection(db_path=str(path))
    return conn


def _ensure_tables(conn):
    """Create hub tables if they do not exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS forge_hub_ratings (
            id TEXT PRIMARY KEY,
            connector_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
            review_text TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(connector_id, user_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS forge_hub_trust_scores (
            id TEXT PRIMARY KEY,
            connector_id TEXT NOT NULL,
            trust_score REAL NOT NULL,
            breakdown TEXT NOT NULL,
            computed_at TEXT NOT NULL
        )
    """)
    conn.commit()


def _safe_query(conn, sql, params=()):
    """Execute query, return list of dicts.  Returns [] on table-missing."""
    try:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError:
        return []


def _log_audit(conn, event_type, action, details):
    """Append an audit trail entry (immutable — INSERT only)."""
    try:
        conn.execute(
            """INSERT INTO audit_trail
               (project_id, event_type, actor, action, details, classification)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (
                "",
                event_type,
                "icdev-forge-community-hub",
                action,
                json.dumps(details),
                "CUI",
            ),
        )
        conn.commit()
    except Exception as exc:
        print(f"Warning: audit log failed: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def browse_connectors(category=None, search=None, db_path=None):
    """Query db_forge_connectors table with optional filters.

    Args:
        category: Filter by connector_type.
        search: Filter by name LIKE pattern.
        db_path: Optional database path override.

    Returns:
        {"connectors": [...], "count": N}
    """
    conn = _get_connection(db_path)
    try:
        _ensure_tables(conn)

        sql = "SELECT * FROM db_forge_connectors WHERE 1=1"
        params = []
        if category:
            sql += " AND connector_type = ?"
            params.append(category)
        if search:
            sql += " AND name LIKE ?"
            params.append(f"%{search}%")

        rows = _safe_query(conn, sql, params)

        # Enrich with avg rating and download count
        enriched = []
        for row in rows:
            cid = row.get("id", row.get("connector_id", ""))
            avg_rating = _compute_avg_rating(conn, cid)
            enriched.append(
                {
                    "id": cid,
                    "name": row.get("name", ""),
                    "connector_type": row.get("connector_type", ""),
                    "status": row.get("status", ""),
                    "trust_score": _get_latest_trust_score(conn, cid),
                    "avg_rating": avg_rating,
                    "download_count": row.get("download_count", 0),
                }
            )

        return {"connectors": enriched, "count": len(enriched)}
    finally:
        conn.close()


def get_connector_detail(connector_id, db_path=None):
    """Full connector info with ratings.

    Returns:
        Connector dict with ratings array, or {"error": "..."}.
    """
    conn = _get_connection(db_path)
    try:
        _ensure_tables(conn)

        rows = _safe_query(
            conn,
            "SELECT * FROM db_forge_connectors WHERE id = ?",
            (connector_id,),
        )
        if not rows:
            return {"error": f"Connector not found: {connector_id}"}

        connector = rows[0]

        ratings = _safe_query(
            conn,
            "SELECT * FROM forge_hub_ratings WHERE connector_id = ? ORDER BY created_at DESC",
            (connector_id,),
        )

        connector["ratings"] = ratings
        connector["avg_rating"] = _compute_avg_rating(conn, connector_id)
        connector["trust_score"] = _get_latest_trust_score(conn, connector_id)

        return connector
    finally:
        conn.close()


def rate_connector(connector_id, user_id, rating, review_text=None, db_path=None):
    """INSERT OR REPLACE a rating for a connector.

    Returns:
        {"connector_id", "user_id", "rating", "new_avg"}
    """
    if not (1 <= rating <= 5):
        return {"error": "Rating must be between 1 and 5"}

    conn = _get_connection(db_path)
    try:
        _ensure_tables(conn)
        now = datetime.now(timezone.utc).isoformat()
        rating_id = uuid4().hex[:12]

        # INSERT OR REPLACE (UNIQUE constraint on connector_id, user_id)
        conn.execute(
            """INSERT INTO forge_hub_ratings
               (id, connector_id, user_id, rating, review_text, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT(connector_id, user_id) DO UPDATE SET
                   rating = excluded.rating,
                   review_text = excluded.review_text,
                   updated_at = excluded.updated_at""",
            (rating_id, connector_id, user_id, rating, review_text, now, now),
        )
        conn.commit()

        new_avg = _compute_avg_rating(conn, connector_id)

        _log_audit(
            conn,
            "forge_hub_rated",
            "rate_connector",
            {
                "connector_id": connector_id,
                "user_id": user_id,
                "rating": rating,
                "new_avg": new_avg,
            },
        )

        return {
            "connector_id": connector_id,
            "user_id": user_id,
            "rating": rating,
            "new_avg": new_avg,
        }
    finally:
        conn.close()


def compute_trust_score(connector_id, db_path=None):
    """Compute weighted trust score for a connector.

    Weights:
      - validation_pass_rate: 0.30
      - avg_rating_normalized: 0.25
      - download_count_normalized: 0.20
      - age_factor: 0.15
      - author_reputation: 0.10

    Returns:
        {"connector_id", "trust_score", "breakdown": {...}}
    """
    conn = _get_connection(db_path)
    try:
        _ensure_tables(conn)

        # Fetch connector
        rows = _safe_query(
            conn,
            "SELECT * FROM db_forge_connectors WHERE id = ?",
            (connector_id,),
        )
        if not rows:
            return {"error": f"Connector not found: {connector_id}"}

        connector = rows[0]

        # Factor 1: validation pass rate (from connector record)
        validation_pass_rate = 0.0
        validation_json = connector.get("validation_result", "")
        if validation_json:
            try:
                vr = json.loads(validation_json) if isinstance(validation_json, str) else validation_json
                total = vr.get("total_checks", 0)
                passed = vr.get("passed", 0)
                if total > 0:
                    validation_pass_rate = passed / total
            except (json.JSONDecodeError, TypeError):
                pass

        # Factor 2: avg rating normalized (1-5 -> 0-1)
        avg_rating = _compute_avg_rating(conn, connector_id)
        avg_rating_normalized = (avg_rating - 1.0) / 4.0 if avg_rating > 0 else 0.0

        # Factor 3: download count normalized (log scale, cap at 1.0)
        download_count = connector.get("download_count", 0) or 0
        download_normalized = min(1.0, math.log1p(download_count) / math.log1p(1000))

        # Factor 4: age factor (older = more trusted, cap at 365 days)
        age_days = 0
        created_at = connector.get("created_at", "")
        if created_at:
            try:
                created_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                age_days = (datetime.now(timezone.utc) - created_dt).days
            except (ValueError, TypeError):
                pass
        age_factor = min(1.0, age_days / 365.0)

        # Factor 5: author reputation (count of connectors by same author)
        author = connector.get("author", connector.get("tenant_id", ""))
        author_reputation = 0.0
        if author:
            author_rows = _safe_query(
                conn,
                "SELECT COUNT(*) as cnt FROM db_forge_connectors WHERE tenant_id = ?",
                (author,),
            )
            if author_rows:
                author_count = author_rows[0].get("cnt", 0)
                author_reputation = min(1.0, author_count / 10.0)

        # Weighted sum
        trust_score = round(
            0.30 * validation_pass_rate
            + 0.25 * avg_rating_normalized
            + 0.20 * download_normalized
            + 0.15 * age_factor
            + 0.10 * author_reputation,
            4,
        )

        breakdown = {
            "validation_pass_rate": round(validation_pass_rate, 4),
            "avg_rating_normalized": round(avg_rating_normalized, 4),
            "download_count_normalized": round(download_normalized, 4),
            "age_factor": round(age_factor, 4),
            "author_reputation": round(author_reputation, 4),
        }

        # Store trust score
        now = datetime.now(timezone.utc).isoformat()
        score_id = uuid4().hex[:12]
        conn.execute(
            """INSERT INTO forge_hub_trust_scores
               (id, connector_id, trust_score, breakdown, computed_at)
               VALUES (%s, %s, %s, %s, %s)""",
            (score_id, connector_id, trust_score, json.dumps(breakdown), now),
        )
        conn.commit()

        _log_audit(
            conn,
            "forge_hub_trust_computed",
            "compute_trust_score",
            {
                "connector_id": connector_id,
                "trust_score": trust_score,
            },
        )

        return {
            "connector_id": connector_id,
            "trust_score": trust_score,
            "breakdown": breakdown,
        }
    finally:
        conn.close()


def get_featured(limit=10, db_path=None):
    """Top connectors by trust score.

    Returns:
        {"featured": [...], "count": N}
    """
    conn = _get_connection(db_path)
    try:
        _ensure_tables(conn)

        # Get latest trust score per connector
        rows = _safe_query(
            conn,
            """SELECT ts.connector_id, ts.trust_score, fc.name, fc.connector_type,
                      fc.status
               FROM forge_hub_trust_scores ts
               LEFT JOIN db_forge_connectors fc ON fc.id = ts.connector_id
               WHERE ts.computed_at = (
                   SELECT MAX(t2.computed_at)
                   FROM forge_hub_trust_scores t2
                   WHERE t2.connector_id = ts.connector_id
               )
               ORDER BY ts.trust_score DESC
               LIMIT ?""",
            (limit,),
        )

        featured = []
        for row in rows:
            featured.append(
                {
                    "connector_id": row.get("connector_id", ""),
                    "name": row.get("name", ""),
                    "connector_type": row.get("connector_type", ""),
                    "status": row.get("status", ""),
                    "trust_score": row.get("trust_score", 0.0),
                }
            )

        return {"featured": featured, "count": len(featured)}
    finally:
        conn.close()


def search_connectors(query, db_path=None):
    """Full-text search on connector name and description.

    Returns:
        {"connectors": [...], "count": N}
    """
    conn = _get_connection(db_path)
    try:
        _ensure_tables(conn)

        pattern = f"%{query}%"
        rows = _safe_query(
            conn,
            """SELECT * FROM db_forge_connectors
               WHERE name LIKE ? OR description LIKE ?""",
            (pattern, pattern),
        )

        results = []
        for row in rows:
            cid = row.get("id", row.get("connector_id", ""))
            results.append(
                {
                    "id": cid,
                    "name": row.get("name", ""),
                    "connector_type": row.get("connector_type", ""),
                    "status": row.get("status", ""),
                    "description": row.get("description", ""),
                    "trust_score": _get_latest_trust_score(conn, cid),
                    "avg_rating": _compute_avg_rating(conn, cid),
                }
            )

        return {"connectors": results, "count": len(results)}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _compute_avg_rating(conn, connector_id):
    """Compute average rating for a connector."""
    rows = _safe_query(
        conn,
        "SELECT AVG(rating) as avg_r FROM forge_hub_ratings WHERE connector_id = ?",
        (connector_id,),
    )
    if rows and rows[0].get("avg_r") is not None:
        return round(rows[0]["avg_r"], 2)
    return 0.0


def _get_latest_trust_score(conn, connector_id):
    """Get most recent trust score for a connector."""
    rows = _safe_query(
        conn,
        """SELECT trust_score FROM forge_hub_trust_scores
           WHERE connector_id = ?
           ORDER BY computed_at DESC LIMIT 1""",
        (connector_id,),
    )
    if rows:
        return rows[0].get("trust_score", 0.0)
    return 0.0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cmd_browse(parser, args):
    """Handle --browse subcommand."""
    return browse_connectors(category=args.category, search=args.browse_search)


def _cmd_detail(parser, args):
    """Handle --detail subcommand."""
    if not args.connector_id:
        parser.error("--detail requires --connector-id")
    return get_connector_detail(args.connector_id)


def _cmd_rate(parser, args):
    """Handle --rate subcommand."""
    if not args.connector_id or not args.user_id or args.rating is None:
        parser.error("--rate requires --connector-id, --user-id, --rating")
    return rate_connector(
        args.connector_id,
        args.user_id,
        args.rating,
        review_text=args.review,
    )


def _cmd_trust(parser, args):
    """Handle --trust subcommand."""
    if not args.connector_id:
        parser.error("--trust requires --connector-id")
    return compute_trust_score(args.connector_id)


def _resolve_hub_command(parser, args):
    """Resolve CLI flags to a result dict. Returns None if no command matched."""
    if args.browse:
        return _cmd_browse(parser, args)
    if args.detail:
        return _cmd_detail(parser, args)
    if args.rate:
        return _cmd_rate(parser, args)
    if args.trust:
        return _cmd_trust(parser, args)
    if args.featured:
        return get_featured(limit=args.limit)
    if args.search_query:
        return search_connectors(args.search_query)
    return None


def main():
    parser = argparse.ArgumentParser(description="Connector Forge Community Hub")
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--browse", action="store_true", help="Browse connectors")
    group.add_argument("--detail", action="store_true", help="Get connector detail")
    group.add_argument("--rate", action="store_true", help="Rate a connector")
    group.add_argument("--trust", action="store_true", help="Compute trust score")
    group.add_argument("--featured", action="store_true", help="Get featured connectors")
    group.add_argument("--search", dest="search_query", type=str, help="Full-text search connectors")

    parser.add_argument("--category", type=str, help="Filter by connector type (for --browse)")
    parser.add_argument("--search-text", type=str, dest="browse_search", help="Name search (for --browse)")
    parser.add_argument("--connector-id", type=str, help="Connector ID (for --detail, --rate, --trust)")
    parser.add_argument("--user-id", type=str, help="User ID (for --rate)")
    parser.add_argument("--rating", type=int, help="Rating 1-5 (for --rate)")
    parser.add_argument("--review", type=str, default=None, help="Review text (for --rate)")
    parser.add_argument("--limit", type=int, default=10, help="Limit (for --featured)")

    args = parser.parse_args()

    result = _resolve_hub_command(parser, args)
    if result is None:
        parser.print_help()
        sys.exit(1)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        for key, val in result.items():
            print(f"  {key}: {val}")


if __name__ == "__main__":
    main()
