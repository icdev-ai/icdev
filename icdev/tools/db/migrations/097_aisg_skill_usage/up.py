#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 097 — aisg_skill_usage table for AISG skill engagement tracking."""
from tools.db.storage import get_connection


def up(conn=None) -> None:
    conn = get_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS aisg_skill_usage (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                user_email     TEXT NOT NULL,
                skill_name     TEXT NOT NULL,
                first_used_at  TEXT NOT NULL,
                last_used_at   TEXT NOT NULL,
                use_count      INTEGER DEFAULT 1,
                UNIQUE(user_email, skill_name)
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_aisg_skill_usage_user "
            "ON aisg_skill_usage(user_email)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_aisg_skill_usage_skill "
            "ON aisg_skill_usage(skill_name)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_aisg_skill_usage_last_used "
            "ON aisg_skill_usage(last_used_at DESC)"
        )
        conn.commit()
        print("Migration 097 up: aisg_skill_usage created.")
    finally:
        conn.close()


if __name__ == "__main__":
    up()
