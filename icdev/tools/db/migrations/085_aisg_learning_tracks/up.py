#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 085: aisg_learning_tracks + aisg_track_progress tables.

Creates two tables for the AISG learning track feature:
  - aisg_learning_tracks  — catalogue of built-in and custom tracks
  - aisg_track_progress   — per-user progress against a track

Also seeds 3 built-in tracks (consumer / configurator / builder) on
first apply; seeding is idempotent (INSERT OR IGNORE / ON CONFLICT DO NOTHING).
"""

MIGRATION_ID = "085"
MIGRATION_NAME = "aisg_learning_tracks"
DESCRIPTION = "Add aisg_learning_tracks and aisg_track_progress tables; seed 3 built-in tracks"

_TRACKS_DDL_SQLITE = """
CREATE TABLE IF NOT EXISTS aisg_learning_tracks (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    level       TEXT NOT NULL CHECK (level IN ('consumer', 'configurator', 'builder')),
    description TEXT,
    task_count  INTEGER DEFAULT 0,
    created_at  TEXT DEFAULT (datetime('now'))
)
"""

_TRACKS_DDL_PG = """
CREATE TABLE IF NOT EXISTS aisg_learning_tracks (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    level       TEXT NOT NULL CHECK (level IN ('consumer', 'configurator', 'builder')),
    description TEXT,
    task_count  INTEGER DEFAULT 0,
    created_at  TIMESTAMPTZ DEFAULT NOW()
)
"""

_PROGRESS_DDL_SQLITE = """
CREATE TABLE IF NOT EXISTS aisg_track_progress (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_email       TEXT NOT NULL,
    track_id         TEXT NOT NULL REFERENCES aisg_learning_tracks(id),
    tasks_completed  INTEGER DEFAULT 0,
    activated_at     TEXT,
    completed_at     TEXT,
    created_at       TEXT DEFAULT (datetime('now'))
)
"""

_PROGRESS_DDL_PG = """
CREATE TABLE IF NOT EXISTS aisg_track_progress (
    id               SERIAL PRIMARY KEY,
    user_email       TEXT NOT NULL,
    track_id         TEXT NOT NULL REFERENCES aisg_learning_tracks(id),
    tasks_completed  INTEGER DEFAULT 0,
    activated_at     TIMESTAMPTZ,
    completed_at     TIMESTAMPTZ,
    created_at       TIMESTAMPTZ DEFAULT NOW()
)
"""

_PROGRESS_INDEX = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_aisg_track_progress_user_track "
    "ON aisg_track_progress (user_email, track_id)"
)

_BUILT_IN_TRACKS = [
    (
        "aisg-track-consumer",
        "AI Consumer",
        "consumer",
        "Use AI tools effectively in your daily workflow — prompting, "
        "reviewing outputs, and integrating AI assistance into existing processes.",
        5,
    ),
    (
        "aisg-track-configurator",
        "AI Configurator",
        "configurator",
        "Configure and customise ICDEV™ AI capabilities for your team — "
        "wizard setup, args tuning, LLM routing, and compliance level selection.",
        8,
    ),
    (
        "aisg-track-builder",
        "AI Builder",
        "builder",
        "Build production-grade AI-powered applications with FORGE — "
        "tools authoring, TDD/BDD pipelines, NIST 800-53 compliance, and multi-agent orchestration.",
        12,
    ),
]


def _is_pg(conn) -> bool:
    return getattr(conn, "_backend", "sqlite") == "postgresql"


def _table_exists(conn, table: str) -> bool:
    if _is_pg(conn):
        row = conn.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = ?",
            (table,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
    return row is not None


def up(conn=None) -> dict:
    """Apply migration: create tables, index, and seed 3 built-in tracks."""
    import sys
    from pathlib import Path

    BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent.parent
    if str(BASE_DIR) not in sys.path:
        sys.path.insert(0, str(BASE_DIR))

    if conn is None:
        from tools.db.storage import get_connection
        conn = get_connection()

    actions = []
    pg = _is_pg(conn)

    # 1. Create aisg_learning_tracks
    conn.execute(_TRACKS_DDL_PG if pg else _TRACKS_DDL_SQLITE)
    actions.append("aisg_learning_tracks_table")

    # 2. Create aisg_track_progress
    conn.execute(_PROGRESS_DDL_PG if pg else _PROGRESS_DDL_SQLITE)
    actions.append("aisg_track_progress_table")

    # 3. Unique index on (user_email, track_id)
    conn.execute(_PROGRESS_INDEX)
    actions.append("idx_aisg_track_progress_user_track")

    # 4. Seed built-in tracks (idempotent)
    seeded = 0
    for track_id, name, level, description, task_count in _BUILT_IN_TRACKS:
        if pg:
            conn.execute(
                "INSERT INTO aisg_learning_tracks (id, name, level, description, task_count) "
                "VALUES (?, ?, ?, ?, ?) ON CONFLICT (id) DO NOTHING",
                (track_id, name, level, description, task_count),
            )
        else:
            conn.execute(
                "INSERT OR IGNORE INTO aisg_learning_tracks "
                "(id, name, level, description, task_count) VALUES (?, ?, ?, ?, ?)",
                (track_id, name, level, description, task_count),
            )
        seeded += 1

    actions.append(f"seeded_{seeded}_built_in_tracks")

    conn.commit()
    return {"status": "applied", "actions": actions}


if __name__ == "__main__":
    result = up()
    import json
    print(json.dumps(result, indent=2))
