# CUI // SP-CTI
"""ACE (Autonomous Collaborative Engine) — DB initializer.

Creates 5 canvas tables.  Uses get_canvas_connection() — NOT get_connection() —
because ace_* tables have no classification/tenant_id columns.

Usage:
    python -m icdev.tools.ace.db.init_db
    python -c "from icdev.tools.ace.db.init_db import init; init()"
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# State constants — SQL CHECK constraints are derived from these; never hardcode
# ---------------------------------------------------------------------------

INSTANCE_STATES: tuple[str, ...] = (
    "assembling",
    "pending",
    "active",
    "paused",
    "complete",
    "failed",
    "cancelled",
)

SKILL_CANDIDATE_STATUSES: tuple[str, ...] = (
    "pending",
    "promoted",   # SIPA passed → written to roles/candidates/ for human review
    "rejected",   # SIPA failed or TDD gate rejected
    "skipped",    # duplicate of an existing candidate
)

COWORKER_STATES: tuple[str, ...] = (
    "idle",
    "active",
    "busy",
    "offline",
    "suspended",
    "working",       # actively executing a step
    "hitl_pending",  # suspended awaiting HITL approval
    "done",          # all steps completed successfully
    "failed",        # terminated due to unrecoverable error
)

# ---------------------------------------------------------------------------
# DDL helpers
# ---------------------------------------------------------------------------

def _check(values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"IN ({joined})"


_CHECK_INSTANCE_STATE = _check(INSTANCE_STATES)
_CHECK_COWORKER_STATE = _check(COWORKER_STATES)
_CHECK_SKILL_STATUS = _check(SKILL_CANDIDATE_STATUSES)

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS ace_instances (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL DEFAULT '',
    role_id         TEXT NOT NULL DEFAULT '',
    state           TEXT NOT NULL DEFAULT 'pending' CHECK(state {_CHECK_INSTANCE_STATE}),
    trust_tier      TEXT NOT NULL DEFAULT 'yellow',
    config_json     TEXT NOT NULL DEFAULT '{{}}',
    result_json     TEXT NOT NULL DEFAULT '{{}}',
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at    TEXT
);

CREATE TABLE IF NOT EXISTS ace_coworkers (
    id              TEXT PRIMARY KEY,
    instance_id     TEXT NOT NULL REFERENCES ace_instances(id) ON DELETE CASCADE,
    role_id         TEXT NOT NULL,
    display_name    TEXT NOT NULL DEFAULT '',
    state           TEXT NOT NULL DEFAULT 'idle' CHECK(state {_CHECK_COWORKER_STATE}),
    trust_tier      TEXT NOT NULL DEFAULT 'yellow',
    assigned_step   TEXT DEFAULT '',
    last_active_at  TEXT,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ace_coworkers_instance ON ace_coworkers(instance_id);

CREATE TABLE IF NOT EXISTS ace_messages (
    id              TEXT PRIMARY KEY,
    instance_id     TEXT NOT NULL REFERENCES ace_instances(id) ON DELETE CASCADE,
    coworker_id     TEXT,
    message_type    TEXT NOT NULL DEFAULT 'info',
    role            TEXT NOT NULL DEFAULT 'user',
    content         TEXT NOT NULL DEFAULT '',
    metadata_json   TEXT NOT NULL DEFAULT '{{}}',
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ace_messages_instance ON ace_messages(instance_id);

CREATE TABLE IF NOT EXISTS ace_artifacts (
    id              TEXT PRIMARY KEY,
    instance_id     TEXT NOT NULL REFERENCES ace_instances(id) ON DELETE CASCADE,
    coworker_id     TEXT,
    artifact_type   TEXT NOT NULL DEFAULT 'document',
    title           TEXT NOT NULL DEFAULT '',
    classification  TEXT NOT NULL DEFAULT 'CUI',
    content_json    TEXT NOT NULL DEFAULT '{{}}',
    content_md      TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ace_artifacts_instance ON ace_artifacts(instance_id);

CREATE TABLE IF NOT EXISTS ace_agent_workflows (
    id              TEXT PRIMARY KEY,
    instance_id     TEXT NOT NULL REFERENCES ace_instances(id) ON DELETE CASCADE,
    name            TEXT NOT NULL DEFAULT '',
    state           TEXT NOT NULL DEFAULT 'pending',
    config_json     TEXT NOT NULL DEFAULT '{{}}',
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ace_workflows_instance ON ace_agent_workflows(instance_id);

CREATE TABLE IF NOT EXISTS ace_audit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    instance_id     TEXT,
    coworker_id     TEXT,
    action          TEXT NOT NULL,
    detail          TEXT NOT NULL DEFAULT '',
    actor           TEXT NOT NULL DEFAULT 'system',
    classification  TEXT NOT NULL DEFAULT 'CUI',
    control_refs    TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ace_audit_instance ON ace_audit_log(instance_id);

CREATE TABLE IF NOT EXISTS coworker_dic_contexts (
    id              TEXT PRIMARY KEY,
    instance_id     TEXT,
    collection_id   TEXT NOT NULL,
    attached_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_cwk_dic_instance ON coworker_dic_contexts(instance_id);

CREATE TABLE IF NOT EXISTS ace_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    topic           TEXT NOT NULL,
    source_canvas   TEXT NOT NULL DEFAULT '',
    source_id       TEXT NOT NULL DEFAULT '',
    payload_json    TEXT NOT NULL DEFAULT '{{}}',
    processed       INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ace_events_processed ON ace_events(processed);
CREATE INDEX IF NOT EXISTS idx_ace_events_topic ON ace_events(topic);

CREATE TABLE IF NOT EXISTS ace_event_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        INTEGER REFERENCES ace_events(id),
    role_id         TEXT NOT NULL,
    instance_id     TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'dispatched',
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ace_event_results_event ON ace_event_results(event_id);

CREATE TABLE IF NOT EXISTS ace_skill_candidates (
    id                    TEXT PRIMARY KEY,
    role_id               TEXT NOT NULL,
    source_role           TEXT NOT NULL DEFAULT '',
    instance_id           TEXT NOT NULL DEFAULT '',
    candidate_yaml        TEXT NOT NULL DEFAULT '',
    trust_tier            TEXT NOT NULL DEFAULT 'yellow',
    status                TEXT NOT NULL DEFAULT 'pending' CHECK(status {_CHECK_SKILL_STATUS}),
    sipa_verdict          TEXT,
    sipa_score            REAL,
    rejection_reason      TEXT,
    created_at            TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at           TEXT
);
CREATE INDEX IF NOT EXISTS idx_ace_skill_candidates_status ON ace_skill_candidates(status);
CREATE INDEX IF NOT EXISTS idx_ace_skill_candidates_role ON ace_skill_candidates(role_id);

CREATE TABLE IF NOT EXISTS ace_sessions (
    session_id            TEXT PRIMARY KEY,
    instance_id           TEXT NOT NULL REFERENCES ace_instances(id) ON DELETE CASCADE,
    conversation_history  TEXT NOT NULL DEFAULT '[]',
    history_json          TEXT NOT NULL DEFAULT '[]',
    resume_token          TEXT NOT NULL UNIQUE,
    turn_count            INTEGER NOT NULL DEFAULT 0,
    created_at            TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ace_sessions_instance ON ace_sessions(instance_id);
CREATE INDEX IF NOT EXISTS idx_ace_sessions_token ON ace_sessions(resume_token);

CREATE TABLE IF NOT EXISTS ace_qa_runs (
    id                TEXT PRIMARY KEY,
    trigger           TEXT NOT NULL DEFAULT '',
    trigger_ref       TEXT NOT NULL DEFAULT '',
    canvas_filter     TEXT NOT NULL DEFAULT '',
    started_at        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at      TEXT,
    status            TEXT NOT NULL DEFAULT 'running',
    total_tests       INTEGER NOT NULL DEFAULT 0,
    passed            INTEGER NOT NULL DEFAULT 0,
    failed            INTEGER NOT NULL DEFAULT 0,
    screenshot_count  INTEGER NOT NULL DEFAULT 0,
    report_path       TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_ace_qa_runs_status  ON ace_qa_runs(status);
CREATE INDEX IF NOT EXISTS idx_ace_qa_runs_started ON ace_qa_runs(started_at);

CREATE TABLE IF NOT EXISTS ace_qa_failures (
    id                    TEXT PRIMARY KEY,
    run_id                TEXT NOT NULL REFERENCES ace_qa_runs(id),
    test_name             TEXT NOT NULL DEFAULT '',
    spec_file             TEXT NOT NULL DEFAULT '',
    error_message         TEXT NOT NULL DEFAULT '',
    screenshot_path       TEXT NOT NULL DEFAULT '',
    severity              TEXT NOT NULL DEFAULT 'medium',
    kanban_task_id        TEXT NOT NULL DEFAULT '',
    healing_attempted     INTEGER NOT NULL DEFAULT 0,
    healing_succeeded     INTEGER NOT NULL DEFAULT 0,
    healed_selector       TEXT NOT NULL DEFAULT '',
    created_at            TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ace_qa_failures_run      ON ace_qa_failures(run_id);
CREATE INDEX IF NOT EXISTS idx_ace_qa_failures_severity ON ace_qa_failures(severity);
"""

# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------


def init() -> None:
    """Create all ACE canvas tables (idempotent)."""
    from icdev.tools.db.storage import get_canvas_connection

    conn = get_canvas_connection("ICDEV_ACE_DB_URL")
    try:
        conn.executescript(SCHEMA)
        conn.commit()
        # Phase 4: add control_refs to existing ace_audit_log installs (idempotent)
        try:
            conn.execute(
                "ALTER TABLE ace_audit_log ADD COLUMN control_refs TEXT NOT NULL DEFAULT ''"
            )
            conn.commit()
        except Exception:
            conn.rollback()  # column already exists — reset aborted PG txn
    finally:
        conn.close()


if __name__ == "__main__":
    init()
    from icdev.tools.db.storage import get_canvas_connection
    conn = get_canvas_connection("ICDEV_ACE_DB_URL")
    try:
        try:
            # PostgreSQL
            rows = conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name LIKE 'ace_%' ORDER BY table_name"
            ).fetchall()
            col = 0
        except Exception:
            # SQLite fallback
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'ace_%' ORDER BY name"
            ).fetchall()
            col = 0
        print(f"ACE canvas DB initialized: {len(rows)} tables")
        for r in rows:
            print(f"  {r[col]}")
    finally:
        conn.close()
