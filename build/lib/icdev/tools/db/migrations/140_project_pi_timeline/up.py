# CUI // SP-CTI
"""Migration 140 — add project_pi_timeline table.

Stores per-project PI timeline entries for the 18-month contract-timeline
feature. Each row represents one Program Increment (PI) within a project's
overall timeline.
"""

SQL_UP = """
CREATE TABLE IF NOT EXISTS project_pi_timeline (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    pi_number TEXT NOT NULL,
    pi_name TEXT,
    pi_theme TEXT,
    pi_phase_group TEXT NOT NULL
        CHECK(pi_phase_group IN ('discovery_planning', 'execution', 'dashboard_integrations')),
    start_date TEXT,
    end_date TEXT,
    status TEXT NOT NULL DEFAULT 'planned'
        CHECK(status IN ('planned', 'active', 'completed', 'delayed')),
    milestones TEXT,
    progress_pct INTEGER DEFAULT 0
        CHECK(progress_pct >= 0 AND progress_pct <= 100),
    notes TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    UNIQUE(project_id, pi_number)
);

CREATE INDEX IF NOT EXISTS idx_project_pi_timeline_project
    ON project_pi_timeline (project_id);

CREATE INDEX IF NOT EXISTS idx_project_pi_timeline_phase_group
    ON project_pi_timeline (pi_phase_group);
"""


def up(conn):
    for stmt in SQL_UP.strip().split(";"):
        s = stmt.strip()
        if s and not s.startswith("--"):
            conn.execute(s)
    conn.commit()
