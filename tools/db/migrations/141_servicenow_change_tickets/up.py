# CUI // SP-CTI
"""Migration 141 — add servicenow_change_tickets table.

Stores ServiceNow change management tickets synced from ServiceNow
change_request table. Tracks RFCs, approvals, implementation status,
and their linkage to ICDEV migration plans and projects.
"""

SQL_UP = """
CREATE TABLE IF NOT EXISTS servicenow_change_tickets (
    id TEXT PRIMARY KEY,
    connection_id TEXT NOT NULL REFERENCES integration_connections(id) ON DELETE CASCADE,
    sys_id TEXT NOT NULL,
    number TEXT NOT NULL,
    short_description TEXT,
    description TEXT,
    type TEXT DEFAULT 'normal'
        CHECK(type IN ('normal', 'standard', 'emergency')),
    state TEXT DEFAULT 'new'
        CHECK(state IN ('new', 'assess', 'authorize', 'scheduled', 'implement', 'review', 'closed', 'canceled')),
    priority TEXT DEFAULT '3'
        CHECK(priority IN ('1', '2', '3', '4', '5')),
    risk TEXT DEFAULT 'moderate'
        CHECK(risk IN ('low', 'moderate', 'high', 'very_high')),
    impact TEXT DEFAULT 'moderate'
        CHECK(impact IN ('low', 'moderate', 'high', 'very_high')),
    category TEXT,
    assignment_group TEXT,
    assigned_to TEXT,
    requested_by TEXT,
    start_date TEXT,
    end_date TEXT,
    approval TEXT DEFAULT 'not requested'
        CHECK(approval IN ('not requested', 'requested', 'approved', 'rejected')),
    close_code TEXT,
    close_notes TEXT,
    project_id TEXT REFERENCES projects(id) ON DELETE SET NULL,
    migration_plan_id TEXT,
    sync_status TEXT DEFAULT 'synced'
        CHECK(sync_status IN ('synced', 'pending_push', 'pending_pull', 'conflict', 'error')),
    raw_json TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    last_synced TEXT DEFAULT (datetime('now')),
    UNIQUE(connection_id, sys_id)
);

CREATE INDEX IF NOT EXISTS idx_sn_change_connection
    ON servicenow_change_tickets (connection_id);
CREATE INDEX IF NOT EXISTS idx_sn_change_state
    ON servicenow_change_tickets (state);
CREATE INDEX IF NOT EXISTS idx_sn_change_project
    ON servicenow_change_tickets (project_id);
CREATE INDEX IF NOT EXISTS idx_sn_change_plan
    ON servicenow_change_tickets (migration_plan_id);
"""


def up(conn):
    for stmt in SQL_UP.strip().split(";"):
        s = stmt.strip()
        if s and not s.startswith("--"):
            conn.execute(s)
    conn.commit()
