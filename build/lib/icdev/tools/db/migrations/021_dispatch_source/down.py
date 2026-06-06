#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 021 rollback."""


MIGRATION_ID = "021"
MIGRATION_NAME = "dispatch_source"


def _is_pg(conn) -> bool:
    return getattr(conn, "_backend", "sqlite") == "postgresql"


def down(conn) -> dict:
    actions = []

    conn.execute("DROP INDEX IF EXISTS idx_kanban_tasks_dispatch_source")
    conn.execute("DROP INDEX IF EXISTS idx_kanban_verifications_dispatch_source")
    actions.append("dropped_indexes")

    if _is_pg(conn):
        for table in ("kanban_tasks", "kanban_verifications"):
            try:
                conn.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS dispatch_source")
                actions.append(f"dropped_from_{table}")
            except Exception as exc:
                actions.append(f"{table}_drop_skipped: {exc}")

    conn.commit()
    return {"status": "reverted", "actions": actions}
