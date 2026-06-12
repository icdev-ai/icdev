#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 020 rollback."""


MIGRATION_ID = "020"
MIGRATION_NAME = "kanban_failure_count"


def _is_pg(conn) -> bool:
    return getattr(conn, "_backend", "sqlite") == "postgresql"


def down(conn) -> dict:
    actions = []

    # Drop the index
    conn.execute("DROP INDEX IF EXISTS idx_kanban_tasks_failure_count")
    actions.append("dropped_index")

    # Drop columns (PG supports DROP COLUMN directly; SQLite needs table rebuild)
    if _is_pg(conn):
        for col in ("failure_count", "last_failure_reason", "last_failure_at"):
            try:
                conn.execute(f"ALTER TABLE kanban_tasks DROP COLUMN IF EXISTS {col}")
                actions.append(f"dropped_{col}")
            except Exception as exc:
                actions.append(f"{col}_drop_skipped: {exc}")

        # Revert status check (without needs_decomposition)
        try:
            conn.execute(
                "ALTER TABLE kanban_tasks DROP CONSTRAINT IF EXISTS kanban_tasks_status_check"
            )
            conn.execute(
                "ALTER TABLE kanban_tasks ADD CONSTRAINT kanban_tasks_status_check "
                "CHECK (status = ANY (ARRAY["
                "'backlog'::text, 'scheduled'::text, 'in_progress'::text, "
                "'done'::text, 'token_exhausted'::text, 'suggested'::text, "
                "'decomposed'::text, 'validating'::text]))"
            )
            actions.append("status_check_reverted")
        except Exception as exc:
            actions.append(f"status_check_revert_skipped: {exc}")

    conn.commit()
    return {"status": "reverted", "actions": actions}
