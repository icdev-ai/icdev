-- CUI // SP-CTI
-- Migration 188: Create genesis_phase_log table.
--
-- genesis_phase_log records each phase execution for a Genesis design run.
-- Referenced by tools/notification_service/event_service.py,
-- digest_service.py, handler_service.py, and render_handler_service.py.
-- Resolves the orphan_db_table gap detected by tools/awareness/gap_detector.py.
--
-- Append-only NIST AU table. Phase log entries are immutable once written.
-- Carries tenant_id + classification so all queries are RLS-aware via
-- tools.db.storage.get_connection().
--
-- Idempotent (IF NOT EXISTS).

-- ── SQLite ────────────────────────────────────────────────────────────────
-- @sqlite-only
CREATE TABLE IF NOT EXISTS genesis_phase_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    design_id       TEXT    NOT NULL,
    phase           TEXT    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'running'
                    CHECK (status IN ('running', 'completed', 'failed', 'skipped')),
    started_at      TEXT,
    completed_at    TEXT,
    detail_json     TEXT    DEFAULT '{}',
    tenant_id       TEXT    NOT NULL DEFAULT 'default',
    classification  TEXT    NOT NULL DEFAULT 'CUI',
    created_at      TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_gpl_design_id    ON genesis_phase_log (design_id);
CREATE INDEX IF NOT EXISTS idx_gpl_completed_at ON genesis_phase_log (completed_at);
CREATE INDEX IF NOT EXISTS idx_gpl_tenant       ON genesis_phase_log (tenant_id);

-- ── PostgreSQL ────────────────────────────────────────────────────────────
-- @pg-only
CREATE TABLE IF NOT EXISTS genesis_phase_log (
    id              BIGSERIAL PRIMARY KEY,
    design_id       TEXT    NOT NULL,
    phase           TEXT    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'running'
                    CHECK (status IN ('running', 'completed', 'failed', 'skipped')),
    started_at      TEXT,
    completed_at    TEXT,
    detail_json     TEXT    DEFAULT '{}',
    tenant_id       TEXT    NOT NULL DEFAULT 'default',
    classification  TEXT    NOT NULL DEFAULT 'CUI',
    created_at      TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_gpl_design_id    ON genesis_phase_log (design_id);
CREATE INDEX IF NOT EXISTS idx_gpl_completed_at ON genesis_phase_log (completed_at);
CREATE INDEX IF NOT EXISTS idx_gpl_tenant       ON genesis_phase_log (tenant_id);

-- @all
