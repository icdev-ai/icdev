#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 020: Track verification failures + add needs_decomposition status.

Adds to kanban_tasks:
  - failure_count INTEGER DEFAULT 0
  - last_failure_reason TEXT
  - last_failure_at TEXT

Also expands kanban_tasks.status CHECK constraint to include
'needs_decomposition' so oversized tasks can be flagged after N failures.

Idempotent — uses ADD COLUMN IF NOT EXISTS semantics.
"""

MIGRATION_ID = "020"
MIGRATION_NAME = "kanban_failure_count"
DESCRIPTION = "Add failure tracking columns + needs_decomposition status"


def _is_pg(conn) -> bool:
    return getattr(conn, "_backend", "sqlite") == "postgresql"


def _table_exists(conn, table: str) -> bool:
    """True if ``table`` exists. ``kanban_tasks`` is created at app runtime by
    tools/kanban/init_db.py (not by this migration chain), so a migrate-only
    fresh DB — e.g. the CI E2E PostgreSQL job's ``migrate.py --up`` — legitimately
    lacks it. Skip rather than abort the whole chain, matching how the fa_*/ttx_*
    canvas migrations already self-skip when their runtime tables are absent."""
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


def _has_column(conn, table: str, column: str) -> bool:
    if _is_pg(conn):
        row = conn.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_schema='public' AND table_name=? AND column_name=?",
            (table, column),
        ).fetchone()
    else:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        row = any(
            (r[1] if isinstance(r, (list, tuple)) else dict(r).get("name")) == column
            for r in rows
        )
    return bool(row)


def up(conn) -> dict:
    actions = []

    if not _table_exists(conn, "kanban_tasks"):
        return {"status": "skipped", "reason": "kanban_tasks absent (created at runtime by tools/kanban/init_db.py)"}

    if not _has_column(conn, "kanban_tasks", "failure_count"):
        conn.execute(
            "ALTER TABLE kanban_tasks ADD COLUMN failure_count INTEGER DEFAULT 0"
        )
        actions.append("added_failure_count")

    if not _has_column(conn, "kanban_tasks", "last_failure_reason"):
        conn.execute(
            "ALTER TABLE kanban_tasks ADD COLUMN last_failure_reason TEXT"
        )
        actions.append("added_last_failure_reason")

    if not _has_column(conn, "kanban_tasks", "last_failure_at"):
        conn.execute(
            "ALTER TABLE kanban_tasks ADD COLUMN last_failure_at TEXT"
        )
        actions.append("added_last_failure_at")

    # Expand status CHECK to include needs_decomposition (PG only)
    if _is_pg(conn):
        try:
            cur = conn.execute(
                "SELECT pg_get_constraintdef(c.oid) as def "
                "FROM pg_constraint c JOIN pg_class t ON c.conrelid = t.oid "
                "WHERE t.relname = ? AND c.conname = ?",
                ("kanban_tasks", "kanban_tasks_status_check"),
            ).fetchone()
            current = dict(cur).get("def", "") if cur else ""
            if "needs_decomposition" not in current:
                conn.execute(
                    "ALTER TABLE kanban_tasks DROP CONSTRAINT IF EXISTS kanban_tasks_status_check"
                )
                conn.execute(
                    "ALTER TABLE kanban_tasks ADD CONSTRAINT kanban_tasks_status_check "
                    "CHECK (status = ANY (ARRAY["
                    "'backlog'::text, 'scheduled'::text, 'in_progress'::text, "
                    "'done'::text, 'token_exhausted'::text, 'suggested'::text, "
                    "'decomposed'::text, 'validating'::text, "
                    "'needs_decomposition'::text]))"
                )
                actions.append("status_check_expanded")
        except Exception as exc:
            actions.append(f"status_check_skipped: {exc}")

    # Index for querying failed tasks quickly
    try:
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_kanban_tasks_failure_count "
            "ON kanban_tasks (failure_count) WHERE failure_count > 0"
        )
        actions.append("failure_count_index")
    except Exception:
        # SQLite doesn't support partial indexes with WHERE — fall back
        try:
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_kanban_tasks_failure_count "
                "ON kanban_tasks (failure_count)"
            )
            actions.append("failure_count_index_full")
        except Exception as exc:
            actions.append(f"index_skipped: {exc}")

    conn.commit()
    return {"status": "applied", "actions": actions}
