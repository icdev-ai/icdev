-- CUI // SP-CTI
-- Migration 192: Create fedramp_ato_packages and fedramp_controls tables.
--
-- fedramp_ato_packages tracks FedRAMP Authorization-to-Operate (ATO) packages
-- for cloud systems, recording authorization status, package type (low/moderate/
-- high), and authorizing official.
--
-- fedramp_controls records per-control implementation status for each ATO
-- package, capturing implementation origin (service provider, customer, hybrid,
-- or inherited), responsible role, and assessment details.
--
-- Both tables are defined in tools/security_canvas/db/init_db.py but lacked a
-- standalone migration.  Resolves the orphan_db_table gap detected by
-- tools/awareness/gap_detector.py.
--
-- Uses tenant_id + classification columns so queries are RLS-aware via
-- tools.db.storage.get_connection().
--
-- Idempotent (IF NOT EXISTS).

-- ── SQLite ────────────────────────────────────────────────────────────────
-- @sqlite-only
CREATE TABLE IF NOT EXISTS fedramp_ato_packages (
    id                   TEXT PRIMARY KEY,
    system_name          TEXT NOT NULL,
    ato_status           TEXT NOT NULL DEFAULT 'in_progress'
                         CHECK (ato_status IN (
                             'in_progress', 'authorized', 'conditional',
                             'denied', 'expired'
                         )),
    authorization_date   TEXT,
    expiry_date          TEXT,
    package_type         TEXT DEFAULT 'moderate'
                         CHECK (package_type IN ('low', 'moderate', 'high')),
    authorizing_official TEXT,
    notes                TEXT NOT NULL DEFAULT '',
    tenant_id            TEXT NOT NULL DEFAULT 'default',
    classification       TEXT NOT NULL DEFAULT 'CUI // SP-CTI',
    created_at           TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at           TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_fedramp_ato_pkgs_status ON fedramp_ato_packages (ato_status);
CREATE INDEX IF NOT EXISTS idx_fedramp_ato_pkgs_tenant ON fedramp_ato_packages (tenant_id);

CREATE TABLE IF NOT EXISTS fedramp_controls (
    id                     TEXT PRIMARY KEY,
    package_id             TEXT NOT NULL REFERENCES fedramp_ato_packages(id) ON DELETE CASCADE,
    control_id             TEXT NOT NULL,
    control_name           TEXT NOT NULL DEFAULT '',
    implementation_status  TEXT NOT NULL DEFAULT 'not_implemented'
                           CHECK (implementation_status IN (
                               'implemented', 'partially_implemented',
                               'planned', 'not_implemented', 'not_applicable'
                           )),
    implementation_origin  TEXT NOT NULL DEFAULT 'service_provider'
                           CHECK (implementation_origin IN (
                               'service_provider', 'customer', 'hybrid', 'inherited'
                           )),
    responsible_role       TEXT NOT NULL DEFAULT '',
    implementation_detail  TEXT NOT NULL DEFAULT '',
    assessment_date        TEXT,
    assessed_by            TEXT NOT NULL DEFAULT '',
    tenant_id              TEXT NOT NULL DEFAULT 'default',
    classification         TEXT NOT NULL DEFAULT 'CUI // SP-CTI',
    created_at             TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at             TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_fedramp_controls_package ON fedramp_controls (package_id);
CREATE INDEX IF NOT EXISTS idx_fedramp_controls_status  ON fedramp_controls (implementation_status);
CREATE INDEX IF NOT EXISTS idx_fedramp_controls_tenant  ON fedramp_controls (tenant_id);

-- ── PostgreSQL ────────────────────────────────────────────────────────────
-- @pg-only
CREATE TABLE IF NOT EXISTS fedramp_ato_packages (
    id                   TEXT        NOT NULL,
    system_name          TEXT        NOT NULL,
    ato_status           TEXT        NOT NULL DEFAULT 'in_progress'
                         CHECK (ato_status IN (
                             'in_progress', 'authorized', 'conditional',
                             'denied', 'expired'
                         )),
    authorization_date   DATE,
    expiry_date          DATE,
    package_type         TEXT        DEFAULT 'moderate'
                         CHECK (package_type IN ('low', 'moderate', 'high')),
    authorizing_official TEXT,
    notes                TEXT        NOT NULL DEFAULT '',
    tenant_id            TEXT        NOT NULL DEFAULT 'default',
    classification       TEXT        NOT NULL DEFAULT 'CUI // SP-CTI',
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- PRIMARY KEY is added idempotently by migration 196 and pg_consolidated.sql
-- (ALTER TABLE fedramp_ato_packages ADD CONSTRAINT fedramp_ato_packages_pkey PRIMARY KEY (id))
CREATE INDEX IF NOT EXISTS idx_fedramp_ato_pkgs_status ON fedramp_ato_packages (ato_status);
CREATE INDEX IF NOT EXISTS idx_fedramp_ato_pkgs_tenant ON fedramp_ato_packages (tenant_id);

CREATE TABLE IF NOT EXISTS fedramp_controls (
    id                     TEXT        PRIMARY KEY,
    package_id             TEXT        NOT NULL REFERENCES fedramp_ato_packages(id) ON DELETE CASCADE,
    control_id             TEXT        NOT NULL,
    control_name           TEXT        NOT NULL DEFAULT '',
    implementation_status  TEXT        NOT NULL DEFAULT 'not_implemented'
                           CHECK (implementation_status IN (
                               'implemented', 'partially_implemented',
                               'planned', 'not_implemented', 'not_applicable'
                           )),
    implementation_origin  TEXT        NOT NULL DEFAULT 'service_provider'
                           CHECK (implementation_origin IN (
                               'service_provider', 'customer', 'hybrid', 'inherited'
                           )),
    responsible_role       TEXT        NOT NULL DEFAULT '',
    implementation_detail  TEXT        NOT NULL DEFAULT '',
    assessment_date        DATE,
    assessed_by            TEXT        NOT NULL DEFAULT '',
    tenant_id              TEXT        NOT NULL DEFAULT 'default',
    classification         TEXT        NOT NULL DEFAULT 'CUI // SP-CTI',
    created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at             TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_fedramp_controls_package ON fedramp_controls (package_id);
CREATE INDEX IF NOT EXISTS idx_fedramp_controls_status  ON fedramp_controls (implementation_status);
CREATE INDEX IF NOT EXISTS idx_fedramp_controls_tenant  ON fedramp_controls (tenant_id);

-- @all
