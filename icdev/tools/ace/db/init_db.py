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

_BASE_SCHEMA = f"""
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
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ace_audit_instance ON ace_audit_log(instance_id);
"""

# ---------------------------------------------------------------------------
# ace_sessions — multi-turn conversation history (PG-primary)
# PG uses JSONB for conversation_history and TIMESTAMPTZ for timestamps.
# SQLite fallback stores the same data as TEXT / TEXT.
# ---------------------------------------------------------------------------

_SESSIONS_SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS ace_sessions (
    session_id             TEXT PRIMARY KEY,
    instance_id            TEXT NOT NULL REFERENCES ace_instances(id) ON DELETE CASCADE,
    conversation_history   TEXT NOT NULL DEFAULT '[]',
    resume_token           TEXT NOT NULL UNIQUE,
    last_user_message      TEXT,
    last_agent_message     TEXT,
    turn_count             INTEGER NOT NULL DEFAULT 0,
    created_at             TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at             TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ace_sessions_instance ON ace_sessions(instance_id);
CREATE INDEX IF NOT EXISTS idx_ace_sessions_resume ON ace_sessions(resume_token);
"""

_SESSIONS_SCHEMA_PG = """
CREATE TABLE IF NOT EXISTS ace_sessions (
    session_id             TEXT PRIMARY KEY,
    instance_id            TEXT NOT NULL REFERENCES ace_instances(id) ON DELETE CASCADE,
    conversation_history   JSONB NOT NULL DEFAULT '[]',
    resume_token           TEXT NOT NULL UNIQUE,
    last_user_message      TEXT,
    last_agent_message     TEXT,
    turn_count             INTEGER NOT NULL DEFAULT 0,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ace_sessions_instance ON ace_sessions(instance_id);
CREATE INDEX IF NOT EXISTS idx_ace_sessions_resume ON ace_sessions(resume_token);
"""

# SCHEMA keeps the SQLite variant appended so existing tests that import SCHEMA
# and open sqlite3.connect() directly continue to work unchanged.
SCHEMA = _BASE_SCHEMA + _SESSIONS_SCHEMA_SQLITE

# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------


def init() -> None:
    """Create all ACE canvas tables (idempotent)."""
    from icdev.tools.db.storage import get_canvas_connection, is_pg

    conn = get_canvas_connection("ICDEV_ACE_DB_URL")
    try:
        if is_pg(conn):
            conn.executescript(_BASE_SCHEMA)
            conn.executescript(_SESSIONS_SCHEMA_PG)
        else:
            conn.executescript(SCHEMA)
        conn.commit()
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
