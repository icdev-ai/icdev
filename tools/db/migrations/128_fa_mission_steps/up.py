"""Migration 128 — create fa_mission_steps table.

Canonical schema registration for the FORGE Academy mission steps table.
The table is defined and owned by apps/forge_academy/db.py; this migration
registers it in the tools/db/migrations scope so that gap detection
(tools/awareness/gap_detector.py orphan_db_table rule) can resolve
references from tools/kanban/seed_m03_m05.py and other tool-layer callers.
"""
# CUI // SP-CTI

SQL = """
CREATE TABLE IF NOT EXISTS fa_mission_steps (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    mission_id           INTEGER NOT NULL REFERENCES fa_missions(id),
    step_num             INTEGER NOT NULL,
    title                TEXT NOT NULL,
    step_type            TEXT NOT NULL DEFAULT 'coding',
    content_path         TEXT,
    starter_code_path    TEXT,
    test_code_path       TEXT,
    config_schema_json   TEXT DEFAULT '{}',
    xp_partial           INTEGER NOT NULL DEFAULT 50,
    skill_tag            TEXT,
    hint_allowed         INTEGER NOT NULL DEFAULT 1,
    estimated_seconds    INTEGER DEFAULT 180
);

CREATE INDEX IF NOT EXISTS idx_fa_mission_steps_mission
    ON fa_mission_steps (mission_id);

CREATE INDEX IF NOT EXISTS idx_fa_mission_steps_mission_step
    ON fa_mission_steps (mission_id, step_num);
"""


def up(conn):
    for stmt in SQL.strip().split(";"):
        s = stmt.strip()
        if s:
            conn.execute(s)
    conn.commit()
