# CUI // SP-CTI
"""Migration 215 — create user_preferences table.

Stores per-user mutable settings, initially populated by the onboarding wizard.
onboarding_state is a JSON blob with the wizard progress fields.
"""
from __future__ import annotations

from tools.db.storage import get_connection

DDL_SQLITE = """
CREATE TABLE IF NOT EXISTS user_preferences (
    user_id          TEXT PRIMARY KEY,
    tenant_id        TEXT NOT NULL DEFAULT 'default',
    onboarding_state TEXT NOT NULL DEFAULT '{}',
    updated_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
"""

DDL_PG = """
CREATE TABLE IF NOT EXISTS user_preferences (
    user_id          TEXT PRIMARY KEY,
    tenant_id        TEXT NOT NULL DEFAULT 'default',
    onboarding_state TEXT NOT NULL DEFAULT '{}',
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""


def up() -> None:
    import os

    with get_connection() as conn:
        backend = os.environ.get("ICDEV_STORAGE_BACKEND", "sqlite").lower()
        ddl = DDL_PG if backend == "postgresql" else DDL_SQLITE
        try:
            conn.execute(ddl.strip())
        except Exception as exc:
            msg = str(exc).lower()
            if "already exists" in msg:
                pass  # idempotent
            else:
                raise
        conn.commit()


if __name__ == "__main__":
    up()
    print("Migration 215 (user_preferences table) applied.")
