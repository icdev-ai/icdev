#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 260: Expand kanban_tasks.status CHECK to the true task lifecycle.

Adds five lifecycle states that previously collapsed in
tools/kanban/state_machine.py::_STATE_TO_DB_STATUS:
  - pr_opened          (was -> in_progress)   "PR open, awaiting merge"
  - ci_failed          (was -> in_progress)
  - merge_conflict     (was -> in_progress)
  - changes_requested  (was -> in_progress)
  - failed             (was -> token_exhausted, i.e. masqueraded as a
                        resumable pause; now a distinct terminal state)

PostgreSQL only (SQLite's kanban_tasks has no CHECK on status). Idempotent —
only rewrites the constraint when a new value is missing. Widening a CHECK is
backward-compatible: every existing row still satisfies the broader set, so
this is safe to apply ahead of the code that emits the new states.

Follows the 019/020 pattern for altering kanban_tasks_status_check.
"""

MIGRATION_ID = "260"
MIGRATION_NAME = "kanban_status_lifecycle"
DESCRIPTION = "Add pr_opened/ci_failed/merge_conflict/changes_requested/failed to kanban_tasks.status CHECK"

# Full allowed set after this migration (source of truth mirrored in
# tools/db/schema/pg_consolidated.sql and the state machine's DB-status map).
STATUS_VALUES = [
    "backlog", "scheduled", "in_progress", "done", "token_exhausted",
    "suggested", "decomposed", "validating", "needs_decomposition",
    "pr_opened", "ci_failed", "merge_conflict", "changes_requested", "failed",
]


def _is_pg(conn) -> bool:
    return getattr(conn, "_backend", "sqlite") == "postgresql"


def _table_exists(conn, table: str) -> bool:
    """kanban_tasks is created at app runtime by tools/kanban/init_db.py, so a
    migrate-only fresh DB (e.g. the CI PostgreSQL job) may legitimately lack it.
    Skip rather than abort the chain — matching migration 020."""
    if _is_pg(conn):
        row = conn.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name=?",
            (table,),
        ).fetchone()
        return row is not None
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def _check_array_sql() -> str:
    arr = ", ".join(f"'{v}'::text" for v in STATUS_VALUES)
    return (
        "ALTER TABLE kanban_tasks ADD CONSTRAINT kanban_tasks_status_check "
        f"CHECK (status = ANY (ARRAY[{arr}]))"
    )


def up(conn) -> dict:
    actions = []

    if not _table_exists(conn, "kanban_tasks"):
        return {"status": "skipped", "reason": "kanban_tasks absent (created at runtime by tools/kanban/init_db.py)"}

    # CHECK constraint on status is PG-only; SQLite kanban_tasks has no CHECK.
    if not _is_pg(conn):
        return {"status": "skipped", "reason": "status CHECK is PostgreSQL-only"}

    try:
        cur = conn.execute(
            "SELECT pg_get_constraintdef(c.oid) as def "
            "FROM pg_constraint c JOIN pg_class t ON c.conrelid = t.oid "
            "WHERE t.relname = ? AND c.conname = ?",
            ("kanban_tasks", "kanban_tasks_status_check"),
        ).fetchone()
        current = dict(cur).get("def", "") if cur else ""
        # Idempotent: only rewrite when a new value is missing.
        if all(v in current for v in ("pr_opened", "ci_failed", "merge_conflict", "changes_requested", "failed")):
            actions.append("status_check_already_current")
        else:
            conn.execute(
                "ALTER TABLE kanban_tasks DROP CONSTRAINT IF EXISTS kanban_tasks_status_check"
            )
            conn.execute(_check_array_sql())
            actions.append("status_check_expanded")
    except Exception as exc:
        actions.append(f"status_check_skipped: {exc}")

    conn.commit()
    return {"status": "applied", "actions": actions}
