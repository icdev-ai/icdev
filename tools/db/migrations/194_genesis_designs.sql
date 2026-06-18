-- CUI // SP-CTI
-- Migration 194: Create genesis_designs and genesis_reflexes tables.
--
-- genesis_designs records each Genesis design run (id, name, status,
-- current_phase).  genesis_reflexes records individual reflex firings
-- tied to a design run.  Both are referenced by:
--   tools/notification_service/event_service.py
--   tools/notification_service/handler_service.py
--   tools/notification_service/render_handler_service.py
--   tools/notification_service/digest_service.py
-- Resolves the orphan_db_table gap detected by tools/awareness/gap_detector.py.
--
-- Carries tenant_id + classification so queries are RLS-aware via
-- tools.db.storage.get_connection().
--
-- Idempotent (IF NOT EXISTS).

-- ── SQLite ────────────────────────────────────────────────────────────────
-- @sqlite-only
CREATE TABLE IF NOT EXISTS genesis_designs (
    id              TEXT    PRIMARY KEY,
    name            TEXT    NOT NULL DEFAULT '',
    status          TEXT    NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'running', 'completed', 'failed', 'cancelled')),
    current_phase   TEXT,
    detail_json     TEXT    DEFAULT '{}',
    tenant_id       TEXT    NOT NULL DEFAULT 'default',
    classification  TEXT    NOT NULL DEFAULT 'CUI',
    created_at      TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_gd_status    ON genesis_designs (status);
CREATE INDEX IF NOT EXISTS idx_gd_tenant    ON genesis_designs (tenant_id);
CREATE INDEX IF NOT EXISTS idx_gd_created   ON genesis_designs (created_at);

CREATE TABLE IF NOT EXISTS genesis_reflexes (
    id              TEXT    PRIMARY KEY,
    design_id       TEXT    NOT NULL,
    name            TEXT    NOT NULL,
    confidence      REAL    NOT NULL DEFAULT 0.0,
    fired_at        TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    result_json     TEXT    DEFAULT '{}',
    tenant_id       TEXT    NOT NULL DEFAULT 'default',
    classification  TEXT    NOT NULL DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_gr_design_id ON genesis_reflexes (design_id);
CREATE INDEX IF NOT EXISTS idx_gr_fired_at  ON genesis_reflexes (fired_at);
CREATE INDEX IF NOT EXISTS idx_gr_tenant    ON genesis_reflexes (tenant_id);

-- ── PostgreSQL ────────────────────────────────────────────────────────────
-- @pg-only
CREATE TABLE IF NOT EXISTS genesis_designs (
    id              TEXT    PRIMARY KEY,
    name            TEXT    NOT NULL DEFAULT '',
    status          TEXT    NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'running', 'completed', 'failed', 'cancelled')),
    current_phase   TEXT,
    detail_json     TEXT    DEFAULT '{}',
    tenant_id       TEXT    NOT NULL DEFAULT 'default',
    classification  TEXT    NOT NULL DEFAULT 'CUI',
    created_at      TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_gd_status    ON genesis_designs (status);
CREATE INDEX IF NOT EXISTS idx_gd_tenant    ON genesis_designs (tenant_id);
CREATE INDEX IF NOT EXISTS idx_gd_created   ON genesis_designs (created_at);

CREATE TABLE IF NOT EXISTS genesis_reflexes (
    id              TEXT    PRIMARY KEY,
    design_id       TEXT    NOT NULL,
    name            TEXT    NOT NULL,
    confidence      REAL    NOT NULL DEFAULT 0.0,
    fired_at        TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    result_json     TEXT    DEFAULT '{}',
    tenant_id       TEXT    NOT NULL DEFAULT 'default',
    classification  TEXT    NOT NULL DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_gr_design_id ON genesis_reflexes (design_id);
CREATE INDEX IF NOT EXISTS idx_gr_fired_at  ON genesis_reflexes (fired_at);
CREATE INDEX IF NOT EXISTS idx_gr_tenant    ON genesis_reflexes (tenant_id);

-- @all
