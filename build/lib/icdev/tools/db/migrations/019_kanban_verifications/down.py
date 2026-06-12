#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 019 rollback: drop kanban_verifications + revert status constraint."""


MIGRATION_ID = "019"
MIGRATION_NAME = "kanban_verifications"


def _is_pg(conn) -> bool:
    return getattr(conn, "_backend", "sqlite") == "postgresql"


def down(conn) -> dict:
    actions = []
    conn.execute("DROP TABLE IF EXISTS kanban_verifications")
    actions.append("dropped_verifications_table")

    if _is_pg(conn):
        try:
            conn.execute(
                "ALTER TABLE kanban_tasks DROP CONSTRAINT IF EXISTS kanban_tasks_status_check"
            )
            conn.execute(
                "ALTER TABLE kanban_tasks ADD CONSTRAINT kanban_tasks_status_check "
                "CHECK (status = ANY (ARRAY["
                "'backlog'::text, 'scheduled'::text, 'in_progress'::text, "
                "'done'::text, 'token_exhausted'::text, 'suggested'::text]))"
            )
            actions.append("status_check_reverted")
        except Exception as exc:
            actions.append(f"status_check_revert_skipped: {exc}")

    conn.commit()
    return {"status": "reverted", "actions": actions}
