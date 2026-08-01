# CUI // SP-CTI
"""AISG (AI Strategy Canvas) — DB initializer.

Dual-backend: PostgreSQL (default) or SQLite fallback.
DB file: data/aisg_canvas.db  |  env: AISG_STORAGE_BACKEND, AISG_DB_PATH
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_ICDEV_ROOT = Path(__file__).resolve().parents[3]
DB_PATH = Path(os.environ.get("AISG_DB_PATH", str(_ICDEV_ROOT / "data" / "aisg_canvas.db")))

_AISG_BACKEND = os.environ.get(
    "AISG_STORAGE_BACKEND",
    os.environ.get("ICDEV_CANVAS_STORAGE_BACKEND", "postgresql"),
).lower()


def get_connection():
    if _AISG_BACKEND == "postgresql":
        try:
            from tools.db.storage import get_canvas_connection
            return get_canvas_connection("AISG_PG_DATABASE")
        except Exception:
            pass
    import sqlite3
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS aisg_roadmaps (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT,
    roadmap_type    TEXT DEFAULT 'transformation',
    phases_json     TEXT DEFAULT '[]',
    status          TEXT DEFAULT 'active',
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS aisg_sprints (
    id              TEXT PRIMARY KEY,
    roadmap_id      TEXT REFERENCES aisg_roadmaps(id),
    sprint_number   INTEGER NOT NULL DEFAULT 1,
    name            TEXT NOT NULL,
    description     TEXT,
    goals_json      TEXT DEFAULT '[]',
    status          TEXT DEFAULT 'planned',
    start_date      TEXT DEFAULT '',
    end_date        TEXT DEFAULT '',
    velocity        REAL DEFAULT 0,
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS aisg_patterns (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    category        TEXT CHECK(category IN ('document_qa','procurement','threat_triage','compliance_evidence','custom')),
    description     TEXT,
    use_case        TEXT,
    deploy_config   TEXT DEFAULT '{}',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    tags            TEXT DEFAULT '[]',
    is_builtin      INTEGER DEFAULT 0,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    classification  TEXT DEFAULT 'CUI'
);

CREATE TABLE IF NOT EXISTS aisg_skills (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    skill_type      TEXT DEFAULT 'technical',
    description     TEXT,
    proficiency_levels_json TEXT DEFAULT '[]',
    gaps_json       TEXT DEFAULT '[]',
    status          TEXT DEFAULT 'active',
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS aisg_compliance_checks (
    id              TEXT PRIMARY KEY,
    roadmap_id      TEXT REFERENCES aisg_roadmaps(id),
    check_type      TEXT NOT NULL DEFAULT 'nist_ai_rmf',
    regime          TEXT DEFAULT 'nist_ai_rmf',
    findings_json   TEXT DEFAULT '[]',
    score           REAL DEFAULT 0,
    status          TEXT DEFAULT 'pending',
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS aisg_executive_summaries (
    id              TEXT PRIMARY KEY,
    roadmap_id      TEXT REFERENCES aisg_roadmaps(id),
    title           TEXT NOT NULL,
    summary         TEXT NOT NULL DEFAULT '',
    kpis_json       TEXT DEFAULT '{}',
    risk_flags_json TEXT DEFAULT '[]',
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS aisg_knowledge_handoffs (
    id              TEXT PRIMARY KEY,
    roadmap_id      TEXT REFERENCES aisg_roadmaps(id),
    handoff_type    TEXT DEFAULT 'transition',
    from_entity     TEXT NOT NULL,
    to_entity       TEXT NOT NULL,
    content_json    TEXT DEFAULT '{}',
    status          TEXT DEFAULT 'draft',
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS aisg_roi_tracking (
    id              TEXT PRIMARY KEY,
    roadmap_id      TEXT REFERENCES aisg_roadmaps(id),
    action_type     TEXT NOT NULL DEFAULT 'self_heal',
    count           INTEGER DEFAULT 0,
    minutes_saved   REAL DEFAULT 0,
    cost_saved_usd  REAL DEFAULT 0,
    period_start    TEXT DEFAULT '',
    period_end      TEXT DEFAULT '',
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS aisg_audit (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    roadmap_id      TEXT,
    actor           TEXT DEFAULT '',
    action          TEXT NOT NULL,
    detail          TEXT,
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_aisg_sprints_roadmap ON aisg_sprints(roadmap_id);
CREATE INDEX IF NOT EXISTS idx_aisg_compliance_roadmap ON aisg_compliance_checks(roadmap_id);
CREATE INDEX IF NOT EXISTS idx_aisg_exec_roadmap ON aisg_executive_summaries(roadmap_id);
CREATE INDEX IF NOT EXISTS idx_aisg_handoff_roadmap ON aisg_knowledge_handoffs(roadmap_id);
CREATE INDEX IF NOT EXISTS idx_aisg_roi_roadmap ON aisg_roi_tracking(roadmap_id);
CREATE INDEX IF NOT EXISTS idx_aisg_audit_roadmap ON aisg_audit(roadmap_id);

-- Immutability triggers (SQLite)
CREATE TRIGGER IF NOT EXISTS aisg_audit_no_update
    BEFORE UPDATE ON aisg_audit
    BEGIN
        SELECT RAISE(ABORT, 'Audit records are immutable — NIST AU-6');
    END;

CREATE TRIGGER IF NOT EXISTS aisg_audit_no_delete
    BEFORE DELETE ON aisg_audit
    BEGIN
        SELECT RAISE(ABORT, 'Audit records cannot be deleted');
    END;
"""


def init_db() -> None:
    conn = get_connection()
    try:
        if _AISG_BACKEND == "postgresql":
            for stmt in SCHEMA.split(";"):
                stmt = stmt.strip()
                if stmt and not stmt.startswith("--"):
                    try:
                        conn.execute(stmt)
                    except Exception:
                        pass
        else:
            conn.executescript(SCHEMA)
        conn.commit()
        print(f"[init_db] AISG schema ready ({_AISG_BACKEND})", file=sys.stderr)
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
