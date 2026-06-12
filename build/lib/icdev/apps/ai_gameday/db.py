# CUI // SP-CTI
"""AI GameDay DB layer — all ttx_* tables via get_connection()."""

from tools.db.storage import get_connection

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
"""

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
    _migrated = True
