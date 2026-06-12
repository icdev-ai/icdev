#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 117 — Mission Canvas tables.

Creates:
  mission_canvas_sessions   — active mission sessions
  mission_canvas_twins      — twin snapshots linked to sessions
  mission_canvas_evidence   — provenance links
  mission_canvas_alerts     — real-time alert queue

Idempotent: CREATE TABLE IF NOT EXISTS.
"""

import sys
from pathlib import Path

_repo_root = Path(__file__).resolve().parents[5]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

MIGRATION_ID = "117"
MIGRATION_NAME = "mission_canvas"
DESCRIPTION = "Create Mission Canvas tables for Program Command Center"

_DDL_SESSIONS = """
CREATE TABLE IF NOT EXISTS mission_canvas_sessions (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'active',
    classification TEXT DEFAULT 'CUI',
    metadata      TEXT,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
)
"""

_DDL_TWINS = """
CREATE TABLE IF NOT EXISTS mission_canvas_twins (
    id            TEXT PRIMARY KEY,
    session_id    TEXT NOT NULL,
    twin_type     TEXT NOT NULL DEFAULT 'data',
    snapshot_json TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at    TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES mission_canvas_sessions(id)
)
"""

_DDL_EVIDENCE = """
CREATE TABLE IF NOT EXISTS mission_canvas_evidence (
    id            TEXT PRIMARY KEY,
    session_id    TEXT NOT NULL,
    artifact_id   TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    source        TEXT NOT NULL,
    actor         TEXT,
    classification TEXT DEFAULT 'CUI',
    metadata      TEXT,
    created_at    TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES mission_canvas_sessions(id)
)
"""

_DDL_ALERTS = """
CREATE TABLE IF NOT EXISTS mission_canvas_alerts (
    id            TEXT PRIMARY KEY,
    session_id    TEXT NOT NULL,
    alert_type    TEXT NOT NULL,
    severity      TEXT NOT NULL DEFAULT 'medium',
    message       TEXT NOT NULL,
    classification TEXT DEFAULT 'CUI',
    acknowledged  INTEGER DEFAULT 0,
    created_at    TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES mission_canvas_sessions(id)
)
"""

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_mc_sessions_status ON mission_canvas_sessions (status)",
    "CREATE INDEX IF NOT EXISTS idx_mc_twins_session ON mission_canvas_twins (session_id)",
    "CREATE INDEX IF NOT EXISTS idx_mc_evidence_session ON mission_canvas_evidence (session_id)",
    "CREATE INDEX IF NOT EXISTS idx_mc_evidence_artifact ON mission_canvas_evidence (artifact_id)",
    "CREATE INDEX IF NOT EXISTS idx_mc_alerts_session ON mission_canvas_alerts (session_id)",
    "CREATE INDEX IF NOT EXISTS idx_mc_alerts_severity ON mission_canvas_alerts (severity, acknowledged)",
]


def up(conn=None) -> dict:
    from tools.db.storage import get_connection
    if conn is None:
        conn = get_connection()

    for ddl in (_DDL_SESSIONS, _DDL_TWINS, _DDL_EVIDENCE, _DDL_ALERTS):
        conn.execute(ddl)
    for idx in _INDEXES:
        conn.execute(idx)
    conn.commit()
    return {"status": "applied", "actions": ["mission_canvas_tables", "indexes_created"]}


def down(conn=None) -> dict:
    from tools.db.storage import get_connection
    if conn is None:
        conn = get_connection()
    conn.execute("DROP TABLE IF EXISTS mission_canvas_alerts")
    conn.execute("DROP TABLE IF EXISTS mission_canvas_evidence")
    conn.execute("DROP TABLE IF EXISTS mission_canvas_twins")
    conn.execute("DROP TABLE IF EXISTS mission_canvas_sessions")
    conn.commit()
    return {"status": "rolled_back"}


if __name__ == "__main__":
    result = up()
    print(result)
