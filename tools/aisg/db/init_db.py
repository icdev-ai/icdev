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
"""

# Immutability triggers in SQLite dialect (BEGIN ... RAISE(ABORT) ... END).
# Kept OUT of SCHEMA: on PostgreSQL they are a syntax error, and the ';' split
# turns each trailing `END` into a bare COMMIT.
_SQLITE_TRIGGERS = """
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

# The PostgreSQL half of the same guarantee (NIST AU-9). Until this existed,
# aisg_audit on PostgreSQL -- the primary backend -- accepted UPDATE and DELETE:
# the SQLite triggers above were the only enforcement, and they never ran there.
# Same shape as sc_audit / nc_audit / bd_audit: one PL/pgSQL function that
# RAISEs, wired BEFORE UPDATE and BEFORE DELETE, FOR EACH ROW. Trigger names
# match the SQLite ones so "is aisg_audit immutable?" has one answer per name.
#
# Separate statements, executed one at a time with conn.execute -- NOT through
# executescript, whose ';' split would cut the $$-quoted function body in two.
# Every statement is idempotent (CREATE OR REPLACE / DROP ... IF EXISTS), so
# init_db and migration 20261008003850 can both run it, in either order.
PG_AUDIT_TRIGGER_STATEMENTS = (
    """CREATE OR REPLACE FUNCTION aisg_audit_immutable()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'Audit records are immutable — NIST AU-6';
END;
$$ LANGUAGE plpgsql""",
    "DROP TRIGGER IF EXISTS aisg_audit_no_update ON aisg_audit",
    """CREATE TRIGGER aisg_audit_no_update
    BEFORE UPDATE ON aisg_audit
    FOR EACH ROW EXECUTE FUNCTION aisg_audit_immutable()""",
    "DROP TRIGGER IF EXISTS aisg_audit_no_delete ON aisg_audit",
    """CREATE TRIGGER aisg_audit_no_delete
    BEFORE DELETE ON aisg_audit
    FOR EACH ROW EXECUTE FUNCTION aisg_audit_immutable()""",
)


def install_pg_audit_triggers(conn) -> None:
    """Make aisg_audit append-only on PostgreSQL. Caller commits.

    Raises on failure: whether to tolerate that is the caller's decision
    (init_db logs it; the migration lets it fail the run).
    """
    for stmt in PG_AUDIT_TRIGGER_STATEMENTS:
        conn.execute(stmt)


def init_db() -> None:
    conn = get_connection()
    try:
        # Both backends: on PostgreSQL StorageConnection.executescript strips
        # `--` comments before splitting on ';' and SAVEPOINT-isolates each
        # statement. The old PG loop skipped any chunk that began with a
        # comment (the CREATE under it included) and never rolled back, so one
        # failure aborted every later one.
        conn.executescript(SCHEMA)
        # Decide on the connection actually returned: get_connection() falls
        # back to SQLite when the PG connection cannot be made.
        if getattr(conn, "_backend", "sqlite") != "postgresql":
            conn.executescript(_SQLITE_TRIGGERS)
        else:
            try:
                install_pg_audit_triggers(conn)
            except Exception as exc:  # noqa: BLE001 — never block canvas start
                conn.rollback()
                print(
                    f"[init_db] WARNING: aisg_audit immutability triggers NOT "
                    f"installed on PostgreSQL: {exc}",
                    file=sys.stderr,
                )
        conn.commit()
        print(f"[init_db] AISG schema ready ({_AISG_BACKEND})", file=sys.stderr)
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
