# CUI // SP-CTI
"""AI GameDay DB layer — all ttx_* tables via get_connection()."""

from tools.db.storage import column_exists, get_connection

_DDL = """
CREATE TABLE IF NOT EXISTS ttx_sessions (
    session_id INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario_slug TEXT NOT NULL,
    session_mode TEXT NOT NULL DEFAULT 'live',
    state TEXT NOT NULL DEFAULT 'pending',
    facilitator_name TEXT,
    join_code TEXT NOT NULL UNIQUE,
    duration_minutes INTEGER NOT NULL DEFAULT 120,
    max_teams INTEGER NOT NULL DEFAULT 8,
    started_at TEXT,
    ended_at TEXT,
    config_json TEXT DEFAULT '{}',
    ontology_tags_json TEXT DEFAULT '{}',
    tenant_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS ttx_teams (
    team_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES ttx_sessions(session_id),
    team_name TEXT NOT NULL,
    join_code TEXT NOT NULL UNIQUE,
    total_score INTEGER NOT NULL DEFAULT 0,
    rank_pos INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS ttx_team_members (
    member_id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id INTEGER NOT NULL REFERENCES ttx_teams(team_id),
    player_name TEXT NOT NULL,
    role_id TEXT NOT NULL,
    persona_json TEXT DEFAULT '{}',
    joined_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS ttx_injects (
    inject_id TEXT PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES ttx_sessions(session_id),
    slug TEXT NOT NULL,
    title TEXT NOT NULL,
    body_md TEXT,
    at_minute INTEGER,
    sequence_num INTEGER,
    depends_on_slug TEXT,
    state TEXT NOT NULL DEFAULT 'pending',
    config_json TEXT DEFAULT '{}',
    ontology_tags_json TEXT DEFAULT '{}',
    dispatched_at TEXT,
    closed_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS ttx_responses (
    response_id INTEGER PRIMARY KEY AUTOINCREMENT,
    team_id INTEGER NOT NULL REFERENCES ttx_teams(team_id),
    inject_id TEXT NOT NULL REFERENCES ttx_injects(inject_id),
    response_text TEXT,
    ai_receipts_json TEXT DEFAULT '[]',
    submitted_at TEXT NOT NULL DEFAULT (datetime('now')),
    time_taken_s REAL
);

CREATE TABLE IF NOT EXISTS ttx_scores (
    score_id INTEGER PRIMARY KEY AUTOINCREMENT,
    response_id INTEGER NOT NULL REFERENCES ttx_responses(response_id),
    team_id INTEGER NOT NULL REFERENCES ttx_teams(team_id),
    inject_id TEXT NOT NULL REFERENCES ttx_injects(inject_id),
    receipt_pts INTEGER NOT NULL DEFAULT 0,
    receipt_count INTEGER NOT NULL DEFAULT 0,
    judge_pts INTEGER NOT NULL DEFAULT 0,
    time_bonus_pts INTEGER NOT NULL DEFAULT 0,
    total_pts INTEGER NOT NULL DEFAULT 0,
    judge_rationale_json TEXT DEFAULT '{}',
    judged_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS ttx_api_log (
    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES ttx_sessions(session_id),
    team_id INTEGER NOT NULL REFERENCES ttx_teams(team_id),
    tool_slug TEXT NOT NULL,
    endpoint TEXT,
    call_id TEXT NOT NULL UNIQUE,
    result_hash TEXT,
    token_count INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0.0,
    called_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS ttx_leaderboard (
    lb_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES ttx_sessions(session_id),
    team_id INTEGER NOT NULL REFERENCES ttx_teams(team_id),
    rank_pos INTEGER NOT NULL DEFAULT 0,
    total_score INTEGER NOT NULL DEFAULT 0,
    receipt_pts INTEGER NOT NULL DEFAULT 0,
    judge_pts INTEGER NOT NULL DEFAULT 0,
    time_bonus_pts INTEGER NOT NULL DEFAULT 0,
    computed_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (session_id, team_id)
);

CREATE TABLE IF NOT EXISTS ttx_scenarios (
    scenario_id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    yaml_content TEXT NOT NULL,
    created_by TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS ttx_inject_templates (
    template_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    inject_type TEXT NOT NULL DEFAULT 'custom',
    body_md TEXT,
    rubric_json TEXT DEFAULT '{}',
    ai_tools_json TEXT DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Pre-session registration + snake-draft formation (gdx-reg-01). Both tables
-- previously existed only in tools/db/schema/pg_consolidated.sql with no
-- migration behind them, so a database built from migrations did not have them.
-- Migration 325 is the shared fix; these blocks keep the app self-bootstrapping
-- the same way every other ttx_* table here does.
CREATE TABLE IF NOT EXISTS ttx_registrations (
    registration_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES ttx_sessions(session_id),
    player_name TEXT NOT NULL,
    email TEXT,
    stated_skill TEXT NOT NULL,
    matched_role_id TEXT NOT NULL,
    matched_role_label TEXT NOT NULL,
    match_confidence REAL NOT NULL DEFAULT 1.0,
    match_method TEXT NOT NULL DEFAULT 'selected',
    match_reasoning TEXT,
    academy_username TEXT,
    registered_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS ttx_formation_plan (
    plan_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES ttx_sessions(session_id),
    registration_id INTEGER NOT NULL REFERENCES ttx_registrations(registration_id),
    team_slot INTEGER NOT NULL,
    team_name TEXT NOT NULL,
    confirmed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_ttx_teams_session ON ttx_teams(session_id);
CREATE INDEX IF NOT EXISTS idx_ttx_injects_session ON ttx_injects(session_id);
CREATE INDEX IF NOT EXISTS idx_ttx_injects_slug ON ttx_injects(session_id, slug);
CREATE INDEX IF NOT EXISTS idx_ttx_responses_team ON ttx_responses(team_id);
CREATE INDEX IF NOT EXISTS idx_ttx_responses_inject ON ttx_responses(inject_id);
CREATE INDEX IF NOT EXISTS idx_ttx_scores_team ON ttx_scores(team_id);
CREATE INDEX IF NOT EXISTS idx_ttx_api_log_team ON ttx_api_log(team_id, session_id);
CREATE INDEX IF NOT EXISTS idx_ttx_leaderboard_session ON ttx_leaderboard(session_id);
CREATE INDEX IF NOT EXISTS idx_ttx_registrations_session ON ttx_registrations(session_id);
CREATE INDEX IF NOT EXISTS idx_ttx_formation_plan_session ON ttx_formation_plan(session_id);
CREATE INDEX IF NOT EXISTS idx_ttx_formation_plan_registration ON ttx_formation_plan(registration_id);
"""

# Columns added after the tables shipped. The CREATE TABLE blocks above already
# include them for fresh installs; this list drives an idempotent, guarded
# ALTER-ADD for pre-existing tables created before the column existed (ontology
# tagging, penta-gd-03). Format: (table, column, column_def).
_ADD_COLUMNS = [
    ("ttx_sessions", "ontology_tags_json", "TEXT DEFAULT '{}'"),
    ("ttx_injects", "ontology_tags_json", "TEXT DEFAULT '{}'"),
    # lpx-teams-03: per-team spend attribution. ttx_api_log was a CALL log with
    # no cost columns; add token/cost so a facilitator can answer "what did each
    # team spend this exercise" from a single-store query (no cross-store join).
    ("ttx_api_log", "token_count", "INTEGER NOT NULL DEFAULT 0"),
    ("ttx_api_log", "cost_usd", "REAL NOT NULL DEFAULT 0.0"),
]

_migrated = False


def migrate() -> None:
    global _migrated
    if _migrated:
        return
    conn = get_connection()
    for stmt in _DDL.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
    for stmt in _INDEXES.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            try:
                conn.execute(stmt)
            except Exception:
                pass
    conn.commit()

    # Guarded column adds for tables that predate the column. CREATE TABLE
    # IF NOT EXISTS above is a no-op on such installs, so the column would
    # otherwise be missing and the ontology read/write paths in blueprint.py
    # would raise on PostgreSQL. Existence-check first to avoid poisoning the
    # PG transaction with an "already exists" error.
    for table, column, coldef in _ADD_COLUMNS:
        try:
            if not column_exists(conn, table, column):
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {coldef}")
                conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
    _migrated = True
