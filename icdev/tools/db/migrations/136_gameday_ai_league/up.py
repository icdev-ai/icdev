"""Migration 136 — AI GameDay League tables.

8 tables supporting autonomous 4-team AI competition: tournaments, teams,
rounds, artifacts, judge evaluations, LLMOps events, training pairs, and
live leaderboard.

Mutability (gdx-aud-01 — audited against every write site in tools/gameday/):

APPEND-ONLY (NIST AU immutable evidence; registered in APPEND_ONLY_TABLES in
.claude/hooks/pre_tool_use.py — never UPDATE/DELETE):
    gd_ai_artifacts       — one row per generated artifact
    gd_ai_llmops_events   — one row per inference call
    gd_ai_training_pairs  — one row per extracted training pair

MUTABLE BY DESIGN (live competition state, deliberately NOT append-only):
    gd_ai_tournaments  — status / current_round / started_at / completed_at
    gd_ai_teams        — cumulative score and counter deltas
    gd_ai_rounds       — status / started_at / completed_at
    gd_ai_judge_evals  — upsert on (round_id, team_id) so a round can be re-judged
    gd_ai_leaderboard  — recomputed snapshot, upsert on (tournament_id, team_id)

The append-only set above is the contract; tests/test_gdx_gameday_append_only.py
fails if this docstring and the hook ever disagree.
"""
# CUI // SP-CTI

SQL = """
CREATE TABLE IF NOT EXISTS gd_ai_tournaments (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    scenario_pack   TEXT NOT NULL DEFAULT 'cyber_adversarial',
    status          TEXT NOT NULL DEFAULT 'pending',
    round_count     INTEGER NOT NULL DEFAULT 5,
    round_duration_minutes INTEGER NOT NULL DEFAULT 60,
    current_round   INTEGER NOT NULL DEFAULT 0,
    config_json     TEXT NOT NULL DEFAULT '{}',
    started_at      TEXT,
    completed_at    TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS gd_ai_teams (
    id              SERIAL PRIMARY KEY,
    tournament_id   INTEGER NOT NULL REFERENCES gd_ai_tournaments(id),
    team_key        TEXT NOT NULL,
    name            TEXT NOT NULL,
    domain          TEXT NOT NULL,
    color           TEXT NOT NULL DEFAULT '#6c6c80',
    total_score     INTEGER NOT NULL DEFAULT 0,
    rounds_won      INTEGER NOT NULL DEFAULT 0,
    artifacts_suggested INTEGER NOT NULL DEFAULT 0,
    training_pairs_contributed INTEGER NOT NULL DEFAULT 0,
    UNIQUE(tournament_id, team_key)
);

CREATE TABLE IF NOT EXISTS gd_ai_rounds (
    id              SERIAL PRIMARY KEY,
    tournament_id   INTEGER NOT NULL REFERENCES gd_ai_tournaments(id),
    round_num       INTEGER NOT NULL,
    scenario_json   TEXT NOT NULL DEFAULT '{}',
    status          TEXT NOT NULL DEFAULT 'pending',
    started_at      TEXT,
    completed_at    TEXT,
    created_at      TEXT NOT NULL,
    UNIQUE(tournament_id, round_num)
);

CREATE TABLE IF NOT EXISTS gd_ai_artifacts (
    id              SERIAL PRIMARY KEY,
    round_id        INTEGER NOT NULL REFERENCES gd_ai_rounds(id),
    team_id         INTEGER NOT NULL REFERENCES gd_ai_teams(id),
    team_key        TEXT NOT NULL,
    member_role     TEXT NOT NULL,
    artifact_type   TEXT NOT NULL,
    content         TEXT NOT NULL,
    tokens_used     INTEGER NOT NULL DEFAULT 0,
    model_used      TEXT NOT NULL DEFAULT 'qwen3.5:9b',
    latency_ms      INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS gd_ai_judge_evals (
    id              SERIAL PRIMARY KEY,
    round_id        INTEGER NOT NULL REFERENCES gd_ai_rounds(id),
    team_id         INTEGER NOT NULL REFERENCES gd_ai_teams(id),
    team_key        TEXT NOT NULL,
    quality_score   REAL NOT NULL DEFAULT 0.0,
    innovation_score REAL NOT NULL DEFAULT 0.0,
    ethics_score    REAL NOT NULL DEFAULT 1.0,
    adversarial_score REAL NOT NULL DEFAULT 0.0,
    compliance_score REAL NOT NULL DEFAULT 1.0,
    total_score     INTEGER NOT NULL DEFAULT 0,
    routed_to_suggested INTEGER NOT NULL DEFAULT 0,
    training_pairs_extracted INTEGER NOT NULL DEFAULT 0,
    ethics_blocked  INTEGER NOT NULL DEFAULT 0,
    judge_notes     TEXT,
    created_at      TEXT NOT NULL,
    UNIQUE(round_id, team_id)
);

CREATE TABLE IF NOT EXISTS gd_ai_llmops_events (
    id              SERIAL PRIMARY KEY,
    tournament_id   INTEGER NOT NULL,
    round_id        INTEGER,
    team_key        TEXT NOT NULL,
    member_role     TEXT NOT NULL,
    model           TEXT NOT NULL,
    prompt_tokens   INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    latency_ms      INTEGER NOT NULL DEFAULT 0,
    error           TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS gd_ai_training_pairs (
    id              SERIAL PRIMARY KEY,
    round_id        INTEGER NOT NULL REFERENCES gd_ai_rounds(id),
    team_key        TEXT NOT NULL,
    member_role     TEXT NOT NULL,
    prompt          TEXT NOT NULL,
    completion      TEXT NOT NULL,
    quality_score   REAL NOT NULL DEFAULT 0.0,
    ft_dataset_id   TEXT,
    created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS gd_ai_leaderboard (
    id              SERIAL PRIMARY KEY,
    tournament_id   INTEGER NOT NULL REFERENCES gd_ai_tournaments(id),
    team_id         INTEGER NOT NULL REFERENCES gd_ai_teams(id),
    team_key        TEXT NOT NULL,
    rank            INTEGER,
    total_score     INTEGER NOT NULL DEFAULT 0,
    rounds_won      INTEGER NOT NULL DEFAULT 0,
    artifacts_suggested INTEGER NOT NULL DEFAULT 0,
    training_pairs_contributed INTEGER NOT NULL DEFAULT 0,
    avg_ethics_score REAL NOT NULL DEFAULT 1.0,
    avg_innovation_score REAL NOT NULL DEFAULT 0.0,
    updated_at      TEXT NOT NULL,
    UNIQUE(tournament_id, team_id)
);

CREATE INDEX IF NOT EXISTS idx_gd_artifacts_round ON gd_ai_artifacts(round_id);
CREATE INDEX IF NOT EXISTS idx_gd_artifacts_team ON gd_ai_artifacts(team_id);
CREATE INDEX IF NOT EXISTS idx_gd_evals_round ON gd_ai_judge_evals(round_id);
CREATE INDEX IF NOT EXISTS idx_gd_llmops_tournament ON gd_ai_llmops_events(tournament_id);
CREATE INDEX IF NOT EXISTS idx_gd_pairs_round ON gd_ai_training_pairs(round_id);
CREATE INDEX IF NOT EXISTS idx_gd_leaderboard_tournament ON gd_ai_leaderboard(tournament_id, rank);
"""


def up(conn):
    for stmt in SQL.strip().split(";"):
        s = stmt.strip()
        if s:
            conn.execute(s)
    conn.commit()
