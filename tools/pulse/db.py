# CUI // SP-CTI
"""ICDEV™ Pulse database layer — uses ICDEV™'s storage abstraction (D-DB-20)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from tools.db.storage import get_connection


PULSE_SCHEMA = """
    CREATE TABLE IF NOT EXISTS pulse_posts (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        slug TEXT UNIQUE NOT NULL,
        status TEXT NOT NULL DEFAULT 'draft',
        topic TEXT,
        pain_points TEXT,
        tldr TEXT,
        body_markdown TEXT,
        body_html TEXT,
        seo_title TEXT,
        seo_description TEXT,
        seo_keywords TEXT,
        og_image_url TEXT,
        hero_image_path TEXT,
        video_embeds TEXT,
        source_urls TEXT,
        word_count INTEGER DEFAULT 0,
        readability_score REAL,
        plagiarism_score REAL,
        ai_detection_score REAL,
        writeguard_passed INTEGER DEFAULT 0,
        author_id TEXT,
        reviewer_id TEXT,
        review_notes TEXT,
        schema_json TEXT,
        frontmatter TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        published_at TEXT,
        archived_at TEXT
    );

    CREATE TABLE IF NOT EXISTS pulse_authors (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        email TEXT,
        bio TEXT,
        avatar_url TEXT,
        role TEXT DEFAULT 'contributor',
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS pulse_research_cache (
        id TEXT PRIMARY KEY,
        query TEXT NOT NULL,
        source TEXT NOT NULL,
        url TEXT,
        title TEXT,
        snippet TEXT,
        full_text TEXT,
        relevance_score REAL,
        sentiment TEXT,
        pain_point_category TEXT,
        fetched_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS pulse_topic_clusters (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT,
        pain_points TEXT,
        research_ids TEXT,
        priority_score REAL DEFAULT 0.5,
        used_count INTEGER DEFAULT 0,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS pulse_post_analytics (
        id TEXT PRIMARY KEY,
        post_id TEXT NOT NULL,
        page_views INTEGER DEFAULT 0,
        unique_visitors INTEGER DEFAULT 0,
        avg_read_time_seconds INTEGER DEFAULT 0,
        bounce_rate REAL DEFAULT 0.0,
        scroll_depth_avg REAL DEFAULT 0.0,
        recorded_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS pulse_post_reviews (
        id TEXT PRIMARY KEY,
        post_id TEXT NOT NULL,
        reviewer_id TEXT,
        action TEXT NOT NULL,
        notes TEXT,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS pulse_schedule_log (
        id TEXT PRIMARY KEY,
        run_type TEXT NOT NULL,
        status TEXT NOT NULL,
        topic_cluster_id TEXT,
        post_id TEXT,
        error_message TEXT,
        started_at TEXT NOT NULL,
        completed_at TEXT,
        created_at TEXT NOT NULL DEFAULT ''
    );

    CREATE TABLE IF NOT EXISTS pulse_sam_article_log (
        id TEXT PRIMARY KEY,
        sam_opportunity_id TEXT NOT NULL,
        opportunity_title TEXT,
        domain_category TEXT,
        extracted_pain_points TEXT NOT NULL,
        article_topic TEXT NOT NULL,
        post_id TEXT,
        pipeline_status TEXT NOT NULL DEFAULT 'pending',
        skip_reason TEXT,
        content_hash TEXT NOT NULL,
        created_at TEXT NOT NULL,
        classification TEXT DEFAULT 'CUI'
    );

    CREATE INDEX IF NOT EXISTS idx_pulse_posts_status ON pulse_posts(status);
    CREATE INDEX IF NOT EXISTS idx_pulse_posts_slug ON pulse_posts(slug);
    CREATE INDEX IF NOT EXISTS idx_pulse_research_query ON pulse_research_cache(query);
    CREATE INDEX IF NOT EXISTS idx_pulse_analytics_post ON pulse_post_analytics(post_id);
    CREATE INDEX IF NOT EXISTS idx_sam_article_hash ON pulse_sam_article_log(content_hash);
    CREATE INDEX IF NOT EXISTS idx_sam_article_status ON pulse_sam_article_log(pipeline_status);

    CREATE TABLE IF NOT EXISTS pulse_demand_signals (
        id TEXT PRIMARY KEY,
        pain_point_text TEXT NOT NULL,
        domain_category TEXT,
        keywords TEXT,
        frequency INTEGER DEFAULT 1,
        velocity REAL DEFAULT 0.0,
        sam_opportunity_ids TEXT,
        is_high_demand INTEGER DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'new',
        article_generated INTEGER DEFAULT 0,
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS pulse_capability_graph (
        id TEXT PRIMARY KEY,
        capability_slug TEXT NOT NULL,
        capability_name TEXT NOT NULL,
        entity_type TEXT NOT NULL,
        entity_id TEXT,
        relationship TEXT NOT NULL,
        confidence REAL DEFAULT 1.0,
        metadata TEXT,
        created_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_demand_signals_domain ON pulse_demand_signals(domain_category);
    CREATE INDEX IF NOT EXISTS idx_demand_signals_high ON pulse_demand_signals(is_high_demand);
    CREATE INDEX IF NOT EXISTS idx_cap_graph_slug ON pulse_capability_graph(capability_slug);
    CREATE INDEX IF NOT EXISTS idx_cap_graph_entity ON pulse_capability_graph(entity_type, entity_id);
"""

# Table name mapping: old standalone names -> new prefixed names
TABLE_MAP = {
    "posts": "pulse_posts",
    "authors": "pulse_authors",
    "research_cache": "pulse_research_cache",
    "topic_clusters": "pulse_topic_clusters",
    "post_analytics": "pulse_post_analytics",
    "post_reviews": "pulse_post_reviews",
    "schedule_log": "pulse_schedule_log",
    "sam_article_log": "pulse_sam_article_log",
    "demand_signals": "pulse_demand_signals",
    "capability_graph": "pulse_capability_graph",
}


def _resolve_table(table: str) -> str:
    """Map old table names to pulse-prefixed names."""
    return TABLE_MAP.get(table, table)


def init_db():
    """Initialize Pulse schema in icdev.db."""
    with get_connection() as conn:
        conn.executescript(PULSE_SCHEMA)
        # Migration: add grammar_score, tone_score, capabilities_referenced, generated video columns
        for col, coltype in [
            ("grammar_score", "REAL"),
            ("tone_score", "REAL"),
            ("capabilities_referenced", "TEXT"),
            ("generated_video_path", "TEXT"),
            ("generated_video_method", "TEXT"),
            ("generated_video_duration", "REAL"),
            ("judge_color", "TEXT"),
            ("judge_composite", "REAL"),
            ("judge_combined", "REAL"),
            ("seo_score", "REAL"),
        ]:
            try:
                conn.execute(f"ALTER TABLE pulse_posts ADD COLUMN {col} {coltype}")
            except Exception:
                pass  # Column already exists
        conn.commit()


# Tables that have a created_at column
_TABLES_WITH_CREATED_AT = {
    "pulse_posts",
    "pulse_authors",
    "pulse_topic_clusters",
    "pulse_post_reviews",
    "pulse_schedule_log",
    "pulse_sam_article_log",
    "pulse_demand_signals",
    "pulse_capability_graph",
}


def insert_row(table: str, data: dict[str, Any]) -> str:
    """Insert a row and return the id."""
    table = _resolve_table(table)
    now = datetime.now(timezone.utc).isoformat()
    if "created_at" not in data and table in _TABLES_WITH_CREATED_AT:
        data["created_at"] = now
    if "updated_at" not in data and table == "pulse_posts":
        data["updated_at"] = now

    cols = ", ".join(data.keys())
    placeholders = ", ".join(["?"] * len(data))
    values = []
    for v in data.values():
        if isinstance(v, (dict, list)):
            values.append(json.dumps(v))
        else:
            values.append(v)

    with get_connection() as conn:
        conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({placeholders})", values)  # nosec B608 -- table/column names are internal constants, not user input
        conn.commit()
    return data.get("id", "")


def update_row(table: str, row_id: str, data: dict[str, Any]):
    """Update a row by id."""
    table = _resolve_table(table)
    if table == "pulse_posts":
        data["updated_at"] = datetime.now(timezone.utc).isoformat()

    set_clause = ", ".join([f"{k} = ?" for k in data.keys()])
    values = []
    for v in data.values():
        if isinstance(v, (dict, list)):
            values.append(json.dumps(v))
        else:
            values.append(v)
    values.append(row_id)

    with get_connection() as conn:
        conn.execute(f"UPDATE {table} SET {set_clause} WHERE id = %s", values)  # nosec B608 -- table/column names are internal constants, not user input
        conn.commit()


def query_rows(table: str, where: str = "", params: tuple = (), limit: int = 100) -> list[dict]:
    """Query rows from a table."""
    table = _resolve_table(table)
    sql = f"SELECT * FROM {table}"  # nosec B608 -- table/column names are internal constants, not user input
    if where:
        sql += f" WHERE {where}"
    sql += f" LIMIT {limit}"

    with get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]


def get_row(table: str, row_id: str) -> dict | None:
    """Get a single row by id."""
    table = _resolve_table(table)
    with get_connection() as conn:
        row = conn.execute(f"SELECT * FROM {table} WHERE id = %s", (row_id,)).fetchone()  # nosec B608 -- table/column names are internal constants, not user input
        return dict(row) if row else None


def get_existing_titles() -> list[str]:
    """Return all existing post titles (for duplicate prevention)."""
    with get_connection() as conn:
        rows = conn.execute("SELECT title FROM pulse_posts ORDER BY created_at DESC LIMIT 200").fetchall()
        return [r["title"] for r in rows if r["title"]]


def get_existing_slugs() -> set[str]:
    """Return all existing post slugs (for uniqueness enforcement)."""
    with get_connection() as conn:
        rows = conn.execute("SELECT slug FROM pulse_posts").fetchall()
        return {r["slug"] for r in rows if r["slug"]}


def get_used_topics() -> list[str]:
    """Return topics already used for published/draft posts (for variety)."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT topic FROM pulse_posts "
            "WHERE status IN ('draft', 'published', 'review') "
            "ORDER BY created_at DESC LIMIT 50"
        ).fetchall()
        return [r["topic"] for r in rows if r["topic"]]
