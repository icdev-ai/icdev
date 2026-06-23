-- Migration 212: Data residency zones + per-tenant zone assignments (ecr-dres-01)
-- Creates the zone registry and tenant assignment tables used by zone_router.py.

CREATE TABLE IF NOT EXISTS data_residency_zones (
    zone_id     TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    pg_dsn_env  TEXT NOT NULL,
    region      TEXT,
    description TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tenant_zone_assignments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id   TEXT NOT NULL UNIQUE,
    zone_id     TEXT NOT NULL REFERENCES data_residency_zones(zone_id),
    assigned_at TEXT NOT NULL DEFAULT (datetime('now')),
    assigned_by TEXT
);

-- Seed the default US zone (maps to the primary ICDEV_DATABASE_URL)
INSERT OR IGNORE INTO data_residency_zones (zone_id, name, pg_dsn_env, region, description)
VALUES ('us-default', 'US Default', 'ICDEV_DATABASE_URL', 'us-east-1',
        'Default US data zone — uses primary ICDEV_DATABASE_URL');
