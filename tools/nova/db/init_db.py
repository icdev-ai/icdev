# CUI // SP-CTI
"""NOVA — Initialize all NOVA DB tables (idempotent, PG-primary).

Tables created:
  agent_execution_traces    — ECHO: per-task execution trace events (append-only)
  agent_improvement_artifacts — ECHO: generational improvement text (append-only)
  ace_coworker_memory       — SOUL: per-role learned facts
  ace_trust_ledger          — TRUST: Bayesian trust event log (append-only)

All DDL uses CURRENT_TIMESTAMP / TEXT defaults compatible with both
PostgreSQL (primary) and SQLite (test fallback).  Never use strftime()
in production DDL — it is SQLite-only.

Usage:
    from tools.nova.db.init_db import init_nova_tables
    init_nova_tables()      # called at dashboard startup

CLI:
    python tools/nova/db/init_db.py
"""
from __future__ import annotations

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

# ── agent_execution_traces ──────────────────────────────────────────────────
_AGENT_EXECUTION_TRACES_DDL = """
CREATE TABLE IF NOT EXISTS agent_execution_traces (
    trace_id          TEXT PRIMARY KEY,
    task_id           TEXT NOT NULL,
    task_type         TEXT NOT NULL DEFAULT '',
    skill_used        TEXT NOT NULL DEFAULT '',
    outcome           TEXT NOT NULL DEFAULT 'unknown',
    events_json       TEXT NOT NULL DEFAULT '[]',
    lesson_pattern    TEXT NOT NULL DEFAULT '',
    improvement_notes TEXT NOT NULL DEFAULT '',
    started_at        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at      TEXT NOT NULL DEFAULT '',
    created_at        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

# ── agent_improvement_artifacts ─────────────────────────────────────────────
_AGENT_IMPROVEMENT_ARTIFACTS_DDL = """
CREATE TABLE IF NOT EXISTS agent_improvement_artifacts (
    artifact_id      TEXT PRIMARY KEY,
    task_type        TEXT NOT NULL,
    skill_used       TEXT NOT NULL DEFAULT '',
    generation_n     INTEGER NOT NULL DEFAULT 1,
    improvement_text TEXT NOT NULL,
    composite_score  REAL NOT NULL DEFAULT 0.0,
    baseline_score   REAL NOT NULL DEFAULT 0.0,
    evidence_traces  TEXT NOT NULL DEFAULT '[]',
    applied_count    INTEGER NOT NULL DEFAULT 0,
    status           TEXT NOT NULL DEFAULT 'pending',
    created_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    applied_at       TEXT,
    -- rem-cap-01: what GEPA decided about this artifact, and when. `status`
    -- only ever recorded 'applied', so an artifact GEPA declined stayed
    -- 'pending' forever and GEPA's work was invisible. Migration
    -- 20260816125047_gepa_decision_columns adds these to existing databases.
    gepa_decision    TEXT,
    gepa_decided_at  TEXT
)
"""

# ── ace_coworker_memory ─────────────────────────────────────────────────────
_ACE_COWORKER_MEMORY_DDL = """
CREATE TABLE IF NOT EXISTS ace_coworker_memory (
    id               TEXT PRIMARY KEY,
    role_id          TEXT NOT NULL,
    fact_type        TEXT NOT NULL DEFAULT 'observation',
    content          TEXT NOT NULL,
    confidence       REAL NOT NULL DEFAULT 0.8,
    source_task_id   TEXT NOT NULL DEFAULT '',
    created_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

# ── ace_trust_ledger ────────────────────────────────────────────────────────
_ACE_TRUST_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS ace_trust_ledger (
    id              TEXT PRIMARY KEY,
    role_id         TEXT NOT NULL,
    delta           REAL NOT NULL,
    reason          TEXT NOT NULL,
    new_score       REAL NOT NULL,
    source_task_id  TEXT NOT NULL DEFAULT '',
    recorded_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""

_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_aet_task_type  ON agent_execution_traces(task_type)",
    "CREATE INDEX IF NOT EXISTS idx_aet_task_id    ON agent_execution_traces(task_id)",
    "CREATE INDEX IF NOT EXISTS idx_aet_outcome    ON agent_execution_traces(outcome)",
    "CREATE INDEX IF NOT EXISTS idx_aia_task_type  ON agent_improvement_artifacts(task_type)",
    "CREATE INDEX IF NOT EXISTS idx_aia_status     ON agent_improvement_artifacts(status)",
    "CREATE INDEX IF NOT EXISTS idx_acm_role       ON ace_coworker_memory(role_id)",
    "CREATE INDEX IF NOT EXISTS idx_atl_role       ON ace_trust_ledger(role_id)",
]

# trust_score column on ace_coworkers — best-effort ADD COLUMN
_ACE_COWORKERS_TRUST_SCORE = "ALTER TABLE ace_coworkers ADD COLUMN trust_score REAL DEFAULT 0.5"


def _column_exists(conn, table: str, column: str) -> bool:
    try:
        from tools.db.storage import is_pg
        if is_pg(conn):
            row = conn.execute(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = %s AND column_name = %s",
                (table, column),
            ).fetchone()
        else:
            rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
            row = next(
                (r for r in rows if (r["name"] if hasattr(r, "keys") else r[1]) == column),
                None,
            )
        return row is not None
    except Exception:
        return False


def init_nova_tables(conn=None) -> dict:
    """Create all NOVA tables. Safe to call on any DB state (idempotent)."""
    from tools.db.storage import get_canvas_connection
    # NOVA system tables (agent_execution_traces, ace_trust_ledger, etc.) have no
    # classification/tenant_id columns — bypass RLS via get_canvas_connection.
    _close = conn is None
    if conn is None:
        conn = get_canvas_connection("NOVA_STORAGE_BACKEND")
    try:
        conn.execute(_AGENT_EXECUTION_TRACES_DDL)
        conn.execute(_AGENT_IMPROVEMENT_ARTIFACTS_DDL)
        conn.execute(_ACE_COWORKER_MEMORY_DDL)
        conn.execute(_ACE_TRUST_LEDGER_DDL)
        for idx in _INDEXES:
            conn.execute(idx)
        # Add trust_score to ace_coworkers if the table exists and the column is absent
        try:
            if not _column_exists(conn, "ace_coworkers", "trust_score"):
                conn.execute(_ACE_COWORKERS_TRUST_SCORE)
        except Exception:
            pass  # ace_coworkers table may not exist yet
        conn.commit()
        logger.info("[nova] NOVA tables initialized")
        return {"status": "ok", "tables": 4}
    except Exception as exc:
        logger.error("[nova] init_nova_tables failed: %s", exc)
        return {"status": "error", "error": str(exc)}
    finally:
        if _close:
            conn.close()


if __name__ == "__main__":
    import json
    print(json.dumps(init_nova_tables(), indent=2))
