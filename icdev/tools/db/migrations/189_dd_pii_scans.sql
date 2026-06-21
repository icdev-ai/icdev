-- CUI // SP-CTI
-- Migration 189: Create dd_pii_scans table.
--
-- dd_pii_scans persists PII scan results from tools/data_canvas/pii_scanner.py
-- (save_pii_scan). The table is written via tools.db.storage.get_connection()
-- so it lives in the main icdev.db and must carry tenant_id + classification
-- for RLS-aware reads.
--
-- Resolves the orphan_db_table gap detected by tools/awareness/gap_detector.py.
--
-- Idempotent (IF NOT EXISTS).

-- ── SQLite ────────────────────────────────────────────────────────────────
-- @sqlite-only
CREATE TABLE IF NOT EXISTS dd_pii_scans (
    scan_id         TEXT    PRIMARY KEY,
    design_id       TEXT,
    overall_risk    TEXT    DEFAULT 'none',
    findings_json   TEXT    DEFAULT '[]',
    scanned_at      TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    tenant_id       TEXT    NOT NULL DEFAULT 'default',
    classification  TEXT    NOT NULL DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_dd_pii_scans_design    ON dd_pii_scans (design_id);
CREATE INDEX IF NOT EXISTS idx_dd_pii_scans_risk      ON dd_pii_scans (overall_risk);
CREATE INDEX IF NOT EXISTS idx_dd_pii_scans_tenant    ON dd_pii_scans (tenant_id);

-- ── PostgreSQL ────────────────────────────────────────────────────────────
-- @pg-only
CREATE TABLE IF NOT EXISTS dd_pii_scans (
    scan_id         TEXT    PRIMARY KEY,
    design_id       TEXT,
    overall_risk    TEXT    DEFAULT 'none',
    findings_json   TEXT    DEFAULT '[]',
    scanned_at      TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    tenant_id       TEXT    NOT NULL DEFAULT 'default',
    classification  TEXT    NOT NULL DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_dd_pii_scans_design    ON dd_pii_scans (design_id);
CREATE INDEX IF NOT EXISTS idx_dd_pii_scans_risk      ON dd_pii_scans (overall_risk);
CREATE INDEX IF NOT EXISTS idx_dd_pii_scans_tenant    ON dd_pii_scans (tenant_id);

-- @all
