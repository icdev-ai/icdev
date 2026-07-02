# CUI // SP-CTI
"""Migration 216 — create webhook_endpoints and webhook_deliveries tables.

webhook_endpoints: tenant-registered HTTP callback URLs per event type.
webhook_deliveries: per-delivery audit rows; append-only after status=delivered/failed.
"""
from __future__ import annotations

from tools.db.storage import get_connection

DDL_SQLITE = """
CREATE TABLE IF NOT EXISTS webhook_endpoints (
    id          TEXT PRIMARY KEY,
    tenant_id   TEXT NOT NULL,
    url         TEXT NOT NULL,
    event_types TEXT NOT NULL,
    secret      TEXT NOT NULL,
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wh_ep_tenant ON webhook_endpoints(tenant_id);
CREATE TABLE IF NOT EXISTS webhook_deliveries (
    id                TEXT PRIMARY KEY,
    endpoint_id       TEXT NOT NULL,
    event_type        TEXT NOT NULL,
    payload           TEXT,
    status            TEXT NOT NULL DEFAULT 'pending'
                          CHECK (status IN ('pending', 'delivered', 'failed')),
    attempts          INTEGER NOT NULL DEFAULT 0,
    last_attempted_at TEXT,
    delivered_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_wh_del_ep    ON webhook_deliveries(endpoint_id);
CREATE INDEX IF NOT EXISTS idx_wh_del_status ON webhook_deliveries(status);
"""

DDL_PG = """
CREATE TABLE IF NOT EXISTS webhook_endpoints (
    id          TEXT PRIMARY KEY,
    tenant_id   TEXT NOT NULL,
    url         TEXT NOT NULL,
    event_types TEXT NOT NULL,
    secret      TEXT NOT NULL,
    enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_wh_ep_tenant ON webhook_endpoints(tenant_id);
CREATE TABLE IF NOT EXISTS webhook_deliveries (
    id                TEXT PRIMARY KEY,
    endpoint_id       TEXT NOT NULL,
    event_type        TEXT NOT NULL,
    payload           TEXT,
    status            TEXT NOT NULL DEFAULT 'pending'
                          CHECK (status IN ('pending', 'delivered', 'failed')),
    attempts          INTEGER NOT NULL DEFAULT 0,
    last_attempted_at TEXT,
    delivered_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_wh_del_ep     ON webhook_deliveries(endpoint_id);
CREATE INDEX IF NOT EXISTS idx_wh_del_status ON webhook_deliveries(status);
"""


def up() -> None:
    import os

    with get_connection() as conn:
        backend = os.environ.get("ICDEV_STORAGE_BACKEND", "sqlite").lower()
        ddl = DDL_PG if backend == "postgresql" else DDL_SQLITE
        try:
            if hasattr(conn, "executescript"):
                conn.executescript(ddl)
            else:
                for stmt in ddl.split(";"):
                    stmt = stmt.strip()
                    if stmt:
                        conn.execute(stmt)
        except Exception as exc:
            msg = str(exc).lower()
            if "already exists" not in msg and "duplicate" not in msg:
                raise
        conn.commit()


if __name__ == "__main__":
    up()
    print("Migration 216 (webhook tables) applied.")
