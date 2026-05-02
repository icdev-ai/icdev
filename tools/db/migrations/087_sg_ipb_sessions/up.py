#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 087 — IPB (Intelligence Preparation of the Battlespace) tables.

sg_ipb_sessions  — one IPB session per theater/scenario
sg_ipb_steps     — four steps per session (env, effects, threat eval, threat COAs)
"""
from tools.db.storage import get_connection


def up(conn=None) -> None:
    conn = get_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sg_ipb_sessions (
                id            TEXT PRIMARY KEY,
                theater       TEXT NOT NULL,
                scenario      TEXT,
                status        TEXT DEFAULT 'active'
                                  CHECK(status IN ('active','complete','archived')),
                created_by    TEXT DEFAULT 'analyst',
                created_at    TEXT NOT NULL,
                updated_at    TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sg_ipb_steps (
                id          TEXT PRIMARY KEY,
                session_id  TEXT NOT NULL,
                step_num    INTEGER NOT NULL CHECK(step_num BETWEEN 1 AND 4),
                step_name   TEXT NOT NULL,
                content     TEXT,
                status      TEXT DEFAULT 'pending'
                                CHECK(status IN ('pending','in_progress','complete')),
                template_output TEXT,
                created_at  TEXT NOT NULL,
                updated_at  TEXT NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sg_ipb_sessions_theater "
            "ON sg_ipb_sessions(theater)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sg_ipb_steps_session "
            "ON sg_ipb_steps(session_id)"
        )
        conn.commit()
        print("Migration 087 up: sg_ipb_sessions + sg_ipb_steps created.")
    finally:
        conn.close()


if __name__ == "__main__":
    up()
