"""Migration 129 — create fa_missions table.

Canonical schema registration for the FORGE Academy missions table.
The table is defined and owned by apps/forge_academy/db.py; this migration
registers it in the tools/db/migrations scope so that gap detection
(tools/awareness/gap_detector.py orphan_db_table rule) can resolve
references from apps/forge_academy/oracle lenses, apps/innovation/reporting_engine.py,
and other tool-layer callers.
"""
# CUI // SP-CTI

SQL = """
CREATE TABLE IF NOT EXISTS fa_missions (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    slug                 TEXT NOT NULL UNIQUE,
    title                TEXT NOT NULL,
    tagline              TEXT,
    tier                 INTEGER NOT NULL DEFAULT 1,
    topic                TEXT,
    role_filter          TEXT DEFAULT 'all',
    mission_type         TEXT NOT NULL DEFAULT 'coding',
    xp_reward            INTEGER NOT NULL DEFAULT 200,
    prereq_slugs_json    TEXT DEFAULT '[]',
    order_idx            INTEGER NOT NULL DEFAULT 0,
    difficulty           TEXT DEFAULT 'intermediate',
    estimated_minutes    INTEGER DEFAULT 30,
    source_credit        TEXT,
    is_active            INTEGER NOT NULL DEFAULT 1,
    status               TEXT NOT NULL DEFAULT 'active',
    updated_at           TEXT,
    created_at           TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_fa_missions_slug
    ON fa_missions (slug);

CREATE INDEX IF NOT EXISTS idx_fa_missions_tier_active
    ON fa_missions (tier, is_active);
"""


def up(conn):
    for stmt in SQL.strip().split(";"):
        s = stmt.strip()
        if s:
            conn.execute(s)
    conn.commit()
