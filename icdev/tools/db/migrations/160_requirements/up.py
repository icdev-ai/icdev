# CUI // SP-CTI
"""Migration 160 — Create requirements table.

General-purpose requirements table used by the JISE portal feed
(tools/dashboard/api/jise.py GET /requirements). Stores requirement
titles, descriptions, status, priority, and classification.
"""
from tools.db.storage import get_connection, is_pg


def up(conn=None) -> None:
    conn = get_connection()

    if is_pg():
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS requirements (
                id             TEXT PRIMARY KEY,
                title          TEXT NOT NULL DEFAULT '',
                description    TEXT NOT NULL DEFAULT '',
                status         TEXT NOT NULL DEFAULT 'open'
                    CHECK(status IN ('open', 'closed', 'in_progress', 'draft', 'review', 'deferred')),
                priority       TEXT NOT NULL DEFAULT 'medium'
                    CHECK(priority IN ('critical', 'high', 'medium', 'low')),
                classification TEXT NOT NULL DEFAULT 'CUI',
                created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    else:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS requirements (
                id             TEXT PRIMARY KEY,
                title          TEXT NOT NULL DEFAULT '',
                description    TEXT NOT NULL DEFAULT '',
                status         TEXT NOT NULL DEFAULT 'open'
                    CHECK(status IN ('open', 'closed', 'in_progress', 'draft', 'review', 'deferred')),
                priority       TEXT NOT NULL DEFAULT 'medium'
                    CHECK(priority IN ('critical', 'high', 'medium', 'low')),
                classification TEXT NOT NULL DEFAULT 'CUI',
                created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                updated_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
            )
            """
        )

    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_requirements_status   ON requirements (status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_requirements_priority ON requirements (priority)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_requirements_created  ON requirements (created_at)"
    )

    conn.commit()
    conn.close()


if __name__ == "__main__":
    up()
    print("Migration 160 applied.")
