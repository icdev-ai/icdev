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
    completed_via_bypass  INTEGER DEFAULT 0,
    source_doc_id         TEXT,
    source_collection_id  TEXT,
    last_heartbeat_at     TEXT,
    max_retries           INTEGER DEFAULT 5,
    idempotency_key       TEXT,
    last_run_summary      TEXT,
    last_run_metadata     TEXT,
    max_runtime_seconds   INTEGER,
    acceptance_criteria   TEXT,
    triage_prompt         TEXT,
    loop_type             TEXT DEFAULT 'deterministic',
    adversarial_enabled   INTEGER DEFAULT 0,
    due_date              TEXT,
    sla_hours             INTEGER
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
    id             TEXT PRIMARY KEY,
    task_id        TEXT NOT NULL,
    executor_type  TEXT NOT NULL DEFAULT 'claude_cli',
    execution_id   TEXT,
    started_at     TEXT NOT NULL,
    completed_at   TEXT,
    status         TEXT NOT NULL DEFAULT 'running',
    exit_code      INTEGER,
    output_summary TEXT,
    executor_url   TEXT,
    created_at     TEXT DEFAULT CURRENT_TIMESTAMP,
    run_summary    TEXT,
    run_metadata   TEXT
)
"""
# NOTE: the column list above is the LIVE schema (migration 010 plus the later
# run_summary/run_metadata additions), verified against information_schema on
# the production PostgreSQL database.
#
# It previously read `finished_at / output / error` — three columns that have
# never existed in any migration. On a real database that mismatch is invisible,
# because CREATE TABLE IF NOT EXISTS is a no-op once migration 010 has run. On a
# fresh SQLite database (CI, a cold worktree) this DDL runs FIRST, creates the
# wrong shape, and then migration 010's own IF NOT EXISTS becomes the no-op — so
# the wrong schema is what sticks, and every INSERT written against the real
# column names fails there and only there.

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
    review_passed         INTEGER,
    review_findings       TEXT,
    pytest_ran            INTEGER DEFAULT 0,
    created_at            TEXT DEFAULT CURRENT_TIMESTAMP,
    -- Present in production (added by migrations) but absent here, so a fresh
    -- SQLite database got 27 columns while PostgreSQL had 32. Every INSERT
    -- naming one of these — including the dispatch-time writer in
    -- genesis/reflexes/kanban.py — raised "no column named ..." on a fresh DB
    -- and was swallowed by its best-effort except. Listing them keeps the
    -- promise this module's docstring already makes.
    dispatch_source       TEXT,
    classification        TEXT,
    remediation_attempted INTEGER DEFAULT 0,
    remediation_success   INTEGER,
    remediation_type      TEXT
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
    "CREATE INDEX IF NOT EXISTS idx_kt_status       ON kanban_tasks(status)",
    "CREATE INDEX IF NOT EXISTS idx_kt_priority     ON kanban_tasks(priority)",
    "CREATE INDEX IF NOT EXISTS idx_kt_updated      ON kanban_tasks(updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_kt_idem_key     ON kanban_tasks(idempotency_key)",
    "CREATE INDEX IF NOT EXISTS idx_kt_heartbeat    ON kanban_tasks(last_heartbeat_at)",
    "CREATE INDEX IF NOT EXISTS idx_ktd_dep         ON kanban_task_deps(depends_on_id)",
    "CREATE INDEX IF NOT EXISTS idx_ke_task_id      ON kanban_executions(task_id)",
    "CREATE INDEX IF NOT EXISTS idx_kv_task_id      ON kanban_verifications(task_id)",
    "CREATE INDEX IF NOT EXISTS idx_kv_result       ON kanban_verifications(result)",
    "CREATE INDEX IF NOT EXISTS idx_kst_task_id     ON kanban_status_transitions(task_id)",
]

# Columns to add for already-created tables, as (column_name, ALTER DDL) pairs.
# We check existence BEFORE issuing the ALTER: an unconditional
# `ALTER TABLE ... ADD COLUMN` acquires an ACCESS EXCLUSIVE lock on PostgreSQL
# *before* it can detect the column already exists, so running it every init
# queues a blocking exclusive-lock request behind readers and causes a
# kanban_tasks lock storm. Gating on existence keeps steady-state init lock-free.
_KANBAN_TASKS_EXTRA_COLUMNS = [
    ("start_date",           "ALTER TABLE kanban_tasks ADD COLUMN start_date          TEXT"),
    ("target_date",          "ALTER TABLE kanban_tasks ADD COLUMN target_date         TEXT"),
    ("files_changed",        "ALTER TABLE kanban_tasks ADD COLUMN files_changed       INTEGER DEFAULT 0"),
    ("lines_added",          "ALTER TABLE kanban_tasks ADD COLUMN lines_added         INTEGER DEFAULT 0"),
    ("lines_removed",        "ALTER TABLE kanban_tasks ADD COLUMN lines_removed       INTEGER DEFAULT 0"),
    ("completed_via_bypass", "ALTER TABLE kanban_tasks ADD COLUMN completed_via_bypass INTEGER DEFAULT 0"),
    ("source_prediction_id", "ALTER TABLE kanban_tasks ADD COLUMN source_prediction_id TEXT"),
    ("failure_count",        "ALTER TABLE kanban_tasks ADD COLUMN failure_count       INTEGER DEFAULT 0"),
    ("last_failure_reason",  "ALTER TABLE kanban_tasks ADD COLUMN last_failure_reason TEXT"),
    ("last_failure_at",      "ALTER TABLE kanban_tasks ADD COLUMN last_failure_at     TEXT"),
    ("dispatch_source",      "ALTER TABLE kanban_tasks ADD COLUMN dispatch_source     TEXT DEFAULT 'unknown'"),
    ("hitl_stage",           "ALTER TABLE kanban_tasks ADD COLUMN hitl_stage          TEXT"),
    ("source_doc_id",        "ALTER TABLE kanban_tasks ADD COLUMN source_doc_id        TEXT"),
    ("source_collection_id", "ALTER TABLE kanban_tasks ADD COLUMN source_collection_id TEXT"),
    # Hermes-inspired reliability features
    ("last_heartbeat_at",   "ALTER TABLE kanban_tasks ADD COLUMN last_heartbeat_at    TEXT"),
    ("max_retries",         "ALTER TABLE kanban_tasks ADD COLUMN max_retries          INTEGER DEFAULT 5"),
    ("idempotency_key",     "ALTER TABLE kanban_tasks ADD COLUMN idempotency_key      TEXT"),
    ("last_run_summary",    "ALTER TABLE kanban_tasks ADD COLUMN last_run_summary     TEXT"),
    ("last_run_metadata",   "ALTER TABLE kanban_tasks ADD COLUMN last_run_metadata    TEXT"),
    # Phase 250b8557 — per-task runtime limit, acceptance criteria, triage prompt
    ("max_runtime_seconds", "ALTER TABLE kanban_tasks ADD COLUMN max_runtime_seconds  INTEGER"),
    # Observed wall-clock runtime, written by _record_execution_seconds on
    # completion. _detect_execution_anomalies reads it to derive an adaptive
    # timeout ceiling; the column was missing on BOTH backends, so that whole
    # feature silently returned {} and fell back to static constants.
    # PostgreSQL gets it via migration 319.
    ("execution_seconds",   "ALTER TABLE kanban_tasks ADD COLUMN execution_seconds    REAL"),
    ("acceptance_criteria", "ALTER TABLE kanban_tasks ADD COLUMN acceptance_criteria  TEXT"),
    ("triage_prompt",       "ALTER TABLE kanban_tasks ADD COLUMN triage_prompt        TEXT"),
    # Trace linkage. PostgreSQL got these via migration; the init fallback never
    # did, so _decompose_batch_tasks (which INSERTs trace_id/span_id) failed on
    # every child and silently decomposed batches into zero children.
    ("trace_id",            "ALTER TABLE kanban_tasks ADD COLUMN trace_id             TEXT"),
    ("span_id",             "ALTER TABLE kanban_tasks ADD COLUMN span_id              TEXT"),
    # crx-kan-01 — SLA / due-date tracking (nullable; opt-in per task).
    ("due_date",            "ALTER TABLE kanban_tasks ADD COLUMN due_date             TEXT"),
    ("sla_hours",           "ALTER TABLE kanban_tasks ADD COLUMN sla_hours            INTEGER"),
]

# Same conditional-ALTER contract as _KANBAN_TASKS_EXTRA_COLUMNS.
_KANBAN_VERIFICATIONS_EXTRA_COLUMNS = [
    ("dispatch_source", "ALTER TABLE kanban_verifications ADD COLUMN dispatch_source TEXT DEFAULT 'unknown'"),
]

_KANBAN_TASK_SUBSCRIPTIONS_DDL = """
CREATE TABLE IF NOT EXISTS kanban_task_subscriptions (
    id          TEXT PRIMARY KEY,
    task_id     TEXT NOT NULL,
    channel     TEXT NOT NULL,
    target      TEXT NOT NULL,
    events      TEXT NOT NULL DEFAULT 'done,token_exhausted',
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
)
"""

_KANBAN_SUBSCRIPTIONS_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_ksub_task_id ON kanban_task_subscriptions(task_id)",
]

_KANBAN_EXECUTIONS_EXTRA_COLUMNS = [
    ("run_summary",  "ALTER TABLE kanban_executions ADD COLUMN run_summary  TEXT"),
    ("run_metadata", "ALTER TABLE kanban_executions ADD COLUMN run_metadata TEXT"),
]


def _existing_columns(conn, table: str) -> set:
    """Return existing column names for *table*.

    Reads only the catalog (information_schema / PRAGMA), which never locks the
    table itself — safe even while the table is under a write-lock storm.
    """
    try:
        from tools.db.storage import is_pg
        pg = is_pg(conn)
    except Exception:
        pg = False
    try:
        if pg:
            rows = conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
                (table,),
            ).fetchall()
            key, idx = "column_name", 0
        else:
            # pg-portability: sqlite-only path — SQLite branch of an explicit
            # is_pg(conn) guard (the PG branch above uses information_schema).
            rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
            key, idx = "name", 1
    except Exception:
        return set()
    out = set()
    for r in rows:
        try:
            out.add(r[key])
        except Exception:
            out.add(r[idx])
    return out


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
        conn.execute(_KANBAN_TASK_SUBSCRIPTIONS_DDL)
        for idx in _KANBAN_SUBSCRIPTIONS_INDEXES:
            conn.execute(idx)
        # Backfill missing columns — only ALTER when the column is actually
        # absent, so steady-state init never requests an ACCESS EXCLUSIVE lock
        # on kanban_tasks (catalog read first, then conditional ALTER).
        existing = _existing_columns(conn, "kanban_tasks")
        for col_name, alter in _KANBAN_TASKS_EXTRA_COLUMNS:
            if col_name in existing:
                continue
            try:
                conn.execute(alter)
            except Exception:
                pass  # raced with another writer; column now exists
        existing_exec = _existing_columns(conn, "kanban_executions")
        for col_name, alter in _KANBAN_EXECUTIONS_EXTRA_COLUMNS:
            if col_name in existing_exec:
                continue
            try:
                conn.execute(alter)
            except Exception:
                pass
        existing_verif = _existing_columns(conn, "kanban_verifications")
        for col_name, alter in _KANBAN_VERIFICATIONS_EXTRA_COLUMNS:
            if col_name in existing_verif:
                continue
            try:
                conn.execute(alter)
            except Exception:
                pass
        # Indexes LAST. `CREATE TABLE IF NOT EXISTS` never alters an existing
        # table, so a kanban_tasks created by an older DDL reaches this function
        # without idempotency_key / last_heartbeat_at — and indexing a column
        # that does not exist yet raised, aborting init before any of the
        # backfills above could run. Ordering them after the ALTERs makes the
        # index and its column arrive together.
        for idx in _KANBAN_INDEXES:
            try:
                conn.execute(idx)
            except Exception:
                pass  # column still absent on a partial legacy table
        conn.commit()
        return {"status": "ok"}
    finally:
        if _close:
            conn.close()


if __name__ == "__main__":
    result = init_kanban_tables()
    print(result)
