# CUI // SP-CTI
"""ACF — Autonomous Capability Foundry — schema initialization.

Creates the six ``foundry_*`` platform findings tables. These are NOT canvas
tables: every row carries ``tenant_id`` + ``classification`` so reads/writes go
through the RLS-aware ``tools.db.storage.get_connection()`` (NEVER
``get_canvas_connection()``), and the global RLS predicate filters every query to
the caller's tenant + clearance.

PG-first dual schema: ``_SCHEMA_SQLITE`` is canonical; ``_SCHEMA_PG`` is derived
from it with a single ``.replace()`` transform (autoincrement PK). Backend is
chosen at runtime from ``ICDEV_STORAGE_BACKEND`` (default ``sqlite``).

All CHECK constraints are derived from ``tools.foundry.constants`` so SQL and
Python never drift. ``init_db()`` is idempotent (``CREATE TABLE IF NOT EXISTS``)
and degrades gracefully (logs a warning, never raises) so a partially migrated
database does not crash an importer.

Append-only: foundry_runs / foundry_signals / foundry_specs /
foundry_tasks_emitted / foundry_outcomes (see APPEND_ONLY_TABLES in
.claude/hooks/pre_tool_use.py). foundry_concepts is mutable (status transitions).
"""
from __future__ import annotations

import os

from tools.foundry.constants import (
    CONCEPT_STATUSES,
    OUTCOME_VALUES,
    REJECT_REASONS,
    RUN_STATUSES,
    SOURCE_ENGINES,
    sql_in,
)
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.foundry.db")

_INIT_DONE = False

# CHECK predicates derived from constants (single source of truth).
_CHK_RUN_STATUS = sql_in("status", RUN_STATUSES)
_CHK_SOURCE_ENGINE = sql_in("source_engine", SOURCE_ENGINES)
_CHK_CONCEPT_STATUS = sql_in("status", CONCEPT_STATUSES)
_CHK_REJECT = sql_in("reject_reason", REJECT_REASONS)
_CHK_OUTCOME = sql_in("outcome", OUTCOME_VALUES)

# SQLite-flavored DDL (canonical). JSON payloads stored as TEXT in both backends.
_SCHEMA_SQLITE = f"""
CREATE TABLE IF NOT EXISTS foundry_runs (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_at           TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    harvested          INTEGER NOT NULL DEFAULT 0,
    concepts_proposed  INTEGER NOT NULL DEFAULT 0,
    concepts_approved  INTEGER NOT NULL DEFAULT 0,
    tasks_emitted      INTEGER NOT NULL DEFAULT 0,
    status             TEXT    NOT NULL DEFAULT 'running' CHECK({_CHK_RUN_STATUS}),
    detail             TEXT    DEFAULT '{{}}',
    tenant_id          TEXT    NOT NULL DEFAULT 'default',
    classification     TEXT    NOT NULL DEFAULT 'CUI',
    created_at         TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_foundry_runs_tenant ON foundry_runs(tenant_id);
CREATE INDEX IF NOT EXISTS idx_foundry_runs_status ON foundry_runs(status);

CREATE TABLE IF NOT EXISTS foundry_signals (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id             TEXT    NOT NULL,
    source_engine      TEXT    NOT NULL CHECK({_CHK_SOURCE_ENGINE}),
    source_ref         TEXT    NOT NULL,
    theme              TEXT,
    raw_score          REAL    DEFAULT 0.0,
    keywords           TEXT    DEFAULT '[]',
    content_hash       TEXT,
    tenant_id          TEXT    NOT NULL DEFAULT 'default',
    classification     TEXT    NOT NULL DEFAULT 'CUI',
    created_at         TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_foundry_signals_run ON foundry_signals(run_id);
CREATE INDEX IF NOT EXISTS idx_foundry_signals_engine ON foundry_signals(source_engine);
CREATE INDEX IF NOT EXISTS idx_foundry_signals_score ON foundry_signals(raw_score);
CREATE INDEX IF NOT EXISTS idx_foundry_signals_tenant ON foundry_signals(tenant_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_foundry_signals_hash ON foundry_signals(content_hash);

CREATE TABLE IF NOT EXISTS foundry_concepts (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id             TEXT    NOT NULL,
    name               TEXT    NOT NULL,
    slug               TEXT    NOT NULL UNIQUE,
    problem_statement  TEXT,
    proposed_capability TEXT,
    target_users       TEXT,
    cluster_signal_ids TEXT    DEFAULT '[]',
    novelty_score      REAL    DEFAULT 0.0,
    market_score       REAL    DEFAULT 0.0,
    fit_score          REAL    DEFAULT 0.0,
    effort_estimate    REAL    DEFAULT 0.0,
    compliance_risk    REAL    DEFAULT 0.0,
    composite_score    REAL    DEFAULT 0.0,
    status             TEXT    NOT NULL DEFAULT 'proposed' CHECK({_CHK_CONCEPT_STATUS}),
    reject_reason      TEXT    CHECK(reject_reason IS NULL OR {_CHK_REJECT}),
    tenant_id          TEXT    NOT NULL DEFAULT 'default',
    classification     TEXT    NOT NULL DEFAULT 'CUI',
    created_by         TEXT    NOT NULL DEFAULT 'system',
    created_at         TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at         TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_foundry_concepts_run ON foundry_concepts(run_id);
CREATE INDEX IF NOT EXISTS idx_foundry_concepts_status ON foundry_concepts(status);
CREATE INDEX IF NOT EXISTS idx_foundry_concepts_score ON foundry_concepts(composite_score);
CREATE INDEX IF NOT EXISTS idx_foundry_concepts_tenant ON foundry_concepts(tenant_id);

CREATE TABLE IF NOT EXISTS foundry_specs (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    concept_id         INTEGER NOT NULL REFERENCES foundry_concepts(id),
    spec_md            TEXT    NOT NULL,
    canvas_contract    TEXT    DEFAULT '{{}}',
    task_count         INTEGER NOT NULL DEFAULT 0,
    tenant_id          TEXT    NOT NULL DEFAULT 'default',
    classification     TEXT    NOT NULL DEFAULT 'CUI',
    created_at         TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_foundry_specs_concept ON foundry_specs(concept_id);
CREATE INDEX IF NOT EXISTS idx_foundry_specs_tenant ON foundry_specs(tenant_id);

CREATE TABLE IF NOT EXISTS foundry_tasks_emitted (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    concept_id         INTEGER NOT NULL REFERENCES foundry_concepts(id),
    kanban_task_id     TEXT    NOT NULL,
    epic               TEXT,
    seq                INTEGER NOT NULL DEFAULT 0,
    tenant_id          TEXT    NOT NULL DEFAULT 'default',
    classification     TEXT    NOT NULL DEFAULT 'CUI',
    created_at         TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_foundry_tasks_concept ON foundry_tasks_emitted(concept_id);
CREATE INDEX IF NOT EXISTS idx_foundry_tasks_tenant ON foundry_tasks_emitted(tenant_id);

CREATE TABLE IF NOT EXISTS foundry_outcomes (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    concept_id         INTEGER NOT NULL REFERENCES foundry_concepts(id),
    outcome            TEXT    NOT NULL CHECK({_CHK_OUTCOME}),
    metric             REAL,
    detail             TEXT    DEFAULT '{{}}',
    tenant_id          TEXT    NOT NULL DEFAULT 'default',
    classification     TEXT    NOT NULL DEFAULT 'CUI',
    created_at         TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_foundry_outcomes_concept ON foundry_outcomes(concept_id);
CREATE INDEX IF NOT EXISTS idx_foundry_outcomes_tenant ON foundry_outcomes(tenant_id);
"""


def _to_pg(sqlite_ddl: str) -> str:
    """Derive the PostgreSQL schema from the SQLite canonical DDL.

    The only backend-specific construct is the autoincrement primary key; every
    other column type (TEXT / INTEGER / REAL, TEXT-as-JSON, CURRENT_TIMESTAMP
    defaults) is valid in both backends.
    """
    return sqlite_ddl.replace(
        "INTEGER PRIMARY KEY AUTOINCREMENT", "BIGSERIAL PRIMARY KEY"
    )


_SCHEMA_PG = _to_pg(_SCHEMA_SQLITE)


def _is_pg() -> bool:
    return os.environ.get("ICDEV_STORAGE_BACKEND", "sqlite").lower() in (
        "postgresql",
        "postgres",
        "pg",
    )


def _ensure_content_hash(conn: Any) -> None:
    """Back-fill ``content_hash`` column + unique index for pre-existing tables."""
    cur = conn.cursor()
    try:
        cur.execute("ALTER TABLE foundry_signals ADD COLUMN content_hash TEXT")
        conn.commit()
    except Exception:
        pass
    try:
        cur.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_foundry_signals_hash ON foundry_signals(content_hash)"
        )
        conn.commit()
    except Exception:
        pass


def init_db(force: bool = False) -> bool:
    """Idempotently create the six ``foundry_*`` tables. Returns True on success.

    Safe to call repeatedly and from any importer: a failure is logged and
    swallowed (returns False) so a partially migrated DB never crashes a caller.
    """
    global _INIT_DONE
    if _INIT_DONE and not force:
        return True
    try:
        from tools.db.storage import get_connection

        schema = _SCHEMA_PG if _is_pg() else _SCHEMA_SQLITE
        conn = get_connection()
        try:
            cur = conn.cursor()
            for stmt in schema.split(";"):
                stmt = stmt.strip()
                if stmt:
                    cur.execute(stmt)
            conn.commit()
            _ensure_content_hash(conn)
        finally:
            conn.close()
        _INIT_DONE = True
        logger.info("foundry_* schema initialized (6 tables)")
        return True
    except Exception as exc:  # noqa: BLE001 - graceful degrade, never raise
        logger.warning("foundry db init error: %s", exc)
        return False


if __name__ == "__main__":  # pragma: no cover - manual invocation
    ok = init_db(force=True)
    print("foundry schema:", "ok" if ok else "FAILED")
