# CUI // SP-CTI
"""Migration 215 — create api_keys table.

api_keys stores hashed API keys for tenant programmatic access.
Rows are append-only: revocation sets revoked_at, never deletes.
"""
from __future__ import annotations

from tools.db.storage import get_connection

DDL_SQLITE = """
CREATE TABLE IF NOT EXISTS api_keys (
    id           TEXT PRIMARY KEY,
    tenant_id    TEXT NOT NULL,
    name         TEXT NOT NULL,
    key_prefix   TEXT NOT NULL,
    key_hash     TEXT NOT NULL UNIQUE,
    scopes       TEXT NOT NULL DEFAULT 'read',
    last_used_at TEXT,
    expires_at   TEXT,
    revoked_at   TEXT,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_api_keys_tenant ON api_keys(tenant_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_hash   ON api_keys(key_hash);
"""

DDL_PG = """
CREATE TABLE IF NOT EXISTS api_keys (
    id           TEXT PRIMARY KEY,
    tenant_id    TEXT NOT NULL,
    name         TEXT NOT NULL,
    key_prefix   TEXT NOT NULL,
    key_hash     TEXT NOT NULL UNIQUE,
    scopes       TEXT NOT NULL DEFAULT 'read',
    last_used_at TEXT,
    expires_at   TEXT,
    revoked_at   TEXT,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_api_keys_tenant ON api_keys(tenant_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_hash   ON api_keys(key_hash);
"""


def up() -> None:
    import os

    with get_connection() as conn:
        backend = os.environ.get("ICDEV_STORAGE_BACKEND", "sqlite").lower()
        ddl = DDL_PG if backend == "postgresql" else DDL_SQLITE
        try:
            conn.executescript(ddl) if hasattr(conn, "executescript") else [
                conn.execute(stmt.strip()) for stmt in ddl.split(";") if stmt.strip()
            ]
        except Exception as exc:
            msg = str(exc).lower()
            if "already exists" in msg or "duplicate" in msg:
                pass
            else:
                raise
        conn.commit()


if __name__ == "__main__":
    up()
    print("Migration 215 (api_keys table) applied.")
