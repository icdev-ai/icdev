#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 247 rollback."""

MIGRATION_ID = "247"
MIGRATION_NAME = "dashboard_users_role_check"


def _is_pg(conn) -> bool:
    return getattr(conn, "_backend", "sqlite") == "postgresql"


def down(conn) -> dict:
    if not _is_pg(conn):
        return {"status": "skipped", "reason": "SQLite constraint reverts via CREATE TABLE only"}

    actions = []
    try:
        conn.execute(
            "ALTER TABLE dashboard_users DROP CONSTRAINT IF EXISTS dashboard_users_role_check"
        )
        conn.execute(
            "ALTER TABLE dashboard_users ADD CONSTRAINT dashboard_users_role_check "
            "CHECK (role = ANY (ARRAY["
            "'admin'::text, 'pm'::text, 'developer'::text, "
            "'isso'::text, 'co'::text, 'cor'::text]))"
        )
        actions.append("role_check_reverted")
    except Exception as exc:
        actions.append(f"role_check_revert_skipped: {exc}")

    conn.commit()
    return {"status": "reverted", "actions": actions}
