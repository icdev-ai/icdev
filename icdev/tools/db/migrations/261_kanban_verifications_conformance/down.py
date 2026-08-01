#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 261 rollback: drop the conformance/pytest_ran columns (PG only)."""

MIGRATION_ID = "261"
MIGRATION_NAME = "kanban_verifications_conformance"

_COLUMNS = ["review_passed", "review_findings", "pytest_ran"]


def _is_pg(conn) -> bool:
    return getattr(conn, "_backend", "sqlite") == "postgresql"


def down(conn) -> dict:
    actions = []
    if not _is_pg(conn):
        return {"status": "skipped", "reason": "SQLite column drop needs table rebuild"}
    for col in _COLUMNS:
        try:
            conn.execute(f"ALTER TABLE kanban_verifications DROP COLUMN IF EXISTS {col}")
            actions.append(f"dropped_{col}")
        except Exception as exc:
            actions.append(f"{col}_drop_skipped: {exc}")
    conn.commit()
    return {"status": "reverted", "actions": actions}
