# CUI // SP-CTI
"""Mission Canvas — DB initializer.

Dual-backend: PostgreSQL (default) or SQLite fallback.
DB file: data/mission_canvas.db  |  env: MCAN_STORAGE_BACKEND, MCAN_DB_PATH
"""
from __future__ import annotations

import os
from pathlib import Path

_ICDEV_ROOT = Path(__file__).resolve().parents[3]
DB_PATH = Path(os.environ.get("MCAN_DB_PATH", str(_ICDEV_ROOT / "data" / "mission_canvas.db")))

# Env var carrying the dedicated SQLite path when the resolved backend is sqlite.
# On PostgreSQL (primary) it is ignored; mission_* tables live in the shared
# icdev database, namespaced by their table prefix.
_MCAN_DB_PATH_ENV = "MCAN_DB_PATH"
os.environ.setdefault(_MCAN_DB_PATH_ENV, str(DB_PATH))

_MCAN_BACKEND = os.environ.get(
    "MCAN_STORAGE_BACKEND",
    os.environ.get("ICDEV_CANVAS_STORAGE_BACKEND", "postgresql"),
).lower()


def get_connection():
    """Get a canvas database connection — RLS disabled (cnr-mc-01).

    mission_* tables carry no tenant_id/classification columns, so the global
    RLS predicate injected by tools.db.storage.get_connection would raise
    UndefinedColumn on PostgreSQL. get_canvas_connection returns a
    StorageConnection with security context None — %s placeholders translate per
    backend, and the dedicated SQLite file (MCAN_DB_PATH) is used only when the
    resolved backend is sqlite.
    """
    from tools.db.storage import get_canvas_connection
    return get_canvas_connection(_MCAN_DB_PATH_ENV)


SCHEMA = """
CREATE TABLE IF NOT EXISTS mission_designs (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT,
    design_type     TEXT DEFAULT 'operational',
    graph_json      TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    template_id     TEXT,
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS mission_templates (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    category    TEXT,
    description TEXT,
    graph_json  TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    tags        TEXT DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS mission_assessments (
    id              TEXT PRIMARY KEY,
    design_id       TEXT REFERENCES mission_designs(id),
    assessment_type TEXT NOT NULL DEFAULT 'full',
    findings_json   TEXT DEFAULT '[]',
    score           REAL DEFAULT 0,
    grade           TEXT DEFAULT 'N/A',
    readiness_score REAL DEFAULT 0,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS mission_audit (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    design_id       TEXT,
    actor           TEXT DEFAULT '',
    action          TEXT NOT NULL,
    detail          TEXT,
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS mission_versions (
    id              TEXT PRIMARY KEY,
    design_id       TEXT REFERENCES mission_designs(id),
    version_number  INTEGER NOT NULL,
    graph_json      TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    change_summary  TEXT DEFAULT '',
    user_id         TEXT DEFAULT '',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS mission_portfolios (
    id              TEXT PRIMARY KEY,
    design_id       TEXT REFERENCES mission_designs(id),
    name            TEXT NOT NULL,
    description     TEXT,
    portfolio_type  TEXT DEFAULT 'program',
    metrics_json    TEXT DEFAULT '{}',
    status          TEXT DEFAULT 'active',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS mission_twin_snapshots (
    id              TEXT PRIMARY KEY,
    design_id       TEXT NOT NULL REFERENCES mission_designs(id) ON DELETE CASCADE,
    snapshot_name   TEXT DEFAULT '',
    snapshot_json   TEXT NOT NULL DEFAULT '{}',
    status          TEXT DEFAULT 'current',
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS mission_evidence (
    id              TEXT PRIMARY KEY,
    design_id       TEXT NOT NULL REFERENCES mission_designs(id) ON DELETE CASCADE,
    evidence_type   TEXT NOT NULL DEFAULT 'document',
    title           TEXT NOT NULL,
    content         TEXT DEFAULT '',
    source          TEXT DEFAULT '',
    provenance_json TEXT DEFAULT '{}',
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS mission_narratives (
    id              TEXT PRIMARY KEY,
    design_id       TEXT NOT NULL REFERENCES mission_designs(id) ON DELETE CASCADE,
    narrative_type  TEXT DEFAULT 'sitrep',
    title           TEXT NOT NULL,
    content         TEXT NOT NULL DEFAULT '',
    source_evidence_ids TEXT DEFAULT '[]',
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS mission_security_posture (
    id              TEXT PRIMARY KEY,
    design_id       TEXT NOT NULL REFERENCES mission_designs(id) ON DELETE CASCADE,
    zta_score       REAL DEFAULT 0,
    fedramp_status  TEXT DEFAULT 'not_started',
    il_level        TEXT DEFAULT 'IL4',
    findings_json   TEXT DEFAULT '[]',
    assessed_at     TEXT DEFAULT CURRENT_TIMESTAMP,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_mission_assessments_design ON mission_assessments(design_id);
CREATE INDEX IF NOT EXISTS idx_mission_audit_design ON mission_audit(design_id);
CREATE INDEX IF NOT EXISTS idx_mission_versions_design ON mission_versions(design_id);
CREATE INDEX IF NOT EXISTS idx_mission_portfolios_design ON mission_portfolios(design_id);
CREATE INDEX IF NOT EXISTS idx_mission_twin_design ON mission_twin_snapshots(design_id);
CREATE INDEX IF NOT EXISTS idx_mission_evidence_design ON mission_evidence(design_id);
CREATE INDEX IF NOT EXISTS idx_mission_narratives_design ON mission_narratives(design_id);
CREATE INDEX IF NOT EXISTS idx_mission_posture_design ON mission_security_posture(design_id);

-- Immutability triggers (SQLite)
CREATE TRIGGER IF NOT EXISTS mission_audit_no_update
    BEFORE UPDATE ON mission_audit
    BEGIN
        SELECT RAISE(ABORT, 'Audit records are immutable — NIST AU-6');
    END;

CREATE TRIGGER IF NOT EXISTS mission_audit_no_delete
    BEFORE DELETE ON mission_audit
    BEGIN
        SELECT RAISE(ABORT, 'Audit records cannot be deleted');
    END;
"""


def init_db() -> None:
    conn = get_connection()
    try:
        # StorageConnection.executescript handles both backends: native on
        # SQLite; on PostgreSQL it splits and isolates each statement in a
        # savepoint (SQLite-only triggers/DDL are skipped without aborting).
        conn.executescript(SCHEMA)
        conn.commit()
        print("[init_db] Mission Canvas schema ready")
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
