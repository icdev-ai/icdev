# CUI // SP-CTI
"""ACE (Autonomous Collaborative Engine) — DB initializer.

Creates every ACE canvas table defined in SCHEMA (instances, coworkers, messages,
artifacts, workflows, audit/step-audit logs, events, sessions, skill candidates,
webhook log, ...).  Uses get_canvas_connection() — NOT get_connection() —
because ace_* tables have no classification/tenant_id columns.

Usage:
    python -m icdev.tools.ace.db.init_db
    python -c "from icdev.tools.ace.db.init_db import init; init()"
"""
from __future__ import annotations

import re
from typing import Any

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
    -- NIST 800-53 references for the action, comma-separated. evidence_report
    -- reads this to build the control-traceability section; without the column
    -- its SELECT falls back and that section is silently always empty.
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

CREATE TABLE IF NOT EXISTS ace_sessions (
    session_id            TEXT PRIMARY KEY,
    instance_id           TEXT NOT NULL REFERENCES ace_instances(id) ON DELETE CASCADE,
    conversation_history  TEXT NOT NULL DEFAULT '[]',
    history_json          TEXT NOT NULL DEFAULT '[]',
    resume_token          TEXT NOT NULL UNIQUE,
    -- The last turn on each side, denormalised so resuming a session does not
    -- have to parse the whole history blob. Defined by the original
    -- ace_sessions commit (b76bf85ad) but only in the icdev/ mirror, so the
    -- next tools/ -> icdev/ sync deleted them.
    last_user_message     TEXT,
    last_agent_message    TEXT,
    turn_count            INTEGER NOT NULL DEFAULT 0,
    created_at            TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ace_sessions_instance ON ace_sessions(instance_id);
CREATE INDEX IF NOT EXISTS idx_ace_sessions_token ON ace_sessions(resume_token);

-- Skill promotion queue (tools/ace/skill_promoter.py + the ace_skill_promoter
-- reflex). The table existed in the live PostgreSQL database but had no DDL
-- anywhere in the repo -- no migration, no init -- so a fresh install had no way
-- to create it and every promoter write failed. Shape mirrors live PG.
CREATE TABLE IF NOT EXISTS ace_skill_candidates (
    id                TEXT PRIMARY KEY,
    role_id           TEXT NOT NULL,
    source_role       TEXT NOT NULL DEFAULT '',
    instance_id       TEXT NOT NULL DEFAULT '',
    candidate_yaml    TEXT NOT NULL DEFAULT '',
    trust_tier        TEXT NOT NULL DEFAULT 'yellow',
    status            TEXT NOT NULL DEFAULT 'pending',
    sipa_verdict      TEXT,
    sipa_score        REAL,
    rejection_reason  TEXT,
    created_at        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at       TEXT
);
CREATE INDEX IF NOT EXISTS idx_ace_skill_cand_status ON ace_skill_candidates(status);
CREATE INDEX IF NOT EXISTS idx_ace_skill_cand_role ON ace_skill_candidates(role_id);

-- Webhook delivery log (tools/ace/webhook.py::_log_attempt). One summary row per
-- delivery attempt sequence. Previously listed in controller._REQUIRED_ACE_TABLES
-- and written by webhook.py, but had NO CREATE TABLE anywhere -- a latent
-- "relation does not exist" on fresh installs. Columns mirror the INSERT in
-- _log_attempt; id AUTOINCREMENT supports the ORDER BY id read path. Append-only
-- (NIST AU) -- see APPEND_ONLY_TABLES in .claude/hooks/pre_tool_use.py.
CREATE TABLE IF NOT EXISTS ace_webhook_log (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    instance_id       TEXT NOT NULL DEFAULT '',
    url               TEXT NOT NULL DEFAULT '',
    status_code       INTEGER,
    response          TEXT,
    attempt_count     INTEGER NOT NULL DEFAULT 0,
    last_attempted_at TEXT,
    created_at        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ace_webhook_log_instance ON ace_webhook_log(instance_id);

-- Chat-initiated team proposals awaiting a human decision.
--
-- The implicit chat trigger used to LAUNCH a team the moment a message matched
-- enough RICOAS signals. This table is the "propose, then confirm" step: a row
-- is written, an action_card is rendered into the conversation, and nothing
-- runs until someone approves it. The explicit "@team ..." command still
-- launches immediately -- an explicit command IS the approval.
--
-- Deliberately NOT append-only: state genuinely transitions
-- proposed -> approved|declined|expired|launched, so it must not be added to
-- APPEND_ONLY_TABLES in .claude/hooks/pre_tool_use.py. Decisions are logged to
-- the append-only ace_audit_log instead, which is where the immutable record
-- belongs. (ace_skill_candidates is registered append-only yet updated in
-- place by skill_promoter -- do not repeat that.)
CREATE TABLE IF NOT EXISTS ace_team_suggestions (
    id                  TEXT PRIMARY KEY,
    context_id          TEXT NOT NULL DEFAULT '',
    user_id             TEXT NOT NULL DEFAULT 'system',
    project_id          TEXT NOT NULL DEFAULT '',
    problem_text        TEXT NOT NULL DEFAULT '',
    proposed_roles_json TEXT NOT NULL DEFAULT '[]',
    sme_gaps_json       TEXT NOT NULL DEFAULT '[]',
    state               TEXT NOT NULL DEFAULT 'proposed'
                        CHECK(state IN ('proposed','approved','declined','expired','launched')),
    instance_id         TEXT NOT NULL DEFAULT '',
    decline_reason      TEXT NOT NULL DEFAULT '',
    created_at          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    decided_at          TEXT
);
CREATE INDEX IF NOT EXISTS idx_ace_suggestions_context ON ace_team_suggestions(context_id);
CREATE INDEX IF NOT EXISTS idx_ace_suggestions_state ON ace_team_suggestions(state);
"""

# ---------------------------------------------------------------------------
# Constraint repair (PostgreSQL only)
# ---------------------------------------------------------------------------

# state CHECK constraints keyed by table -> (constraint_name, allowed states).
# CREATE TABLE IF NOT EXISTS never repairs a constraint on a pre-existing table,
# so a live PostgreSQL database whose ace_instances_state_check /
# ace_coworkers_state_check drifted away from these Python constants keeps
# raising CheckViolation on every state transition (silently swallowed at the
# call site). repair_state_constraints() re-derives the constraint from the
# constants and rewrites it in place when it has drifted.
_STATE_CONSTRAINTS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("ace_instances", "ace_instances_state_check", INSTANCE_STATES),
    ("ace_coworkers", "ace_coworkers_state_check", COWORKER_STATES),
)

# Matches every single-quoted token inside a CHECK(... IN (...)) definition.
_QUOTED_RE = re.compile(r"'([^']*)'")


def repair_state_constraints(conn: Any) -> dict[str, str]:
    """Rewrite drifted ``state`` CHECK constraints from the Python constants.

    PostgreSQL only. On SQLite this is a no-op (the test harness recreates the
    tables from :data:`SCHEMA`, so the constraint is always fresh there).

    For each managed constraint we read ``pg_get_constraintdef`` and compare the
    set of allowed values it encodes against the set derived from the module
    constant. If they differ (or the constraint is missing) we DROP and re-ADD
    it inside a single transaction so the table is never left unconstrained.

    Returns a ``{table: action}`` map where action is ``"ok"`` (already
    matched), ``"repaired"``, ``"added"``, or ``"skipped:<reason>"``. Idempotent:
    a second call after a repair reports ``"ok"`` for every constraint.
    """
    from icdev.tools.db.storage import is_pg

    results: dict[str, str] = {}
    if not is_pg(conn):
        for table, _cname, _states in _STATE_CONSTRAINTS:
            results[table] = "skipped:sqlite"
        return results

    for table, cname, states in _STATE_CONSTRAINTS:
        expected = set(states)
        try:
            row = conn.execute(
                "SELECT pg_get_constraintdef(c.oid) "
                "FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
                "WHERE t.relname = %s AND c.conname = %s",
                (table, cname),
            ).fetchone()
        except Exception as exc:  # information_schema / pg_catalog unavailable
            results[table] = f"skipped:{type(exc).__name__}"
            continue

        current = set(_QUOTED_RE.findall(row[0])) if row else None
        if current == expected:
            results[table] = "ok"
            continue

        joined = ", ".join(f"'{v}'" for v in states)
        try:
            if row is not None:
                conn.execute(f"ALTER TABLE {table} DROP CONSTRAINT {cname}")
            conn.execute(
                f"ALTER TABLE {table} ADD CONSTRAINT {cname} "
                f"CHECK (state IN ({joined}))"
            )
            conn.commit()
            results[table] = "repaired" if row is not None else "added"
        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            results[table] = f"skipped:{type(exc).__name__}"

    return results


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------


def init() -> None:
    """Create all ACE canvas tables (idempotent) and repair drifted constraints."""
    from icdev.tools.db.storage import get_canvas_connection

    conn = get_canvas_connection("ICDEV_ACE_DB_URL")
    try:
        conn.executescript(SCHEMA)
        conn.commit()
        # Columns added to tables that already exist. CREATE TABLE IF NOT
        # EXISTS cannot add a column to a table that is already there, so a
        # live database never picks these up from SCHEMA alone.
        for _ddl in (
            "ALTER TABLE ace_audit_log ADD COLUMN control_refs TEXT NOT NULL DEFAULT ''",
            "ALTER TABLE ace_sessions ADD COLUMN last_user_message TEXT",
            "ALTER TABLE ace_sessions ADD COLUMN last_agent_message TEXT",
        ):
            try:
                conn.execute(_ddl)
                conn.commit()
            except Exception:
                # Column already exists. rollback(), not pass: PostgreSQL aborts
                # the whole transaction on a failed DDL, so every later
                # statement would fail too if the txn were left in that state.
                conn.rollback()
        # Repair state CHECK constraints that CREATE TABLE IF NOT EXISTS can't
        # fix on a pre-existing (live PG) table. No-op on SQLite.
        try:
            repair_state_constraints(conn)
        except Exception:
            pass  # constraint repair is best-effort; never block init
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
