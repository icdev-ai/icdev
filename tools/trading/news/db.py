# CUI // SP-CTI
"""FathomDesk News DB layer — DDL and CRUD helpers.

Phase A3 of plan ~/.claude/plans/recursive-noodling-meteor.md.

Tables (all append-only, NIST AU):
  ad_news_items         — raw ingested news items
  ad_news_scenario_links — item ↔ scenario run associations
  ad_news_clusters      — multi-source signal clusters

All tables use get_connection() — NEVER sqlite3.connect() directly.
storage.translate_sql() handles PostgreSQL translation automatically.

CLI:
  python -m tools.trading.news.db --migrate   Apply DDL
  python -m tools.trading.news.db --json      Print status as JSON
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT))

from tools.db.storage import get_connection, is_pg, sql_now, sql_placeholder

# Default DB path — SQLite uses this; PostgreSQL ignores it (uses env vars).
FATHOMDESK_DB = _ROOT / "data" / "fathomdesk.db"

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

_DDL_NEWS_ITEMS = """
CREATE TABLE IF NOT EXISTS ad_news_items (
    id                  TEXT PRIMARY KEY,
    source              TEXT NOT NULL,
    feed_url            TEXT,
    title               TEXT,
    link                TEXT,
    published_at        TEXT,
    ingested_at         TEXT NOT NULL,
    summary             TEXT,
    category            TEXT,
    impact_level        TEXT,
    net_direction       TEXT,
    mentioned_tickers   TEXT NOT NULL DEFAULT '[]',
    classifier_version  TEXT
)
"""

_DDL_IDX_PUBLISHED = """
CREATE INDEX IF NOT EXISTS idx_news_published
    ON ad_news_items (published_at)
"""

_DDL_IDX_CATEGORY = """
CREATE INDEX IF NOT EXISTS idx_news_category
    ON ad_news_items (category)
"""

_DDL_SCENARIO_LINKS = """
CREATE TABLE IF NOT EXISTS ad_news_scenario_links (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    news_id         TEXT NOT NULL,
    scenario_run_id TEXT,
    scenario_key    TEXT,
    match_confidence REAL,
    match_reason    TEXT,
    created_at      TEXT NOT NULL
)
"""

_DDL_IDX_SCENARIO_NEWS = """
CREATE INDEX IF NOT EXISTS idx_news_scenario_news
    ON ad_news_scenario_links (news_id)
"""

_DDL_CLUSTERS = """
CREATE TABLE IF NOT EXISTS ad_news_clusters (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario_key     TEXT,
    category         TEXT,
    time_window      TEXT,
    item_ids         TEXT NOT NULL DEFAULT '[]',
    cumulative_score REAL,
    first_seen       TEXT,
    last_seen        TEXT,
    status           TEXT CHECK(status IN ('emerging', 'cluster', 'regime'))
)
"""

_ALL_DDL = [
    _DDL_NEWS_ITEMS,
    _DDL_IDX_PUBLISHED,
    _DDL_IDX_CATEGORY,
    _DDL_SCENARIO_LINKS,
    _DDL_IDX_SCENARIO_NEWS,
    _DDL_CLUSTERS,
]


def get_db(db_path: str | Path | None = None):
    """Return a StorageConnection for the FathomDesk news DB."""
    path = str(db_path) if db_path else str(FATHOMDESK_DB)
    return get_connection(path)


def ensure_tables(db_path: str | Path | None = None) -> None:
    """Apply DDL — idempotent (CREATE IF NOT EXISTS)."""
    conn = get_db(db_path)
    try:
        for stmt in _ALL_DDL:
            conn.execute(stmt)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CRUD helpers
# ---------------------------------------------------------------------------

def insert_news_item(
    item: dict[str, Any],
    db_path: str | Path | None = None,
) -> bool:
    """Insert a news item row.  Returns True if inserted, False if duplicate."""
    conn = get_db(db_path)
    ph = sql_placeholder(conn)
    try:
        existing = conn.execute(
            f"SELECT id FROM ad_news_items WHERE id = {ph}", (item["id"],)  # nosec B608 -- ph is placeholder
        ).fetchone()
        if existing:
            return False

        conn.execute(
            f"""INSERT INTO ad_news_items
                (id, source, feed_url, title, link, published_at, ingested_at,
                 summary, category, impact_level, net_direction,
                 mentioned_tickers, classifier_version)
                VALUES
                ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})""",  # nosec B608 -- ph is placeholder
            (
                item["id"],
                item.get("source", ""),
                item.get("feed_url"),
                item.get("title"),
                item.get("link"),
                item.get("published_at"),
                item["ingested_at"],
                item.get("summary"),
                item.get("category"),
                item.get("impact_level"),
                item.get("net_direction"),
                json.dumps(item.get("mentioned_tickers", [])),
                item.get("classifier_version"),
            ),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def list_news(
    category: str | None = None,
    limit: int = 100,
    db_path: str | Path | None = None,
) -> list[dict]:
    """Return news items, optionally filtered by category."""
    conn = get_db(db_path)
    ph = sql_placeholder(conn)
    try:
        # Sort: HIGH impact first, then by most recent
        order = ("ORDER BY CASE impact_level "
                 "WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, "
                 "published_at DESC")
        if category:
            rows = conn.execute(
                f"SELECT * FROM ad_news_items WHERE category = {ph}"  # nosec B608 -- ph is placeholder
                f" {order} LIMIT {ph}",
                (category, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                f"SELECT * FROM ad_news_items {order} LIMIT {ph}",  # nosec B608 -- ph is placeholder
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_news_by_id(
    item_id: str,
    db_path: str | Path | None = None,
) -> dict | None:
    """Fetch a single news item by id."""
    conn = get_db(db_path)
    ph = sql_placeholder(conn)
    try:
        row = conn.execute(
            f"SELECT * FROM ad_news_items WHERE id = {ph}", (item_id,)  # nosec B608 -- ph is placeholder
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def insert_scenario_link(
    news_id: str,
    scenario_run_id: str | None,
    scenario_key: str | None,
    match_confidence: float,
    match_reason: str | None,
    db_path: str | Path | None = None,
) -> int:
    """Append a scenario link row; returns the new row id."""
    conn = get_db(db_path)
    ph = sql_placeholder(conn)
    now = sql_now(conn)
    try:
        import uuid
        link_id = uuid.uuid4().hex[:16]
        conn.execute(
            f"""INSERT INTO ad_news_scenario_links
                (id, news_id, scenario_run_id, scenario_key,
                 match_confidence, match_reason, created_at)
                VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{now})""",  # nosec B608 -- ph is placeholder
            (link_id, news_id, scenario_run_id, scenario_key, match_confidence, match_reason),
        )
        conn.commit()
        return link_id
    finally:
        conn.close()


def upsert_cluster(
    scenario_key: str | None,
    category: str | None,
    time_window,
    item_ids: list[str],
    cumulative_score: float,
    first_seen: str,
    last_seen: str,
    status: str,
    db_path: str | Path | None = None,
) -> str:
    """Insert a cluster row or update an existing one matching (category, scenario_key).

    Prevents duplicate accumulation when the aggregator runs on a cadence without
    a successful purge. Returns the cluster id (existing or new).
    """
    conn = get_db(db_path)
    ph = sql_placeholder(conn)
    try:
        if scenario_key is None:
            existing = conn.execute(
                f"SELECT id FROM ad_news_clusters "
                f"WHERE category = {ph} AND scenario_key IS NULL LIMIT 1",  # nosec B608
                (category,),
            ).fetchone()
        else:
            existing = conn.execute(
                f"SELECT id FROM ad_news_clusters "
                f"WHERE category = {ph} AND scenario_key = {ph} LIMIT 1",  # nosec B608
                (category, scenario_key),
            ).fetchone()

        if existing:
            cluster_id = existing[0] if not hasattr(existing, "keys") else existing["id"]
            conn.execute(
                f"UPDATE ad_news_clusters SET "
                f"time_window = {ph}, item_ids = {ph}, cumulative_score = {ph}, "
                f"first_seen = {ph}, last_seen = {ph}, status = {ph} "
                f"WHERE id = {ph}",  # nosec B608
                (
                    str(time_window),
                    json.dumps(item_ids),
                    cumulative_score,
                    first_seen,
                    last_seen,
                    status,
                    cluster_id,
                ),
            )
            conn.commit()
            return cluster_id

        import uuid
        cluster_id = uuid.uuid4().hex[:16]
        conn.execute(
            f"""INSERT INTO ad_news_clusters
                (id, scenario_key, category, time_window, item_ids,
                 cumulative_score, first_seen, last_seen, status)
                VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})""",  # nosec B608
            (
                cluster_id,
                scenario_key,
                category,
                str(time_window),
                json.dumps(item_ids),
                cumulative_score,
                first_seen,
                last_seen,
                status,
            ),
        )
        conn.commit()
        return cluster_id
    finally:
        conn.close()


def insert_cluster(
    scenario_key: str | None,
    category: str | None,
    time_window,
    item_ids: list[str],
    cumulative_score: float,
    first_seen: str,
    last_seen: str,
    status: str,
    db_path: str | Path | None = None,
) -> int:
    """Append a cluster row; returns the new row id."""
    conn = get_db(db_path)
    ph = sql_placeholder(conn)
    try:
        import uuid
        cluster_id = uuid.uuid4().hex[:16]
        conn.execute(
            f"""INSERT INTO ad_news_clusters
                (id, scenario_key, category, time_window, item_ids,
                 cumulative_score, first_seen, last_seen, status)
                VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})""",  # nosec B608 -- ph is placeholder
            (
                cluster_id,
                scenario_key,
                category,
                str(time_window),
                json.dumps(item_ids),
                cumulative_score,
                first_seen,
                last_seen,
                status,
            ),
        )
        conn.commit()
        return cluster_id
    finally:
        conn.close()


def list_active_clusters(
    status: str | None = None,
    db_path: str | Path | None = None,
) -> list[dict]:
    """Return clusters, optionally filtered by status."""
    conn = get_db(db_path)
    ph = sql_placeholder(conn)
    try:
        if status:
            rows = conn.execute(
                f"SELECT * FROM ad_news_clusters WHERE status = {ph}"  # nosec B608 -- ph is placeholder
                " ORDER BY last_seen DESC",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM ad_news_clusters ORDER BY last_seen DESC"
            ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _main() -> None:
    parser = argparse.ArgumentParser(
        description="FathomDesk News DB — DDL and status"
    )
    parser.add_argument("--migrate", action="store_true", help="Apply DDL")
    parser.add_argument("--json", action="store_true", help="Output JSON")
    parser.add_argument("--db", help="Override DB path (SQLite only)")
    args = parser.parse_args()

    if args.migrate:
        ensure_tables(args.db)
        db_path = args.db or str(FATHOMDESK_DB)
        conn = get_db(args.db)
        try:
            if is_pg(conn):
                rows = conn.execute(
                    "SELECT table_name FROM information_schema.tables"
                    " WHERE table_schema = 'public' AND table_name LIKE 'ad_news%'"
                ).fetchall()
                tables = [r[0] for r in rows]
            else:
                # pg-portability: sqlite-only path — SQLite branch of an explicit
                # is_pg(conn) guard (the PG branch above uses information_schema).
                rows = conn.execute(
                    "SELECT name FROM sqlite_master"
                    " WHERE type='table' AND name LIKE 'ad_news%'"
                ).fetchall()
                tables = [r[0] for r in rows]
        finally:
            conn.close()
        result = {"status": "ok", "tables": tables, "db": db_path}
        if args.json:
            print(json.dumps(result))
        else:
            print(f"Migration complete. Tables: {tables}")
        return

    # Default: print status
    db_path = args.db or str(FATHOMDESK_DB)
    try:
        conn = get_db(args.db)
        count = conn.execute("SELECT COUNT(*) FROM ad_news_items").fetchone()[0]
        conn.close()
        status = {"status": "ok", "ad_news_items_count": count, "db": db_path}
    except Exception as exc:
        status = {"status": "error", "error": str(exc), "db": db_path}
    if args.json:
        print(json.dumps(status))
    else:
        print(status)


if __name__ == "__main__":
    _main()
