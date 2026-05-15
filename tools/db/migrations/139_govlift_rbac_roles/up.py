# CUI // SP-CTI
"""Migration 139 — add GovLift RBAC roles to dashboard_users CHECK constraint.

Adds five new roles required by the GovLift RBAC feature (AC-2, AC-3, AC-6):
  migration_engineer, component_admin, auditor, ciso

'isso' already existed; 'admin' / 'pm' / 'developer' / 'co' / 'cor' retained.

SQLite does not support ALTER TABLE … MODIFY COLUMN, so we recreate the table
with the expanded CHECK constraint, copy existing data, and rename.
"""

SQL_UP = """
PRAGMA foreign_keys = OFF;

CREATE TABLE IF NOT EXISTS dashboard_users_new (
    id           TEXT PRIMARY KEY,
    email        TEXT UNIQUE NOT NULL,
    display_name TEXT NOT NULL,
    role         TEXT NOT NULL DEFAULT 'developer'
        CHECK(role IN (
            'admin', 'pm', 'developer', 'isso', 'co', 'cor',
            'migration_engineer', 'component_admin', 'auditor', 'ciso'
        )),
    status       TEXT NOT NULL DEFAULT 'active'
        CHECK(status IN ('active', 'suspended')),
    created_by   TEXT,
    tenant_id    TEXT,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

INSERT OR IGNORE INTO dashboard_users_new
    SELECT id, email, display_name, role, status, created_by, tenant_id, created_at, updated_at
    FROM dashboard_users;

DROP TABLE dashboard_users;

ALTER TABLE dashboard_users_new RENAME TO dashboard_users;

CREATE INDEX IF NOT EXISTS idx_dashboard_users_tenant
    ON dashboard_users (tenant_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_dashboard_users_email_tenant
    ON dashboard_users (email, COALESCE(tenant_id, ''));

PRAGMA foreign_keys = ON;
"""


def up(conn):
    for stmt in SQL_UP.strip().split(";"):
        s = stmt.strip()
        if s and not s.startswith("--"):
            conn.execute(s)
    conn.commit()
