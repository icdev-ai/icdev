# CUI // SP-CTI
"""Initialize Kanban DB tables (idempotent — safe to call at startup).

Creates kanban_tasks, kanban_task_deps, kanban_executions, kanban_verifications,
and kanban_status_transitions using CREATE TABLE IF NOT EXISTS so the call is
safe on both fresh SQLite databases (CI) and fully-migrated production DBs.

All columns that were added via migrations 012–120 are included in the base
DDL, so CI environments that skip migration steps still get the full schema.
"""
from __future__ import annotations


_KANBAN_TASKS_DDL = """
CREATE TABLE IF NOT EXISTS kanban_tasks (
    id                    TEXT PRIMARY KEY,
    title                 TEXT NOT NULL,
    description           TEXT,
    task_type             TEXT DEFAULT 'build',
    priority              TEXT DEFAULT 'high',
    status                TEXT DEFAULT 'backlog',
    scheduled_at          TEXT,
    created_at            TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at            TEXT DEFAULT CURRENT_TIMESTAMP,
    completed_at          TEXT,
    executor_type         TEXT DEFAULT 'claude_cli',
    execution_id          TEXT,
    executor_url          TEXT,
    depends_on_task_id    TEXT,
    source_prediction_id  TEXT,
    failure_count         INTEGER DEFAULT 0,
    last_failure_reason   TEXT,
    last_failure_at       TEXT,
    dispatch_source       TEXT DEFAULT 'unknown',
    hitl_stage            TEXT,
    start_date            TEXT,
    target_date           TEXT,
    files_changed         INTEGER DEFAULT 0,
    lines_added           INTEGER DEFAULT 0,
    lines_removed         INTEGER DEFAULT 0,
    completed_via_bypass  INTEGER DEFAULT 0
)
"""

_KANBAN_TASK_DEPS_DDL = """
CREATE TABLE IF NOT EXISTS kanban_task_deps (
    task_id         TEXT NOT NULL,
    depends_on_id   TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    PRIMARY KEY (task_id, depends_on_id),
    FOREIGN KEY (task_id)       REFERENCES kanban_tasks(id) ON DELETE CASCADE,
    FOREIGN KEY (depends_on_id) REFERENCES kanban_tasks(id) ON DELETE CASCADE
)
"""

_KANBAN_EXECUTIONS_DDL = """
CREATE TABLE IF NOT EXISTS kanban_executions (
    id          TEXT PRIMARY KEY,
    task_id     TEXT NOT NULL,
    status      TEXT DEFAULT 'pending',
    started_at  TEXT,
    finished_at TEXT,
    output      TEXT,
    error       TEXT,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
)
"""

# Full schema from migration 019 — includes all audit columns
_KANBAN_VERIFICATIONS_DDL = """
CREATE TABLE IF NOT EXISTS kanban_verifications (
    id                    TEXT PRIMARY KEY,
    task_id               TEXT NOT NULL,
    verified_at           TEXT NOT NULL,
    result                TEXT NOT NULL,
    reason                TEXT,
    output_length         INTEGER DEFAULT 0,
    fail_markers_found    TEXT,
    claimed_paths         INTEGER DEFAULT 0,
    existing_paths        INTEGER DEFAULT 0,
    phantom_ratio         REAL DEFAULT 0,
    git_commits           INTEGER DEFAULT 0,
    specific_checks       TEXT,
    codelens_passed       INTEGER,
    ruff_issues           INTEGER,
    bandit_issues         INTEGER,
    pytest_passed         INTEGER,
    failed_tests          TEXT,
    coherence_passed      INTEGER,
    coherence_violations  TEXT,
    e2e_ran               INTEGER,
    e2e_passed            INTEGER,
    e2e_errors            TEXT,
    companion_synced      INTEGER,
    created_at            TEXT DEFAULT CURRENT_TIMESTAMP
)
"""

# Column name is recorded_at (migration 025) not created_at
_KANBAN_STATUS_TRANSITIONS_DDL = """
CREATE TABLE IF NOT EXISTS kanban_status_transitions (
    id           TEXT PRIMARY KEY,
    task_id      TEXT NOT NULL,
    from_status  TEXT,
    to_status    TEXT NOT NULL,
    actor        TEXT,
    reason       TEXT,
    recorded_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
)
"""

_KANBAN_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_kt_status   ON kanban_tasks(status)",
    "CREATE INDEX IF NOT EXISTS idx_kt_priority ON kanban_tasks(priority)",
    "CREATE INDEX IF NOT EXISTS idx_kt_updated  ON kanban_tasks(updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_ktd_dep     ON kanban_task_deps(depends_on_id)",
    "CREATE INDEX IF NOT EXISTS idx_ke_task_id  ON kanban_executions(task_id)",
    "CREATE INDEX IF NOT EXISTS idx_kv_task_id  ON kanban_verifications(task_id)",
    "CREATE INDEX IF NOT EXISTS idx_kv_result   ON kanban_verifications(result)",
    "CREATE INDEX IF NOT EXISTS idx_kst_task_id ON kanban_status_transitions(task_id)",
]

# Columns to ADD IF NOT EXISTS for already-created tables (ALTER TABLE is idempotent via try/except).
_KANBAN_TASKS_EXTRA_COLUMNS = [
    "ALTER TABLE kanban_tasks ADD COLUMN start_date          TEXT",
    "ALTER TABLE kanban_tasks ADD COLUMN target_date         TEXT",
    "ALTER TABLE kanban_tasks ADD COLUMN files_changed       INTEGER DEFAULT 0",
    "ALTER TABLE kanban_tasks ADD COLUMN lines_added         INTEGER DEFAULT 0",
    "ALTER TABLE kanban_tasks ADD COLUMN lines_removed       INTEGER DEFAULT 0",
    "ALTER TABLE kanban_tasks ADD COLUMN completed_via_bypass INTEGER DEFAULT 0",
    "ALTER TABLE kanban_tasks ADD COLUMN source_prediction_id TEXT",
    "ALTER TABLE kanban_tasks ADD COLUMN failure_count       INTEGER DEFAULT 0",
    "ALTER TABLE kanban_tasks ADD COLUMN last_failure_reason TEXT",
    "ALTER TABLE kanban_tasks ADD COLUMN last_failure_at     TEXT",
    "ALTER TABLE kanban_tasks ADD COLUMN dispatch_source     TEXT DEFAULT 'unknown'",
    "ALTER TABLE kanban_tasks ADD COLUMN hitl_stage          TEXT",
]


def init_kanban_tables(conn=None) -> dict:
    """Create all Kanban tables.  Safe to call on any DB state."""
    from tools.db.storage import get_connection
    _close = conn is None
    if conn is None:
        conn = get_connection()
    try:
        conn.execute(_KANBAN_TASKS_DDL)
        conn.execute(_KANBAN_TASK_DEPS_DDL)
        conn.execute(_KANBAN_EXECUTIONS_DDL)
        conn.execute(_KANBAN_VERIFICATIONS_DDL)
        conn.execute(_KANBAN_STATUS_TRANSITIONS_DDL)
        for idx in _KANBAN_INDEXES:
            conn.execute(idx)
        # Backfill missing columns on pre-existing tables (idempotent)
        for alter in _KANBAN_TASKS_EXTRA_COLUMNS:
            try:
                conn.execute(alter)
            except Exception:
                pass  # column already exists
        conn.commit()
        return {"status": "ok"}
    finally:
        if _close:
            conn.close()


if __name__ == "__main__":
    result = init_kanban_tables()
    print(result)
