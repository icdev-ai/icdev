# CUI // SP-CTI
"""Migration 212 — data_residency_zones + tenant_zone_assignments tables.

tenant_zone_assignments is append-only (NIST AU-9): zone assignments are
immutable audit records. Never UPDATE or DELETE rows — insert a new
assignment to change a tenant's zone.

data_residency_zones is mutable (admin-managed zone registry).
"""
from __future__ import annotations

from tools.db.storage import get_connection

DDL = """
CREATE TABLE IF NOT EXISTS data_residency_zones (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    region      TEXT NOT NULL,
    pg_dsn_env  TEXT NOT NULL,
    description TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tenant_zone_assignments (
    tenant_id   TEXT PRIMARY KEY,
    zone_id     TEXT NOT NULL REFERENCES data_residency_zones(id),
    assigned_at TEXT NOT NULL DEFAULT (datetime('now')),
    assigned_by TEXT
);

CREATE INDEX IF NOT EXISTS idx_tenant_zone_assignments_zone
    ON tenant_zone_assignments(zone_id);
"""

_SEED = """
INSERT OR IGNORE INTO data_residency_zones (id, name, region, pg_dsn_env, description)
VALUES (
    'us-default',
    'US Default',
    'us-east-1',
    'ICDEV_DATABASE_URL',
    'Default US data residency zone — routes to the primary ICDEV database'
);
"""


def up() -> None:
    with get_connection() as conn:
        for stmt in DDL.strip().split(";"):
            s = stmt.strip()
            if s:
                conn.execute(s)
        for stmt in _SEED.strip().split(";"):
            s = stmt.strip()
            if s:
                conn.execute(s)
        conn.commit()


if __name__ == "__main__":
    up()
    print("Migration 212 (data_residency_zones + tenant_zone_assignments) applied.")
