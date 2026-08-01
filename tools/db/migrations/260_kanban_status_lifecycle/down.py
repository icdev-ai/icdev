#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 260 rollback: revert kanban_tasks.status CHECK to the pre-260 set.

Only safe if no rows currently use the five new states. Callers that roll back
should first re-map any pr_opened/ci_failed/merge_conflict/changes_requested ->
in_progress and failed -> token_exhausted (the old collapse targets).
"""

MIGRATION_ID = "260"
MIGRATION_NAME = "kanban_status_lifecycle"

PRE_260_VALUES = [
    "backlog", "scheduled", "in_progress", "done", "token_exhausted",
    "suggested", "decomposed", "validating", "needs_decomposition",
]


def _is_pg(conn) -> bool:
    return getattr(conn, "_backend", "sqlite") == "postgresql"


def down(conn) -> dict:
    actions = []
    if not _is_pg(conn):
        return {"status": "skipped", "reason": "status CHECK is PostgreSQL-only"}
    try:
        # Re-map new states to their old collapse targets so the narrower
        # constraint validates.
        conn.execute(
            "UPDATE kanban_tasks SET status='in_progress' "
            "WHERE status IN ('pr_opened','ci_failed','merge_conflict','changes_requested')"
        )
        conn.execute(
            "UPDATE kanban_tasks SET status='token_exhausted' WHERE status='failed'"
        )
        arr = ", ".join(f"'{v}'::text" for v in PRE_260_VALUES)
        conn.execute(
            "ALTER TABLE kanban_tasks DROP CONSTRAINT IF EXISTS kanban_tasks_status_check"
        )
        conn.execute(
            "ALTER TABLE kanban_tasks ADD CONSTRAINT kanban_tasks_status_check "
            f"CHECK (status = ANY (ARRAY[{arr}]))"
        )
        actions.append("status_check_reverted")
    except Exception as exc:
        actions.append(f"status_check_revert_skipped: {exc}")
    conn.commit()
    return {"status": "reverted", "actions": actions}
