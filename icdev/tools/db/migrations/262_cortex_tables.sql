-- Migration 262: ICDEV Cortex session state + append-only governance audit trail.
-- CUI // SP-CTI
--
-- ctx-govern-03. Persists two tables behind the Cortex governance pipeline:
--   * cortex_sessions — per-caller Cortex session state (tenant/classification/
--     user/domain). Mutable lifecycle (status/updated_at).
--   * cortex_audit    — one append-only row per governed Cortex call (NIST AU).
--                       NEVER UPDATE/DELETE (enforced by APPEND_ONLY_TABLES in
--                       .claude/hooks/pre_tool_use.py).
--
-- PostgreSQL is THE runtime backend (repo is PG-primary); this DDL is authored
-- for PG first. It is deliberately written so the SQLite init-fallback applies
-- cleanly too WITHOUT relying on translate_sql (which only rewrites %s->? for
-- SQLite, not DDL types): the id columns are TEXT (not SERIAL, so there is no
-- autoincrement dialect gap), JSONB / TIMESTAMP / BOOLEAN DEFAULT FALSE are all
-- syntax SQLite tolerates verbatim. translate_sql stays init-fallback only,
-- never load-bearing.
--
-- Both tables carry tenant_id + classification, so they are RLS-governed: the
-- get_connection() row predicate filters reads by tenant and Bell-LaPadula
-- read-down. See tools/cortex/db/init_db.py for the connection-choice rationale.
--
-- Idempotent: CREATE TABLE / INDEX IF NOT EXISTS. Companion source of truth is
-- tools/cortex/db/init_db.py (SCHEMA_PG), which fresh databases run directly;
-- this migration materializes the same shape on databases bootstrapped earlier.

CREATE TABLE IF NOT EXISTS cortex_sessions (
    id              TEXT PRIMARY KEY,
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    classification  TEXT NOT NULL DEFAULT 'CUI',
    user_id         TEXT,
    domain          TEXT,
    air_gap         BOOLEAN NOT NULL DEFAULT FALSE,
    status          TEXT NOT NULL DEFAULT 'active',
    metadata_json   JSONB,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_cortex_sessions_tenant ON cortex_sessions(tenant_id);
CREATE INDEX IF NOT EXISTS idx_cortex_sessions_user ON cortex_sessions(user_id);

CREATE TABLE IF NOT EXISTS cortex_audit (
    id              TEXT PRIMARY KEY,
    session_id      TEXT,
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    classification  TEXT NOT NULL DEFAULT 'CUI',
    function        TEXT NOT NULL DEFAULT 'cortex',
    agent_id        TEXT,
    user_id         TEXT,
    gates_json      JSONB,
    outcome         TEXT NOT NULL DEFAULT 'pass'
        CHECK (outcome IN ('pass', 'warn', 'fail', 'blocked')),
    blocked         BOOLEAN NOT NULL DEFAULT FALSE,
    provenance_id   TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_cortex_audit_session ON cortex_audit(session_id);
CREATE INDEX IF NOT EXISTS idx_cortex_audit_tenant ON cortex_audit(tenant_id);
CREATE INDEX IF NOT EXISTS idx_cortex_audit_function ON cortex_audit(function);
CREATE INDEX IF NOT EXISTS idx_cortex_audit_created_at ON cortex_audit(created_at);
