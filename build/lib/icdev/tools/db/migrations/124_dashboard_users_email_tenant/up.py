#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 124 — dashboard_users composite email+tenant unique index.

Adds a composite unique index on dashboard_users(email, COALESCE(tenant_id, ''))
so the same email address can be registered under different tenants without
violating uniqueness. The COALESCE ensures NULL tenant rows compare consistently.

The original single-column email uniqueness (if present as an index or column
constraint) is left in place — this is additive only.

Tables modified (icdev.db):
  dashboard_users — CREATE UNIQUE INDEX idx_dashboard_users_email_tenant

Idempotent: IF NOT EXISTS guard.
"""
from __future__ import annotations

MIGRATION_ID = "124"
MIGRATION_NAME = "dashboard_users_email_tenant"
DESCRIPTION = (
    "Composite unique index on dashboard_users(email, tenant_id) for "
    "multi-tenant email reuse without collision."
)


def up(conn=None) -> None:
    from tools.db.storage import get_connection

    c = conn or get_connection()
    try:
        c.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_dashboard_users_email_tenant "
            "ON dashboard_users(email, COALESCE(tenant_id, ''))"
        )
        c.commit()
        print(f"[migration-{MIGRATION_ID}] {MIGRATION_NAME} applied OK")
    finally:
        if conn is None:
            c.close()


def run(conn=None) -> None:
    up(conn)


if __name__ == "__main__":
    up()
