"""Migration 137 — add game_key discriminator to gd_ai_* tables.

Makes the AI Game Engine tables multi-game-capable. Each row is tagged
with a game_key (e.g., 'gameday', 'compliance_challenge') so new game
packs can share the same schema without new tables.

Existing rows default to game_key='gameday'.
"""
# CUI // SP-CTI

SQL = """
ALTER TABLE gd_ai_tournaments      ADD COLUMN IF NOT EXISTS game_key TEXT NOT NULL DEFAULT 'gameday';
ALTER TABLE gd_ai_teams            ADD COLUMN IF NOT EXISTS game_key TEXT NOT NULL DEFAULT 'gameday';
ALTER TABLE gd_ai_rounds           ADD COLUMN IF NOT EXISTS game_key TEXT NOT NULL DEFAULT 'gameday';
ALTER TABLE gd_ai_artifacts        ADD COLUMN IF NOT EXISTS game_key TEXT NOT NULL DEFAULT 'gameday';
ALTER TABLE gd_ai_judge_evals      ADD COLUMN IF NOT EXISTS game_key TEXT NOT NULL DEFAULT 'gameday';
ALTER TABLE gd_ai_llmops_events    ADD COLUMN IF NOT EXISTS game_key TEXT NOT NULL DEFAULT 'gameday';
ALTER TABLE gd_ai_training_pairs   ADD COLUMN IF NOT EXISTS game_key TEXT NOT NULL DEFAULT 'gameday';
ALTER TABLE gd_ai_leaderboard      ADD COLUMN IF NOT EXISTS game_key TEXT NOT NULL DEFAULT 'gameday';

CREATE INDEX IF NOT EXISTS idx_gd_tournaments_game ON gd_ai_tournaments(game_key, status);
CREATE INDEX IF NOT EXISTS idx_gd_artifacts_game   ON gd_ai_artifacts(game_key, team_key);
CREATE INDEX IF NOT EXISTS idx_gd_leaderboard_game ON gd_ai_leaderboard(game_key, tournament_id, rank);
"""


def up(conn):
    for stmt in SQL.strip().split(";"):
        s = stmt.strip()
        if s:
            conn.execute(s)
    conn.commit()
