-- CUI // SP-CTI
-- Migration 206: Create domain_coverage table for orphaned-domain tracking.
--
-- Tracks coverage metrics per knowledge/data domain, including domains that
-- become orphaned (have no matching coverage data or whose source canvas no
-- longer exists).  Rows carry tenant_id + classification so all queries are
-- RLS-aware via tools.db.storage.get_connection().
--
-- Append-only intent: INSERT rows for each coverage scan; status transitions
-- ('active' -> 'orphaned' -> 'resolved') are tracked via separate rows so the
-- full audit trail is preserved.
--
-- CHECK constraints are derived from the constants below (single source of
-- truth); update both if the allowed value sets change.
--
--   STATUS_VALUES  = ('active', 'orphaned', 'resolved', 'pending')
--   ORPHAN_REASONS = ('no_canvas', 'no_source', 'schema_mismatch',
--                     'stale_data', 'removed_integration', NULL)

-- ── SQLite ────────────────────────────────────────────────────────────────
-- @sqlite-only
CREATE TABLE IF NOT EXISTS domain_coverage (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    domain_key       TEXT    NOT NULL,
    domain_name      TEXT    NOT NULL,
    domain_type      TEXT    NOT NULL DEFAULT 'knowledge'
                     CHECK (domain_type IN ('knowledge', 'data', 'system', 'integration', 'canvas')),
    source_canvas    TEXT,
    coverage_score   REAL    NOT NULL DEFAULT 0.0
                     CHECK (coverage_score >= 0.0 AND coverage_score <= 1.0),
    gap_count        INTEGER NOT NULL DEFAULT 0,
    status           TEXT    NOT NULL DEFAULT 'active'
                     CHECK (status IN ('active', 'orphaned', 'resolved', 'pending')),
    orphan_reason    TEXT
                     CHECK (orphan_reason IS NULL OR orphan_reason IN (
                         'no_canvas', 'no_source', 'schema_mismatch',
                         'stale_data', 'removed_integration'
                     )),
    last_checked_at  TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at      TEXT,
    detail           TEXT    NOT NULL DEFAULT '{}',
    tenant_id        TEXT    NOT NULL DEFAULT 'default',
    classification   TEXT    NOT NULL DEFAULT 'CUI',
    created_at       TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at       TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_domain_coverage_key      ON domain_coverage(domain_key);
CREATE INDEX IF NOT EXISTS idx_domain_coverage_status   ON domain_coverage(status);
CREATE INDEX IF NOT EXISTS idx_domain_coverage_canvas   ON domain_coverage(source_canvas);
CREATE INDEX IF NOT EXISTS idx_domain_coverage_score    ON domain_coverage(coverage_score);
CREATE INDEX IF NOT EXISTS idx_domain_coverage_tenant   ON domain_coverage(tenant_id);
CREATE INDEX IF NOT EXISTS idx_domain_coverage_checked  ON domain_coverage(last_checked_at);

-- ── PostgreSQL ────────────────────────────────────────────────────────────
-- @pg-only
CREATE TABLE IF NOT EXISTS domain_coverage (
    id               BIGSERIAL PRIMARY KEY,
    domain_key       TEXT    NOT NULL,
    domain_name      TEXT    NOT NULL,
    domain_type      TEXT    NOT NULL DEFAULT 'knowledge'
                     CHECK (domain_type IN ('knowledge', 'data', 'system', 'integration', 'canvas')),
    source_canvas    TEXT,
    coverage_score   REAL    NOT NULL DEFAULT 0.0
                     CHECK (coverage_score >= 0.0 AND coverage_score <= 1.0),
    gap_count        INTEGER NOT NULL DEFAULT 0,
    status           TEXT    NOT NULL DEFAULT 'active'
                     CHECK (status IN ('active', 'orphaned', 'resolved', 'pending')),
    orphan_reason    TEXT
                     CHECK (orphan_reason IS NULL OR orphan_reason IN (
                         'no_canvas', 'no_source', 'schema_mismatch',
                         'stale_data', 'removed_integration'
                     )),
    last_checked_at  TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    resolved_at      TEXT,
    detail           TEXT    NOT NULL DEFAULT '{}',
    tenant_id        TEXT    NOT NULL DEFAULT 'default',
    classification   TEXT    NOT NULL DEFAULT 'CUI',
    created_at       TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at       TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_domain_coverage_key      ON domain_coverage(domain_key);
CREATE INDEX IF NOT EXISTS idx_domain_coverage_status   ON domain_coverage(status);
CREATE INDEX IF NOT EXISTS idx_domain_coverage_canvas   ON domain_coverage(source_canvas);
CREATE INDEX IF NOT EXISTS idx_domain_coverage_score    ON domain_coverage(coverage_score);
CREATE INDEX IF NOT EXISTS idx_domain_coverage_tenant   ON domain_coverage(tenant_id);
CREATE INDEX IF NOT EXISTS idx_domain_coverage_checked  ON domain_coverage(last_checked_at);

-- @all
