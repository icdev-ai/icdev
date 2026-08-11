#!/usr/bin/env python3
# CUI // SP-CTI
"""Rollback for 20260809213046_agov_unattended_sessions (agov-inbox-04).

Drops ``agent_unattended_sessions``. Rolling back is fail-SAFE by construction:
with the table gone every session reads as attended, so asks go back to the
console approver, which denies on EOF. A rollback can therefore only ever make
the gate stricter, never looser.

``agent_cron_jobs.unattended`` is dropped only on PostgreSQL. SQLite's
``DROP COLUMN`` is a table rebuild the applier cannot do safely mid-chain, and a
leftover integer column is inert — ``cron.py`` reads it defensively and treats
an absent or NULL value as ``0``.
"""

MIGRATION_ID = "20260809213046"
MIGRATION_NAME = "agov_unattended_sessions"

TABLE = "agent_unattended_sessions"


def _is_pg(conn) -> bool:
    return getattr(conn, "_backend", "sqlite") == "postgresql"


def down(conn) -> dict:
    actions = []
    conn.execute(f"DROP TABLE IF EXISTS {TABLE}")
    actions.append(f"dropped_{TABLE}")
    if _is_pg(conn):
        try:
            conn.execute(
                "ALTER TABLE agent_cron_jobs DROP COLUMN IF EXISTS unattended"
            )
            actions.append("dropped_agent_cron_jobs_unattended")
        except Exception as exc:  # noqa: BLE001 — the table may be absent
            actions.append(f"cron_column_skipped: {exc}")
    else:
        actions.append("cron_column_left (SQLite DROP COLUMN is a table rebuild)")
    conn.commit()
    return {"status": "rolled_back", "actions": actions}
