-- Migration: 20260808000000_sbom_components_pg_snapshot_gap
-- CUI // SP-CTI
--
-- Restore the three tables migration 209 creates, which a FRESH PostgreSQL
-- bootstrap does not have.
--
-- THE DEFECT
--
-- `tools/db/schema/pg_consolidated.sql` contains no `sbom_components`,
-- `supply_chain_vulnerabilities` or `supply_chain_risk_scores` — grep it. Its
-- sidecar marks the snapshot as covering everything through version 301, so
-- `bootstrap_pg` records 209 as applied WITHOUT running it. On a fresh PG the
-- three tables therefore never exist, while `schema_migrations` says they do.
-- This is the "snapshot omission breaks only fresh installs" failure: every
-- long-lived database ran 209 for real and is fine, so nothing surfaced it.
--
-- WHAT IT BROKE, SILENTLY
--
-- `20260808030213_sbom_2026_minimum_elements` (sbx-fnd-02) ALTERs
-- `sbom_components` and creates `sbom_dependencies` with a foreign key to it.
-- On a fresh PG both statements failed and were swallowed by executescript's
-- skip-failed-statement handling, so the migration recorded SUCCESS while
-- adding nothing: no Producer, Hash, Identifiers or explicit-unknown columns,
-- and no dependency edge table at all. `handler_service.py` SELECTs the two
-- supply-chain tables and would fail the same way.
--
-- sbx-cov-02 is what made it visible: its migration refuses to record itself as
-- applied when `sbom_dependencies` is absent, so CI failed loudly on the fresh
-- PostgreSQL tier instead of building another database that lies about its own
-- schema.
--
-- WHY THE VERSION IS DATED BEFORE 20260808030213
--
-- Ordering, not chronology. This has to run BEFORE sbx-fnd-02 so that
-- migration's ALTERs land on a table that exists; `baseline_versions` orders by
-- `_version_order_key`, under which every timestamp id sorts after the whole
-- numeric 001-341 family, so a timestamp below 20260808030213 is the only way
-- to sequence ahead of it. On a database where sbx-fnd-02 already ran this is a
-- pending no-op — CREATE TABLE IF NOT EXISTS throughout.
--
-- SHAPE
--
-- Exactly what 209 declares, not the current shape. sbx-fnd-02 runs next and
-- adds its own columns; duplicating them here would give that migration a
-- column that already exists, and its SQLite branch has no
-- ADD COLUMN IF NOT EXISTS.
--
-- SQLITE
--
-- Nothing. 209 is a real, replayable migration on SQLite and its
-- CREATE TABLE IF NOT EXISTS statements already run there. The defect is
-- specific to the PostgreSQL snapshot.

-- ── PostgreSQL ────────────────────────────────────────────────────────────
-- @pg-only

CREATE TABLE IF NOT EXISTS sbom_components (
    id              TEXT    PRIMARY KEY,
    component_name  TEXT    NOT NULL,
    version         TEXT,
    vendor          TEXT,
    component_type  TEXT    CHECK(component_type IN (
                                'library', 'framework', 'container', 'os',
                                'firmware', 'device', 'application', 'service', 'other')),
    purl            TEXT,
    license         TEXT,
    classification  TEXT    NOT NULL DEFAULT 'CUI',
    created_at      TEXT    DEFAULT (now()::text),
    updated_at      TEXT    DEFAULT (now()::text)
);
CREATE INDEX IF NOT EXISTS idx_sbom_comp_name   ON sbom_components(component_name);
CREATE INDEX IF NOT EXISTS idx_sbom_comp_vendor ON sbom_components(vendor);

CREATE TABLE IF NOT EXISTS supply_chain_vulnerabilities (
    id                TEXT    PRIMARY KEY,
    sbom_id           TEXT    NOT NULL REFERENCES sbom_components(id),
    cve_id            TEXT    NOT NULL,
    cvss_score        REAL    NOT NULL DEFAULT 0.0,
    severity          TEXT    CHECK(severity IN (
                                  'critical', 'high', 'medium', 'low',
                                  'informational', 'none')),
    affected_versions TEXT,
    fixed_version     TEXT,
    classification    TEXT    NOT NULL DEFAULT 'CUI',
    created_at        TEXT    DEFAULT (now()::text)
);
CREATE INDEX IF NOT EXISTS idx_scv_sbom_id    ON supply_chain_vulnerabilities(sbom_id);
CREATE INDEX IF NOT EXISTS idx_scv_cvss_score ON supply_chain_vulnerabilities(cvss_score);

CREATE TABLE IF NOT EXISTS supply_chain_risk_scores (
    id              TEXT    PRIMARY KEY,
    sbom_id         TEXT    NOT NULL REFERENCES sbom_components(id),
    risk_level      TEXT    CHECK(risk_level IN (
                                'critical', 'high', 'medium', 'low', 'none')),
    exploitability  TEXT    CHECK(exploitability IN (
                                'functional', 'poc', 'unproven', 'not_defined')),
    patch_available INTEGER NOT NULL DEFAULT 0,
    last_assessed   TEXT    NOT NULL,
    classification  TEXT    NOT NULL DEFAULT 'CUI',
    created_at      TEXT    DEFAULT (now()::text)
);
CREATE INDEX IF NOT EXISTS idx_scrs_sbom_id       ON supply_chain_risk_scores(sbom_id);
CREATE INDEX IF NOT EXISTS idx_scrs_last_assessed ON supply_chain_risk_scores(last_assessed);

-- @all
